#!/usr/bin/env python
"""Build-time patch (v1.28.0): env-gate the create_download_link tool registration.

Reason (Faz 2 güvenlik incelemesi): create_download_link mints a one-time download
URL for a vault file. Its path validation (utils.validate_path) checks traversal +
.md but does NOT call is_path_excluded — unlike indexing/search which DO honour the
exclude list. Our vault deliberately EXCLUDEs sensitive files (security/**,
hesap-mimarisi.md, sermaye-zenginlik-prensibi.md, pilot-islem-gunlugu.md,
guvenlik-mimarisi-haritasi.md). The tool is NEW in v1.28.0 (absent in the live v1.9.0
13-tool set) and is not needed for Faz 2 (link-graph). To avoid introducing an
exclude-bypass file-serving vector, we skip its registration unless explicitly enabled.
Re-enable in Faz 3 (after verifying exclude-aware serving) by leaving the flag unset.

Idempotent + fail-safe (anchor count==1 assert + py_compile).

Seam (v1.28.0 _server_tools.py register_tools):
    if transport != "stdio":
        _register_download_link_tool(mcp)   <-- ANCHOR

Usage:
    python download-gate-inject-v128.py          # in-image path
    python download-gate-inject-v128.py <path>   # dry-run
"""
import sys
import py_compile

TARGET = sys.argv[1] if len(sys.argv) > 1 else "/app/src/markdown_vault_mcp/_server_tools.py"
ANCHOR = "        _register_download_link_tool(mcp)\n"
MARKER = "DOWNLOAD_LINK gate"
BLOCK = (
    f"        # {MARKER} (env-gated, Faz 2 güvenlik): exclude-bypass riskli, Faz 3'e ertelendi\n"
    '        if __import__("os").environ.get("MARKDOWN_VAULT_MCP_DISABLE_DOWNLOAD_LINK") != "true":\n'
    "            _register_download_link_tool(mcp)\n"
)

with open(TARGET, encoding="utf-8") as f:
    src = f.read()

if MARKER in src:
    print("download-gate(v128): already patched, skipping")
    sys.exit(0)

n = src.count(ANCHOR)
if n != 1:
    print(f"download-gate(v128): anchor found {n}x (need exactly 1) — FAIL", file=sys.stderr)
    sys.exit(1)

src = src.replace(ANCHOR, BLOCK, 1)
with open(TARGET, "w", encoding="utf-8") as f:
    f.write(src)

py_compile.compile(TARGET, doraise=True)
print(f"download-gate(v128): patched + compiled OK -> {TARGET}")
