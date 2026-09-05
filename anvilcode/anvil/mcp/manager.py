"""MCPManager — registry of configured MCP servers and their tools for the model."""
from __future__ import annotations

import sys
from pathlib import Path

from .client import MCPClient, MCPError

BUNDLED_DIR = Path(__file__).parent.parent / "servers"


class MCPManager:
    def __init__(self, cfg: dict, ui):
        self.cfg = cfg
        self.ui = ui
        self.clients: dict[str, MCPClient] = {}

    # ---------------- registry ----------------

    def servers_config(self) -> dict:
        return self.cfg.setdefault("mcp_servers", {})

    def bundled_server_paths(self) -> dict[str, Path]:
        return {p.stem: p for p in sorted(BUNDLED_DIR.glob("*.py"))}

    def add_bundled(self, key: str, extra: dict | None = None) -> str:
        """Register one of the bundled servers ('generic', 'ida', ...) in config."""
        paths = self.bundled_server_paths()
        if key not in paths:
            return f"unknown bundled server {key!r} — available: {', '.join(paths) or 'none'}"
        python = sys.executable or "python"
        entry = {"command": python, "args": [str(paths[key])],
                 "description": f"bundled anvil MCP server: {key}"}
        if extra:
            entry.update(extra)
        self.servers_config()[key] = entry
        from .. import config
        config.save(self.cfg)
        return f"registered {key} — connect now with /mcp connect {key}"

    # ---------------- lifecycle ----------------

    def connect(self, name: str):
        entry = self.servers_config().get(name)
        if not entry:
            raise MCPError(f"no server named {name!r} — add one with /mcp add generic|ida")
        client = MCPClient(name, entry["command"], entry.get("args"),
                           entry.get("env"), entry.get("cwd"))
        self.clients[name] = client
        client.start()
        client.list_tools()
        return client

    def disconnect(self, name: str):
        client = self.clients.pop(name, None)
        if client:
            client.stop()
            return True
        return False

    def autostart(self):
        for name, entry in self.servers_config().items():
            if entry.get("autostart"):
                try:
                    self.connect(name)
                    self.ui.ok(f"mcp: connected {name} ({self.clients[name].detail}) "
                               f"· {len(self.clients[name].tools)} tools")
                except MCPError as e:
                    self.ui.warn(f"mcp: {name} failed to connect — {e}")

    # ---------------- model surface ----------------

    def connected(self) -> dict[str, MCPClient]:
        return {n: c for n, c in self.clients.items() if c.status == "connected"}

    def tool_specs(self) -> list[dict]:
        specs = []
        for name, client in self.connected().items():
            for t in client.tools:
                specs.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp__{name}__{t.get('name', '?')}",
                        "description": f"[mcp:{name}] {t.get('description') or t.get('name', '')}",
                        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                    },
                })
        return specs

    def dispatch(self, spec_name: str, args: dict) -> str:
        _, server, tool = spec_name.split("__", 2)
        client = self.connected().get(server)
        if not client:
            raise MCPError(f"MCP server {server!r} is not connected")
        return client.call_tool(tool, args)

    def status_lines(self) -> list[str]:
        lines = []
        for name, entry in self.servers_config().items():
            client = self.clients.get(name)
            if client and client.status == "connected":
                lines.append(f"  {name}: connected ({client.detail}) · {len(client.tools)} tools")
            elif client:
                lines.append(f"  {name}: {client.status} {client.detail}")
            else:
                lines.append(f"  {name}: configured, not connected (/mcp connect {name})")
        return lines or ["  (none — try /mcp add generic, then /mcp connect generic)"]


def tool_result_text(result: str) -> str:
    return result if result else "(empty)"
