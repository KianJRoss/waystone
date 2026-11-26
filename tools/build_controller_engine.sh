#!/usr/bin/env bash
# Build the versioned Linux headless engine; never launch it or create devices.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT/components/controller_studio/src/headless/UnifiedController.Headless.csproj"
OUTPUT="$ROOT/components/controller_studio/headless/publish"
if [[ $# -gt 0 ]]; then
  [[ $# -eq 2 && "$1" == --output ]] || { echo 'Usage: build_controller_engine.sh [--output DIR]' >&2; exit 2; }
  OUTPUT="$2"
fi
if ! command -v dotnet >/dev/null || ! dotnet --list-sdks | grep -q '^10\.'; then
  echo 'A .NET 10 SDK is required (the .NET runtime alone cannot build).' >&2
  exit 3
fi
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
exec dotnet publish "$PROJECT" -c Release --self-contained false -p:ContinuousIntegrationBuild=true -p:DebugType=none -o "$OUTPUT"
