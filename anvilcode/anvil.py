#!/usr/bin/env python3
"""AnvilCode v2 — a GLM-5.3 coding agent in your terminal.

  / for capabilities  ·  @ for context  ·  $ for skills
  modes: ask | auto-edit | plan | full
  plugins, skills and MCP servers are optional drop-in extensions
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

# Windows consoles: force UTF-8 so box art survives piping
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from anvil import __version__, agent as agentmod, client as clientmod, config, sessions
from anvil import commands as cmdmod
from anvil import expander, plugins as plugins_mod, skills as skills_mod
from anvil.commands import App
from anvil.mcp.manager import MCPManager
from anvil.ui import UI


def parse_args():
    ap = argparse.ArgumentParser(
        prog="anvil", description="AnvilCode — GLM-5.3 coding agent in your terminal.")
    ap.add_argument("prompt", nargs="*", help="one-shot prompt: run it and exit")
    ap.add_argument("-p", "--plain", action="store_true", help="one-shot with minimal chrome")
    ap.add_argument("--model", help="model id or alias: flash | glm | org/model:provider")
    ap.add_argument("--effort", choices=config.EFFORTS, help="reasoning effort")
    ap.add_argument("--mode", choices=list(("ask", "auto-edit", "plan", "full")),
                    help="permission mode (default ask)")
    ap.add_argument("--key", help="HuggingFace token (else HF_TOKEN env, else saved, else asked)")
    ap.add_argument("--cwd", help="working directory to start in")
    ap.add_argument("--yolo", action="store_true", help="shortcut for --mode full")
    ap.add_argument("--resume", metavar="NAME", help="resume a saved session (autosave if empty)")
    ap.add_argument("--check", action="store_true", help="run offline self-checks and exit")
    ap.add_argument("--selftest", action="store_true", help="scripted agent-loop test (no API)")
    ap.add_argument("--version", action="store_true")
    return ap.parse_args()


def apply_cli_overrides(cfg: dict, args) -> None:
    if args.key:
        cfg["api_key"] = args.key.strip()
    if args.model:
        cfg["model"] = config.resolve_model(args.model)
    if args.effort:
        cfg["effort"] = args.effort
    if args.mode:
        cfg["mode"] = args.mode
    if args.yolo:
        cfg["mode"] = "full"


def build_app(cfg: dict, ui: UI, client) -> App:
    app = App()
    app.cfg = cfg
    app.ui = ui
    app.manager = MCPManager(cfg, ui)
    app.agent = agentmod.Agent(cfg, ui, client, app.manager)
    app.repo_dir = Path(__file__).resolve().parent
    app.reload_plugins_and_skills()
    return app


# ---------------- offline checks ----------------

def run_check(cfg: dict, ui: UI) -> int:
    """No API key needed. Exercises every subsystem that works without the network."""
    failures = 0

    def step(name, fn):
        nonlocal failures
        try:
            fn()
            ui.ok(name)
        except Exception as e:
            failures += 1
            ui.error(f"{name}: {type(e).__name__}: {e}")

    ui.console.print(f"[bold]anvilcode v{__version__} — offline check[/]\n")

    step("config store", lambda: (config.save(cfg),
                                  assert_true(config.CONFIG_FILE.exists())))

    def _tools():
        with _temp_cwd() as td:
            ctx = _toolkit_ctx(cfg, ui)
            from anvil.toolkit import (t_list_dir, t_read_file, t_write_file, t_edit_file,
                                       t_grep, t_glob, t_run_command)
            assert t_write_file({"path": "hello.txt", "content": "hello world\nsecond line\n"}, ctx).startswith("OK")
            assert "hello world" in t_read_file({"path": "hello.txt"}, ctx)
            assert t_edit_file({"path": "hello.txt", "old_string": "hello", "new_string": "HELLO"}, ctx).startswith("OK")
            assert "HELLO" in t_read_file({"path": "hello.txt"}, ctx)
            assert "hello.txt" in t_grep({"pattern": "HELLO"}, ctx)
            assert "hello.txt" in t_glob({"pattern": "*.txt"}, ctx)
            assert "hello.txt" in t_list_dir({"path": "."}, ctx)
            r = t_run_command({"command": "echo anvil-check-log"}, ctx)
            assert "anvil-check-log" in r, r
            assert ctx.last_log and Path(ctx.last_log).exists(), "command output log missing"
    step("toolkit: write · read · edit · grep · glob · list · run+log", _tools)

    def _undo():
        from anvil.toolkit import journal
        with _temp_cwd() as td:
            ctx = _toolkit_ctx(cfg, ui)
            from anvil.toolkit import t_write_file, t_edit_file
            t_write_file({"path": "u.txt", "content": "one"}, ctx)
            t_edit_file({"path": "u.txt", "old_string": "one", "new_string": "two"}, ctx)
            path, ok = journal.undo_last()
            assert ok and Path(path).read_text(encoding="utf-8") == "one", "undo did not restore"
            path2, ok2 = journal.undo_last()
            assert ok2, "second undo failed"
            assert not Path(path2).exists(), "create-undo should delete the file"
    step("undo journal (edit + create revert)", _undo)

    def _modes():
        from anvil import modes
        assert modes.needs_confirmation({"mode": "ask"}, "edit_file")
        assert not modes.needs_confirmation({"mode": "auto-edit"}, "edit_file")
        assert modes.needs_confirmation({"mode": "auto-edit"}, "run_command")
        assert not modes.needs_confirmation({"mode": "full"}, "run_command")
        assert modes.is_blocked({"mode": "plan"}, "write_file")
        assert not modes.is_blocked({"mode": "plan"}, "read_file")
        assert "plan" not in config.EFFORTS
    step("permission modes", _modes)

    def _expander():
        from anvil import expander as ex
        with _temp_cwd():
            Path("ctx.txt").write_text("ctx content", encoding="utf-8")
            (Path("folder") / "secret.txt").parent.mkdir(exist_ok=True)
            Path("folder") / "secret.txt"
            (Path("folder") / "secret.txt").write_text("hidden", encoding="utf-8")
            skills = {"explain": ("d", "EXPL {args} END")}

            r = ex.expand("look at @ctx.txt please", Path.cwd(), skills)
            assert "ctx content" in r.text and not r.chdir

            r2 = ex.expand("$explain foo.py", Path.cwd(), skills)
            assert r2.skill == "explain" and r2.text.startswith("EXPL foo.py END")

            r3 = ex.expand("no mentions here", Path.cwd(), skills)
            assert r3.skill is None and "<attached-context>" not in r3.text

            target = Path.cwd() / "folder"
            r4 = ex.expand(f'@cd "{target}"', Path.cwd(), skills)
            assert r4.chdir == target.resolve(), r4.chdir
            assert r4.note_only and "hidden" not in r4.text, "read something it must not"

            r4b = ex.expand(f'@cd "{target}" now list the icons', Path.cwd(), skills)
            assert r4b.chdir == target.resolve() and not r4b.note_only and "list the icons" in r4b.text

            r5 = ex.expand("@cd", Path.cwd(), skills)
            assert r5.note_only and r5.chdir is None

            r6 = ex.expand(f"@{Path.cwd() / 'ctx.txt'}", Path.cwd(), skills)
            assert "ctx content" in r6.text, "absolute Windows path with drive colon failed"

            r7 = ex.expand("@folder", Path.cwd(), skills)
            assert "hidden" not in r7.text and any("not read" in a for a in r7.attached)

            r8 = ex.expand('@cd "Z:/does/not/exist"', Path.cwd(), skills)
            assert r8.chdir is None and r8.note_only and any("not found" in a for a in r8.attached)

    step("@ files · @cd connect (never reads) · $ skills", _expander)

    step("skills registry", lambda: assert_true(len(skills_mod.load(cfg)) >= 6))

    def _plugins():
        found = plugins_mod.discover([Path(__file__).parent / "plugins"])
        names = {p.name for p in found}
        assert "git-helper" in names, names
        gh = next(p for p in found if p.name == "git-helper")
        assert "pr" in gh.commands and "changelog" in gh.commands
    step("plugin discovery (bundled git-helper)", _plugins)

    def _mcp():
        manager = MCPManager(cfg, ui)
        entry = {"command": sys.executable,
                 "args": [str(manager.bundled_server_paths()["generic"])]}
        cfg.setdefault("mcp_servers", {})["__check_generic"] = entry
        try:
            client = manager.connect("__check_generic")
            tools = [t["name"] for t in client.tools]
            assert "http_get" in tools and "sys_info" in tools, tools
            out = client.call_tool("sys_info", {})
            assert "platform:" in out, out
            specs = manager.tool_specs()
            assert any(s["function"]["name"] == "mcp____check_generic__sys_info" for s in specs)
        finally:
            manager.disconnect("__check_generic")
            cfg["mcp_servers"].pop("__check_generic", None)
    step("MCP roundtrip (bundled generic server)", _mcp)

    def _diff():
        from anvil.toolkit import make_diff
        d = make_diff("a\nb\n", "a\nc\n", "demo.txt")
        assert "-b" in d and "+c" in d
        ui.diff("diff renderer", d)
    step("unified diff renderer", _diff)

    step("system prompt builder", lambda: assert_true(
        "Anvil" in _sysprompt(cfg) and "Working directory" in _sysprompt(cfg)))

    def _sessions():
        p = sessions.save([{"role": "user", "content": "x"}], "anvil-check")
        assert sessions.load("anvil-check")[0]["content"] == "x"
        p.unlink()
        assert sessions.autosave([{"role": "system", "content": "s"}]) is None
        a = sessions.autosave([{"role": "user", "content": "hi"}])
        assert a and a.stem == "autosave"
        a.unlink()
    step("sessions + autosave", _sessions)

    def _approval_and_buttons():
        from anvil.ui import CONFIRM_MAP
        assert CONFIRM_MAP.get("always") == "a" and CONFIRM_MAP.get("always no") == "v"
        assert CONFIRM_MAP.get("never") == "v"
        ui.buttons(("open demo", "file:///C:/anvil-demo"))
    step("approval choices (y/n/a/v) + clickable buttons", _approval_and_buttons)

    def _skills_override():
        f = config.USER_SKILLS_DIR / "anvil-check.md"
        f.write_text("---\ndescription: check skill\n---\nBODY {args}\n", encoding="utf-8")
        try:
            loaded = skills_mod.load(cfg)
            assert loaded["anvil-check"][1].startswith("BODY")
        finally:
            f.unlink()
    step("user skill overrides (~/.anvilcode/skills)", _skills_override)

    def _banner_art():
        from anvil import _art, theme
        assert _art.ART and _art.PX == 32, "pixel art not generated"
        light = sum(n for row in _art.ART for top, bot, n in row
                    if (top and sum(top) > 550) or (bot and sum(bot) > 550))
        assert light > 80, "anvil shape missing from pixel art"
        rows = theme.banner_rows()
        assert len(rows) == 6 and all(r.cell_len == theme.WORDMARK_WIDTH for r in rows)
        if ui.console.is_terminal:
            ui.banner({**cfg, "_cwd": "demo"})
        else:
            ui.dim("  (pixel art + gradient render in the live terminal)")
    step("pixel-art icon + gradient wordmark banner", _banner_art)

    if failures:
        ui.error(f"{failures} check(s) failed")
        return 1
    ui.console.print("\n[bold]all checks pass — ready to forge.[/]")
    ui.dim("run  python anvil.py  (or anvil.bat) — your saved HF token is reused automatically")
    return 0


def assert_true(cond, msg="assertion failed"):
    if not cond:
        raise AssertionError(msg)


def _temp_cwd():
    class _T:
        def __enter__(self):
            self.td = tempfile.TemporaryDirectory()
            self.old = os.getcwd()
            os.chdir(self.td.name)
            return self.td.name

        def __exit__(self, *a):
            os.chdir(self.old)
            self.td.cleanup()
    return _T()


def _toolkit_ctx(cfg, ui):
    from anvil.toolkit import ToolContext
    return ToolContext(Path.cwd(), dict(cfg), ui)


def _sysprompt(cfg):
    from anvil import system_prompt
    return system_prompt.build({**cfg, "mode": "plan"}, Path.cwd())


# ---------------- selftest ----------------

def _chunk(content=None, tool_calls=None, usage=None):
    import types
    delta = types.SimpleNamespace(content=content, reasoning_content=None, tool_calls=tool_calls)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta, finish_reason=None)],
                                 usage=usage)


def _tcd(index=0, id=None, name=None, args=None):
    import types
    return types.SimpleNamespace(index=index, id=id,
                                 function=types.SimpleNamespace(name=name, arguments=args))


class FakeClient:
    """Replays scripted chunks through the real consume_stream parser (cycling)."""

    def __init__(self, script):
        self.script = script
        self.i = 0
        self.calls = []

    def stream(self, *, model, messages, tools=None, effort=None, on_status=None):
        self.calls.append({"model": model, "tools": tools, "effort": effort})
        item = self.script[self.i % len(self.script)]
        self.i += 1
        return clientmod.consume_stream(item, on_status or (lambda evt: None))


def _tool_script(tool: str, args_chunks: list[str]):
    """Scripted multi-round exchange: announce -> tool call (args streamed) -> final reply."""
    return [
        [_chunk(content="Using a tool.\n"),
         _chunk(tool_calls=[_tcd(0, id="call_1", name=tool)]),
         *[_chunk(tool_calls=[_tcd(0, args=a)]) for a in args_chunks],
         _chunk(usage=_usage(120, 40))],
        [_chunk(content="Done. Anything else to forge?"),
         _chunk(usage=_usage(300, 30))],
    ]


def _write_file_script():
    return _tool_script("write_file", ['{"path": "hello.py", ',
                                       '"content": "print(\'hello from anvil\\n\')"\n}'])


def _run_command_script():
    return _tool_script("run_command", ['{"command": "echo ', 'anvil-log-test"}'])


def _usage(pin, pout):
    import types
    return types.SimpleNamespace(prompt_tokens=pin, completion_tokens=pout)


def run_selftest(cfg: dict, ui: UI) -> int:
    ui.console.print(f"[bold]anvilcode v{__version__} — selftest (scripted model, real loop)[/]\n")

    def scenario_ask():
        confirm_calls = []
        with _temp_cwd():
            app = _selftest_app(cfg, ui)
            real_confirm = ui.confirm
            ui.confirm = lambda *a, **k: (confirm_calls.append(1), "y")[1]
            try:
                app.agent.run_turn("create hello.py that prints hello from anvil")
            finally:
                ui.confirm = real_confirm
            assert Path("hello.py").exists(), "hello.py not created"
            assert confirm_calls, "ask mode should have prompted for approval"
            tool_msgs = [m for m in app.agent.messages if m.get("role") == "tool"]
            assert tool_msgs and tool_msgs[0]["content"].startswith("OK")

    def scenario_auto_edit():
        with _temp_cwd():
            app = _selftest_app({**cfg, "mode": "auto-edit"}, ui)
            def _no_confirm(*a, **k):
                raise AssertionError("auto-edit mode must not prompt for file edits")
            ui.confirm = _no_confirm
            try:
                app.agent.run_turn("create hello.py")
            finally:
                pass
            assert Path("hello.py").exists(), "auto-edit did not write the file"

    def scenario_plan():
        with _temp_cwd():
            app = _selftest_app({**cfg, "mode": "plan"}, ui)
            app.agent.run_turn("try to create hello.py")
            assert not Path("hello.py").exists(), "plan mode must not write files"
            tool_msgs = [m for m in app.agent.messages if m.get("role") == "tool"]
            assert tool_msgs and tool_msgs[0]["content"].startswith("BLOCKED"), \
                "plan mode should block write_file"

    def scenario_never():
        with _temp_cwd():
            app = _selftest_app(cfg, ui)
            calls = []
            first_answer = iter(["v"])

            def _confirm(*a, **k):
                calls.append(1)
                return next(first_answer)
            ui.confirm = _confirm
            app.agent.run_turn("create hello.py")
            assert not Path("hello.py").exists(), "'always no' (v) should have denied the write"
            app.agent.run_turn("try again")
            assert not Path("hello.py").exists(), "always-no must block on later turns too"
            assert len(calls) == 1, f"expected exactly 1 prompt across both turns, got {len(calls)}"

    def scenario_run_log():
        from anvil.config import LOGS_DIR
        before = {p.name for p in LOGS_DIR.glob("cmd-*.log")}
        with _temp_cwd():
            app = _selftest_app({**cfg, "mode": "ask"}, ui, script=_run_command_script())
            ui.confirm = lambda *a, **k: "y"
            app.agent.run_turn("run echo anvil-log-test")
            tool_msgs = [m for m in app.agent.messages if m.get("role") == "tool"]
            assert tool_msgs and "exit code: 0" in tool_msgs[0]["content"], tool_msgs
            assert "anvil-log-test" in tool_msgs[0]["content"]
            assert app.agent.edits == []
        after = {p.name for p in LOGS_DIR.glob("cmd-*.log")}
        assert len(after - before) == 1, "run_command did not write an output log"

    failures = 0
    for name, fn in (("ask mode: prompt + write", scenario_ask),
                     ("auto-edit mode: no prompt", scenario_auto_edit),
                     ("plan mode: writes blocked", scenario_plan),
                     ("always-no: denies now and later", scenario_never),
                     ("run_command: output logged", scenario_run_log)):
        try:
            fn()
            ui.ok(name)
        except Exception as e:
            failures += 1
            ui.error(f"{name}: {type(e).__name__}: {e}")

    if failures:
        return 1
    ui.console.print("\n[bold]selftest passes.[/]")
    return 0


def _selftest_app(cfg, ui, script=None):
    test_cfg = {**cfg, "api_key": "unused", "effort": "medium",
                "mode": cfg.get("mode", "ask"), "mcp_servers": {}}
    client = FakeClient(script or _write_file_script())
    app = App()
    app.cfg = test_cfg
    app.ui = ui
    app.manager = MCPManager(test_cfg, ui)
    app.agent = agentmod.Agent(test_cfg, ui, client, app.manager)
    return app


# ---------------- main ----------------

def main():
    args = parse_args()
    if args.version:
        print(f"anvilcode {__version__}")
        return

    cfg = config.load()
    apply_cli_overrides(cfg, args)
    ui = UI()

    if args.check:
        sys.exit(run_check(cfg, ui))
    if args.selftest:
        sys.exit(run_selftest(cfg, ui))

    if args.cwd:
        os.chdir(Path(args.cwd).expanduser())

    # token: env -> saved (reused; asked before replacing) -> first-run setup
    key = config.ensure_api_key(cfg, ui)
    config.save(cfg)

    client = clientmod.HFClient(key)
    try:
        n = len(client.validate())
        ui.ok(f"huggingface router connected · {n} models visible · token {config.mask_key(key)}")
    except Exception as e:
        ui.warn(f"token check failed: {type(e).__name__}: {str(e)[:160]}")
        ui.dim("  continuing anyway — if calls fail, fix the token with /key")

    app = build_app(cfg, ui, client)
    app.manager.autostart()

    def send(text: str, images: list[str] | None = None):
        """Run an agent turn (used by slash commands like /init and /review)."""
        try:
            app.last_reply = app.agent.run_turn(text, images=images) or app.last_reply
        except KeyboardInterrupt:
            ui.dim("· interrupted")
        if cfg.get("autosave"):
            sessions.autosave(app.agent.messages)
    app.send = send

    if args.resume is not None:
        name = args.resume or sessions.AUTOSAVE
        try:
            msgs = sessions.load(name)
            app.agent.messages = msgs
            app.agent.messages[0] = {"role": "system",
                                     "content": app.agent.messages[0]["content"]}
            ui.ok(f"resumed {name} — {len(msgs) - 1} messages")
        except FileNotFoundError as e:
            ui.error(str(e))
            return

    prompt = " ".join(args.prompt).strip() if args.prompt else ""
    if args.plain:
        if not prompt:
            ui.error("usage: anvil -p \"prompt\"")
            return
        send(prompt)
        return

    ui.banner(cfg)
    ui.dim("  / capabilities · @ file context · @cd folder (no reading) · $ skills · /mode · /help")

    if prompt:
        send(prompt)
        return

    repl(app, ui)


def repl(app: App, ui: UI):
    while True:
        try:
            line = ui.input_line()
        except KeyboardInterrupt:
            continue
        if line is None:
            break
        line = line.strip()
        while line.endswith("\\"):
            more = ui.input_line()
            if more is None:
                break
            line = line[:-1].rstrip() + "\n" + more.rstrip()
        if not line:
            continue
        if line.startswith("/"):
            if line == "/":
                cmdmod.dispatch("/help", app)
                continue
            if cmdmod.dispatch(line, app):
                ui.console.print("[dim]anvil cools down. bye.[/]")
                return
            continue

        ex = expander.expand(line, Path.cwd(), app.skills)
        for note in ex.attached:
            (ui.warn if ("[" in note and "]" in note) else ui.dim)(f"  {note}")
        if ex.chdir:
            os.chdir(ex.chdir)
            app.agent.reload_system()
            ui.ok(f"connected -> {ex.chdir}")
        if ex.note_only:
            continue
        # merge /attach blocks + inline @mentions into one text payload
        text = ex.text
        if app.pending_blocks:
            text = text + "\n\n<attached-context>\n" + "\n\n".join(app.pending_blocks) \
                   + "\n</attached-context>"
            app.pending_blocks.clear()
        images = ex.images + app.pending_images
        app.pending_images.clear()

        try:
            app.last_reply = app.agent.run_turn(text, images=images or None) or app.last_reply
        except KeyboardInterrupt:
            ui.dim("· interrupted — /new drops any half-finished context")
            continue
        except Exception as e:
            ui.error(f"{type(e).__name__}: {e}")
            ui.dim("  auth/API trouble? try /key · /model · or https://huggingface.co/status")
            continue
        if app.cfg.get("autosave"):
            sessions.autosave(app.agent.messages)

    sessions.autosave(app.agent.messages)
    ui.console.print("[dim]anvil cools down. bye.[/]")


if __name__ == "__main__":
    main()
