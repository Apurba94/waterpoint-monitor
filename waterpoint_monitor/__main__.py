"""Start the app: python -m waterpoint_monitor [data.db] [--port 8035]"""
import sys

from .fieldkit import main_cli
from .web import create_app

sys.exit(main_cli(create_app, "water.db", 8035, sys.argv))
