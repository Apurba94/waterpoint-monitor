"""A small offline web app kit, shared by the suite. It uses only the standard library.

Apps run on your own computer at 127.0.0.1, with all data in one SQLite file, so they work
with no internet. Everything shown is escaped, POST forms carry a token that a page from
another site cannot read, and requests naming an unexpected host are refused.
"""
import csv
import html
import io
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

h = html.escape

CSS = """
:root { --bg:#f6f7f9; --card:#fff; --ink:#1c2430; --muted:#5d6878; --line:#dde2e8; --accent:#0f6e8c; --bad:#b3261e; --good:#1b7f4b; --warn:#9a6700; }
@media (prefers-color-scheme: dark) { :root { --bg:#12161c; --card:#1b2129; --ink:#e7ebf0; --muted:#9aa6b4; --line:#2e3742; --accent:#5cc2e0; --bad:#f28b82; --good:#6fd39a; --warn:#e5b95c; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, "Noto Sans Bengali", "Nirmala UI", sans-serif; }
header { background:var(--card); border-bottom:1px solid var(--line); padding:12px 16px; }
header b { font-size:18px; margin-right:16px; }
nav { display:flex; flex-wrap:wrap; gap:4px 14px; margin-top:6px; }
nav a { color:var(--accent); text-decoration:none; padding:4px 0; }
main { max-width:1040px; margin:0 auto; padding:16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; margin:0 0 16px; overflow-x:auto; }
h1 { font-size:24px; margin:0 0 12px; } h2 { font-size:18px; margin:0 0 8px; }
table { border-collapse:collapse; width:100%; } th, td { text-align:left; padding:8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; }
label { display:block; margin:0 0 12px; font-weight:600; }
input, select, textarea { display:block; width:100%; max-width:440px; min-height:42px; padding:8px; font:inherit; color:inherit; background:var(--bg); border:1px solid var(--line); border-radius:6px; margin-top:4px; }
input[type=checkbox], input[type=radio] { display:inline-block; width:18px; height:18px; min-height:0; padding:0; margin:0 6px 0 0; vertical-align:middle; accent-color:var(--accent); }
a { color:var(--accent); }
button, .button { display:inline-block; min-height:42px; padding:8px 16px; font:inherit; font-weight:600; color:#fff; background:var(--accent); border:0; border-radius:6px; cursor:pointer; text-decoration:none; }
@media (prefers-color-scheme: dark) { button, .button { color:#10222b; } }
.stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:12px; margin-bottom:16px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; } .stat b { display:block; font-size:24px; } .stat span { color:var(--muted); font-size:14px; }
.flash { padding:12px 16px; border-radius:8px; margin-bottom:16px; border:1px solid var(--line); background:var(--card); }
.bad { color:var(--bad); } .good { color:var(--good); } .warn { color:var(--warn); } .muted { color:var(--muted); }
svg text { fill:var(--ink); font-size:12px; } svg .bar { fill:var(--accent); } svg .line { stroke:var(--accent); fill:none; stroke-width:2; } svg .axis { stroke:var(--line); }
@media (max-width: 640px) {
  table.stack thead { display:none; } table.stack, table.stack tbody, table.stack tr, table.stack td { display:block; width:100%; }
  table.stack tr { padding:8px 0; border-bottom:1px solid var(--line); } table.stack td { border:0; padding:3px 0; }
  table.stack td[data-label]:not([data-label=""])::before { content:attr(data-label); display:block; font-size:13px; color:var(--muted); }
}
@media print { header, nav, form, .no-print { display:none; } body { background:#fff; color:#000; } .card { border:0; padding:0; } }
"""


class Fail(Exception):
    """Show a message to the person using the app, in place of the page."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Request:
    def __init__(self, method, path, query, form, params=None):
        self.method, self.path, self.query, self.form, self.params = method, path, query, form, params or {}

    def get(self, name, default=""):
        return self.form.get(name, self.query.get(name, default))

    def number(self, name, default=None, low=None, high=None):
        text = str(self.get(name, "")).strip()
        if text == "":
            if default is None:
                raise Fail(f"{name} is needed")
            return default
        try:
            v = float(text)
        except ValueError:
            raise Fail(f"{name} must be a number") from None
        if v != v or v in (float("inf"), float("-inf")):
            raise Fail(f"{name} must be a number")
        if (low is not None and v < low) or (high is not None and v > high):
            raise Fail(f"{name} must be between {low} and {high}")
        return v

    def text(self, name, required=True, limit=2000):
        v = str(self.get(name, "")).strip()
        if required and not v:
            raise Fail(f"{name} is needed")
        if len(v) > limit:
            raise Fail(f"{name} is too long")
        return v

    def date(self, name, required=True):
        v = str(self.get(name, "")).strip()
        if not v and not required:
            return ""
        try:
            return datetime.strptime(v, "%Y-%m-%d").date().isoformat()
        except ValueError:
            raise Fail(f"{name} must be a date like 2026-11-02") from None


class Response:
    def __init__(self, body="", status=200, kind="text/html; charset=utf-8", headers=None):
        self.body = body.encode() if isinstance(body, str) else body
        self.status, self.kind, self.headers = status, kind, headers or {}


def redirect(url):
    return Response("", 303, headers={"Location": url})


def to_csv(rows, columns):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([safe_cell(r[c] if not isinstance(r, (list, tuple)) else r[i]) for i, c in enumerate(columns)])
    return out.getvalue()


def safe_cell(v):
    """Stop a value such as =HYPERLINK(...) from running as a formula when opened in a spreadsheet."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def download(rows, columns, name):
    return Response("﻿" + to_csv(rows, columns), kind="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


class App:
    def __init__(self, title, db=":memory:", schema="", nav=None, tagline=""):
        self.title, self.db_path, self.nav, self.tagline = title, db, nav or [], tagline
        self.routes = []
        self.token = secrets.token_hex(16)
        self.local = threading.local()
        self.lock = threading.Lock()
        self.shared = sqlite3.connect(db, check_same_thread=False, isolation_level=None) if db == ":memory:" else None
        if self.shared:
            self.shared.row_factory = sqlite3.Row
        self.c.execute("PRAGMA foreign_keys=ON")
        if db != ":memory:":
            self.c.execute("PRAGMA journal_mode=WAL")
        self.c.executescript(schema + "\nCREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');")

    @property
    def c(self):
        if self.shared:
            return self.shared
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            self.local.conn = conn
        return conn

    def rows(self, sql, *args):
        return self.c.execute(sql, args).fetchall()

    def row(self, sql, *args):
        return self.c.execute(sql, args).fetchone()

    def one(self, sql, *args):
        r = self.c.execute(sql, args).fetchone()
        return r[0] if r else None

    def run(self, sql, *args):
        return self.c.execute(sql, args)

    def log(self, action, detail=""):
        self.run("INSERT INTO audit (at, action, detail) VALUES (?, ?, ?)", datetime.now(timezone.utc).isoformat(timespec="seconds"), action, str(detail))

    def backup(self, path):
        target = sqlite3.connect(path)
        self.c.backup(target)
        target.close()

    def route(self, pattern, methods=("GET",)):
        rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

        def add(fn):
            self.routes.append((rx, methods, fn))
            return fn
        return add

    def form_token(self):
        return f'<input type="hidden" name="_token" value="{self.token}">'

    def page(self, title, body, flash=""):
        links = "".join(f'<a href="{h(u)}">{h(t)}</a>' for t, u in self.nav)
        note = f'<div class="flash">{h(flash)}</div>' if flash else ""
        return Response(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{h(title)} · {h(self.title)}</title><style>{CSS}</style></head><body><header><b>{h(self.title)}</b><nav>{links}</nav></header><main><h1>{h(title)}</h1>{note}{body}</main></body></html>')

    def handle(self, method, target, form=None, host="127.0.0.1", token=None):
        parts = urlsplit(target)
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        if host.split(":")[0] not in ("127.0.0.1", "localhost", "[::1]"):
            return _status(self.page("Refused", '<div class="card">This app only answers on this computer.</div>'), 403)
        form = form or {}
        if method == "POST" and not secrets.compare_digest(str(form.get("_token", token or "")), self.token):
            return _status(self.page("Refused", '<div class="card">This form has expired. Go back and reload the page.</div>'), 403)
        allowed = False
        for rx, methods, fn in self.routes:
            m = rx.match(parts.path)
            if m:
                allowed = True
                if method in methods:
                    req = Request(method, parts.path, query, form, m.groupdict())
                    with self.lock:
                        return self._call(fn, req, m.groupdict())
        if allowed:
            return Response("method not allowed", 405, "text/plain")
        return _status(self.page("Not found", '<div class="card">There is nothing here.</div>'), 404)

    def _call(self, fn, req, params):
        """Run a handler. A POST is one transaction, so a form either saves fully or not at all."""
        write = req.method == "POST"
        if write:
            self.run("BEGIN")
        try:
            out = fn(req, **params)
        except Fail as exc:
            self._undo(write)
            return _status(self.page("Please check this", f'<div class="card"><p class="bad">{h(str(exc))}</p><p><a href="javascript:history.back()">Go back</a></p></div>'), exc.status)
        except sqlite3.IntegrityError as exc:
            self._undo(write)
            return _status(self.page("Please check this", f'<div class="card"><p class="bad">That conflicts with existing records.</p><p class="muted">{h(str(exc))}</p></div>'), 409)
        except BaseException:
            self._undo(write)
            raise
        if write:
            self.run("COMMIT")
        return out if isinstance(out, Response) else self.page(self.title, out)

    def _undo(self, write):
        if write and self.c.in_transaction:
            self.run("ROLLBACK")

    def close(self):
        self.c.close()

    def __del__(self):
        try:
            if self.shared:
                self.shared.close()
        except Exception:
            pass

    def get(self, target):
        return self.handle("GET", target)

    def post(self, url, /, **form):
        return self.handle("POST", url, {k: str(v) for k, v in form.items()}, token=self.token)

    def serve(self, port=8000, host="127.0.0.1"):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, r):
                self.send_response(r.status)
                self.send_header("Content-Type", r.kind)
                self.send_header("Content-Length", str(len(r.body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                for k, v in r.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(r.body)

            def do_GET(self):
                self.send(app.handle("GET", self.path, host=self.headers.get("Host", "")))

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > 5_000_000:
                    self.send(Response("too large", 413, "text/plain"))
                    return
                data = self.rfile.read(n).decode("utf-8", "replace")
                form = {k: v[0] for k, v in parse_qs(data, keep_blank_values=True).items()}
                self.send(app.handle("POST", self.path, form, host=self.headers.get("Host", "")))
        server = ThreadingHTTPServer((host, port), Handler)
        print(f"{self.title} is running. Open http://127.0.0.1:{server.server_address[1]} in your browser. Press Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return server


def _status(response, status):
    response.status = status
    return response


def table(rows, columns, empty="Nothing here yet."):
    """columns: list of (heading, key or function). Values are escaped unless wrapped in Raw."""
    if not rows:
        return f'<p class="muted">{h(empty)}</p>'
    head = "".join(f"<th>{h(t)}</th>" for t, _ in columns)
    body = []
    for r in rows:
        cells = []
        for t, key in columns:
            v = key(r) if callable(key) else r[key]
            cells.append(f"<td data-label=\"{h(t)}\">{v.html if isinstance(v, Raw) else h('' if v is None else str(v))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="stack"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


class Raw:
    """HTML that was built safely and must not be escaped again."""

    def __init__(self, text):
        self.html = text


def link(url, text):
    return Raw(f'<a href="{h(url)}">{h(str(text))}</a>')


def form(app, action, fields, button="Save", inline=False):
    """fields: list of (name, label, kind, options). kind: text, number, date, select, textarea, checkbox, password."""
    out = [f'<form method="post" action="{h(action)}">{app.form_token()}']
    for f in fields:
        name, label, kind = f[0], f[1], f[2] if len(f) > 2 else "text"
        extra = f[3] if len(f) > 3 else None
        if kind == "select":
            opts = "".join(f'<option value="{h(str(v))}">{h(str(t))}</option>' for v, t in extra)
            out.append(f'<label>{h(label)}<select name="{h(name)}">{opts}</select></label>')
        elif kind == "textarea":
            out.append(f'<label>{h(label)}<textarea name="{h(name)}" rows="4">{h(str(extra or ""))}</textarea></label>')
        elif kind == "hidden":
            out.append(f'<input type="hidden" name="{h(name)}" value="{h(str(extra))}">')
        else:
            value = f' value="{h(str(extra))}"' if extra is not None else ""
            step = ' step="any"' if kind == "number" else ""
            out.append(f'<label>{h(label)}<input type="{kind}" name="{h(name)}"{value}{step}></label>')
    out.append(f"<button>{h(button)}</button></form>")
    return "".join(out)


def card(title, body):
    return f'<section class="card"><h2>{h(title)}</h2>{body}</section>'


def stats(items):
    return '<div class="stats">' + "".join(f'<div class="stat"><b>{h(str(v))}</b><span>{h(k)}</span></div>' for k, v in items) + "</div>"


def bar_chart(items, width=640, unit=""):
    """A horizontal bar chart as inline SVG. items: list of (label, value)."""
    if not items:
        return '<p class="muted">No data yet.</p>'
    top = max(max(v for _, v in items), 1e-9)
    row = 26
    out = [f'<svg viewBox="0 0 {width} {row * len(items) + 4}" role="img" width="100%">']
    for i, (label, v) in enumerate(items):
        w = max(v, 0) / top * (width - 260)
        out.append(f'<text x="0" y="{i * row + 17}">{h(str(label)[:24])}</text><rect class="bar" x="170" y="{i * row + 5}" width="{w:.1f}" height="16" rx="2"></rect><text x="{178 + w:.1f}" y="{i * row + 17}">{h(fmt(v))}{h(unit)}</text>')
    out.append("</svg>")
    return "".join(out)


def line_chart(points, width=640, height=200):
    """A simple line chart as inline SVG. points: list of (label, value)."""
    if len(points) < 2:
        return '<p class="muted">Not enough data to draw a trend yet.</p>'
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pad = 34
    xy = [(pad + i * (width - 2 * pad) / (len(points) - 1), height - pad - (v - lo) / span * (height - 2 * pad)) for i, v in enumerate(vals)]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    return f'<svg viewBox="0 0 {width} {height}" role="img" width="100%"><line class="axis" x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}"></line><polyline class="line" points="{path}"></polyline><text x="{pad}" y="{height - 12}">{h(str(points[0][0]))}</text><text x="{width - pad}" y="{height - 12}" text-anchor="end">{h(str(points[-1][0]))}</text><text x="0" y="{pad}">{h(fmt(hi))}</text><text x="0" y="{height - pad}">{h(fmt(lo))}</text></svg>'


def fmt(v):
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{v:,}" if isinstance(v, int) else str(v)


def main_cli(make_app, default_db, default_port, argv):
    """python -m package [data.db] [--port N]"""
    args = [a for a in argv[1:] if not a.startswith("--")]
    port = default_port
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
        args = [a for a in args if a != str(port)]
    make_app(args[0] if args else default_db).serve(port)
    return 0
