#!/usr/bin/env bash
set -uo pipefail

LOG_FILE="${HOME}/soak.log"

while true; do
    timestamp="$(date --iso-8601=seconds)"
    temperature="$(
        vcgencmd measure_temp 2>/dev/null | sed 's/^temp=//' || echo unavailable
    )"
    throttled="$(
        vcgencmd get_throttled 2>/dev/null | sed 's/^throttled=//' || echo unavailable
    )"
    chromium_rss_mb="$(
        ps -eo rss=,comm= |
            awk '$2 ~ /^chromium/ { total += $1 } END { printf "%.1f", total / 1024 }'
    )"
    python_rss_mb="$(
        ps -eo rss=,args= |
            awk '$0 ~ /python.*-m dashboard run/ { total += $1 } END { printf "%.1f", total / 1024 }'
    )"
    free_disk_mb="$(df -Pm "$HOME" | awk 'NR == 2 { print $4 }')"
    restart_count="$(systemctl show dashboard.service -p NRestarts --value 2>/dev/null || echo unknown)"

    printf '%s temperature=%s throttled=%s chromium_rss_mb=%s python_rss_mb=%s free_disk_mb=%s restarts=%s\n' \
        "$timestamp" \
        "$temperature" \
        "$throttled" \
        "${chromium_rss_mb:-0.0}" \
        "${python_rss_mb:-0.0}" \
        "${free_disk_mb:-unknown}" \
        "${restart_count:-unknown}" >>"$LOG_FILE"
    sleep 3600
done
