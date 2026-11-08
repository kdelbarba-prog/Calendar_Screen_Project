# Calendar Screen Project

A Raspberry Pi wall display for calendars, local weather, and a clock.

## Getting started

Install the pinned dependencies and package with `pip install -r requirements.txt -e .`, copy
`config/config.example.yaml` to `config/config.yaml`, enable the example calendar
(or add your own), and add its feed URL to a root `.env` file using the configured
environment-variable name. Then run `python -m dashboard --check-config`.

The project targets Python 3.11 and uses `src/dashboard/models.py` as the
authoritative schema for the generated `data.json` file.

## Calendar tools

- `python -m dashboard fetch-calendars --days 30` fetches and lists configured
  calendar events.
- `python -m dashboard layout-preview --today 2026-03-08` prints a terminal
  preview using the pure layout services in `src/dashboard/services/`.
