"""Permission modes — who gets asked before what.

  ask        every edit and every command needs explicit approval (default)
  auto-edit  file edits apply instantly (diff still shown); commands ask
  plan       read-only: the model can look but not touch — it produces a plan
  full       nothing asks ("yolo")
"""
from __future__ import annotations

READ_ONLY_TOOLS = {"list_dir", "read_file", "grep", "glob"}
EDIT_TOOLS = {"write_file", "edit_file"}


class Mode:
    def __init__(self, key: str, label: str, desc: str,
                 auto_edit: bool, auto_cmd: bool, read_only: bool):
        self.key = key
        self.label = label
        self.desc = desc
        self.auto_edit = auto_edit
        self.auto_cmd = auto_cmd
        self.read_only = read_only

    @property
    def system_note(self) -> str:
        if self.key == "plan":
            return ("MODE: PLAN — read-only. Do NOT modify files or run mutating commands; "
                    "investigate, then end with a numbered, step-by-step implementation plan "
                    "the user can approve.")
        if self.key == "full":
            return ("MODE: FULL ACCESS — approvals are disabled, so double-check before acting; "
                    "prefer safe, reversible commands and keep edits surgical.")
        if self.key == "auto-edit":
            return ("MODE: AUTO-EDIT — file edits apply instantly (the user still sees each diff); "
                    "shell commands still ask first, so batch your edits and stay deliberate.")
        return "MODE: ASK — every edit and command is shown to the user for approval first."


MODES: dict[str, Mode] = {
    "ask": Mode("ask", "Ask before changes",
                "every edit and command asks (safest default)",
                auto_edit=False, auto_cmd=False, read_only=False),
    "auto-edit": Mode("auto-edit", "Edit automatically",
                      "edits apply at once (diff shown); commands still ask",
                      auto_edit=True, auto_cmd=False, read_only=False),
    "plan": Mode("plan", "Plan mode",
                 "read-only — investigates, then proposes a numbered plan",
                 auto_edit=False, auto_cmd=False, read_only=True),
    "full": Mode("full", "Full access",
                 "nothing asks — edits and commands run straight away",
                 auto_edit=True, auto_cmd=True, read_only=False),
}


def get(cfg: dict) -> Mode:
    key = cfg.get("mode", "ask")
    return MODES.get(key, MODES["ask"])


def needs_confirmation(cfg: dict, tool_name: str) -> bool:
    """True when this tool should prompt the user before running."""
    mode = get(cfg)
    if mode.read_only and tool_name not in READ_ONLY_TOOLS:
        return False  # blocked entirely (agent returns a read-only notice)
    if mode.auto_cmd:
        return False
    if tool_name in EDIT_TOOLS and mode.auto_edit:
        return False
    if tool_name in READ_ONLY_TOOLS:
        return False
    return True


def is_blocked(cfg: dict, tool_name: str) -> bool:
    return get(cfg).read_only and tool_name not in READ_ONLY_TOOLS
