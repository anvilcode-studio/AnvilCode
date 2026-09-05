"""anvil-mcp — the generic MCP server bundled with AnvilCode (stdlib only).

Run standalone:  python servers/generic.py
Register in-app: /mcp add generic   then   /mcp connect generic

Tools:
  sys_info        platform / cwd / python info
  http_get        fetch a URL and return it as text (web reading for the agent)
  clipboard_read  read the system clipboard (Windows PowerShell / macOS pbpaste / xclip)
  clipboard_write set the system clipboard
  open_url        open a URL in the default browser
"""
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS = [
    {
        "name": "sys_info",
        "description": "Platform, working directory and Python version of this machine.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "http_get",
        "description": "Fetch a URL (http/https) and return the body as text, HTML tags stripped.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer", "description": "Cap on returned text (default 8000)"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "clipboard_read",
        "description": "Read the system clipboard as text.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "clipboard_write",
        "description": "Write text to the system clipboard.",
        "inputSchema": {"type": "object",
                        "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "open_url",
        "description": "Open a URL in the user's default browser.",
        "inputSchema": {"type": "object",
                        "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
]


# ---------------- tool implementations ----------------

def sys_info(_: dict) -> str:
    import platform
    return (f"platform: {platform.platform()}\n"
            f"python: {platform.python_version()}\n"
            f"cwd: {Path.cwd()}")


def http_get(args: dict) -> str:
    url = args["url"]
    cap = int(args.get("max_chars") or 8000)
    req = urllib.request.Request(url, headers={"User-Agent": "anvilcode-mcp/2.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read(400_000).decode("utf-8", errors="replace")
    text = raw
    if "<html" in raw[:2000].lower():
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    return text[:cap] + (f"\n… [{len(text) - cap} chars trimmed]" if len(text) > cap else "")


def _clipboard_cmd_read() -> str:
    if sys.platform == "win32":
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout
    if sys.platform == "darwin":
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=10).stdout
    for tool in (["xclip", "-selection", "clipboard", "-o"], ["wl-paste"]):
        if shutil.which(tool[0]):
            return subprocess.run(tool, capture_output=True, text=True, timeout=10).stdout
    raise RuntimeError("no clipboard tool found (install xclip)")


def clipboard_read(_: dict) -> str:
    out = _clipboard_cmd_read()
    return out if out.strip() else "(clipboard is empty)"


def clipboard_write(args: dict) -> str:
    text = args.get("text", "")
    if sys.platform == "win32":
        subprocess.run(["clip"], input=text, text=True, timeout=10, check=True)
    elif sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text, text=True, timeout=10, check=True)
    else:
        if not shutil.which("xclip"):
            raise RuntimeError("no clipboard tool found (install xclip)")
        subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True,
                       timeout=10, check=True)
    return f"OK: clipboard set ({len(text)} chars)"


def open_url(args: dict) -> str:
    url = args["url"]
    if not url.startswith(("http://", "https://")):
        raise ValueError("only http(s) URLs are allowed")
    webbrowser.open(url)
    return f"OK: opened {url}"


HANDLERS = {
    "sys_info": sys_info,
    "http_get": http_get,
    "clipboard_read": clipboard_read,
    "clipboard_write": clipboard_write,
    "open_url": open_url,
}


# ---------------- tiny MCP stdio server loop ----------------

def serve(name: str, version: str, tools: list[dict], handlers: dict):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, rid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            _reply(rid, {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": name, "version": version}})
        elif method == "ping":
            _reply(rid, {})
        elif method == "tools/list":
            _reply(rid, {"tools": tools})
        elif method == "tools/call":
            tool = params.get("name", "")
            fn = handlers.get(tool)
            if fn is None:
                _error(rid, -32602, f"unknown tool {tool!r}")
                continue
            try:
                text = fn(params.get("arguments") or {})
                _reply(rid, {"content": [{"type": "text", "text": str(text)}], "isError": False})
            except Exception as e:
                _reply(rid, {"content": [{"type": "text",
                                          "text": f"{type(e).__name__}: {e}"}], "isError": True})
        elif rid is not None:
            _error(rid, -32601, f"method not supported: {method}")


def _reply(rid, result):
    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, ensure_ascii=False), flush=True)


def _error(rid, code, message):
    print(json.dumps({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": code, "message": message}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    serve("anvil-mcp", "2.0.0", TOOLS, HANDLERS)
