"""Search tools: regex grep and glob, with dependency-folder pruning."""
from __future__ import annotations

import os
import re
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".anvilcode",
    "dist", "build", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
}


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            p = Path(dirpath) / f
            try:
                if p.stat().st_size <= 1_500_000:
                    yield p
            except OSError:
                continue


def _truncate(text: str, limit: int = 12_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [{len(text) - limit} chars trimmed]"


def t_grep(args: dict, ctx) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: empty pattern."
    flags = re.IGNORECASE if args.get("ignore_case", True) else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return f"Error: bad regex: {e}"
    root = ctx.resolve(args.get("path") or ".")
    matches = []
    for p in iter_files(root):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text[:1024]:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                rel = p.relative_to(ctx.cwd) if p.is_relative_to(ctx.cwd) else p
                matches.append(f"{rel}:{i}: {line.strip()[:240]}")
                if len(matches) >= 200:
                    return _truncate("\n".join(matches)) + "\n… [200 match limit hit]"
    return "\n".join(matches) if matches else f"No matches for {pattern!r} under {root}"


def t_glob(args: dict, ctx) -> str:
    pattern = args.get("pattern") or "*"
    root = ctx.resolve(args.get("path") or ".")
    hits = []
    try:
        for p in sorted(root.glob(pattern)):
            rel = p.relative_to(ctx.cwd) if p.is_relative_to(ctx.cwd) else p
            hits.append(f"{rel}{'/' if p.is_dir() else ''}")
            if len(hits) >= 300:
                break
    except (OSError, ValueError) as e:
        return f"Error: {e}"
    return "\n".join(hits) if hits else f"No matches: {pattern}"
