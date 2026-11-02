# Waterpoint Monitor

Offline monitoring for water points: how much water each one gives per person, whether it is safe, how long the queues are, and which broken point to fix first.

```
python -m waterpoint_monitor
```

Then open http://127.0.0.1:8035. It needs Python 3.10 or newer.

## What it does

- **Readings:** litres, people, queue time, free chlorine, E. coli and turbidity
- **Automatic checks** against planning targets, most urgent first. Any E. coli is critical
- **Reliability:** uptime, breakdowns, mean days to repair and mean days between failures
- **Repair order:** ranks broken points by people affected, how far they must walk instead, and days down
- **Fairness:** a site average can hide a block with far too little water, so the home page shows how unevenly water is spread, as a Gini coefficient

## The targets

| Measure | Default |
|---|---|
| Water | 15 litres a person a day |
| Crowding | 250 people a tap, 500 a hand pump |
| Queue | 30 minutes |
| Free chlorine | 0.2 to 0.5 mg/l |
| Distance | 500 m |
| Turbidity | 5 NTU |

These are commonly quoted planning figures based on the Sphere Handbook. Always check the current handbook and your cluster's own standards, because the right targets depend on context, such as during a cholera outbreak. Edit `TARGETS` in `core.py` to change them.

**This tool does not decide whether water is safe.** It records results from your testing. Follow your WASH lead's guidance on any positive result.

## How it is tested

Each check is tested at its limits, including a chlorine reading of exactly zero, which must count as a reading and not as missing. Reliability figures are checked against hand calculations, including an outage still going on today.

## Limits

One computer, with no sensors or remote data. The "nearest other point" used in repair ranking is approximated by the distance you enter. Population figures are what you type.

## Tests

```
python -m unittest discover -s tests
```

MIT licence.
