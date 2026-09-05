"""ida-mcp — bundled MCP server that bridges AnvilCode into IDA Pro.

Targets the popular, free "ida-pro-mcp" IDA plugin (mrexodia/ida-pro-mcp),
which serves XML-RPC on http://127.0.0.1:13337 while IDA is open.

Setup:
  1. install the ida-pro-mcp plugin into IDA (free, see its GitHub README)
  2. open a database in IDA and start the plugin (Edit > MCP > Start server)
  3. in AnvilCode:  /mcp add ida   then   /mcp connect ida

If IDA is not running you'll get a clear "connection refused" — everything
here fails soft and never crashes the agent.
"""
import json
import sys
import xmlrpc.client

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_PORT = 13337
MAX_TEXT = 60_000


def _proxy(port: int) -> xmlrpc.client.ServerProxy:
    return xmlrpc.client.ServerProxy(f"http://127.0.0.1:{port}", allow_none=True)


def _call(port: int, method: str, *args):
    try:
        server = _proxy(port)
        result = getattr(server, method)(*args)
    except OSError as e:
        raise RuntimeError(
            f"cannot reach IDA on port {port} ({e}) — is IDA open with the "
            f"ida-pro-mcp plugin server started (Edit > MCP > Start)?")
    except xmlrpc.client.Fault as e:
        raise RuntimeError(f"IDA RPC error: {e.faultString}")
    text = str(result)
    return text[:MAX_TEXT] + (f"\n… [trimmed]" if len(text) > MAX_TEXT else "")


def _port_from(args: dict) -> int:
    return int(args.get("port") or DEFAULT_PORT)


# ---------------- tools ----------------

TOOLS = [
    {
        "name": "ida_metadata",
        "description": "Basic info about the database open in IDA (file, arch, entry points).",
        "inputSchema": {"type": "object",
                        "properties": {"port": {"type": "integer"}}, "required": []},
    },
    {
        "name": "ida_list_functions",
        "description": "List functions in the IDA database (start address, end address, name).",
        "inputSchema": {"type": "object",
                        "properties": {"port": {"type": "integer"},
                                       "count": {"type": "integer",
                                                 "description": "Max functions (default 100)"}},
                        "required": []},
    },
    {
        "name": "ida_decompile",
        "description": "Decompile a function by name or address into pseudocode.",
        "inputSchema": {"type": "object",
                        "properties": {"query": {"type": "string",
                                                 "description": "Function name or hex address"},
                                       "port": {"type": "integer"}},
                        "required": ["query"]},
    },
    {
        "name": "ida_imports",
        "description": "List imported functions/addresses.",
        "inputSchema": {"type": "object",
                        "properties": {"port": {"type": "integer"}}, "required": []},
    },
    {
        "name": "ida_strings",
        "description": "List strings found in the binary.",
        "inputSchema": {"type": "object",
                        "properties": {"port": {"type": "integer"},
                                       "count": {"type": "integer"}},
                        "required": []},
    },
]


def ida_metadata(args: dict) -> str:
    return _call(_port_from(args), "get_metadata")


def ida_list_functions(args: dict) -> str:
    count = max(1, min(int(args.get("count") or 100), 500))
    out = _call(_port_from(args), "list_functions", False, count)
    return out


def ida_decompile(args: dict) -> str:
    query = str(args["query"]).strip()
    port = _port_from(args)
    if query.startswith(("0x", "0X")) or query.isdigit():
        return _call(port, "decompile_function", int(query, 16) if query.lower().startswith("0x")
                     else int(query))
    return _call(port, "decompile_function", query)


def ida_imports(args: dict) -> str:
    return _call(_port_from(args), "list_imports")


def ida_strings(args: dict) -> str:
    count = max(1, min(int(args.get("count") or 100), 500))
    return _call(_port_from(args), "list_strings", count)


HANDLERS = {
    "ida_metadata": ida_metadata,
    "ida_list_functions": ida_list_functions,
    "ida_decompile": ida_decompile,
    "ida_imports": ida_imports,
    "ida_strings": ida_strings,
}


# ---------------- tiny MCP stdio server loop (same shape as generic.py) ----------------

def _reply(rid, result):
    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, ensure_ascii=False), flush=True)


def _error(rid, code, message):
    print(json.dumps({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": code, "message": message}}, ensure_ascii=False), flush=True)


def serve():
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
                         "serverInfo": {"name": "ida-mcp", "version": "2.0.0"}})
        elif method == "ping":
            _reply(rid, {})
        elif method == "tools/list":
            _reply(rid, {"tools": TOOLS})
        elif method == "tools/call":
            fn = HANDLERS.get(params.get("name", ""))
            if fn is None:
                _error(rid, -32602, f"unknown tool {params.get('name')!r}")
                continue
            try:
                text = fn(params.get("arguments") or {})
                _reply(rid, {"content": [{"type": "text", "text": str(text)}], "isError": False})
            except Exception as e:
                _reply(rid, {"content": [{"type": "text",
                                          "text": f"{type(e).__name__}: {e}"}], "isError": True})
        elif rid is not None:
            _error(rid, -32601, f"method not supported: {method}")


if __name__ == "__main__":
    serve()
