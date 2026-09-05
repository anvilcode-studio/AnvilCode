"""MCP (Model Context Protocol) support — connect external tool servers over stdio.

Implemented with zero dependencies: newline-delimited JSON-RPC 2.0 over the
server process's stdin/stdout, per the MCP stdio transport spec.
"""
