#!/bin/sh
set -eu
scripts=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
host=${CORTEX_PYTHON:-}
if [ -z "$host" ]; then host=$(command -v python3 2>/dev/null || true); fi
if [ -z "$host" ]; then printf '%s\n' 'cortex collaborative workspace runtime error: python_host_required' >&2; exit 70; fi
exec "$host" -I -B "$scripts/select_collaborative_workspace.py" --discover "$scripts/run_collaborative_workspace.py" "$@"
