#!/usr/bin/env python
"""Build-time patch (v1.28.0): server.py'ye env-gated scoped-write install enjekte et.

Anchor: read-only disable blogundaki `mcp.disable(tags={"write"})` (server.py:216).
Bu satirdan SONRA (if is_read_only blogunun icinde, 8-space) env-gated install
ekler: MARKDOWN_VAULT_MCP_WRITE_QUARANTINE_PREFIX set ise yalniz `write` geri acilir
+ scope gate takilir. Flag yoksa davranis degismez.

server.py module-scope'ta `os` import ETMEZ -> __import__("os") (oauth-inject ile ayni).
Idempotent (MARKER) + anchor count==1 + py_compile.

Usage:
    python quarantine-write-inject-v128.py          # in-image path
    python quarantine-write-inject-v128.py <path>   # dry-run
"""
import sys
import py_compile

TARGET = sys.argv[1] if len(sys.argv) > 1 else "/app/src/markdown_vault_mcp/server.py"
ANCHOR = '        mcp.disable(tags={"write"})\n'
MARKER = "QUARANTINE_WRITE install"
BLOCK = (
    f"        # {MARKER} (env-gated, Faz 3 scoped write-back)\n"
    '        if __import__("os").environ.get("MARKDOWN_VAULT_MCP_WRITE_QUARANTINE_PREFIX"):\n'
    "            from markdown_vault_mcp._quarantine_write import install_quarantine_write\n"
    "            install_quarantine_write(mcp)\n"
)

with open(TARGET, encoding="utf-8") as f:
    src = f.read()

if MARKER in src:
    print("quarantine-inject(v128): already patched, skipping")
    sys.exit(0)

n = src.count(ANCHOR)
if n != 1:
    print(f"quarantine-inject(v128): anchor found {n}x (need exactly 1) — FAIL", file=sys.stderr)
    sys.exit(1)

src = src.replace(ANCHOR, ANCHOR + BLOCK, 1)
with open(TARGET, "w", encoding="utf-8") as f:
    f.write(src)

py_compile.compile(TARGET, doraise=True)
print(f"quarantine-inject(v128): patched + compiled OK -> {TARGET}")
