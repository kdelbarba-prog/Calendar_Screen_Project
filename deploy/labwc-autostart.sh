#!/usr/bin/env bash

export XDG_SESSION_TYPE=wayland
if [ -f /etc/default/dashboard-display ]; then
    # shellcheck disable=SC1091
    . /etc/default/dashboard-display
fi
systemctl --user import-environment WAYLAND_DISPLAY XDG_RUNTIME_DIR DISPLAY XDG_SESSION_TYPE

if command -v wlr-randr >/dev/null 2>&1; then
    /opt/calendar-screen/deploy/display-setup.sh
fi

if command -v xset >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
    xset s off -dpms s noblank >/dev/null 2>&1 || true
fi

systemctl --user daemon-reload
systemctl --user enable --now dashboard-kiosk.service dashboard-soak.service
systemctl --user enable --now dashboard-chromium-restart.timer
