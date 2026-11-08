# Wall display (DAKboard replacement)
 
Target: Raspberry Pi 4 (4GB, SD card), Pi OS 64-bit Bookworm, Python 3.11, Chromium kiosk, portrait 4K display. Layout is designed at 1080x1920 CSS px and rendered with device scale factor 2.
Development: Windows 11 + VS Code. Use pathlib, never hardcode path separators, and include `tzdata` for Windows zoneinfo support.
 
## Architecture
- Pipeline: sources -> fetcher -> cache/state.json (atomic write) -> Jinja2 renderer -> static HTML + data.json -> local stdlib http.server -> Chromium kiosk.
- `CalendarSource` and `WeatherProvider` abstract base classes in `src/dashboard/sources/`. New sources implement these and are selected by `type:` / `provider:` in config.
- The page consumes only `data.json`, defined by the pydantic model `DashboardData` in `models.py` (see the "Data contract" section of docs/COPILOT_PLAN.md). Do not change field names without updating the model, tests/fixtures/sample_data.json, tests, and that section.
- Calendar parsing uses `icalendar` + `recurring-ical-events`. Never hand-roll RRULE logic.
- All datetimes are timezone-aware. Convert to the configured local timezone only at the render layer. Handle all-day, multi-day, and DST explicitly.
- Time is injected: logic functions take `now`/`today` as parameters. Only entry points (CLI, scheduler) call `datetime.now()`.
- A failing source must never break others or blank the display. Serve last-known-good data and mark it stale.
- Layout logic (week fitting, bar lanes) is pure functions, fully unit tested, with no I/O.
## Code standards
- Type hints everywhere, pydantic models for config and data, `logging` (no print), small modules, docstrings on public functions.
- Minimize dependencies. Use stdlib `http.server` (no Flask) and no scheduler library. Runtime deps only in requirements.txt (pinned); pytest and ruff in requirements-dev.txt. Do not add a dependency without a clear need.
- Config lives in `config/`: `config.yaml` (gitignored, `config.example.yaml` committed) and `colors.yaml` (committed). Secrets (ICS URLs, credentials) live in `.env` only. Never log them, and never include them in exception messages, status messages, or /healthz.
- Frontend: no CDNs, no frameworks, vanilla JS, no blur/shadow/backdrop-filter/animations, page under 300KB excluding the background image. Must stay smooth on a Pi 4 at 4K.
- Display stays on 24/7: never enable screen blanking, DPMS, or a screensaver.
## Testing
- pytest with fixtures in tests/fixtures; no network calls (mock HTTP). Tests that need real network are marked `live` and skipped by default.
- Before finishing any task run `ruff check .` and `pytest -q`.
- Every bug fix adds a regression test that fails before the fix.
- Keep each change small and runnable.