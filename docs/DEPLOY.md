# Raspberry Pi deployment

This guide targets a Raspberry Pi 4 (4 GB) running 64-bit Raspberry Pi OS
Bookworm with its Wayland/labwc desktop, a portrait 4K display, and this
project checked out at `/opt/calendar-screen`. The app runs as the unprivileged
`wallcalendar` user. Do not run the dashboard service as root.

## Install

Clone the repository to the deployment path, then run the idempotent installer:

```sh
sudo git clone <repository-url> /opt/calendar-screen
cd /opt/calendar-screen
sudo bash deploy/install.sh
```

The script installs the OS packages and Python virtual environment, creates the
`wallcalendar` account when missing, installs system and user service files,
and enables volatile journald storage. It preserves an existing
`config/config.yaml`, `.env`, and existing labwc autostart commands. Run it
again after updates to refresh the installed service files.

Edit `/opt/calendar-screen/config/config.yaml` and `/opt/calendar-screen/.env`.
Keep calendar feed URLs and any credentials only in `.env`; protect that file
with mode `0600`. Then check the machine:

```sh
sudo -u wallcalendar /opt/calendar-screen/.venv/bin/python -m dashboard doctor
sudo systemctl status dashboard.service
```

`doctor` reports source labels and pass/fail states, never feed URLs. Its source
connectivity checks require DNS and HTTPS access to the configured weather and
calendar hosts. Plain HTTP calendar feeds are accepted only for loopback
addresses, for example a local test fixture server.

Set Raspberry Pi OS to log in automatically to the labwc desktop as
`wallcalendar` (Raspberry Pi Configuration or `raspi-config`). On the next
graphical login, `~/.config/labwc/autostart` imports the Wayland session
environment, applies display settings, starts the Chromium and soak-monitor
user services, and enables the nightly browser restart timer.

The dashboard system service starts at boot after `network-online.target` and
uses `Restart=always`. It renders the cached page before attempting source
refreshes. Logs go to the system journal. A systemd watchdog is intentionally
not enabled: the Python process does not send systemd watchdog notifications.

## Display and kiosk

The kiosk launcher waits for `http://127.0.0.1:8080/healthz` before starting
Chromium. It launches fullscreen with `--kiosk`,
`--force-device-scale-factor=2`, the native Wayland Ozone backend, suppressed
infobars/error dialogs, and a persistent kiosk profile. The dashboard CSS
hides the pointer while it is over the page. The Chromium user service is
restarted nightly at 04:00 by a persistent systemd timer to limit long-running
browser memory growth.

The `wlr-randr` helper defaults to output `HDMI-A-1` and transform `90` for
portrait orientation. Check the actual connector name first:

```sh
wlr-randr
```

Set `DASHBOARD_OUTPUT` to the connector shown on the Pi and
`DASHBOARD_ROTATION` to `90` or `270` in the user's labwc environment if
needed. These values are read from `/etc/default/dashboard-display`. To select
a mode, set `DASHBOARD_MODE`, for example `3840x2160@30Hz`.
Apply changes in `~/.config/labwc/autostart` and log out/in, or run
`deploy/display-setup.sh` from the active Wayland session.

For a calendar display, prefer 4K30 unless 60 Hz is a specific requirement.
4K60 increases display bandwidth and can increase heat/power use without
improving a mostly static page. The Pi 4's 4K60 mode requires HDMI 0 and a
suitable cable/display; if needed, add `hdmi_enable_4kp60=1` under the
`[all]` section of `/boot/firmware/config.txt`, then reboot. If 4K60 causes
black screens, instability, or excess heat, remove that option and select a
4K30 mode. The dashboard layout is 1080x1920 CSS pixels and is rendered at
device scale factor 2.

### Keep the display on

The screen is intended to stay on 24/7. Do not enable a screensaver, display
blanking, DPMS timeout, suspend, or an idle manager such as `swayidle`. labwc
does not blank the display by itself. The startup helper applies
`xset s off -dpms s noblank` to an available XWayland display, and explicitly
turns the selected Wayland output on. Keep any desktop power-management
settings set to never blank/suspend. The dashboard's night theme changes page
colors only; it does not power down the display.

## SD-card longevity and soak monitoring

- The installer configures journald with `Storage=volatile` and a 64 MiB
  runtime limit. Journal records are lost on reboot, avoiding continuous
  journal writes to the SD card.
- `/var/log/dashboard` is mounted as a 16 MiB tmpfs for optional transient
  application logs. The current service writes to journald rather than this
  directory.
- `deploy/soak_monitor.sh` appends one line per hour to
  `/home/wallcalendar/soak.log`. Each line contains the timestamp, SoC
  temperature, `vcgencmd get_throttled`, aggregate Chromium RSS in MiB,
  dashboard Python RSS in MiB, free disk space in MiB, and
  `dashboard.service`'s systemd restart count. This low-frequency file is
  intentionally persistent; copy it to another machine before replacing the
  SD card if you need to retain it.
- Prefer a high-endurance SD card, keep free space available, and shut down
  cleanly before removing power. The dashboard cache is written atomically.

Inspect the monitor and service logs with:

```sh
tail -n 24 /home/wallcalendar/soak.log
sudo journalctl -u dashboard.service -n 100 --no-pager
sudo journalctl -u dashboard.service -f
```

## Update procedure

From the deployment checkout:

```sh
cd /opt/calendar-screen
sudo -u wallcalendar git pull --ff-only
sudo bash deploy/install.sh
sudo systemctl restart dashboard.service
```

The installer does not overwrite local configuration or credentials. The app
renders the current cached data during restart and resumes source refreshes.
If user service files or kiosk scripts changed, log out/in to re-import the
Wayland environment and restart the user services. For a code-only update,
restarting `dashboard.service` is sufficient.

## Troubleshooting

### Dashboard page is not available

Check `sudo systemctl status dashboard.service`, then
`sudo journalctl -u dashboard.service -n 100 --no-pager`. Run the app's
`doctor` command and verify `/opt/calendar-screen/output/index.html` exists.
Confirm `server.host` is `127.0.0.1` and that the kiosk URL's port matches
`server.port`. If you change the port, update `DASHBOARD_URL` in
`/home/wallcalendar/.config/systemd/user/dashboard-kiosk.service`, then run
`systemctl --user daemon-reload` in the logged-in graphical session.

### Chromium stays on the wait screen or does not start

Check the app at `http://127.0.0.1:8080/healthz` from the Pi. In the logged-in
`wallcalendar` desktop session, inspect `systemctl --user status
dashboard-kiosk.service` and `journalctl --user -u dashboard-kiosk.service`.
Confirm labwc is using Wayland, Chromium is installed, and the kiosk service
received `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR` from autostart.

### Display is landscape, black, or limited to 30 Hz

Run `wlr-randr` in the labwc session and use its exact output and mode names.
Try transform `90` or `270`; try 4K30 before enabling 4K60. For 4K60, confirm
the cable is connected to HDMI 0 and the display accepts that mode. Revert
`hdmi_enable_4kp60=1` if it causes boot or link problems.

### Display blanks or sleeps

Disable desktop power management, DPMS, screensavers, and any active
`swayidle`/idle service. Check XWayland settings with `xset q` where
`xset` is available. The kiosk must not rely on user interaction to keep the
screen awake.

### Temperature, throttling, or memory keeps increasing

Review `/home/wallcalendar/soak.log` for temperature, throttling flags, RSS,
disk space, and service restarts. Confirm unobstructed cooling and a stable
power supply. Chromium is restarted nightly; repeated dashboard service
restarts usually indicate a configuration or startup error visible in the
system journal.

### Source checks fail

Run `python -m dashboard doctor` as `wallcalendar`. It does not disclose
configured URLs; check the corresponding `.env` variable locally if a
calendar is unreachable. Confirm the Pi's network, DNS, NTP synchronization,
and system clock. Do not paste feed URLs or `.env` contents into logs or
support requests.
