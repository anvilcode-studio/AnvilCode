"""All slash commands, in one registry.

Each handler receives the App (shared state) and its argument string and
returns True only when the REPL should quit.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import config, modes, plugins as plugins_mod, sessions, skills as skills_mod
from . import system_prompt
from .config import USER_SKILLS_DIR
from .toolkit import IMAGE_MIME, journal

IMAGE_MAX_BYTES = 5 * 1024 * 1024


class App:
    """Shared state wired up once in anvil.py and passed to every command."""

    def __init__(self):
        self.cfg: dict = {}
        self.ui = None
        self.agent = None
        self.manager = None
        self.skills: dict = {}
        self.plugins: list = []
        self.plugin_dirs: list[Path] = []
        self.pending_blocks: list[str] = []   # context blocks from /attach
        self.pending_images: list[str] = []   # data-URLs from /image or /attach
        self.last_reply: str = ""
        self.repo_dir: Path = Path(__file__).resolve().parent.parent
        self.send = None                      # callable set by anvil.py (text -> agent turn)

    def reload_plugins_and_skills(self):
        self.plugins = plugins_mod.discover(plugins_mod.default_search_dirs(self.repo_dir, Path.cwd()))
        self.skills = skills_mod.load(self.cfg, plugins_mod.merge_skills(self.plugins))
        self.agent.plugin_extra = plugins_mod.prompt_extras(self.plugins)
        self.agent.reload_system()


# ---------------- shared helpers ----------------

def _to_clipboard(text: str, app: App) -> bool:
    try:
        exe = "clip" if shutil.which("clip") else "clip.exe"
        subprocess.run([exe], input=text, text=True, check=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        app.ui.error(f"clipboard failed: {e}")
        return False


def _open_editor(path: Path):
    """Open a file in the user's editor ($EDITOR, Notepad on Windows, vi elsewhere)."""
    editor = os.environ.get("EDITOR") or ("notepad.exe" if os.name == "nt" else "vi")
    subprocess.run([editor, str(path)], check=False)


def _open_in_os(path: Path, app: App):
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        app.ui.ok(f"opened {path}")
    except OSError as e:
        app.ui.error(str(e))


def _load_session(app: App, msgs: list[dict]):
    app.agent.messages = msgs
    app.agent.messages[0] = {"role": "system",
                             "content": system_prompt.build(app.cfg, Path.cwd(), app.agent.plugin_extra)}


# ---------------- registry ----------------

COMMANDS: dict[str, dict] = {}


def command(name, *aliases, help_text=""):
    def deco(fn):
        COMMANDS[name] = {"fn": fn, "help": help_text}
        for a in aliases:
            COMMANDS[a] = {"fn": fn, "help": help_text}
        return fn
    return deco


def dispatch(line: str, app: App) -> bool:
    """Run /command. Returns True to quit."""
    parts = line[1:].strip().split(maxsplit=1)
    cmd = parts[0].lower() if parts else "help"
    rest = parts[1].strip() if len(parts) > 1 else ""
    entry = COMMANDS.get(cmd)
    if not entry:
        app.ui.warn(f"unknown command /{cmd} — /help lists them all")
        return False
    return entry["fn"](rest, app) is True


# ---------------- session & lifecycle ----------------

@command("quit", "exit", "q", help_text="exit (Ctrl+D also works)")
def cmd_quit(rest: str, app: App) -> bool:
    sessions.autosave(app.agent.messages)
    return True


@command("new", "clear", help_text="wipe context and start a new session")
def cmd_new(rest: str, app: App) -> bool:
    app.agent.reset()
    app.pending_blocks.clear()
    app.pending_images.clear()
    app.ui.ok("new session — context cleared" + (" (autosave keeps the old one)" if app.cfg.get("autosave") else ""))
    return False


@command("save", help_text="save the session (/save [name])")
def cmd_save(rest: str, app: App) -> bool:
    p = sessions.save(app.agent.messages, rest or None)
    app.ui.ok(f"session saved -> {p.name}")
    return False


@command("load", help_text="load a saved session (/load <name>)")
def cmd_load(rest: str, app: App) -> bool:
    if not rest:
        app.ui.warn("usage: /load <name>  (see /sessions)")
        return False
    try:
        _load_session(app, sessions.load(rest))
        app.ui.ok(f"loaded {rest} — {len(app.agent.messages) - 1} messages back in context")
    except FileNotFoundError as e:
        app.ui.error(str(e))
    return False


@command("resume", help_text="resume a session (/resume [name] — no name picks from a list)")
def cmd_resume(rest: str, app: App) -> bool:
    if rest:
        return cmd_load(rest, app)
    rows = sessions.list_sessions()
    if not rows:
        app.ui.dim("no saved sessions yet")
        return False
    from rich.prompt import Prompt
    for i, (name, size, _mt) in enumerate(rows[:12], 1):
        app.ui.dim(f"  {i:>2}. {name}  ({size:,} B)")
    pick = Prompt.ask("  resume which? (number, Enter = autosave)", default="")
    if pick == "":
        return cmd_load(sessions.AUTOSAVE, app)
    if pick.isdigit() and 1 <= int(pick) <= min(12, len(rows)):
        return cmd_load(rows[int(pick) - 1][0], app)
    app.ui.warn("cancelled")
    return False


@command("sessions", help_text="list saved sessions")
def cmd_sessions(rest: str, app: App) -> bool:
    rows = sessions.list_sessions()
    if not rows:
        app.ui.dim("no saved sessions yet — /save names one, autosave keeps the latest automatically")
        return False
    for name, size, _mt in rows[:15]:
        mark = " (latest auto-save)" if name == sessions.AUTOSAVE else ""
        app.ui.dim(f"  {name}  ({size:,} B){mark}")
    return False


@command("autosave", help_text="toggle session autosave")
def cmd_autosave(rest: str, app: App) -> bool:
    app.cfg["autosave"] = not app.cfg.get("autosave", True)
    config.save(app.cfg)
    app.ui.ok(f"autosave -> {app.cfg['autosave']}")
    return False


# ---------------- modes & model config ----------------

@command("mode", "modes", help_text="ask | auto-edit | plan | full")
def cmd_mode(rest: str, app: App) -> bool:
    if not rest:
        for key, m in modes.MODES.items():
            cur = " <- current" if key == app.cfg["mode"] else ""
            app.ui.dim(f"  {key:10} {m.label} — {m.desc}{cur}")
        app.ui.dim("switch with /mode <key>")
        return False
    key = rest.lower().replace("_", "-")
    if key == "yolo":
        key = "full"
    if key not in modes.MODES:
        app.ui.warn(f"unknown mode {rest!r} — one of: {', '.join(modes.MODES)}")
        return False
    app.cfg["mode"] = key
    config.save(app.cfg)
    app.agent.reload_system()
    app.ui.ok(f"mode -> {key}: {modes.MODES[key].desc}")
    return False


@command("yolo", help_text="toggle between ask and full access")
def cmd_yolo(rest: str, app: App) -> bool:
    return cmd_mode("full" if app.cfg.get("mode") != "full" else "ask", app)


@command("model", help_text="switch model: flash | glm | any 'org/model:provider' id")
def cmd_model(rest: str, app: App) -> bool:
    if not rest:
        app.ui.info(f"current: {app.cfg['model']}")
        app.ui.dim("aliases: flash = GLM-5.3-Flash · glm / full = GLM-5.3 · or paste any 'org/model:provider' id")
        return False
    app.cfg["model"] = config.resolve_model(rest)
    config.save(app.cfg)
    app.ui.ok(f"model -> {config.model_label(app.cfg['model'])}")
    return False


@command("effort", help_text="reasoning effort: low | medium | high | max")
def cmd_effort(rest: str, app: App) -> bool:
    if rest in config.EFFORTS:
        app.cfg["effort"] = rest
        config.save(app.cfg)
        app.agent.reload_system()
        app.ui.ok(f"effort -> {rest}" + ("  (high + exhaustive-thinking nudge)" if rest == "max" else ""))
    else:
        app.ui.info(f"effort: {app.cfg['effort']} — pick one of: {' '.join(config.EFFORTS)}")
    return False


@command("key", help_text="set your HuggingFace token")
def cmd_key(rest: str, app: App) -> bool:
    import getpass
    key = getpass.getpass("  HF token (input hidden): ").strip()
    if key:
        app.cfg["api_key"] = key
        config.save(app.cfg)
        app.ui.ok(f"token saved ({config.mask_key(key)})")
    return False


@command("system", help_text="custom system prompt: <text> | off | edit | file PATH")
def cmd_system(rest: str, app: App) -> bool:
    if not rest:
        cur = app.cfg.get("system_prompt") or ""
        app.ui.info("custom system prompt: " + (f"{len(cur)} chars set" if cur else "not set — default"))
        app.ui.dim("  /system <text…> · /system off · /system file PATH · /system edit (opens editor)")
        return False
    if rest == "off":
        app.cfg["system_prompt"] = ""
    elif rest == "edit":
        p = config.CONFIG_DIR / "system-prompt.md"
        if not p.exists():
            p.write_text(app.cfg.get("system_prompt") or "", encoding="utf-8")
        _open_editor(p)
        app.cfg["system_prompt"] = p.read_text(encoding="utf-8", errors="replace")
        config.save(app.cfg)
        app.agent.reload_system()
        app.ui.ok("system prompt updated from editor")
    elif rest.startswith("file "):
        p = Path(rest[5:].strip()).expanduser()
        try:
            app.cfg["system_prompt"] = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            app.ui.error(str(e))
            return False
    else:
        app.cfg["system_prompt"] = rest
    config.save(app.cfg)
    app.agent.reload_system()
    app.ui.ok("system prompt updated (applies from the next message)")
    return False


@command("thinking", help_text="toggle showing the model's raw reasoning")
def cmd_thinking(rest: str, app: App) -> bool:
    app.cfg["show_thinking"] = not app.cfg.get("show_thinking", False)
    config.save(app.cfg)
    app.ui.ok(f"show reasoning -> {app.cfg['show_thinking']}")
    return False


# ---------------- attachments ----------------

@command("attach", help_text="attach a file/folder as context for your next message")
def cmd_attach(rest: str, app: App) -> bool:
    if not rest:
        app.ui.warn("usage: /attach <path>   (or just type @path inside a message)")
        return False
    return _attach_path(rest.strip('"'), app)


@command("image", help_text="attach an image for your next message")
def cmd_image(rest: str, app: App) -> bool:
    if not rest:
        app.ui.warn("usage: /image <path>  (png/jpg/gif/webp)")
        return False
    return _attach_path(rest.strip('"'), app)


def _attach_path(token: str, app: App) -> bool:
    p = Path(token).expanduser()
    if not p.exists():
        app.ui.error(f"not found: {p}")
        return False
    if p.suffix.lower() in IMAGE_MIME and p.is_file():
        if p.stat().st_size > IMAGE_MAX_BYTES:
            app.ui.error("image too large (max 5 MB)")
            return False
        import base64
        b64 = base64.b64encode(p.read_bytes()).decode()
        app.pending_images.append(f"data:{IMAGE_MIME[p.suffix.lower()]};base64,{b64}")
        app.ui.ok(f"attached {p.name} — {len(app.pending_images)} image(s) ride with your next message")
        return False
    if p.is_dir():
        # folders connect as context references — contents are never dumped (same rule as @cd)
        app.pending_blocks.append(f"/attach {p} (folder referenced — contents not read; "
                                  f"inspect with list_dir only if the task needs it)")
        app.ui.ok(f"connected folder: {p.name} (contents not read)")
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        app.ui.error(str(e))
        return False
    if len(text) > 24_000:
        text = text[:24_000] + f"\n… [truncated at 24,000 chars]"
    app.pending_blocks.append(f"/attach {p.name}\n```\n{text}\n```")
    app.ui.ok(f"attached {p.name} ({len(text):,} chars)")
    return False


# ---------------- context & info ----------------

@command("history", help_text="what's currently in context")
def cmd_history(rest: str, app: App) -> bool:
    app.ui.info(f"{len(app.agent.messages) - 1} messages in context "
                f"(~{app.agent.context_size():,} chars)")
    for m in app.agent.messages[-8:]:
        c = m.get("content")
        text = c if isinstance(c, str) else "(tool call / multimodal)"
        app.ui.dim(f"  {m.get('role', '?'):>9}: {str(text)[:110]!r}")
    return False


@command("compact", help_text="summarize the conversation to shrink context")
def cmd_compact(rest: str, app: App) -> bool:
    app.agent.compact()
    return False


@command("cd", help_text="change working directory")
def cmd_cd(rest: str, app: App) -> bool:
    target = Path(rest or Path.home()).expanduser()
    try:
        os.chdir(target)
        app.agent.reload_system()
        app.ui.ok(f"cwd -> {Path.cwd()}")
    except OSError as e:
        app.ui.error(str(e))
    return False


@command("tools", help_text="list builtin + MCP tools")
def cmd_tools(rest: str, app: App) -> bool:
    from .toolkit import HANDLERS
    app.ui.info("builtin tools:")
    for name in sorted(HANDLERS):
        app.ui.dim(f"  {name:12} (read-only)" if name in ("list_dir", "read_file", "grep", "glob")
                   else f"  {name}")
    mcp_specs = app.manager.tool_specs()
    if mcp_specs:
        app.ui.info("mcp tools:")
        for s in mcp_specs:
            app.ui.dim(f"  {s['function']['name']}")
    return False


@command("status", help_text="current configuration at a glance")
def cmd_status(rest: str, app: App) -> bool:
    from . import __version__
    app.ui.panel("anvil status", "\n".join([
        f"version   {__version__}",
        f"model     {app.cfg['model']}",
        f"effort    {app.cfg['effort']}   mode: {app.cfg.get('mode', 'ask')}",
        f"cwd       {Path.cwd()}",
        f"token     {config.mask_key(app.cfg.get('api_key', ''))}",
        f"context   {len(app.agent.messages) - 1} messages, ~{app.agent.context_size():,} chars",
        f"skills    {len(app.skills)} loaded: {', '.join(skills_mod.list_names(app.skills))}",
        f"plugins   {len(app.plugins)}: " + (", ".join(p.name for p in app.plugins) or "(none)"),
    ]))
    app.ui.info("mcp servers:")
    for line in app.manager.status_lines():
        app.ui.dim(line)
    return False


@command("copy", help_text="copy the last reply to the clipboard")
def cmd_copy(rest: str, app: App) -> bool:
    if not app.last_reply:
        app.ui.warn("nothing to copy yet")
        return False
    if _to_clipboard(app.last_reply, app):
        app.ui.ok("last reply copied to clipboard")
    return False


@command("export", help_text="export the conversation to markdown")
def cmd_export(rest: str, app: App) -> bool:
    name = rest or f"anvil-session-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out = Path(name).expanduser()
    lines = [f"# AnvilCode session — {datetime.datetime.now():%Y-%m-%d %H:%M}", ""]
    for m in app.agent.messages[1:]:
        role = m.get("role", "?").upper()
        c = m.get("content")
        if isinstance(c, list):
            c = "(multimodal content)"
        if c is None and m.get("tool_calls"):
            c = "(tool calls: " + ", ".join(
                tc.get("function", {}).get("name", "?") for tc in m["tool_calls"]) + ")"
        lines += [f"## {role}", "", str(c), ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    app.ui.ok(f"exported -> {out.resolve()}")
    return False


# ---------------- git helpers ----------------

def _git(*args) -> str | None:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True,
                           timeout=15, errors="replace")
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


@command("diff", help_text="show git diff summary (no model call)")
def cmd_diff(rest: str, app: App) -> bool:
    stat = _git("diff", "--stat") or _git("diff", "--stat", "HEAD")
    short = _git("status", "--short")
    if stat is None and short is None:
        app.ui.warn("not a git repository (or git missing)")
        return False
    if short:
        app.ui.dim(short)
    if stat:
        app.ui.dim(stat)
    if not stat and not short:
        app.ui.ok("working tree clean")
    return False


@command("review", help_text="have the model review your uncommitted changes")
def cmd_review(rest: str, app: App) -> bool:
    diff = _git("diff") or _git("diff", "HEAD") or ""
    stat = _git("diff", "--stat", "HEAD") or ""
    if not diff:
        app.ui.warn("no uncommitted changes found (or not a git repo)")
        return False
    prompt = (f"Review these uncommitted changes like a strict senior engineer.\n\n"
              f"Diff stat:\n{stat[:2000]}\n\nDiff (truncated):\n```diff\n{diff[:60_000]}\n```")
    app.send(prompt)
    return False


@command("init", help_text="generate an AGENTS.md for this project")
def cmd_init(rest: str, app: App) -> bool:
    if (Path.cwd() / "AGENTS.md").exists():
        app.ui.warn("AGENTS.md already exists — delete it first if you want it regenerated")
        return False
    tree = _git("ls-files") or "\n".join(
        str(p.relative_to(Path.cwd())) for p in list(Path.cwd().glob("**/*"))[:200]
        if p.is_file() and "node_modules" not in p.parts and ".git" not in p.parts)
    readme = ""
    for cand in ("README.md", "readme.md", "README"):
        if (Path.cwd() / cand).exists():
            readme = (Path.cwd() / cand).read_text(encoding="utf-8", errors="replace")[:3000]
            break
    prompt = (
        "Create an AGENTS.md file for this project — guidance a coding agent needs: build/test "
        "commands, layout, conventions, gotchas. Base it ONLY on the evidence below; keep it "
        "under 60 lines.\n\nFiles:\n" + tree[:6000]
        + ("\n\nREADME excerpt:\n" + readme if readme else ""))
    app.send(prompt)
    return False


@command("undo", help_text="revert the last file change the agent made")
def cmd_undo(rest: str, app: App) -> bool:
    res = journal.undo_last()
    if not res:
        app.ui.warn("nothing to undo")
        return False
    path, ok = res
    if ok:
        app.ui.ok(f"reverted -> {path}")
    else:
        app.ui.error(f"could not revert {path}")
    return False


@command("logo", help_text="show the anvil banner again")
def cmd_logo(rest: str, app: App) -> bool:
    app.ui.banner(app.cfg)
    return False


# ---------------- extensibility ----------------

@command("mcp", help_text="mcp: list | add generic|ida | connect NAME | disconnect NAME | call SRV TOOL '{json}'")
def cmd_mcp(rest: str, app: App) -> bool:
    parts = rest.split(maxsplit=2)
    sub = parts[0].lower() if parts else "list"
    if sub == "list":
        app.ui.info("mcp servers:")
        for line in app.manager.status_lines():
            app.ui.dim(line)
        return False
    if sub == "add":
        if len(parts) < 2:
            app.ui.warn("usage: /mcp add generic|ida  (bundled servers)")
            return False
        app.ui.info(app.manager.add_bundled(parts[1].lower()))
        return False
    if sub == "connect":
        if len(parts) < 2:
            app.ui.warn("usage: /mcp connect NAME")
            return False
        try:
            client = app.manager.connect(parts[1])
            app.ui.ok(f"connected {parts[1]} ({client.detail}) · {len(client.tools)} tools: "
                      + ", ".join(t["name"] for t in client.tools[:10]))
        except Exception as e:
            app.ui.error(f"{parts[1]}: {e}")
        return False
    if sub == "disconnect":
        app.ui.ok(f"disconnected {parts[1]}" if app.manager.disconnect(parts[1].lower())
                  else f"{parts[1]} was not connected")
        return False
    if sub == "call":
        if len(parts) < 3:
            app.ui.warn("usage: /mcp call SERVER TOOL '{\"arg\": 1}'")
            return False
        srv, tool = parts[1], parts[2]
        args = {}
        if len(parts) > 3:
            try:
                args = json.loads(parts[3])
            except json.JSONDecodeError as e:
                app.ui.error(f"bad JSON args: {e}")
                return False
        try:
            out = app.manager.clients.get(srv)
            app.ui.ok(out.call_tool(tool, args) if out else f"{srv} not connected")
        except Exception as e:
            app.ui.error(str(e))
        return False
    app.ui.warn("usage: /mcp list | add generic|ida | connect NAME | disconnect NAME | call SRV TOOL '{json}'")
    return False


@command("plugins", help_text="plugins: list | reload")
def cmd_plugins(rest: str, app: App) -> bool:
    if rest == "reload":
        app.reload_plugins_and_skills()
        app.ui.ok("plugins and skills reloaded")
    if not app.plugins:
        app.ui.dim("no plugins found — drop a folder in ~/.anvilcode/plugins/ or ./plugins/")
        app.ui.dim("a plugin = plugin.json + commands/*.md + skills/*.md + prompt.md")
        return False
    for p in app.plugins:
        app.ui.dim(f"  {p.name} v{p.version} — {p.description or '(no description)'}")
        for cname, (cdesc, _b) in p.commands.items():
            app.ui.dim(f"    /{cname}: {cdesc}")
        for sname, (sdesc, _t) in p.skills.items():
            app.ui.dim(f"    ${sname}: {sdesc}")
    return False


@command("skills", help_text="skills: list | show N | edit N | new N | copy N")
def cmd_skills(rest: str, app: App) -> bool:
    parts = rest.split(maxsplit=1)
    sub = parts[0].lower() if parts else "list"
    name = parts[1].strip() if len(parts) > 1 else ""

    if sub == "list" and not name:
        app.ui.info(f"{len(app.skills)} skills available (invoke with $name):")
        for sname in skills_mod.list_names(app.skills):
            app.ui.dim(f"  ${sname:10} {app.skills[sname][0]}")
        app.ui.dim("  show | edit | new | copy — e.g. /skills edit explain")
        return False
    if not name:
        app.ui.warn(f"usage: /skills {sub} <name>")
        return False
    if sub == "new":
        if name in app.skills:
            app.ui.warn(f"${name} already exists — /skills edit {name}")
            return False
        path = USER_SKILLS_DIR / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ndescription: {name}\n---\nDescribe what the agent should do. "
                        f"{{args}} is replaced by what the user types after ${name}.\n", encoding="utf-8")
        _open_editor(path)
        app.reload_plugins_and_skills()
        app.ui.ok(f"skill ${name} created — invoke it with ${name}")
        return False
    if name not in app.skills:
        app.ui.warn(f"unknown skill ${name} — /skills lists them")
        return False
    desc, body = app.skills[name]
    if sub == "show":
        app.ui.panel(f"$ {name} — {desc}", body)
    elif sub == "copy":
        if _to_clipboard(body, app):
            app.ui.ok(f"skill ${name} copied to clipboard ({len(body)} chars)")
    elif sub == "edit":
        path = _skill_file(name, app)
        _open_editor(path)
        app.reload_plugins_and_skills()
        app.ui.ok(f"skill ${name} saved" + (" — now overriding the builtin" if name in skills_mod.BUILTIN else ""))
    else:
        app.ui.warn("usage: /skills <list|show|edit|new|copy> [name]")
    return False


def _skill_file(name: str, app: App) -> Path:
    """Path of the editable skill file — creating a user override for builtins."""
    path = USER_SKILLS_DIR / f"{name}.md"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        desc, body = app.skills.get(name, (name, ""))
        path.write_text(f"---\ndescription: {desc}\n---\n{body}\n", encoding="utf-8")
    return path


@command("allow", "permissions", help_text="session approvals: (no args) list | clear")
def cmd_allow(rest: str, app: App) -> bool:
    if rest == "clear":
        app.agent.always.clear()
        app.agent.never.clear()
        app.ui.ok("session approvals and denials cleared")
        return False
    app.ui.info("always-yes this session: " + (", ".join(sorted(app.agent.always)) or "(none)"))
    app.ui.info("always-no this session:  " + (", ".join(sorted(app.agent.never)) or "(none)"))
    app.ui.dim("  /allow clear resets both (they also reset when anvil exits)")
    return False


@command("edits", help_text="every file the agent changed this session, with open buttons")
def cmd_edits(rest: str, app: App) -> bool:
    edits = getattr(app.agent, "edits", [])
    if not edits:
        app.ui.dim("no files changed this session yet")
        return False
    app.ui.info(f"{len(edits)} change(s) this session:")
    ordered: list[str] = []
    for _tool, path in edits:
        if path not in ordered:
            ordered.append(path)
        app.ui.dim(f"  {_tool:10} {path}")
    try:
        app.ui.buttons(*[(f"open {Path(p).name}", Path(p).resolve().as_uri())
                         for p in ordered[:6]])
    except (OSError, ValueError):
        pass
    app.ui.dim("  /undo reverts the last change · full diffs live in ~/.anvilcode/logs")
    return False


@command("open", help_text="open a file or folder with the OS default app")
def cmd_open(rest: str, app: App) -> bool:
    if not rest:
        app.ui.warn("usage: /open <path>")
        return False
    p = Path(rest.strip('"')).expanduser()
    if not p.exists():
        app.ui.error(f"not found: {p}")
        return False
    _open_in_os(p, app)
    return False


@command("logs", help_text="open the logs folder (command output + edit diffs)")
def cmd_logs(rest: str, app: App) -> bool:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _open_in_os(config.LOGS_DIR, app)
    return False


# ---------------- help ----------------

@command("help", "?", help_text="show this list")
def cmd_help(rest: str, app: App) -> bool:
    from rich.table import Table
    from .theme import BLUE
    t = Table(box=None, show_header=False, padding=(0, 2), title="capabilities — /command · @file · $skill",
              title_style=f"bold {BLUE}")
    t.add_column(style=f"bold {BLUE}", no_wrap=True)
    t.add_column(style="dim")
    seen = set()
    for name, entry in COMMANDS.items():
        if name in seen:
            continue
        seen.add(name)
        t.add_row("/" + name, entry["help"])
    app.ui.console.print(t)
    app.ui.dim("  also: @file attaches context · $skill runs a template · \\ at line end continues input")
    return False
