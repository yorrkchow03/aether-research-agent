#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
for aether_python in python3.14 python3.13 python3.12 python3; do
    if command -v "$aether_python" >/dev/null 2>&1 && "$aether_python" -c 'import sys; raise SystemExit(not ((3,12)<=sys.version_info[:2]<(3,15)))' >/dev/null 2>&1; then
        exec "$aether_python" scripts/setup_local.py "$@"
    fi
done
printf '%s\n' 'Install Python 3.12, 3.13 or 3.14, then run ./start.sh again.' >&2
exit 1
