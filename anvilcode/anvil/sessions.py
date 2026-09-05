"""Sessions — named saves plus a rolling autosave after every turn."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from .config import SESSIONS_DIR

AUTOSAVE = "autosave"


def _path(name: str) -> Path:
    name = name.replace(".json", "").replace("/", "-").replace("\\", "-")
    return SESSIONS_DIR / f"{name}.json"


def save(messages: list[dict], name: str | None = None) -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if not name:
        name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = _path(name)
    path.write_text(json.dumps(messages, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def autosave(messages: list[dict]) -> Path | None:
    """Rolling snapshot of the current conversation (kept only if it has real content)."""
    if not any(m.get("role") == "user" for m in messages):
        return None
    return save(messages, AUTOSAVE)


def load(name: str) -> list[dict]:
    path = _path(name)
    if not path.exists():
        raise FileNotFoundError(f"no session named {name!r} in {SESSIONS_DIR}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_sessions(include_autosave: bool = True) -> list[tuple[str, int, float]]:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in SESSIONS_DIR.glob("*.json"):
        if p.stem == AUTOSAVE and not include_autosave:
            continue
        out.append((p.stem, p.stat().st_size, p.stat().st_mtime))
    return sorted(out, key=lambda t: -t[2])
