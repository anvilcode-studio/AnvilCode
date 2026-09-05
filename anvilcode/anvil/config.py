"""Configuration, model registry, tokens — everything persists in ~/.anvilcode/config.json."""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".anvilcode"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_DIR = CONFIG_DIR / "sessions"
UNDO_DIR = CONFIG_DIR / "undo"
LOGS_DIR = CONFIG_DIR / "logs"
USER_SKILLS_DIR = CONFIG_DIR / "skills"
USER_PLUGINS_DIR = CONFIG_DIR / "plugins"

MODELS = {
    "flash": "zai-org/GLM-5.3-Flash:novita",
    "glm": "zai-org/GLM-5.3:novita",
    "5.3": "zai-org/GLM-5.3:novita",
    "full": "zai-org/GLM-5.3:novita",
}

EFFORTS = ("low", "medium", "high", "max")

DEFAULTS = {
    "api_key": "",
    "model": MODELS["flash"],
    "effort": "medium",
    "mode": "ask",              # ask | auto-edit | plan | full
    "system_prompt": "",
    "show_thinking": False,
    "max_rounds": 30,
    "history_max_chars": 600_000,
    "command_timeout": 120,
    "autosave": True,
    "mcp_servers": {},          # name -> {command, args, env, autostart}
}


def load() -> dict:
    for d in (CONFIG_DIR, SESSIONS_DIR, UNDO_DIR, LOGS_DIR, USER_SKILLS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    cfg = {**DEFAULTS, "mcp_servers": {}}
    if CONFIG_FILE.exists():
        try:
            stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for k, v in stored.items():
                cfg[k] = v
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in cfg.items() if not k.startswith("_")}
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_model(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return DEFAULTS["model"]
    key = name.lower().replace(" ", "")
    if key in MODELS:
        return MODELS[key]
    if "/" in name:
        return name if ":" in name else f"{name}:novita"
    return f"zai-org/{name}:novita"


def model_label(model_id: str) -> str:
    if model_id == MODELS["flash"]:
        return "GLM-5.3-Flash"
    if model_id == MODELS["glm"]:
        return "GLM-5.3"
    return model_id


def mask_key(key: str) -> str:
    if not key:
        return "not set"
    return f"{key[:5]}…{key[-4:]}" if len(key) > 12 else "set"


def ensure_api_key(cfg: dict, ui) -> str:
    """Token priority: env var -> saved token (kept unless you type a new one) -> first-run ask."""
    env = os.environ.get("HF_TOKEN") or os.environ.get("HF_API_KEY")
    if env:
        return env.strip()

    saved = (cfg.get("api_key") or "").strip()
    if saved:
        # reuse the previous token; ask before replacing it
        new = getpass.getpass(
            f"  [Enter] keep saved token ({mask_key(saved)}) · paste a new one to replace: "
        ).strip()
        if new:
            saved = new
            cfg["api_key"] = new
            save(cfg)
            ui.ok(f"token updated ({mask_key(new)})")
        return saved

    ui.first_run_hint()
    key = getpass.getpass("  HF token (input hidden): ").strip()
    while not key:
        ui.warn("A HuggingFace token is required — free at https://huggingface.co/settings/tokens")
        key = getpass.getpass("  HF token (input hidden): ").strip()
    cfg["api_key"] = key
    save(cfg)
    return key
