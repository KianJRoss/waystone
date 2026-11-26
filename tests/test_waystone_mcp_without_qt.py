"""The live MCP server runs in a venv without PySide6 (~/.local/share/waystone/mcp-venv).

Regression 2026-11-25: native_services.waystone_control imported Qt at module level, so the MCP crashed at
startup with ModuleNotFoundError: PySide6 (hidden for a day by a long-lived MCP process from an older release).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''
import builtins, sys
real = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "PySide6" or name.startswith("PySide6."):
        raise ModuleNotFoundError("No module named 'PySide6'")
    return real(name, *args, **kwargs)
builtins.__import__ = guarded
sys.path.insert(0, sys.argv[1])
from tools import waystone_mcp
app = waystone_mcp.create_mcp_app(None)
print("ok")
'''


def test_mcp_app_builds_without_pyside6():
    result = subprocess.run([sys.executable, "-c", PROBE, str(ROOT)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("ok")
