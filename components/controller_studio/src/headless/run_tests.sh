#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
if [[ "${WAYSTONE_SKIP_ENGINE_BUILD:-0}" != 1 ]]; then
  bash "$ROOT/tools/build_controller_engine.sh"
fi
# Every successful startup in this script uses --fixture and private temp data.
exec python3 "$SCRIPT_DIR/test_headless.py"
