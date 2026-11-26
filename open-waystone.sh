#!/bin/bash
# Start Waystone. Machine-specific settings are optional and live outside the app folder:
#   ${XDG_CONFIG_HOME:-~/.config}/waystone/waystone.env   shell variables, e.g. WAYSTONE_PYTHON=/path/to/python
#   ${XDG_CONFIG_HOME:-~/.config}/waystone/launch-args    extra app arguments, one per line ('#' starts a comment)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/waystone"
if [ -f "$CONFIG/waystone.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$CONFIG/waystone.env"
    set +a
fi
# Never write bytecode (.pyc) into the application directory (e.g. read-only /usr/share/waystone).
export PYTHONDONTWRITEBYTECODE=1
# Each game runs in its own systemd scope, so closing or updating Waystone never ends a game.
export WAYSTONE_GAME_SCOPES="${WAYSTONE_GAME_SCOPES:-1}"
# Waystone's overlay windows need X11 (XWayland on Wayland desktops); set WAYSTONE_QT_PLATFORM to override.
export QT_QPA_PLATFORM="${WAYSTONE_QT_PLATFORM:-xcb}"
export QT_QUICK_CONTROLS_STYLE=Basic
export QT_QUICK_BACKEND="${WAYSTONE_QT_BACKEND:-software}"
EXTRA=()
if [ -f "$CONFIG/launch-args" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        EXTRA+=("$line")
    done < "$CONFIG/launch-args"
fi
exec "${WAYSTONE_PYTHON:-python3}" -B "$HERE/craft-projects/playnite-desktop/app/app.py" \
    --native-controller-ui "${EXTRA[@]}" "$@"
