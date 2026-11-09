#!/usr/bin/env bash
set -eu

DASHBOARD_URL="${DASHBOARD_URL:-http://127.0.0.1:8080/}"
HEALTH_URL="${DASHBOARD_URL%/}/healthz"

while ! curl --silent --fail --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; do
    sleep 2
done

if command -v chromium >/dev/null 2>&1; then
    CHROMIUM="$(command -v chromium)"
elif command -v chromium-browser >/dev/null 2>&1; then
    CHROMIUM="$(command -v chromium-browser)"
else
    echo "Chromium executable not found." >&2
    exit 1
fi

exec "$CHROMIUM" \
    --ozone-platform=wayland \
    --kiosk \
    --force-device-scale-factor=2 \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --hide-crash-restore-bubble \
    --no-first-run \
    --no-default-browser-check \
    --disable-translate \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --user-data-dir="$HOME/.config/chromium-kiosk" \
    "$DASHBOARD_URL"
