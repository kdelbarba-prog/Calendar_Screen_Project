#!/usr/bin/env bash
set -eu

OUTPUT="${DASHBOARD_OUTPUT:-HDMI-A-1}"
ROTATION="${DASHBOARD_ROTATION:-90}"

if ! wlr-randr --output "$OUTPUT" --transform "$ROTATION"; then
    echo "Could not rotate output '$OUTPUT'; check the output name with wlr-randr." >&2
    exit 1
fi

if [ -n "${DASHBOARD_MODE:-}" ]; then
    wlr-randr --output "$OUTPUT" --mode "$DASHBOARD_MODE"
fi

# labwc does not blank displays by itself. Do not start swayidle or another
# idle/DPMS manager for this always-on signage session.
wlr-randr --output "$OUTPUT" --on
