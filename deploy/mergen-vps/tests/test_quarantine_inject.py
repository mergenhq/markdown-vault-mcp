#!/usr/bin/env python
"""quarantine-write-inject testi: anchor==1, idempotent, py_compile, no-anchor=FAIL."""
import subprocess, sys, tempfile, os, py_compile

INJECT = os.path.join(os.path.dirname(__file__), "..", "quarantine-write-inject-v128.py")
FIXTURE = (
    "def make_server(config):\n"
    "    mcp = _Mcp()\n"
    "    is_read_only = config.read_only\n"
    "    if is_read_only:\n"
    '        mcp.disable(tags={"write"})\n'
    "\n"
    "    return mcp\n"
)

def run(path):
    return subprocess.run([sys.executable, INJECT, path], capture_output=True, text=True)

def main():
    fails = []
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(FIXTURE); p = f.name
    r = run(p)
    src = open(p).read()
    if r.returncode != 0: fails.append(f"happy exit={r.returncode} {r.stderr}")
    if "QUARANTINE_WRITE install" not in src: fails.append("MARKER yok")
    if "install_quarantine_write(mcp)" not in src: fails.append("install cagrisi yok")
    try: py_compile.compile(p, doraise=True)
    except Exception as e: fails.append(f"py_compile FAIL: {e}")
    r2 = run(p)
    if r2.returncode != 0 or "already patched" not in r2.stdout:
        fails.append(f"idempotent degil: {r2.returncode} {r2.stdout}")
    os.unlink(p)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write("def make_server(c):\n    return None\n"); p2 = f.name
    r3 = run(p2)
    if r3.returncode == 0: fails.append("anchor yokken exit 0 (FAIL bekleniyordu)")
    os.unlink(p2)
    if fails:
        print("FAIL:"); [print("  -", x) for x in fails]; sys.exit(1)
    print("ALL PASS")

main()
