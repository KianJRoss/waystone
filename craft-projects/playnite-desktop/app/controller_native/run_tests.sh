#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "=== Running Controller Native QML Tests in Private Xvfb ==="
mkdir -p "$SCRIPT_DIR/evidence"

if command -v xvfb-run >/dev/null 2>&1; then
    QT_QPA_PLATFORM=xcb WAYSTONE_PRIVATE_XVFB=1 xvfb-run -s "-screen 0 1920x1080x24" -a \
        uv run --with PySide6 python3 "$SCRIPT_DIR/test_controller_native_ui.py"
else
    QT_QPA_PLATFORM=offscreen WAYSTONE_PRIVATE_XVFB=1 \
        uv run --with PySide6 python3 "$SCRIPT_DIR/test_controller_native_ui.py"
fi

echo "=== Controller Native QML Tests Passed Successfully ==="
