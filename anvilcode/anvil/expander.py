"""Input expander — turns @file mentions, @cd connections and $skills into model context.

  @path/to/file        attaches a text file (or image) as context
  @"path with spaces"  same, for paths containing spaces
  @cd "C:\\some\\dir"  CONNECTS a folder as the working directory. The only rule:
                       its contents are never read or listed unless explicitly
                       commanded afterwards.
  $skillname args      expands a skill template (see skills.py)
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from .toolkit import IMAGE_MIME

MAX_TEXT_CONTEXT = 24_000      # chars per text file
IMAGE_MAX_BYTES = 5 * 1024 * 1024

# any non-space run after @ (paths may contain ~, (, #, !, unicode letters, drive colons…);
# trailing prose punctuation is stripped from the token afterwards)
MENTION_RE = re.compile(r'(?<![\w.\\])@("(?:[^"\n]+)"|[^\s]+)')
_TRAILING_PUNCT = ".,;:!?)]}>'\""
SKILL_RE = re.compile(r"^\s*\$([A-Za-z][\w-]*)(?:\s+(.*))?$", re.S)
CD_RE = re.compile(r"^\s*@cd\b\s*(.*)$", re.S)


class Expanded:
    def __init__(self):
        self.text = ""                 # final text sent to the model
        self.images: list[str] = []    # data-URLs
        self.attached: list[str] = []  # human-readable summary for the user
        self.skill: str | None = None
        self.chdir: Path | None = None # set by @cd — caller performs the chdir
        self.note_only = False         # True -> no model turn needed


def _resolve(cwd: Path, token: str) -> Path:
    token = token.strip('"').strip("'")
    p = Path(token).expanduser()
    return p if p.is_absolute() else (cwd / p)


def _read_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if len(raw) > MAX_TEXT_CONTEXT:
        raw = raw[:MAX_TEXT_CONTEXT] + f"\n… [{path.name} truncated at {MAX_TEXT_CONTEXT} chars]"
    return raw


def _split_cd_arg(arg: str) -> tuple[str | None, str]:
    """@cd arg -> (path token, remaining message). Supports quotes and spaces."""
    arg = arg.strip()
    if not arg:
        return None, ""
    m = re.match(r'"([^"\n]+)"\s*(.*)', arg, re.S)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    parts = arg.split(maxsplit=1)
    return parts[0], (parts[1].strip() if len(parts) > 1 else "")


def _handle_cd(raw_text: str, cwd: Path) -> Expanded | None:
    """@cd is handled before everything else and never reads the target folder."""
    m = CD_RE.match(raw_text)
    if not m:
        return None
    ex = Expanded()
    token, rest = _split_cd_arg(m.group(1))
    if token is None:
        ex.note_only = True
        ex.attached.append(f"current directory: {cwd}")
        return ex
    target = _resolve(cwd, token)
    if not target.is_dir():
        ex.note_only = True
        ex.attached.append(f"@cd {token} — [not found or not a folder]; working directory unchanged")
        return ex
    ex.chdir = target.resolve()
    ex.text = (f"[The user connected this working directory: {ex.chdir}. "
               f"Its contents were NOT read — only inspect it with tools if asked.]")
    if rest:
        ex.text += f"\n\n{rest}"
    else:
        ex.note_only = True
    ex.attached.append(f"@cd -> {ex.chdir} (contents not read)")
    return ex


def expand(raw_text: str, cwd: Path, skills: dict) -> Expanded:
    """Expand $skill, @cd and @mentions into model-ready context."""
    cd = _handle_cd(raw_text, cwd)
    if cd is not None:
        return cd

    ex = Expanded()
    text = raw_text

    # ---- $skill (only when it starts the message) ----
    m = SKILL_RE.match(text)
    if m and skills:
        name, args = m.group(1), (m.group(2) or "").strip()
        entry = skills.get(name)
        if entry:
            desc, template = entry
            ex.skill = name
            text = template.replace("{args}", args or "").strip()
            if not args:
                text += (f"\n\n(subject: the user gave no arguments — ask or infer "
                         f"from context: '{raw_text}')")

    # ---- @mentions ----
    blocks = []
    for m in MENTION_RE.finditer(text):
        token = m.group(1)
        if not token.startswith('"'):
            token = token.rstrip(_TRAILING_PUNCT)
            if not token:
                continue
        path = _resolve(cwd, token)
        label = token.strip('"')
        try:
            if not path.exists():
                blocks.append(f"@{label} — [not found]")
                continue
            if path.is_dir():
                # folders are connected, never read (same rule as @cd)
                blocks.append(f"@{label}/ (folder referenced — contents not read; "
                              f"inspect with list_dir only if the task needs it)")
                ex.attached.append(f"{label}/ (folder, not read)")
            elif path.suffix.lower() in IMAGE_MIME:
                if path.stat().st_size > IMAGE_MAX_BYTES:
                    blocks.append(f"@{label} — [image too large, max 5 MB]")
                    continue
                b64 = base64.b64encode(path.read_bytes()).decode()
                ex.images.append(f"data:{IMAGE_MIME[path.suffix.lower()]};base64,{b64}")
                ex.attached.append(f"{label} (image)")
            else:
                blocks.append(f"@{label}\n```\n{_read_text(path)}\n```")
                ex.attached.append(label)
        except OSError as e:
            blocks.append(f"@{label} — [error: {e}]")

    if blocks:
        text = text + "\n\n<attached-context>\n" + "\n\n".join(blocks) + "\n</attached-context>"
    ex.text = text
    return ex
