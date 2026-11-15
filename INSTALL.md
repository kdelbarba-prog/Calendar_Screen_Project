# Install and run

The dashboard runs on Python 3.11 or newer on Raspberry Pi OS, Windows, macOS,
and Linux. The common installer creates a project-local `.venv`, installs the
pinned dependencies, copies the example config only when no local config
exists, creates an empty `.env` only when needed, and checks the configuration.
It does not configure an operating-system service or kiosk browser.

## 1. Install prerequisites

- **Raspberry Pi OS Bookworm (64-bit):** Python 3.11 and Git are available with
  `sudo apt update && sudo apt install -y python3 python3-venv git`.
- **Windows 10/11:** Install Python 3.11 or newer from
  [python.org](https://www.python.org/downloads/) and select the option to add
  Python to `PATH`. Install Git for Windows if you plan to clone the repository.
- **macOS:** Install Python 3.11 or newer (for example with
  `brew install python@3.11`) and Git (included with Xcode Command Line Tools
  or available through Homebrew).
- **Linux:** Install Python 3.11 or newer, its `venv` support package, and Git
  from your distribution's package manager. On Debian/Ubuntu:
  `sudo apt update && sudo apt install -y python3 python3-venv git`.

## 2. Get the project

In a terminal, clone the repository and enter its folder:

```sh
git clone <repository-url> calendar-screen
cd calendar-screen
```

Replace `<repository-url>` with the URL for your repository. If you already
have a checkout, open a terminal in its root folder instead.

## 3. Run the installer

Run the command for your terminal:

**Windows PowerShell**

```powershell
py -3 deploy\install.py
```

**Raspberry Pi OS, macOS, and Linux**

```sh
python3 deploy/install.py
```

The installer reports errors from Python or pip rather than silently continuing.
Re-run it after updating the checkout to refresh installed dependencies; local
configuration and credentials are kept.

## 4. Configure calendars and weather

Edit `config/config.yaml`. To enable the example calendar, set its `enabled`
value to `true`. Add the calendar feed URL to the root `.env` file using the
variable name configured by `url_env_var`, for example:

```dotenv
HOME_CALENDAR_URL=<your-private-calendar-feed-url>
```

Replace the placeholder with your actual feed URL. Do not commit `.env` or
share its contents. Newly created `.env` files are owner-readable on macOS and
Linux; on Windows, keep the file in your private user account.

You can change the timezone, display format, weather coordinates/provider, and
other display options in `config/config.yaml`. The example defaults use
`America/Chicago` and Chicago coordinates.

## 5. Start and open the dashboard

Keep the following command running:

**Windows PowerShell**

```powershell
.\.venv\Scripts\python.exe -m dashboard run
```

**Raspberry Pi OS, macOS, and Linux**

```sh
./.venv/bin/python -m dashboard run
```

Open [http://127.0.0.1:8080/](http://127.0.0.1:8080/) in a browser on the same
computer. Stop the dashboard with `Ctrl+C`. For a quick health check, visit
[http://127.0.0.1:8080/healthz](http://127.0.0.1:8080/healthz).

## Raspberry Pi kiosk deployment

For a dedicated Raspberry Pi 4 wall display, use the automated Raspberry Pi OS
Bookworm kiosk installer instead of just the local setup above. It installs
system packages, a restricted service account, systemd services, and Chromium
kiosk startup:

```sh
sudo git clone <repository-url> /opt/calendar-screen
cd /opt/calendar-screen
sudo bash deploy/install.sh
```

Then configure `/opt/calendar-screen/config/config.yaml` and
`/opt/calendar-screen/.env`, set Raspberry Pi OS to auto-login as
`wallcalendar` to the labwc desktop, and check the installation:

```sh
sudo -u wallcalendar /opt/calendar-screen/.venv/bin/python -m dashboard doctor
sudo systemctl status dashboard.service
```

This kiosk setup is specifically for 64-bit Raspberry Pi OS Bookworm with the
Wayland/labwc desktop. Read [docs/DEPLOY.md](./docs/DEPLOY.md) for display
rotation, 4K mode, keep-awake settings, updates, and troubleshooting. Do not run
the system-level kiosk installer on Windows, macOS, or a general-purpose Linux
computer.

## Troubleshooting

- **Python version error:** Check that Python 3.11 or newer is installed. On
  Windows, use the Python launcher command `py -3`.
- **`venv` is missing:** Install the platform's Python virtual-environment
  package (on Debian/Ubuntu, `python3-venv`) and rerun the installer.
- **Invalid timezone:** Use a valid IANA timezone name such as
  `America/Chicago`. The pinned `tzdata` package supports timezone data on
  Windows.
- **No calendar events:** Verify the calendar is enabled and its configured
  environment-variable name matches the entry in `.env`. Run
  `./.venv/bin/python -m dashboard doctor` (or its Windows executable path)
  to check the configuration and connectivity without printing feed URLs.
