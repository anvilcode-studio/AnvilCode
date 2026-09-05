#!/usr/bin/env bash
# Git Bash / WSL launcher for AnvilCode
PYTHONUTF8=1 exec python "$(cd "$(dirname "$0")" && pwd)/anvil.py" "$@"
