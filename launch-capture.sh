#!/bin/sh
set -eu
umask 077
fluff_root=$(dirname -- "$(readlink -f -- "$0")")
export PYTHONNOUSERSITE=1
exec "$fluff_root/.venv/bin/python" -B "$fluff_root/run.py" capture "$@"
