"""Undo journal — every file change is backed up so /undo can restore it."""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import UNDO_DIR

JOURNAL = UNDO_DIR / "journal.jsonl"
MAX_ENTRIES = 60


def record(path: Path, prev_content: str | None):
    """Store the previous content (None = file did not exist) before it changes."""
    UNDO_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.time(), "path": str(path),
             "prev": prev_content, "prev_missing": prev_content is None}
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _trim()


def undo_last() -> tuple[str, bool] | None:
    """Restore the most recent backup. Returns (path, ok) or None if empty."""
    if not JOURNAL.exists():
        return None
    lines = [l for l in JOURNAL.read_text(encoding="utf-8").splitlines() if l.strip()]
    while lines:
        try:
            entry = json.loads(lines.pop())
        except json.JSONDecodeError:
            continue
        JOURNAL.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        path = Path(entry["path"])
        if entry.get("prev_missing"):
            if path.exists():
                path.unlink()
            return (str(path), True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry["prev"], encoding="utf-8")
        return (str(path), True)
    return None


def _trim():
    if not JOURNAL.exists():
        return
    lines = JOURNAL.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_ENTRIES:
        JOURNAL.write_text("\n".join(lines[-MAX_ENTRIES:]) + "\n", encoding="utf-8")
