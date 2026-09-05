"""Tool registry — the bridge between the model, the toolkit and the permission modes."""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from .. import modes
from .files import t_edit_file, t_list_dir, t_read_file, t_write_file
from .search import t_glob, t_grep
from .shell import t_run_command

IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

HANDLERS = {
    "list_dir": t_list_dir,
    "read_file": t_read_file,
    "write_file": t_write_file,
    "edit_file": t_edit_file,
    "run_command": t_run_command,
    "grep": t_grep,
    "glob": t_glob,
}

SAFE = {"list_dir", "read_file", "grep", "glob"}


class ToolContext:
    def __init__(self, cwd, cfg: dict, ui):
        self.cwd = Path(cwd).resolve()
        self.cfg = cfg
        self.ui = ui
        self.last_log = None      # full output log written by run_command
        self.last_command = None

    def resolve(self, p) -> Path:
        path = Path(str(p)).expanduser()
        return path if path.is_absolute() else (self.cwd / path)


def make_diff(old_text: str, new_text: str, path) -> str:
    lines = difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
    )
    return "\n".join(lines)


# ---------------- confirmation plans ----------------

def confirm_plan(name: str, args: dict, ctx: ToolContext):
    """Return (title, rich-renderable body) shown before/while the tool runs, else None."""
    if name in SAFE:
        return None
    if name == "write_file":
        path = ctx.resolve(args["path"])
        content = args.get("content", "")
        if path.exists():
            try:
                old = path.read_text(encoding="utf-8", errors="replace")
                if old == content:
                    return None
                return ("overwrite " + path.name, colorize_diff_wrap(make_diff(old, content, path.name)))
            except OSError:
                pass
        preview = content if len(content) <= 2400 else content[:2400] + "\n…"
        return ("create " + path.name, preview)
    if name == "edit_file":
        path = ctx.resolve(args["path"])
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        old, new = args.get("old_string", ""), args.get("new_string", "")
        if not text.count(old):
            return None
        new_text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        if new_text == text:
            return None
        return ("edit " + path.name, colorize_diff_wrap(make_diff(text, new_text, path.name)))
    if name == "run_command":
        return ("run command", args.get("command", ""))
    return ("run " + name, json.dumps(args, indent=2)[:2000])


def colorize_diff(diff_text: str):
    from ..ui import colorize_diff
    return colorize_diff(diff_text)


# ---------------- specs ----------------

def tool_specs(cfg: dict, extra: list[dict] | None = None) -> list[dict]:
    """Builtin specs, filtered by the permission mode, plus any MCP tools."""
    if modes.get(cfg).read_only:
        names = modes.READ_ONLY_TOOLS
    else:
        names = set(HANDLERS)
    specs = [_spec(n) for n in sorted(names)]
    if extra:
        specs.extend(extra)
    return specs


def _spec(name: str) -> dict:
    props, desc = _PARAMS[name]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": _REQUIRED[name]},
        },
    }


_REQUIRED = {
    "list_dir": [], "read_file": ["path"], "write_file": ["path", "content"],
    "edit_file": ["path", "old_string"], "run_command": ["command"],
    "grep": ["pattern"], "glob": ["pattern"],
}

_PARAMS = {
    "list_dir": ({"path": {"type": "string", "description": "Directory path (default '.')"}},
                 "List directory contents (names, sizes, folders first)."),
    "read_file": ({"path": {"type": "string"},
                   "offset": {"type": "integer", "description": "1-based first line"},
                   "limit": {"type": "integer", "description": "Max lines (default 1200)"}},
                  "Read a text file with line numbers; use offset/limit for big files."),
    "write_file": ({"path": {"type": "string"}, "content": {"type": "string"}},
                   "Create a new file or fully overwrite an existing one."),
    "edit_file": ({"path": {"type": "string"},
                   "old_string": {"type": "string", "description": "Exact text to find"},
                   "new_string": {"type": "string"},
                   "replace_all": {"type": "boolean", "description": "Replace every occurrence"}},
                  "Replace an exact string in a file; old_string must be unique unless replace_all."),
    "run_command": ({"command": {"type": "string"},
                     "timeout": {"type": "integer", "description": "Seconds before kill"}},
                    "Run a shell command (bash if available); returns exit code + output."),
    "grep": ({"pattern": {"type": "string"},
              "path": {"type": "string"},
              "ignore_case": {"type": "boolean", "description": "Default true"}},
             "Regex-search files under a path (skips deps/binaries); returns file:line: text."),
    "glob": ({"pattern": {"type": "string"}, "path": {"type": "string"}},
             "Find files by wildcard, e.g. '**/*.py'."),
}


def summarize_args(name: str, args: dict) -> str:
    if name == "run_command":
        return f"$ {args.get('command', '')[:100]}"
    if name == "edit_file":
        return str(args.get("path", ""))
    if name == "write_file":
        return f"{args.get('path', '')} ({len(args.get('content', ''))} chars)"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"[{parts[1]}] {' '.join(f'{k}={str(v)[:40]}' for k, v in list(args.items())[:3])}"
    return " ".join(f"{k}={str(v)[:60]}" for k, v in args.items() if k in ("path", "pattern"))


def parse_tool_blocks(text: str) -> list[tuple[str, dict]]:
    """Fallback JSON-mode parser: <tool>{"name":...,"arguments":{...}}</tool>"""
    out = []
    for m in re.finditer(r"<tool>\s*(\{.*?\})\s*</tool>", text or "", re.S):
        try:
            d = json.loads(m.group(1))
            out.append((d.get("name", ""), d.get("arguments") or {}))
        except json.JSONDecodeError:
            continue
    return out
