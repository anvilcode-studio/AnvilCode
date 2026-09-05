"""Shell tool — runs a command via Git Bash (if found) or cmd.exe, logging full output.

Encodings: bash speaks UTF-8; cmd.exe speaks the ANSI code page ('mbcs'), which is
what makes ä/ö and friends survive the round trip on Windows.
"""
from __future__ import annotations

import datetime
import os
import shutil
import subprocess
from pathlib import Path

from ..config import LOGS_DIR

_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "bin", "bash.exe"),
)

_cached_shell: str | None = None


def find_bash() -> str | None:
    global _cached_shell
    if _cached_shell is not None:
        return _cached_shell or None
    found = shutil.which("bash")
    if not found:
        for cand in _BASH_CANDIDATES:
            if cand and Path(cand).exists():
                found = cand
                break
    _cached_shell = found or ""
    return found


def active_shell_name() -> str:
    return "bash (Git for Windows)" if find_bash() else "cmd.exe"


def t_run_command(args: dict, ctx) -> str:
    cmd = args.get("command", "").strip()
    if not cmd:
        return "Error: empty command."
    timeout = int(args.get("timeout") or ctx.cfg.get("command_timeout", 120))
    bash = find_bash()
    try:
        if bash:
            r = subprocess.run([bash, "-lc", cmd], capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=str(ctx.cwd))
        else:
            r = subprocess.run(["cmd", "/c", cmd], capture_output=True, text=True,
                               encoding="mbcs", errors="replace",
                               timeout=timeout, cwd=str(ctx.cwd))
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except OSError as e:
        return f"Error running command: {e}"
    out = r.stdout or ""
    if r.stderr:
        out += ("\n[stderr]\n" if out else "[stderr]\n") + r.stderr
    out = out.strip() or "(no output)"

    ctx.last_command = cmd
    ctx.last_log = None
    try:  # full output saved so the UI can offer a clickable [ output ] button
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / f"cmd-{datetime.datetime.now():%Y%m%d-%H%M%S%f}.log"
        log_path.write_text(f"$ {cmd}\n\n{out}\n", encoding="utf-8")
        ctx.last_log = log_path
    except OSError:
        pass

    body = out if len(out) <= 12_000 else out[:7_200] + f"\n… [{len(out) - 12_000} chars trimmed] …\n" + out[-4_800:]
    return f"exit code: {r.returncode}\n{body}"
