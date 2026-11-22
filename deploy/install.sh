#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer with sudo: sudo ./deploy/install.sh" >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="/opt/calendar-screen"
APP_USER="wallcalendar"
APP_GROUP="wallcalendar"
APP_HOME="/home/${APP_USER}"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    chromium \
    curl \
    git \
    python3 \
    python3-venv \
    rsync \
    wlr-randr \
    x11-xserver-utils

if ! getent group "$APP_GROUP" >/dev/null; then
    groupadd --system "$APP_GROUP"
fi
if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --create-home --home-dir "$APP_HOME" --shell /bin/bash --gid "$APP_GROUP" "$APP_USER"
fi
usermod -aG "$APP_GROUP",video,render,input "$APP_USER"

install -d -o "$APP_USER" -g "$APP_GROUP" "$APP_DIR"
if [ "$SOURCE_ROOT" != "$APP_DIR" ]; then
    rsync -a \
        --exclude=.git \
        --exclude=.venv \
        --exclude=cache \
        --exclude=output \
        --exclude=logs \
        --exclude=backgrounds \
        --exclude=.env \
        --exclude=config/config.yaml \
        "${SOURCE_ROOT}/" "${APP_DIR}/"
fi
chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"

if [ ! -x "${APP_DIR}/.venv/bin/python" ]; then
    runuser -u "$APP_USER" -- python3 -m venv "${APP_DIR}/.venv"
fi
runuser -u "$APP_USER" -- "${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
runuser -u "$APP_USER" -- "${APP_DIR}/.venv/bin/python" -m pip install \
    -r "${APP_DIR}/requirements.txt" \
    -e "${APP_DIR}"

install -d -o "$APP_USER" -g "$APP_GROUP" \
    "${APP_DIR}/cache" \
    "${APP_DIR}/output" \
    "${APP_DIR}/backgrounds" \
    "${APP_HOME}/.config/labwc" \
    "${APP_HOME}/.config/systemd/user"
if [ ! -f /etc/default/dashboard-display ]; then
    install -o root -g root -m 0644 \
        "${APP_DIR}/deploy/dashboard-display.conf" \
        /etc/default/dashboard-display
fi
if [ ! -f "${APP_DIR}/config/config.yaml" ]; then
    install -o "$APP_USER" -g "$APP_GROUP" -m 0640 \
        "${APP_DIR}/config/config.example.yaml" "${APP_DIR}/config/config.yaml"
fi
if [ ! -f "${APP_DIR}/.env" ]; then
    install -o "$APP_USER" -g "$APP_GROUP" -m 0600 /dev/null "${APP_DIR}/.env"
fi
chown "$APP_USER:$APP_GROUP" "${APP_DIR}/config/config.yaml" "${APP_DIR}/.env"
chmod 0640 "${APP_DIR}/config/config.yaml"
chmod 0600 "${APP_DIR}/.env"

install -o root -g root -m 0644 \
    "${APP_DIR}/deploy/dashboard.service" \
    /etc/systemd/system/dashboard.service
install -o "$APP_USER" -g "$APP_GROUP" -m 0644 \
    "${APP_DIR}/deploy/dashboard-kiosk.service" \
    "${APP_HOME}/.config/systemd/user/dashboard-kiosk.service"
install -o "$APP_USER" -g "$APP_GROUP" -m 0644 \
    "${APP_DIR}/deploy/dashboard-chromium-restart.service" \
    "${APP_HOME}/.config/systemd/user/dashboard-chromium-restart.service"
install -o "$APP_USER" -g "$APP_GROUP" -m 0644 \
    "${APP_DIR}/deploy/dashboard-chromium-restart.timer" \
    "${APP_HOME}/.config/systemd/user/dashboard-chromium-restart.timer"
install -o "$APP_USER" -g "$APP_GROUP" -m 0644 \
    "${APP_DIR}/deploy/dashboard-soak.service" \
    "${APP_HOME}/.config/systemd/user/dashboard-soak.service"
chmod 0755 \
    "${APP_DIR}/deploy/kiosk.sh" \
    "${APP_DIR}/deploy/display-setup.sh" \
    "${APP_DIR}/deploy/soak_monitor.sh"
AUTOSTART="${APP_HOME}/.config/labwc/autostart"
AUTOSTART_MARKER="# Calendar Screen dashboard startup"
if ! grep -Fq "$AUTOSTART_MARKER" "$AUTOSTART" 2>/dev/null; then
    {
        printf '\n%s\n' "$AUTOSTART_MARKER"
        cat "${APP_DIR}/deploy/labwc-autostart.sh"
    } >>"$AUTOSTART"
fi
chown "$APP_USER:$APP_GROUP" "$AUTOSTART"
chmod 0755 "$AUTOSTART"

install -d -m 0755 /etc/systemd/journald.conf.d /var/log/dashboard
install -o root -g root -m 0644 \
    "${APP_DIR}/deploy/dashboard-volatile-logs.conf" \
    /etc/systemd/journald.conf.d/dashboard-volatile.conf
install -o root -g root -m 0644 \
    "${APP_DIR}/deploy/var-log-dashboard.mount" \
    /etc/systemd/system/var-log-dashboard.mount

systemctl daemon-reload
systemctl enable --now var-log-dashboard.mount
systemctl restart systemd-journald
systemctl enable --now dashboard.service

echo "Installed dashboard service. Configure ${APP_DIR}/config/config.yaml and ${APP_DIR}/.env."
echo "Set Raspberry Pi OS to auto-login as ${APP_USER} into the labwc desktop session."
