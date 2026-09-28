#!/bin/sh
set -eu
umask 077
fluff_root=$(dirname -- "$(readlink -f -- "$0")")
unset GTK_PATH GTK_IM_MODULE_FILE GTK_EXE_PREFIX GIO_MODULE_DIR
export PYTHONNOUSERSITE=1
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user start fluff-monitor-activity.service >/dev/null 2>&1 || true
fi
exec "$fluff_root/.venv/bin/python" -B "$fluff_root/run.py" pet "$@"
