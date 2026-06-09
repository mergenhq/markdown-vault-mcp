#!/usr/bin/env python
"""Build-time patch (v1.28.0): inject env-gated GitHub OAuth override into server.py.

Idempotent + fail-safe. Anchors on the ``auth_mode`` assignment inside
``make_server()`` and inserts an override block AFTER it, so that when
``MARKDOWN_VAULT_MCP_GITHUB_OAUTH=true`` the auth provider becomes
``build_github_auth()`` (GitHub OAuth + mergenhq allowlist + MultiAuth that
preserves the CLI bearer channel). When the flag is unset, the native
``build_auth()`` selection (bearer/oidc) is left untouched.

Seam evidence (v1.28.0 src/markdown_vault_mcp/server.py, make_server):
    auth = build_auth(config.server)
    # ...comment...
    auth_mode = resolve_auth_mode(config.server) if auth is not None else "none"   <-- ANCHOR
    if auth_mode == "none": ...
    mcp = FastMCP(server_name, ..., auth=auth)

server.py does NOT import ``os`` at module scope -> the gate uses
``__import__("os")``. ``logger`` is already defined in server.py.

Usage:
    python oauth-github-inject-v128.py          # patches the in-image path
    python oauth-github-inject-v128.py <path>   # patches a given file (dry-run)
"""
import sys
import py_compile

TARGET = sys.argv[1] if len(sys.argv) > 1 else "/app/src/markdown_vault_mcp/server.py"
ANCHOR = '    auth_mode = resolve_auth_mode(config.server) if auth is not None else "none"\n'
MARKER = "GITHUB_OAUTH spike override"
BLOCK = (
    f"    # {MARKER} (env-gated, v1.28 seam)\n"
    '    if __import__("os").environ.get("MARKDOWN_VAULT_MCP_GITHUB_OAUTH") == "true":\n'
    "        from markdown_vault_mcp._oauth_github_spike import build_github_auth\n"
    "\n"
    "        auth = build_github_auth()\n"
    '        auth_mode = "github-oauth-spike"\n'
    '        logger.info("auth override: GitHub OAuth spike active (env-gated)")\n'
)

with open(TARGET, encoding="utf-8") as f:
    src = f.read()

if MARKER in src:
    print("oauth-inject(v128): already patched, skipping")
    sys.exit(0)

n = src.count(ANCHOR)
if n != 1:
    print(
        f"oauth-inject(v128): anchor found {n}x (need exactly 1) — FAIL", file=sys.stderr
    )
    sys.exit(1)

src = src.replace(ANCHOR, ANCHOR + BLOCK, 1)
with open(TARGET, "w", encoding="utf-8") as f:
    f.write(src)

py_compile.compile(TARGET, doraise=True)
print(f"oauth-inject(v128): patched + compiled OK -> {TARGET}")
