"""File tools: list, read, write, edit — the write/edit pair journals the previous content."""
from __future__ import annotations

from . import journal as undo_journal


def t_list_dir(args: dict, ctx) -> str:
    path = ctx.resolve(args.get("path") or ".")
    if not path.exists():
        return f"Error: path not found: {path}"
    if path.is_file():
        return f"{path.name} is a file ({path.stat().st_size:,} B)"
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    lines = []
    for p in entries[:400]:
        try:
            lines.append(f"  {p.name}/" if p.is_dir() else f"  {p.name}  ({p.stat().st_size:,} B)")
        except OSError:
            continue
    if len(entries) > 400:
        lines.append(f"  … and {len(entries) - 400} more")
    return "\n".join(lines) or "  (empty directory)"


def t_read_file(args: dict, ctx) -> str:
    path = ctx.resolve(args["path"])
    if not path.exists():
        return f"Error: file not found: {path}"
    if path.is_dir():
        return f"Error: {path} is a directory — use list_dir"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error reading {path}: {e}"
    lines = text.splitlines()
    offset = max(1, int(args.get("offset") or 1))
    limit = max(1, min(int(args.get("limit") or 1200), 4000))
    chunk = lines[offset - 1: offset - 1 + limit]
    out = [f"{i:>6}\t{ln}" for i, ln in enumerate(chunk, start=offset)]
    remaining = len(lines) - (offset - 1 + len(chunk))
    if remaining > 0:
        out.append(f"… [{remaining} more lines — call again with offset={offset + len(chunk)}]")
    return "\n".join(out) or "(empty file)"


def t_write_file(args: dict, ctx) -> str:
    path = ctx.resolve(args["path"])
    content = args.get("content", "")
    prev = None
    try:
        if path.exists():
            prev = path.read_text(encoding="utf-8", errors="replace")
            if prev == content:
                return f"OK: {path} already has exactly this content (nothing written)"
        path.parent.mkdir(parents=True, exist_ok=True)
        undo_journal.record(path, prev)
        path.write_text(content, encoding="utf-8", newline="\n")
        verb = "overwrote" if prev is not None else "created"
        return f"OK: {verb} {path} ({len(content.splitlines())} lines)"
    except OSError as e:
        return f"Error writing {path}: {e}"


def t_edit_file(args: dict, ctx) -> str:
    path = ctx.resolve(args["path"])
    if not path.exists():
        return f"Error: file not found: {path}"
    old, new = args.get("old_string", ""), args.get("new_string", "")
    if old == "":
        return "Error: old_string must not be empty (use write_file to replace whole content)."
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error reading {path}: {e}"
    n = text.count(old)
    if n == 0:
        return "Error: old_string not found in file. Read the file again and copy the text exactly."
    if n > 1 and not args.get("replace_all"):
        return f"Error: old_string appears {n} times — add surrounding context or set replace_all=true."
    new_text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
    if new_text == text:
        return "OK: replacement produced identical content (nothing written)"
    try:
        undo_journal.record(path, text)
        path.write_text(new_text, encoding="utf-8", newline="")
    except OSError as e:
        return f"Error writing {path}: {e}"
    how = f"all {n} occurrences" if (n > 1 and args.get("replace_all")) else "1 occurrence"
    return f"OK: edited {path} ({how})"
