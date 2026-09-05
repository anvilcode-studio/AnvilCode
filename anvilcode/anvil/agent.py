"""The agent loop: user turn -> model -> tool calls (mode-aware approvals) -> final answer."""
from __future__ import annotations

import datetime
import json
import time
from pathlib import Path

from . import modes, system_prompt, toolkit
from .client import ToolsUnsupported
from .config import LOGS_DIR
from .mcp.manager import MCPManager
from .toolkit import ToolContext, confirm_plan, make_diff, parse_tool_blocks, summarize_args
from .toolkit import HANDLERS, SAFE
from .ui import StreamView


class Agent:
    def __init__(self, cfg: dict, ui, client, manager: MCPManager):
        self.cfg = cfg
        self.ui = ui
        self.client = client
        self.manager = manager
        self.native_tools = True
        self.always: set[str] = set()   # tools the user approved for the whole session
        self.never: set[str] = set()    # tools the user denied for the whole session
        self.edits: list[tuple[str, str]] = []  # (tool, path) changes made this session
        self.total_in = 0
        self.total_out = 0
        self.plugin_extra = ""
        self.messages: list[dict] = [self._system_msg()]

    # ---------------- context management ----------------

    def _system_msg(self) -> dict:
        return {"role": "system",
                "content": system_prompt.build(self.cfg, Path.cwd(), self.plugin_extra)}

    def reset(self):
        """Fresh conversation (/new)."""
        self.messages = [self._system_msg()]
        self.total_in = self.total_out = 0

    def reload_system(self):
        self.messages[0] = self._system_msg()

    def context_size(self) -> int:
        return sum(len(json.dumps(m, ensure_ascii=False)) for m in self.messages[1:])

    # ---------------- the turn loop ----------------

    def run_turn(self, user_text: str, images: list[str] | None = None) -> str:
        text = user_text
        if images:
            parts = [{"type": "text", "text": user_text}]
            parts += [{"type": "image_url", "image_url": {"url": img}} for img in images]
            self.messages.append({"role": "user", "content": parts})
        else:
            self.messages.append({"role": "user", "content": text})

        started = time.monotonic()
        n_tools = 0
        final = ""

        for _ in range(int(self.cfg.get("max_rounds", 30))):
            if self._should_autocompact():
                self.ui.info("context is getting large — compacting automatically")
                self.compact()

            self._trim()
            view = StreamView(self.ui.console)
            try:
                res = self._call(view)
            except ToolsUnsupported:
                self._enable_json_mode()
                continue
            except KeyboardInterrupt:
                view.abort()
                raise
            except Exception:
                view.abort()
                raise

            self.total_in += getattr(res.usage, "prompt_tokens", 0) or 0
            self.total_out += getattr(res.usage, "completion_tokens", 0) or 0
            if res.reasoning and self.cfg.get("show_thinking"):
                self.ui.show_reasoning(res.reasoning)
            view.finish()

            if res.tool_calls and self.native_tools:
                self.messages.append({
                    "role": "assistant",
                    "content": res.content or None,
                    "tool_calls": [
                        {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}
                        for cid, name, args in res.tool_calls
                    ],
                })
                for cid, name, args in res.tool_calls:
                    n_tools += 1
                    self._execute_native(cid, name, args)
                continue

            if res.tool_calls and not self.native_tools:
                self.messages.append({"role": "assistant", "content": res.content})
                blocks = parse_tool_blocks(res.content)
                if not blocks:
                    final = res.content
                    break
                results = [f"[{name}]\n{self._run_tool(name, args)}"
                           for name, args in blocks]
                self.messages.append({"role": "user",
                                      "content": "TOOL RESULTS:\n\n" + "\n\n".join(results)})
                continue

            final = res.content
            break
        else:
            self.ui.warn("stopped: hit the tool-round limit — /new to start fresh")

        self.ui.usage_line(time.monotonic() - started, self.total_in, self.total_out, n_tools)
        return final

    # ---------------- model call ----------------

    def _call(self, view: StreamView):
        status = None

        def ensure_status():
            nonlocal status
            if status is None:
                status = self.ui.console.status("anvil is working", spinner="arc")
                status.start()

        def on_status(evt):
            if isinstance(evt, str):
                ensure_status()
                status.update(f"{evt}")
            else:
                kind, delta = evt
                if kind == "reason":
                    ensure_status()
                    view.on_reasoning(delta)
                elif kind == "content":
                    if status is not None:
                        status.stop()
                    view.on_content(delta)

        try:
            return self.client.stream(
                model=self.cfg["model"],
                messages=self.messages,
                tools=self._tool_specs(),
                effort=self.cfg.get("effort", "medium"),
                on_status=on_status,
            )
        finally:
            if status is not None:
                status.stop()

    def _tool_specs(self):
        """Native tool specs, or None in JSON-protocol mode (the protocol lives in the prompt)."""
        if not self.native_tools:
            return None
        return toolkit.tool_specs(self.cfg, extra=self.manager.tool_specs())

    def _enable_json_mode(self):
        self.native_tools = False
        self.ui.warn("provider rejected native tools — switching to JSON tool protocol")
        specs = self._all_specs()
        lines = "\n".join(
            f"- {s['function']['name']}: {s['function']['description']}" for s in specs)
        proto = system_prompt.JSON_TOOL_PROTOCOL.format(tools=lines)
        self.messages[0] = {"role": "system",
                            "content": self.messages[0]["content"] + "\n\n" + proto}

    # ---------------- tool execution ----------------

    def _execute_native(self, call_id: str, name: str, raw_args: str):
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            self.ui.tool_header(name, "(malformed arguments)")
            self.messages.append({
                "role": "tool", "tool_call_id": call_id,
                "content": "Error: arguments were not valid JSON. Re-check the schema and retry.",
            })
            return
        result = self._run_tool(name, args)
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": result})

    def _run_tool(self, name: str, args: dict) -> str:
        self.ui.tool_header(name, summarize_args(name, args))

        if modes.is_blocked(self.cfg, name):
            self.ui.tool_result("blocked — plan mode is read-only")
            return ("BLOCKED: the current mode is 'plan' (read-only). Do not try to modify "
                    "anything; finish your investigation and present a numbered plan instead.")

        if name.startswith("mcp__"):
            return self._run_mcp_tool(name, args)

        handler = HANDLERS.get(name)
        if handler is None:
            self.ui.tool_result(f"Error: unknown tool {name}")
            return f"Error: unknown tool '{name}'. Available: {', '.join(sorted(HANDLERS))}"

        if name in self.never:
            self.ui.tool_result("blocked (always-no for this session)")
            return ("BLOCKED for this session: the user chose 'always no' for this tool. "
                    "Do not retry — ask the user what to change first.")

        ctx = ToolContext(Path.cwd(), self.cfg, self.ui)
        plan = confirm_plan(name, args, ctx) if name not in SAFE else None

        if plan is not None and not self._approved(name):
            title, body = plan
            if modes.get(self.cfg).auto_edit and name in modes.EDIT_TOOLS:
                self.ui.show_plan(title, body, note="applied automatically (mode: auto-edit)")
            else:
                ans = self.ui.confirm(title, body)
                if ans in ("n", "v"):
                    if ans == "v":
                        self.never.add(name)
                        self.ui.warn(f"always-no: {name} is blocked for this session (/allow to review)")
                    self.ui.tool_result("declined by user")
                    return ("User DECLINED this action. Do not retry it silently; ask what they "
                            "would like changed or take a different approach.")
                if ans == "a":
                    self.always.add(name)
                    self.ui.info(f"auto-approving {name} this session (/allow to review · /undo reverts file edits)")

        prev_text = None
        target = None
        if name in modes.EDIT_TOOLS:
            target = ctx.resolve(args.get("path", ""))
            if target.exists():
                try:
                    prev_text = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    prev_text = None

        try:
            result = handler(args, ctx)
        except Exception as e:  # a crashing tool must never kill the loop
            result = f"Error: {type(e).__name__}: {e}"

        if result.startswith("OK") and name in modes.EDIT_TOOLS:
            self.edits.append((name, str(target)))
            self._show_applied(target, prev_text)
        self.ui.tool_result(result)
        if name == "run_command" and ctx.last_log:
            self.ui.buttons(("output", ctx.last_log.resolve().as_uri()))
        return result

    def _show_applied(self, path: Path, prev_text: str | None):
        """Colored green/red diff of what was just written, plus clickable buttons."""
        try:
            new_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        diff = make_diff(prev_text or "", new_text, path.name)
        lines = diff.splitlines()
        if not any(l.startswith(("+", "-")) for l in lines):
            return  # no-op write
        if len(lines) > 60:
            diff = "\n".join(lines[:40]) + f"\n… [{len(lines) - 60} lines trimmed] …\n" \
                   + "\n".join(lines[-20:])
        self.ui.diff(f"changes: {path.name}", diff,
                     note="new file" if prev_text is None else "changed")
        try:
            buttons = [("open file", path.resolve().as_uri())]
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            dfile = LOGS_DIR / f"edit-{datetime.datetime.now():%Y%m%d-%H%M%S%f}.diff"
            dfile.write_text("\n".join(lines), encoding="utf-8")
            buttons.append(("open diff", dfile.resolve().as_uri()))
            self.ui.buttons(*buttons)
        except (OSError, ValueError):
            pass

    def _run_mcp_tool(self, name: str, args: dict) -> str:
        from .mcp.client import MCPError

        if name in self.never:
            self.ui.tool_result("blocked (always-no for this session)")
            return ("BLOCKED for this session: the user chose 'always no' for this tool. "
                    "Do not retry — ask the user what to change first.")

        plan = ("mcp tool " + name.split("__", 2)[2], json.dumps(args, indent=2)[:2000])
        if plan is not None and not self._approved(name):
            title, body = plan
            if modes.get(self.cfg).key == "full":
                self.ui.show_plan(title, body, note="applied automatically (mode: full)")
            else:
                ans = self.ui.confirm(title, body)
                if ans in ("n", "v"):
                    if ans == "v":
                        self.never.add(name)
                        self.ui.warn(f"always-no: {name} is blocked for this session (/allow to review)")
                    self.ui.tool_result("declined by user")
                    return "User DECLINED this MCP tool call."
                if ans == "a":
                    self.always.add(name)

        try:
            result = self.manager.dispatch(name, args)
        except MCPError as e:
            result = f"MCP error: {e}"
        except Exception as e:
            result = f"Error: {type(e).__name__}: {e}"
        self.ui.tool_result(result)
        return result

    def _approved(self, name: str) -> bool:
        if name in self.always:
            return True
        if modes.needs_confirmation(self.cfg, name):
            return False
        return True

    # ---------------- context helpers ----------------

    def _should_autocompact(self) -> bool:
        return (self.context_size() > int(self.cfg.get("history_max_chars", 600_000)) * 0.9
                and len(self.messages) > 12)

    def _trim(self):
        limit = int(self.cfg.get("history_max_chars", 600_000))
        trimmed = False
        while self.context_size() > limit and len(self.messages) > 8:
            del self.messages[1]
            while len(self.messages) > 1 and self.messages[1].get("role") == "tool":
                del self.messages[1]
            trimmed = True
        if trimmed and not any(m.get("role") == "user" for m in self.messages[1:]):
            self.messages.insert(1, {"role": "user", "content": "(earlier context was trimmed to fit)"})

    def compact(self) -> str:
        """Summarize the conversation via the model and replace history with the summary."""
        lines = []
        for m in self.messages[1:]:
            c = m.get("content")
            if isinstance(c, list):
                c = "(multimodal content)"
            if c is None and m.get("tool_calls"):
                c = "(tool calls: " + ", ".join(
                    tc.get("function", {}).get("name", "?") for tc in m["tool_calls"]) + ")"
            lines.append(f"{m.get('role', '?')}: {str(c)[:400]}")
        transcript = "\n".join(lines)[-120_000:]

        self.ui.info("compacting context — asking the model for a dense summary…")
        res = self.client.stream(
            model=self.cfg["model"],
            messages=[
                {"role": "system", "content": self.messages[0]["content"]},
                {"role": "user",
                 "content": ("Summarize this coding-agent conversation for continuation. Keep: the "
                             "goal, key files and their state, decisions made, tool results that "
                             "matter, and the exact next steps. Be dense.\n\n" + transcript)},
            ],
            tools=None,
            effort="low",
            on_status=lambda e: None,
        )
        self.messages = [
            {"role": "system", "content": self.messages[0]["content"]},
            {"role": "user",
             "content": "Earlier conversation summary (compacted):\n" + res.content
                        + "\n\nContinue from here."},
            {"role": "assistant", "content": "Understood — continuing with that context."},
        ]
        self.ui.ok(f"compacted: {len(lines)} messages -> summary ({len(res.content)} chars)")
        return res.content
