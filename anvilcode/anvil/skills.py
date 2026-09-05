"""Skills — reusable prompt templates invoked with $name args.

Built-ins live here; user skills in ~/.anvilcode/skills/*.md override them.
Skill file format: optional '--- description: ... ---' frontmatter, then the
template body. '{args}' is replaced by whatever follows $name.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import USER_SKILLS_DIR

BUILTIN: dict[str, tuple[str, str]] = {
    "explain": ("Explain code or a concept",
                "Explain {args} to me: what it does, how it works, and anything surprising. "
                "Read the relevant files first — never guess contents."),
    "review": ("Review code or changes",
               "Review {args} like a strict senior engineer: bugs, security, edge cases, style. "
               "Group findings by severity and show concrete fixes."),
    "test": ("Write tests",
             "Write tests for {args}. First check how the project's tests are structured and "
             "which runner is used, then follow that convention exactly."),
    "refactor": ("Refactor safely",
                 "Refactor {args} in small, individually reviewable steps. "
                 "Show a diff for each step and verify nothing else changed behavior."),
    "commit": ("Write a commit message",
               "Run git diff and git log --oneline -10, then write a conventional-commit "
               "message (subject + body) for the current staged or unstaged changes. "
               "Do not commit unless I ask."),
    "plan": ("Draft an implementation plan",
             "Draft a step-by-step implementation plan for {args}. No edits — investigate, "
             "list steps with risks and file paths, and end with the first concrete step."),
    "doc": ("Write documentation",
            "Write documentation for {args} in this project's existing doc style "
            "(README section or docstrings). Keep it accurate — read before writing."),
    "find": ("Hunt for code",
             "Find where {args} lives in this project: definition, usages, and related tests. "
             "Show file:line references."),
}


def _parse_skill_md(path: Path) -> tuple[str, str] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    desc = path.stem
    body = raw
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.S)
    if m:
        dm = re.search(r"description:\s*(.+)", m.group(1))
        if dm:
            desc = dm.group(1).strip()
        body = raw[m.end():]
    return (desc, body.strip() or path.stem)


def load(cfg: dict, plugin_skills: dict | None = None) -> dict[str, tuple[str, str]]:
    """Builtins <- user dir <- plugins (later wins)."""
    skills = dict(BUILTIN)
    if USER_SKILLS_DIR.exists():
        for p in sorted(USER_SKILLS_DIR.glob("*.md")):
            parsed = _parse_skill_md(p)
            if parsed:
                skills[p.stem] = parsed
    for name, entry in (plugin_skills or {}).items():
        skills[name] = entry
    return skills


def list_names(skills: dict) -> list[str]:
    return sorted(skills)
