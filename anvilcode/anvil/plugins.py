"""Plugins — drop-in folders that add slash commands, skills and system-prompt notes.

Layout (each part optional):
  my-plugin/
    plugin.json        {"name": "...", "description": "...", "version": "0.1.0"}
    commands/*.md      -> /slash commands  (frontmatter 'description:', body = prompt)
    skills/*.md        -> $skills
    prompt.md          -> appended to the system prompt

Search order (later wins on name clashes):
  bundled repo plugins/  ->  project ./plugins  ->  ~/.anvilcode/plugins/
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import USER_PLUGINS_DIR


@dataclass
class Plugin:
    name: str
    path: Path
    description: str = ""
    version: str = ""
    commands: dict = field(default_factory=dict)   # name -> (desc, body)
    skills: dict = field(default_factory=dict)     # name -> (desc, template)
    prompt_extra: str = ""


def _parse_md(path: Path) -> tuple[str, str] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    desc, body = path.stem, raw
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.S)
    if m:
        dm = re.search(r"description:\s*(.+)", m.group(1))
        if dm:
            desc = dm.group(1).strip()
        body = raw[m.end():]
    return (desc, body.strip())


def _load_plugin_dir(path: Path) -> Plugin | None:
    meta_file = path / "plugin.json"
    if not path.is_dir():
        return None
    name, desc, version = path.name, "", "0.0.0"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            name = meta.get("name", name)
            desc = meta.get("description", "")
            version = meta.get("version", version)
        except (json.JSONDecodeError, OSError):
            pass
    plugin = Plugin(name=name, path=path, description=desc, version=version)

    for f in sorted((path / "commands").glob("*.md")) if (path / "commands").exists() else []:
        parsed = _parse_md(f)
        if parsed:
            plugin.commands[f.stem] = parsed
    for f in sorted((path / "skills").glob("*.md")) if (path / "skills").exists() else []:
        parsed = _parse_md(f)
        if parsed:
            plugin.skills[f.stem] = parsed
    prompt_file = path / "prompt.md"
    if prompt_file.exists():
        try:
            plugin.prompt_extra = prompt_file.read_text(encoding="utf-8")[:4000]
        except OSError:
            pass
    return plugin


def discover(search_dirs: list[Path]) -> list[Plugin]:
    found: dict[str, Plugin] = {}
    for base in search_dirs:
        if not base.exists():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir() and child.suffix != ".json":
                continue
            p = _load_plugin_dir(child if child.is_dir() else None) if child.is_dir() else None
            if p:
                found[p.name] = p  # later search dirs win
    return list(found.values())


def default_search_dirs(repo_dir: Path, cwd: Path) -> list[Path]:
    return [repo_dir / "plugins", cwd / "plugins", USER_PLUGINS_DIR]


def merge_skills(plugins: list[Plugin]) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for p in plugins:
        out.update(p.skills)
    return out


def prompt_extras(plugins: list[Plugin]) -> str:
    extras = [p.prompt_extra.strip() for p in plugins if p.prompt_extra.strip()]
    return "\n\n".join(extras)
