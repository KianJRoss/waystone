#!/usr/bin/env python3
"""Run a Mod Organizer 2 instance's game the way a launcher should: MO2 stays out of sight, Waystone sees the
game's real lifetime, and the stages are reported in a small status file.

    mo2_launch.py --proton .../GE-Proton10-14/proton --compat-data .../compatdata/2843608499 \
        --mo2 ".../MO2/ModOrganizer.exe" --shortcut SKSE --game-exe SkyrimSE.exe \
        --status-file ~/.local/state/waystone/launch-status/nolvus.json --env SteamAppId=489830 ...

1. Starts MO2 with `moshortcut://:<shortcut>` through `proton run` (no Steam container).
2. Moves every MO2 window (the main window, the "locked while running" dialog) to a hidden Hyprland special
   workspace as soon as it appears. If the game has not started after --reveal-after seconds, MO2's windows are
   brought back so an error dialog is never stuck out of sight.
3. Exits only when the game has exited (then closes MO2 and the prefix's wineserver), so the launcher's lifetime
   is the play session. If MO2 is closed before the game starts, it exits too.
Status file: {"stage": starting|loading|game-starting|playing|closing|done|failed|attention, "text": ..., "game_pid"}.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from native_services.launcher_owner import (  # noqa: E402
    HIDDEN_WORKSPACE as HIDDEN,
    Hyprland as Hypr,
    WindowOwnership,
    atomic_status_write,
    pids_with_env,
    process_snapshot as processes,
    process_tree as tree,
)


def prefix_pids(compat_data: str, table: dict[int, tuple[int, str]]) -> set[int]:
    """Processes running in this Proton prefix (Wine re-parents some children, so the tree alone misses them).
    Matched on WINEPREFIX: Proton rewrites STEAM_COMPAT_DATA_PATH inside the game (e.g. to compatdata/<SteamAppId>)."""
    return pids_with_env("WINEPREFIX", str(Path(compat_data) / "pfx"), table)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proton", required=True)
    ap.add_argument("--compat-data", required=True)
    ap.add_argument("--mo2", required=True, help="path of ModOrganizer.exe")
    ap.add_argument("--shortcut", default="SKSE", help="MO2 executable name to run (moshortcut://:NAME)")
    ap.add_argument("--profile", default="", help="MO2 profile (default: MO2's selected profile)")
    ap.add_argument("--game-exe", required=True, help="the game's process name, e.g. SkyrimSE.exe")
    ap.add_argument("--loader-exe", default="skse64_loader.exe")
    ap.add_argument("--status-file", required=True)
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE for the game (repeat)")
    ap.add_argument("--reveal-after", type=int, default=240, help="show MO2 again if the game has not started")
    ap.add_argument("--no-hide", action="store_true")
    ap.add_argument("--game-monitor", default="", help="move the game window to this monitor (e.g. DP-2)")
    ap.add_argument("--fullscreen", action="store_true", help="make the game window fullscreen")
    a = ap.parse_args(argv)

    status_path = Path(a.status_file).expanduser()
    status_path.parent.mkdir(parents=True, exist_ok=True)

    def status(stage: str, text: str, **extra) -> None:
        atomic_status_write(status_path, {"stage": stage, "text": text, "time": time.time(), "launcher_pid": os.getpid(), **extra})

    env = dict(os.environ)
    for pair in a.env:
        key, _, value = pair.partition("=")
        env[key] = value
    env["STEAM_COMPAT_DATA_PATH"] = a.compat_data
    env.setdefault("STEAM_COMPAT_CLIENT_INSTALL_PATH", str(Path.home() / ".local/share/Steam"))
    argv_mo2 = [a.proton, "run", a.mo2] + (["-p", a.profile] if a.profile else []) + [f"moshortcut://:{a.shortcut}"]

    status("starting", "Starting Mod Organizer")
    launcher = subprocess.Popen(argv_mo2, cwd=str(Path(a.mo2).parent), env=env, start_new_session=True)

    def stop_everything() -> None:
        status("closing", "Closing Mod Organizer")
        try:
            os.killpg(launcher.pid, signal.SIGTERM)
        except OSError:
            pass
        wineserver = Path(a.proton).parent / "files/bin/wineserver"
        if wineserver.exists():
            subprocess.run([str(wineserver), "-k"], env={**env, "WINEPREFIX": str(Path(a.compat_data) / "pfx")},
                           capture_output=True, timeout=30)
        try:
            launcher.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(launcher.pid, signal.SIGKILL)

    def on_signal(signum, _frame):        # Waystone (or the user) stops the session
        stop_everything()
        status("done", "Stopped")
        sys.exit(0)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    hypr = Hypr()
    owned_windows = WindowOwnership(hypr)
    placed: set[str] = set()
    revealed = False
    started = time.time()
    game_pid = 0
    game_gone_since = 0.0
    stage = "starting"
    while True:
        time.sleep(0.5)
        table = processes()
        ours = tree(launcher.pid, table) | prefix_pids(a.compat_data, table)
        game = [p for p in ours if a.game_exe.lower() in table[p][1].lower() and a.loader_exe.lower() not in table[p][1].lower()]
        loader = [p for p in ours if a.loader_exe.lower() in table[p][1].lower()]

        # Keep MO2 (and its dialogs) out of sight; the game window stays where it is. Re-check every tick: MO2
        # re-maps its window when it switches to the "locked while running" view, which brings it back.
        clients = hypr.clients()
        # Put the game where the user plays (their main monitor, fullscreen) once its window exists.
        if (a.game_monitor or a.fullscreen) and game:
            for c in clients:
                if c.get("address") not in placed and (c.get("pid") in game or (c.get("pid") in ours and game_window(c, a.game_exe))):
                    hypr.place(c["address"], a.game_monitor, a.fullscreen and not c.get("fullscreen"))
                    placed.add(c["address"])
        if not a.no_hide and not revealed:
            owned_windows.hide(clients, ours, exclude_pids=set(game),
                               exclude_window=lambda client: game_window(client, a.game_exe))

        if game:
            game_pid = game[0]
            game_gone_since = 0.0
            if stage != "playing":
                stage = "playing"
                status("playing", "Playing", game_pid=game_pid)
        elif game_pid:
            game_gone_since = game_gone_since or time.time()
            if time.time() - game_gone_since > 3:      # the game has really exited
                stop_everything()
                status("done", "Game closed")
                return 0
        elif loader and stage != "game-starting":
            stage = "game-starting"
            status("game-starting", "Starting the game (script extender)")
        elif stage == "starting" and time.time() - started > 2:
            stage = "loading"
            status("loading", "Mod Organizer is loading the mod list")

        if not game_pid and not revealed and time.time() - started > a.reveal_after:
            revealed = True
            ws = hypr.active_workspace()
            owned_windows.restore(clients, destination=ws)
            status("attention", "Mod Organizer needs attention (the game has not started)")

        if launcher.poll() is not None and not (ours - {launcher.pid}):
            status("done" if game_pid else "failed", "Game closed" if game_pid else "Mod Organizer closed before the game started")
            return 0 if game_pid else 1


def game_window(client: dict, game_exe: str) -> bool:
    title = str(client.get("title") or "")
    stem = Path(game_exe).stem.lower()
    # Bethesda games title their window after the game, not the exe; MO2 titles start with "Mod Organizer".
    return bool(title) and not title.lower().startswith(("mod organizer", "modorganizer")) and (
        stem in title.lower().replace(" ", "") or "skyrim" in title.lower() or "fallout" in title.lower())


if __name__ == "__main__":
    sys.exit(main())
