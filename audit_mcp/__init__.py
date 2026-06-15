"""
audit_mcp/

Phase 2A — Stock Lens MCP Audit Server V1 (2026-05-16).

This package hosts a read-only audit MCP server. Claude is the auditor.
Nothing in this package may submit orders, mutate the database, or call
provider APIs (FMP / Alpaca / Tradier). See
``docs/ops/STOCKLENS_MCP_AUDIT_SERVER.md`` for the operator doctrine.

The package is named ``audit_mcp`` (not ``mcp``) to avoid shadowing the
upstream ``mcp`` Python SDK on ``sys.path``.
"""
