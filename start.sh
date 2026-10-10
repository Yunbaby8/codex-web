#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
if [[ -x .venv/bin/python ]]; then
    codex_web_python=.venv/bin/python
else
    codex_web_python=python3
fi
exec "$codex_web_python" -m backend.run
