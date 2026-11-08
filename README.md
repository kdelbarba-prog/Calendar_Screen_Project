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
- `python -m dashboard fetch-weather [--compare]` prints normalized weather
  data or compares the NWS and Open-Meteo providers. Local weather icons are in
  `static/icons/`; provider failures use the configured fallback when available.

## Dashboard rendering

- `python -m dashboard fetch-once` fetches configured sources and atomically
  updates `cache/state.json`, retaining last-known-good calendar or weather
  data when an individual source fails.
- `python -m dashboard render` renders that state to `output/index.html` and
  `output/data.json`.
- `python -m dashboard render --sample --scenario normal` renders the committed
  sample data without using the local config or contacting external services.
  Other scenarios are `busy`, `empty`, `long-titles`, `stale`, `offline`, and
  `night`.
- `python -m dashboard check-budget` checks the generated page/assets against
  the page-size and background-image limits.

The page is designed for a 1080x1920 portrait kiosk display. Open
`output/index.html` in Chromium or serve `output/` over HTTP so the page can
poll `data.json` and rotate generated backgrounds.
