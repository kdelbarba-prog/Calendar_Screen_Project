# Calendar Screen Project

A Raspberry Pi wall display for calendars, local weather, and a clock.

## Getting started

See [INSTALL.md](./INSTALL.md) for the install script and step-by-step setup on
Raspberry Pi, Windows, macOS, and Linux. For a dedicated Raspberry Pi OS kiosk,
see the [Pi deployment guide](./docs/DEPLOY.md).

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

Run `python -m dashboard run` to start the refresh scheduler and local web
server. It refreshes each calendar and weather source at its configured
interval, serves `output/` on the configured host/port, and exposes
URL-free source health at `/healthz`. The cached page is rendered before
network refreshes begin.

- **Color palette page:** while the dashboard is running, open
  `http://<pi-address>:8080/static/colors.html` to see every color in
  `config/colors.yaml` drawn the way the calendar uses it (multi-day bar,
  confirmed event, tentative event), with text-contrast checks. Edit swatches,
  add or delete colors, then copy or download the generated YAML and replace
  `config/colors.yaml`, then restart `dashboard.service`. The page does not write
  files itself. Calendars refer to colors by name via `color:` in
  `config/config.yaml`.

- `python -m dashboard doctor` checks the configuration, source connectivity,
  timezone/NTP, free disk space, and rendered page without printing feed URLs.
- Raspberry Pi OS Bookworm deployment and kiosk setup are documented in
  [docs/DEPLOY.md](./docs/DEPLOY.md).
