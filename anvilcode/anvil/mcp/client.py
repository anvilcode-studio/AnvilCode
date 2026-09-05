"""A minimal, dependency-free MCP stdio client.

Speaks newline-delimited JSON-RPC 2.0 to a server subprocess:
  initialize -> notifications/initialized -> tools/list -> tools/call
A reader thread matches responses to pending request ids; server-initiated
pings are answered, anything else is politely declined.
"""
from __future__ import annotations

import json
import subprocess
import threading
import queue
import itertools

PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    pass


class MCPClient:
    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict | None = None, cwd: str | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env_extra = env or {}
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self._replies: "queue.Queue[dict]" = queue.Queue()
        self._ids = itertools.count(1)
        self.tools: list[dict] = []
        self.status = "not started"   # not started | connected | dead | error
        self.detail = ""

    # ---------------- lifecycle ----------------

    def start(self, timeout: float = 15.0):
        if self.proc and self.proc.poll() is None:
            return
        try:
            self.proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                cwd=self.cwd,
            )
        except OSError as e:
            self.status = "error"
            self.detail = str(e)
            raise MCPError(f"cannot start MCP server {self.name!r}: {e}")

        threading.Thread(target=self._reader, daemon=True).start()
        try:
            result = self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "anvilcode", "version": "2.0.0"},
            }, timeout=timeout)
            self._notify("notifications/initialized")
            self.status = "connected"
            server = (result or {}).get("serverInfo", {})
            self.detail = f"{server.get('name', '?')} {server.get('version', '')}".strip()
        except (MCPError, queue.Empty) as e:
            self.status = "error"
            self.detail = str(e)
            raise MCPError(f"MCP handshake failed for {self.name!r}: {e}")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.proc.kill()
                except OSError:
                    pass
        self.status = "not started"
        self.tools = []

    # ---------------- protocol ----------------

    def _send(self, msg: dict):
        if not self.proc or self.proc.poll() is not None:
            raise MCPError(f"MCP server {self.name!r} is not running")
        try:
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise MCPError(f"failed writing to {self.name!r}: {e}")

    def _notify(self, method: str, params: dict | None = None):
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        rid = next(self._ids)
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    **({"params": params} if params is not None else {})})
        deadline = timeout
        while True:
            try:
                msg = self._replies.get(timeout=deadline)
            except queue.Empty:
                raise MCPError(f"MCP {self.name!r}: timed out on {method!r}")
            if msg.get("id") != rid:
                continue  # stale reply from an earlier timed-out call
            if "error" in msg:
                err = msg["error"]
                raise MCPError(f"MCP {self.name!r}: {err.get('message', err)}")
            return msg.get("result", {})

    def _reader(self):
        """Pump stdout lines into the reply queue; answer server pings."""
        proc = self.proc
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    self._replies.put(msg)
                elif "method" in msg:
                    self._handle_server_request(msg)
        except (ValueError, OSError):
            pass
        if self.status != "not started":
            self.status = "dead"
            self.detail = "server process exited"

    def _handle_server_request(self, msg: dict):
        method = msg.get("method", "")
        if method == "ping":
            self._send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {}})
        else:
            self._send({"jsonrpc": "2.0", "id": msg.get("id"),
                        "error": {"code": -32601, "message": f"client does not support {method}"}})

    # ---------------- tool surface ----------------

    def list_tools(self, timeout: float = 15.0) -> list[dict]:
        result = self.request("tools/list", {}, timeout=timeout)
        self.tools = result.get("tools", [])
        return self.tools

    def call_tool(self, tool: str, arguments: dict, timeout: float = 90.0) -> str:
        result = self.request("tools/call", {"name": tool, "arguments": arguments}, timeout=timeout)
        if result.get("isError"):
            raise MCPError(_content_text(result) or "tool reported an error")
        return _content_text(result)


def _content_text(result: dict) -> str:
    parts = []
    for c in result.get("content", []) or []:
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
        else:
            parts.append(json.dumps(c, ensure_ascii=False)[:500])
    if not parts and "structuredContent" in result:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False)[:4000])
    return "\n".join(parts) or "(empty response)"
