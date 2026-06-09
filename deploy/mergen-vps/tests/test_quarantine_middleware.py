#!/usr/bin/env python
"""QuarantineWriteMiddleware testleri. Image içinde python ile çalışır (pytest yok)."""
import asyncio, sys
from types import SimpleNamespace
from markdown_vault_mcp._quarantine_write import QuarantineWriteMiddleware
from fastmcp.exceptions import ToolError

mw = QuarantineWriteMiddleware("inbox/wc/")

def ctx(name, **args):
    return SimpleNamespace(message=SimpleNamespace(name=name, arguments=args))

async def call_next(context):
    return "PASSED"

async def main():
    fails = []
    ALLOW = ["inbox/wc/draft-1.md", "inbox/wc/sub/d.md", "inbox/wc/a/b/c.md"]
    DENY  = ["state/note.md", "security/secret.md", "/etc/passwd",
             "inbox/wc/../../security/x.md", "../outside.md", "inbox/wcX/y.md", ""]
    for p in ALLOW:
        if await mw.on_call_tool(ctx("write", path=p, content="x"), call_next) != "PASSED":
            fails.append(f"ALLOW bekleniyordu: {p!r}")
    for p in DENY:
        try:
            await mw.on_call_tool(ctx("write", path=p), call_next)
            fails.append(f"DENY bekleniyordu: {p!r}")
        except ToolError:
            pass
    if await mw.on_call_tool(ctx("search", query="x"), call_next) != "PASSED":
        fails.append("write-dışı araç pass-through olmalı")
    big = "x" * (300 * 1024)
    try:
        await mw.on_call_tool(ctx("write", path="inbox/wc/big.md", content=big), call_next)
        fails.append("oversized içerik DENY olmalı")
    except ToolError:
        pass
    if fails:
        print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
    print("ALL PASS")

asyncio.run(main())
