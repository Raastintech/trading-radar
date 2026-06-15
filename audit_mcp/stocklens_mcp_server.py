#!/usr/bin/env python3
"""
audit_mcp/stocklens_mcp_server.py

Phase 2A — Stock Lens MCP Audit Server V1 (2026-05-16).

A read-only Model Context Protocol (MCP) server that exposes cached
research artifacts to Claude for audit. The server is a thin stdio
wrapper around :mod:`audit_mcp.stocklens_mcp_tools`; all real logic
lives in that module so tests don't need the MCP runtime.

Doctrine (enforced by code review, not by the MCP framework):

- READ-ONLY. No order submission, no broker close calls, no DB writes.
- No provider HTTP clients. Cached JSON sidecars only, plus a SQLite
  read-only URI handle for ``circuit_breaker_state``.
- Every tool degrades gracefully when an artifact is missing — see
  ``stocklens_mcp_tools.py`` for the ``status: missing_artifact`` shape.

Run locally::

    python -m audit_mcp.stocklens_mcp_server

Or wire into Claude Desktop config — see
``docs/ops/STOCKLENS_MCP_AUDIT_SERVER.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Dict, List

from mcp.server import Server
from mcp.server.lowlevel import NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
import mcp.types as mcp_types

# Import the pure tool layer. This package is named ``audit_mcp`` (not
# ``mcp``) deliberately so it does not shadow the SDK on sys.path.
from audit_mcp.stocklens_mcp_tools import TOOLS, dispatch

logger = logging.getLogger("stocklens_mcp_server")

SERVER_NAME = "stocklens-audit"
SERVER_VERSION = "1.0.0"


def _build_tool_list() -> List[mcp_types.Tool]:
    out: List[mcp_types.Tool] = []
    for name, spec in TOOLS.items():
        out.append(
            mcp_types.Tool(
                name=name,
                description=spec["description"],
                inputSchema=spec["args_schema"],
            )
        )
    return out


def _json_text(payload: Dict[str, Any]) -> str:
    """Serialise a tool payload to a single JSON string.

    Uses ``default=str`` so any stray ``datetime`` / ``Decimal`` value
    in an artifact does not crash the response.
    """
    try:
        return json.dumps(payload, default=str, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "status": "serialisation_error",
            "message": f"{type(exc).__name__}: {exc}",
        })


def build_server() -> Server:
    """Build and wire the MCP Server.

    Exposed as a function so tests can import and inspect handler
    registration without spinning up the stdio loop.
    """
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> List[mcp_types.Tool]:
        return _build_tool_list()

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any] | None) -> List[mcp_types.TextContent]:
        result = dispatch(name, arguments or {})
        return [mcp_types.TextContent(type="text", text=_json_text(result))]

    return server


async def _main_async() -> None:
    server = build_server()
    init_opts = InitializationOptions(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_opts)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        asyncio.run(_main_async())
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001
        logger.exception("stocklens_mcp_server crashed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
