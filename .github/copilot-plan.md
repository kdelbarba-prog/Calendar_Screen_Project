# Copilot Prompt Plan: Wall Display (DAKboard replacement)
 
This is the single source of truth for the build plan, decisions, data contract, prompts, and test methods. Update it as decisions change, and tick the status boxes as you go.
 
- Standing instructions live in `.github/copilot-instructions.md` (Copilot auto-loads that exact path). Keep it short, because it is sent with every request.
- Run the prompts one at a time in Copilot **Agent mode**. After each prompt, run its **Verify** block, then commit.
## Goals
 
- Portrait 4K wall calendar on a Samsung Frame 50" (LCD, always on) driven by a Raspberry Pi 4 (4GB, SD card).
- Blocks: clock, local weather, month grid. Optional rotating background image in the non-calendar zone only.
- Multiple calendars (unlimited; realistically 2-5), ICS feeds first (Google secret address, public iCloud link).
- Weather: current temp, today's icon, precipitation %, hi/lo, next 5-7 days. Stretch: radar snapshot.
- Remote access via SSH/VNC only (no admin page).
## Decisions log
 
| Topic | Decision |
|---|---|
| Python | 3.11 (matches Pi OS Bookworm) |
| Dependencies | requests, icalendar, recurring-ical-events, pydantic, pyyaml, python-dotenv, jinja2, pillow, tzdata. No Flask, no scheduler lib. Dev only: pytest, ruff (optional: hypothesis, pytest-cov). |
| Calendar sources | ICS only for v1. CalDAV (private iCloud) is optional later and adds the `caldav` library. |
| Refresh | Per-calendar `refresh_minutes`, validated 1..1440, default 5 |
| Colors | Named palette in `config/colors.yaml`; each entry has bg, fg, tentative_bg, tentative_fg (same hue family so the calendar is still identifiable) |
| Tentative events | Shown, softer shade + dashed left border |
| Declined events | Hidden by default (`show_declined: false`); when shown, same calendar color, line-through, reduced opacity |
| Multi-day events | Spanning bars (>= 2 local days AND (all-day OR >= 24h)); single all-day events are chips at the top of the day; timed overnight events are chips on the start day |
| Month grid | Starts at the current week; adds weeks (min 2, max 6) while the line budget allows |
| Display | Always on. 4K30 is probably enough (cooler); 4K60 optional. Chromium restarts nightly to limit memory growth. |
| Weather | NWS primary (observation station for current temp, optional station override), Open-Meteo fallback, both behind `WeatherProvider` |
| Radar | Stretch goal, server-side static PNG over a basemap bundled in the repo |
| Frontend scale | Design at 1080x1920 CSS px, Chromium `--force-device-scale-factor=2` |
| Time handling | Logic functions take `now` as a parameter (injected clock). Only entry points call `datetime.now()`. This makes DST/midnight/month-edge tests possible without waiting. |
| Build order | Data contract first, renderer before runner, Pi performance check before the runner/deploy work (see Build order) |
| Minimum event font size | TBD. Decide at Checkpoint A by viewing the real screen from your normal viewing distance, then record it here. |
 
### Known limitations
 
- Tentative/declined status needs attendee data in the feed. Google feeds include it; a public iCloud link usually does not, so those events will mostly render as normal.
- A public iCloud link is readable by anyone with the URL. Treat feed URLs as secrets (`.env` only, never logged).
- Google ICS feeds can lag by hours. The Google Calendar API (later) is the fix if that matters.
- `fetch()` does not work from `file://` pages in Chromium. Always view the page through a local web server (the app's own, or `python -m http.server`).
## Copilot Free: token strategy
 
- Free tier is about 50 chat/agent requests and 2,000 completions per month. Agent mode costs one request per prompt (times the model rate), so big well-specified prompts are cheaper than many small ones. Verify current limits and set your spending budget to $0 at github.com/settings/copilot.
- Plan: 7 core prompts, leaving most of the month in reserve for fixes.
- **Do the testing yourself**: the Verify blocks are commands and manual checks, not Copilot requests. Use Copilot only to fix what fails, and use the bug template below so each fix takes one request.
- Use inline completions (separate allowance) and hand edits for small changes.
- The frontend (Prompt 5) is the most iteration-heavy; consider getting the layout built outside Copilot and using Prompt 5 only to wire it to data.
### Bug-fix prompt template (one request per fix)
 
```text
Bug: <one line>
Steps / command: <exact command or click path>
Expected: <what should happen>
Actual: <paste the traceback, output, or describe the screenshot>
Constraints: keep the data contract unchanged; add a regression test that fails before the fix.
```
 
## Build order and gates
 
```
P1 -> P2 -> P3 -> P4 -> P5 -> [Checkpoint A: Pi static page] -> P6 -> P7 -> [Soak test]
```
 
Each prompt ends at a **gate**: do not start the next prompt until the Verify block passes and the work is committed.
 
## Status
 
- [ ] Prompt 1: Scaffold, config, palette, data contract
- [ ] Prompt 2: ICS ingest and location formatting
- [ ] Prompt 3: Layout engine
- [ ] Prompt 4: Weather
- [ ] Prompt 5: Renderer and frontend
- [ ] Checkpoint A: Pi static-page performance check
- [ ] Prompt 6: Runner, cache, server
- [ ] Prompt 7: Raspberry Pi deployment
- [ ] Soak test (72 hours), then one week of normal use
- [ ] Optional: CalDAV source
- [ ] Optional: Radar snapshot
- [ ] Optional: Polish (doctor command, CI)
---
 
## Data contract
 
`data.json` is the one interface between the backend and the page. Every producer (layout engine, weather) must emit it, and the page must only consume it. The authoritative definition is the pydantic model `DashboardData` in `src/dashboard/models.py`, plus the hand-written `tests/fixtures/sample_data.json`. If a field changes, update the model, the sample file, the tests, and this section together.
 
```json
{
  "schema_version": 1,
  "generated_at": "2026-11-07T09:40:00-06:00",
  "timezone": "America/Chicago",
  "theme": { "night_enabled": true, "night_start": "21:00", "night_end": "06:30" },
  "status": {
    "offline": false,
    "sources": [
      { "id": "family", "name": "Family", "kind": "calendar",
        "state": "ok", "last_success": "2026-11-07T09:38:00-06:00", "message": null },
      { "id": "weather", "name": "Weather", "kind": "weather",
        "state": "ok", "last_success": "2026-11-07T09:30:00-06:00", "message": null }
    ]
  },
  "palette": {
    "blue": { "bg": "#1f4e8c", "fg": "#ffffff", "tentative_bg": "#9db8dc", "tentative_fg": "#10243f" }
  },
  "weather": {
    "units": "imperial", "provider": "nws", "fallback_used": false,
    "fetched_at": "2026-11-07T09:30:00-06:00",
    "current": { "temp": 41, "feels_like": 36, "condition": "Cloudy", "icon_key": "cloudy", "precip_chance": 20 },
    "today": { "hi": 47, "lo": 33, "precip_chance": 30, "icon_key": "showers" },
    "hourly": [ { "time": "2026-11-07T10:00:00-06:00", "temp": 42, "precip_chance": 20, "icon_key": "cloudy" } ],
    "daily": [ { "date": "2026-11-08", "hi": 50, "lo": 35, "precip_chance": 10, "icon_key": "partly_cloudy" } ],
    "alerts": [ { "headline": "Wind Advisory", "severity": "Moderate" } ],
    "radar_url": null
  },
  "calendar": {
    "today": "2026-11-07",
    "weeks": [
      {
        "start": "2026-11-01",
        "lanes": 1,
        "bars": [
          { "id": "e1", "title": "Conference", "lane": 0, "col_start": 2, "col_span": 4,
            "continues_left": false, "continues_right": true, "calendar": "blue", "status": "normal" }
        ],
        "days": [
          { "date": "2026-11-01", "is_today": false, "is_first_of_month": true, "more_count": 0,
            "chips": [
              { "id": "e2", "title": "Soccer", "time_label": "9:30a", "location": "Wheaton",
                "calendar": "blue", "status": "tentative" }
            ] }
        ]
      }
    ]
  }
}
```
 
Rules for the contract:
 
- Titles and locations in the contract are display-ready (already truncated and formatted). `time_label` is empty for all-day chips.
- `status` is `normal`, `tentative`, or `declined`. `state` is `ok`, `stale`, or `error`.
- `message` fields must never contain feed URLs or credentials.
- `calendar` values are palette keys; colors come only from `palette`.
- `col_start` is 0-6 (Sunday-first or Monday-first per config) and `col_span` is 1-7.
---
 
## Dev environment setup (Windows, PowerShell)
 
```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
copy config\config.example.yaml config\config.yaml
# put real feed URLs in .env (never commit it)
ruff check . ; pytest -q
```
 
## Testing strategy
 
| Layer | What it covers | How | When |
|---|---|---|---|
| Unit | Config validation, ICS parsing, location formatter, layout engine, weather mapping | `pytest -q` with fixtures; no network | Every prompt, every commit |
| Lint | Style, unused code, obvious bugs | `ruff check .` | Every commit |
| Contract | Every producer's output validates against `DashboardData`; sample file validates | pytest tests in `tests/test_contract.py` | Every commit |
| Golden / snapshot | Layout engine output for fixed inputs (ASCII preview) | pytest compares to stored `.txt` goldens | When layout changes |
| Invariants | Layout rules always hold (no overlapping bars per lane, nothing silently dropped) | pytest; optional `hypothesis` for randomized inputs | Prompt 3 |
| Live smoke | Real feeds and real weather APIs still parse | `pytest -m live` (skipped by default; reads `.env`) | After Prompts 2 and 4, and when a feed misbehaves |
| Visual QA | Layout, readability, event states, indicators | `render --sample --scenario ...` in Chrome DevTools at 1080x1920, DPR 2 | Prompt 5, and when CSS changes |
| Performance | CPU, temperature, memory, throttling, page weight | `check-budget` on Windows; Pi commands at Checkpoint A | Checkpoint A, Prompt 7 |
| Resilience | Offline, bad feed, malformed ICS, corrupt cache, crashes, power loss | Manual fault injection (tables below) | Prompts 6 and 7 |
| Soak | Memory growth, log growth, restarts, midnight/DST rollover | 72-hour run with the monitor script | After Prompt 7 |
| Security | No secrets in logs, repo, or `/healthz` | Unit test on logging + manual grep | Prompts 2 and 6 |
 
### Test conventions (Copilot is told these in Prompt 1)
 
- `live` tests are marked `@pytest.mark.live` and skipped unless run with `-m live`.
- Network is mocked in all other tests. Fixtures live in `tests/fixtures/` (`.ics` files, weather JSON, `sample_data.json`).
- "Now" is injected. Tests pass fixed times (for example `2026-03-08` US spring-forward, `2026-11-01` fall-back, `2026-12-28` month/year rollover).
- Every bug fix adds a regression test.
### Commit gate (every prompt)
 
```powershell
ruff check . ; pytest -q ; git status
```
 
Then commit with a message that names the prompt (for example `P3: layout engine`).
 
---
 
## Prompt 1: Scaffold, config, palette, data contract
 
```text
Read .github/copilot-instructions.md and the "Data contract" section of docs/COPILOT_PLAN.md. Create the scaffold:
- src/dashboard/ (__init__, __main__, config.py, models.py, logging_setup.py), requirements.txt (pinned: requests, icalendar, recurring-ical-events, pydantic, pyyaml, python-dotenv, jinja2, pillow, tzdata), requirements-dev.txt (pytest, ruff), .gitignore (.env, config/config.yaml, cache/, output/, logs/, .venv, backgrounds/), README stub.
- config/config.example.yaml: display (timezone, 12/24h, clock_show_seconds, night_theme {enabled, start, end}); calendars[] (unlimited) {name, type: ics, url_env_var, color (palette name), enabled, refresh_minutes (validated 1..1440, default 5), my_email (optional), show_declined: false, show_tentative: true}; calendar_view {min_weeks: 2, max_weeks: 6, line_budget, max_events_per_day, max_bar_lanes: 3, max_title_chars, max_lines_per_event, location_mode: city|full|none}; weather {provider: nws|openmeteo, fallback_provider, nws_station (optional override), lat, lon, units, refresh_minutes, user_agent}; backgrounds {folder, change_minutes, enabled}; server {host, port}.
- config/colors.yaml: ~10 named colors, each with bg, fg, tentative_bg, tentative_fg (tentative = a lighter/softer shade of the SAME hue so the calendar is still identifiable).
- config.py: pydantic models, fail-fast clear errors, unknown palette names rejected, secrets from .env.
- models.py: Event (calendar_name, color, title, start, end, all_day, location, description_short, status normal|tentative|declined), WeatherSnapshot, SourceStatus, and the data-contract models (DashboardData and its nested models) exactly matching the contract section.
- tests/fixtures/sample_data.json: hand-written realistic data matching the contract: 2 calendars, one bar crossing a week boundary, a tentative chip, a declined chip, a day with '+N more', a 7-day forecast, one stale source.
- tests/conftest.py and pytest config registering a `live` marker that is skipped unless `-m live` is given.
- Tests: example config loads; refresh_minutes out of range fails; unknown color fails; every palette fg/bg and tentative_fg/tentative_bg pair has WCAG contrast >= 4.5; sample_data.json validates against DashboardData; DashboardData rejects a bad status value.
- `python -m dashboard --check-config` exits 0/1.
```
 
**Verify (Gate 1)**
 
- `ruff check . ; pytest -q` passes.
- `python -m dashboard --check-config` exits 0 (`echo $LASTEXITCODE` in PowerShell).
- Break the config three ways (`refresh_minutes: 0`, unknown color name, missing `url_env_var` in `.env`). Each fails with a clear, specific message, not a traceback.
- `git check-ignore -v .env config/config.yaml` shows both are ignored.
- Read `models.py` against the Data contract section once, since every later prompt builds on it.
## Prompt 2: ICS ingest and location formatting
 
```text
Implement calendar ingestion. "Now" and the date window are always passed in as parameters; never call datetime.now() inside parsing or layout logic.
- sources/base.py CalendarSource ABC; sources/ics.py IcsCalendarSource: requests with timeout, retry/backoff, User-Agent, ETag/If-Modified-Since; convert webcal:// to https://; parse with icalendar, expand with recurring-ical-events for the needed window only. Handle all-day, multi-day, EXDATE/RECURRENCE-ID overrides, cancelled events, floating/UTC/TZID times, DST. Status: tentative if STATUS=TENTATIVE or (my_email set and PARTSTAT=TENTATIVE for that attendee); declined if PARTSTAT=DECLINED. Drop declined unless show_declined, drop tentative unless show_tentative.
- location.py format_location(raw, mode): 'city' extracts the city from US-style addresses ("123 Main St, Wheaton, IL 60187, USA" -> "Wheaton"), tolerates missing zip/country/suite lines, venue-only strings, returns the original if unsure; video-call URLs -> empty. Table-driven tests with 15+ examples.
- Never log feed URLs, and make sure exception messages and SourceStatus.message never contain them (redact). Add a unit test that triggers an HTTP error and asserts the URL does not appear in captured logs or in the status message.
- Fixture .ics tests: recurring + exception, all-day, multi-day, DST change, Google-style and iCloud-style TZIDs, tentative, declined, webcal scheme, empty feed, malformed feed (must raise a clean, typed error). Mock HTTP.
Acceptance: `python -m dashboard fetch-calendars --days 30` prints merged, sorted events with status and color name.
```
 
**Verify (Gate 2)**
 
- Commit gate passes.
- **Live smoke:** `pytest -m live -k ics` against your real feeds. The test should print only counts and never URLs.
- **Side-by-side check:** run `python -m dashboard fetch-calendars --days 14`, then compare against the Google and Apple calendar apps for the same 14 days. Check at least: per-day event counts, times, all-day events, 3 recurring events (including one with an edited or deleted occurrence), and 1 multi-day event. Add any mismatch as a fixture and a bug-template request.
- **Secret check:** run with debug logging, then confirm the logs contain no part of your URLs:
```powershell
  Select-String -Path logs\*.log -Pattern "<a fragment of your secret URL>"
```
  Expect no matches.
- **Offline check:** disable the network adapter and run the command again. Expect a clean one-line error per source, not a traceback.
## Prompt 3: Layout engine (pure functions)
 
```text
Implement services/agenda.py and services/month_layout.py as pure, fully unit-tested functions (no I/O, `now`/`today` passed in).
- Merge and dedupe events across calendars (same UID, or same title+start), attach palette color.
- Classify: BAR = touches >=2 local calendar days AND (all_day OR duration >= 24h). Everything else is a CHIP on its start day (single all-day events sort first in the day; timed overnight events show the end time).
- Month layout: start at the week containing today. For each week produce: bar segments clipped to the week with continues_left / continues_right flags, assigned to lanes by greedy interval packing (earlier start, then longer, first); per-day chip lists truncated to max_events_per_day with '+N more' that also counts bars beyond max_bar_lanes covering that day.
- Week height in lines = 1 (date row) + lanes used + max(chips shown on any day, +1 if '+N more'). Add weeks (min_weeks..max_weeks) only while the cumulative height <= line_budget.
- Output is the `calendar` section of DashboardData (display-ready titles/locations, truncated to max_title_chars).
- Tests: bar crossing a week boundary, two overlapping bars, bar longer than the grid, 4+ bars overflowing lanes, DST weekend, month rollover, all-day singles, auto-fit stopping at the budget. Add invariant tests: no two bar segments in the same lane overlap; every event covering a day is either shown or counted in more_count; weeks stay within min..max; output validates against the contract.
Acceptance: `python -m dashboard layout-preview [--today YYYY-MM-DD]` prints an ASCII version of the grid so it can be checked without a browser.
```
 
**Verify (Gate 3)**
 
- Commit gate passes (including invariant tests).
- Run the preview against your real calendars: `python -m dashboard layout-preview`. Check that bars sit on the right days and the week count looks plausible for your calendar density.
- Change `line_budget` up and down. The number of weeks should change monotonically.
- Edge dates (US DST 2026):
```powershell
  python -m dashboard layout-preview --today 2026-03-08   # spring forward
  python -m dashboard layout-preview --today 2026-11-01   # fall back
  python -m dashboard layout-preview --today 2026-12-28   # month/year rollover
```
  Look for duplicated or missing days and events shifted by an hour.
- Stress it: add a temporary fixture with 12 events on one day and 5 overlapping bars. Confirm `+N more` counts are correct and the grid does not grow past the budget.
## Prompt 4: Weather
 
```text
Implement weather behind a WeatherProvider ABC returning the WeatherSnapshot model (output maps into the `weather` section of DashboardData).
- sources/weather_nws.py (api.weather.gov): send the configured User-Agent; resolve and cache the gridpoint and nearest observation station from lat/lon (or use config nws_station if set); current temp from the latest station observation (fall back to the forecast if the observation is null or older than 90 minutes); daily and hourly forecast including probabilityOfPrecipitation; today hi/lo; next 7 days; optional active-alerts list.
- sources/weather_openmeteo.py with the same output.
- Automatic fallback: if the primary provider fails, use fallback_provider and record which one supplied the data (fallback_used).
- Map each provider's conditions to one shared icon_key set (day/night variants). Add simple local SVG icons in static/icons/ (no external assets).
- Tests with saved JSON fixtures, no network: null observation, stale observation, missing precipitation, night icon selection, fallback path, output validates against the contract.
Acceptance: `python -m dashboard fetch-weather [--compare]` prints the normalized snapshot; --compare prints both providers side by side (current temp, hi/lo, precip chance).
```
 
**Verify (Gate 4)**
 
- Commit gate passes.
- **Live smoke:** `pytest -m live -k weather`.
- **Accuracy check:** run `python -m dashboard fetch-weather --compare` a few times over a day or two and compare current temp against a real thermometer or a trusted station. If NWS is consistently off, set `weather.nws_station` to a closer station. If one provider is consistently better, make it the primary.
- **Fallback check:** set the primary provider's host to something invalid (or block it in your hosts file). The snapshot should still return with `fallback_used: true`.
- Eyeball every SVG icon in a browser (day and night) and confirm every `icon_key` the mapper can produce has an icon file. A test should enforce this.
## Prompt 5: Renderer and frontend
 
```text
(Or skip the layout part if you already have a template, and ask Copilot only to wire it to data.json.)
Create the renderer and the page. Input is cache/state.json (or tests/fixtures/sample_data.json with --sample); output is output/index.html + output/data.json (matching the data contract).
- `python -m dashboard fetch-once`: runs all sources once and writes cache/state.json atomically (temp file + os.replace). `python -m dashboard render`: state.json -> output/. (The scheduler in the next prompt will just call these.)
- templates/ and static/ at 1080x1920 CSS px: top zone (clock, date, weather current + today icon + precip % + hi/lo, 7-day strip) over a rotating background; opaque calendar zone below.
- Each week is a CSS grid with 7 columns: row 1 date numbers, then one row per lane for bars (grid-column: start / span n, with arrow/flat edges for continues_left/right), then one row of per-day chip lists. A background layer of 7 day cells spans all rows for borders and today highlight.
- Event colors: use the palette's bg/fg as CSS variables per calendar; tentative uses the tentative_bg/fg plus a dashed left border; declined keeps the calendar color with line-through and reduced opacity.
- Vanilla JS: update the clock (per minute unless clock_show_seconds), poll data.json every 60s and patch the DOM without reloading, show an offline icon (global) and a small stale dot per calendar; optional night theme by time. backgrounds.py pre-resizes images from the folder with Pillow to the top-zone size and rotates every change_minutes. No blur/shadows/animation.
- `python -m dashboard render --sample --scenario normal|busy|empty|long-titles|stale|offline|night`. Scenarios are small mutations applied to sample_data.json (no extra fixture files).
- `python -m dashboard check-budget`: fails if output/ excluding backgrounds exceeds 300KB, or if a background JPEG exceeds ~1MB.
- Tests: renderer output for sample data contains expected bars/chips/classes; every scenario validates against the contract; a template error on missing optional fields (no weather, no calendars) must still render a page.
```
 
**Verify (Gate 5, on Windows)**
 
1. Serve the output (never open it as `file://`):
```powershell
   python -m dashboard render --sample --scenario normal
   python -m http.server 8080 --directory output
```
2. In Chrome, open `http://localhost:8080`, press F12, enable the device toolbar, choose Responsive, set 1080x1920, DPR 2.
3. Walk the visual checklist for each scenario (`normal`, `busy`, `empty`, `long-titles`, `stale`, `offline`, `night`):
   - Bars span the right days, with arrow edges where they continue past the week.
   - Tentative looks softer in the same hue; declined is struck through and dimmed; you can still tell which calendar each belongs to.
   - `+N more` appears instead of overflow; long titles truncate, never wrap into the next day.
   - Stale dot shows only on the stale calendar; offline icon shows only in `offline`.
   - Nothing in the calendar zone sits on top of the background image.
4. **Live JS check:** with the page open, edit `output/data.json` (change the temperature), wait up to 60 seconds, and confirm the page updates without a reload. Then rename `data.json` and confirm the page shows an offline or stale state instead of breaking.
5. **Real data:** `python -m dashboard fetch-once ; python -m dashboard render`, then compare with your phone calendar.
6. `python -m dashboard check-budget` passes. Commit gate passes.
7. In DevTools, run Lighthouse or the Performance panel once and fix anything that thrashes layout.
## Checkpoint A: Pi static-page performance check (go/no-go)
 
Do this before Prompts 6 and 7. It tells you whether the design is feasible on the Pi 4 at 4K, while changes are still cheap.
 
1. **Set up the Pi display manually** (Prompt 7 will automate it): Pi OS 64-bit Bookworm Desktop, HDMI0 (the port nearest the USB-C power port), the Frame's HDMI input set to accept 4K signals, then rotate:
```bash
   wlr-randr                                   # note the output name, e.g. HDMI-A-1
   wlr-randr --output HDMI-A-1 --transform 90  # or 270, depending on mounting
```
   Start at 4K30. Try 4K60 later only if you can see a difference.
2. **Copy the rendered page to the Pi** and serve it (Python is already on the Pi):
```powershell
   scp -r output pi@raspberrypi.local:~/dashboard-test/
```
```bash
   cd ~/dashboard-test && python3 -m http.server 8080 --directory output &
   chromium-browser --kiosk --force-device-scale-factor=2 http://localhost:8080   # or `chromium`
```
3. **Look at it from your real viewing distance.** Check text readability, contrast on the Frame's matte screen, and whether the grid density feels right. Record the minimum event font size in the decisions log.
4. **Measure** after the page has been up for 5 minutes, using the `normal` and `busy` scenarios:
```bash
   vcgencmd measure_temp
   vcgencmd get_throttled          # want 0x0 (anything else means throttling or under-voltage)
   top -b -n1 | head -15           # Chromium CPU
   free -m
   ps -eo rss,comm | awk '/chrom/ {s+=$1} END {print s/1024 " MB chromium"}'
```
   Initial targets (adjust once you have real numbers): temperature under 70 C, `get_throttled` = `0x0`, average Chromium CPU under about 20% when idle, total Chromium memory under about 1 GB.
5. **Update test:** change `data.json` on the Pi, and confirm the page updates within 60 seconds with no flicker or CPU spike.
 
**Go:** targets met. Continue to Prompt 6.
**No-go:** simplify first. In this order: confirm 4K30 (not 60), remove any semi-transparent overlays, reduce DOM node count (fewer per-cell wrapper elements), shrink or simplify the background image, drop fonts to one family. Use the bug template to ask Copilot for specific changes, and re-measure.
 
## Prompt 6: Runner, cache, server
 
```text
Build `python -m dashboard run`: a thread/asyncio scheduler (no scheduler library) with per-calendar intervals and a weather interval, jitter, and per-source error isolation. It calls the existing fetch-once and render logic; do not duplicate it. Write cache/state.json atomically with last-known-good data and per-source SourceStatus (stale after 2x that source's interval). Add a cheap connectivity check (every ~60s; offline after 2 consecutive failures) producing the global 'offline' flag. Serve output/ with stdlib http.server (ThreadingHTTPServer) on the configured host/port (127.0.0.1 by default), no-cache for data.json, long cache for static assets, plus /healthz JSON (per-source status and last update times; never URLs or secrets). Render from cached state at startup before the network is up. If cache/state.json is corrupt, move it to state.json.bak, log a warning, and start empty instead of crashing. Graceful SIGTERM/Ctrl-C shutdown with no leftover temp files. Allow http:// feed URLs only for localhost so tests can serve fixture feeds. Log fetch+render duration and process memory at INFO once per hour.
Tests: failure isolation (one source raising does not affect others), stale and offline transitions with an injected clock, corrupt-cache recovery, atomic write leaves no partial file, /healthz output contains no URLs.
```
 
**Verify (Gate 6, on Windows first)**
 
Run `python -m dashboard run` with `refresh_minutes: 1` on one calendar to speed up the staleness checks, with the app page open in Chrome.
 
| Test | How | Expected |
|---|---|---|
| Normal run | Leave it 10 minutes | Page updates in place; `/healthz` shows all sources ok |
| Network loss | Disable the network adapter | Offline icon within ~3 minutes; last data stays on screen |
| Network restored | Re-enable the adapter | Offline icon clears and data refreshes within one interval |
| Bad feed URL | Point one calendar's env var at a wrong URL | Only that calendar goes stale (dot appears after ~2 intervals); the other calendar and weather are unaffected |
| Malformed ICS | Put garbage in `bad.ics`, run `python -m http.server 9000` in that folder, point a calendar at `http://localhost:9000/bad.ics` | Clean error in logs and status; app keeps running |
| Start offline | Disable the network, start `run` | Page renders immediately from cached state, with the offline indicator |
| Corrupt cache | Truncate `cache/state.json`, restart | Warning logged, `.bak` created, app starts and refetches |
| Graceful stop | Ctrl-C | Exits cleanly, no `*.tmp` files left in `cache/` |
| Secrets | `Select-String -Path logs\*.log -Pattern "<URL fragment>"`; open `/healthz` | No URL fragments anywhere |
| Timing | Check the hourly log line | Fetch+render time is small and memory is flat between hours |
 
Then run the commit gate.
 
## Prompt 7: Raspberry Pi deployment (always on)
 
```text
Create deploy/ and docs/DEPLOY.md:
- systemd service for the app (non-root, Restart=always, After=network-online.target, WatchdogSec optional).
- Chromium kiosk autostart for Pi OS Bookworm (Wayland/labwc) with --kiosk, --force-device-scale-factor=2, hidden cursor, no infobars/error dialogs, wait-for-server loop.
- a systemd timer that restarts Chromium nightly to limit memory growth.
- portrait rotation via wlr-randr; notes on 4K30 vs 4K60 and hdmi_enable_4kp60.
- explicitly disable screen blanking/DPMS/screensaver (the display stays on 24/7).
- install.sh (idempotent), SD longevity notes (volatile journald, logs to tmpfs), update procedure (git pull + restart), troubleshooting.
- deploy/soak_monitor.sh: appends one line per hour to ~/soak.log with date, temperature, `vcgencmd get_throttled`, total Chromium RSS (MB), Python app RSS (MB), free disk space, and the app's restart count from systemd.
- `python -m dashboard doctor`: checks config validity, DNS and HTTPS reachability of each source host (never prints URLs), timezone and NTP sync (`timedatectl`), free disk space, and that the output page exists. Exit 0/1.
```
 
**Verify (Gate 7, on the Pi)**
 
| Test | How | Expected |
|---|---|---|
| Clean install | Fresh SD card (or a fresh user), clone the repo, run `install.sh` twice | Works, and the second run changes nothing |
| Boot test | `sudo reboot` | Dashboard appears with no keyboard or mouse input |
| Boot offline | Unplug the network, reboot | Page appears from cached state with the offline indicator; recovers when the network returns |
| App crash | `sudo systemctl kill -s KILL dashboard` | Service restarts within seconds; page reconnects |
| Browser crash | `pkill -9 chromium` | Kiosk comes back (autostart or supervisor) |
| Nightly restart | `sudo systemctl start <restart-service>` manually | Chromium restarts and returns to the kiosk page |
| Power loss | Pull power 3 times while running, including once during a refresh | Boots cleanly each time; `cache/state.json` is valid JSON |
| No blanking | Leave it idle for 30+ minutes | Screen never blanks or dims |
| Time sync | `timedatectl` | `System clock synchronized: yes`, correct timezone |
| Doctor | `python -m dashboard doctor` | Exit 0 |
| Thermals | `vcgencmd measure_temp ; vcgencmd get_throttled` after an hour | Within the Checkpoint A targets |
 
### Soak test (72 hours)
 
```bash
nohup bash deploy/soak_monitor.sh &
```
 
After 72 hours review `~/soak.log` and `journalctl -u dashboard --since "3 days ago"`:
 
- Chromium and app memory are flat (a steady upward trend means a leak; check that it drops after the nightly restart).
- No unexpected restarts, no throttling, and disk usage is stable (logs are not growing unbounded).
- The calendar rolled over midnight correctly each day, with "today" highlighted on the right day.
- Check the display once at midnight and once at 6 AM in person.
- If a US DST change falls in the window, verify the clock and the event times afterward.
Then use it normally for a week, keeping notes on anything wrong. Each issue becomes one bug-template request and a regression test.
 
---
 
## Optional later prompts
 
### CalDAV source (private iCloud calendars)
 
```text
Add CalDavCalendarSource using the `caldav` library with iCloud app-specific-password support and sync-token/ctag change detection. It implements CalendarSource, is selected via `type: caldav` in config, has mocked tests, and is documented in docs/SOURCES.md (how to create an app-specific password; where credentials live in .env). Never log credentials.
```
 
Verify: unit tests pass; live smoke against one private calendar; the side-by-side comparison from Gate 2 for 14 days; change an event in the Apple Calendar app and confirm it appears within one refresh interval.
 
### Radar snapshot
 
```text
Add a radar module: fetch a radar image around the configured lat/lon from a free public source, composite it over a basemap image bundled in the repo (no runtime map-tile calls), and write one static PNG to output/ every 5-10 minutes; set weather.radar_url in the contract. Show it in the weather zone if space allows. Fail gracefully (keep the last image, mark stale). Mocked tests only. Before building, verify the radar source's availability and terms of use.
```
 
Verify: unit tests with a mocked image; block the radar host and confirm the last image stays up and the status goes stale; re-run Checkpoint A measurements, since extra imagery can add CPU load.
 
### Polish: CI
 
```text
Add a GitHub Actions workflow that runs `ruff check .` and `pytest -q` (live tests excluded) on every push to the private repo, a CHANGELOG, and a README with the architecture diagram and screenshots. Review the whole codebase for unused code, missing type hints, and secrets that could reach logs or /healthz.
```
 
---
 
## Change log
 
- Initial plan created.
- Added data contract and sample data, injected-clock rule, per-prompt Verify gates, Checkpoint A (Pi performance go/no-go), resilience and soak tests. Reordered so the renderer (Prompt 5) comes before the runner (Prompt 6) and the Pi performance check happens before deployment work. Add dated notes below when decisions change.