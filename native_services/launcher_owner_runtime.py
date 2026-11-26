"""Waystone's monitored Steam/Heroic handoff. Tests inject every OS boundary."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Callable
import uuid

if __package__ in (None, ""):  # run as a file path from a release: make the repo importable without PYTHONPATH
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_services.launcher_owner import (HIDDEN_WORKSPACE, Hyprland, WindowOwnership, ancestors,
                                            atomic_status_write, matching_game_processes, process_records,
                                            process_snapshot)

ATTENTION_WORDS = ("login", "log in", "sign in", "eula", "cloud conflict", "cloud sync",
                   "missing install", "update", "error", "failed", "needs attention")
_LAUNCHER_NAMES = {
    "steam": frozenset({"steam", "steam.sh", "steamwebhelper"}),
    "heroic": frozenset({"heroic", "heroicgameslauncher", "heroicgameslauncher-linux"}),
}


def window_needs_attention(client: dict, provider: str) -> bool:
    """Conservative title check only on a matched launcher window; live titles need validation."""
    title = " ".join(str(client.get(k) or "") for k in ("title", "initialTitle")).casefold()
    return any(word in title for word in ATTENTION_WORDS)


def provider_processes(records: dict, provider: str) -> set[int]:
    names = _LAUNCHER_NAMES[provider]
    result = set()
    for pid, record in records.items():
        comm = str(getattr(record, "comm", "")).casefold()
        exe = Path(str(getattr(record, "exe", "") or "")).name.casefold()
        if comm in names or exe in names:
            result.add(pid)
    return result


def _starttime(pid: int, proc_root: str | Path = "/proc") -> int | None:
    try:
        raw = (Path(proc_root) / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        return int(raw[raw.rindex(")") + 2 :].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _terminate_owned(pid: int, expected_starttime: int, provider: str, *, proc_root="/proc", kill=os.kill) -> bool:
    """Signal exactly one still-matching newly-started Heroic PID; never used for Steam."""
    if provider != "heroic" or _starttime(pid, proc_root) != expected_starttime:
        return False
    records = process_records(proc_root)
    if pid not in provider_processes(records, provider):
        return False
    try:
        kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def separate_scope_argv(argv: list[str], provider: str, *, environ=None, which=shutil.which) -> list[str]:
    """Start a launcher client that Waystone has to start (Steam) outside the game's systemd scope.

    Waystone runs each launch in its own `waystone-game-*` scope and Stop ends that scope. A Steam client started
    from inside it would be closed with the game, so it gets its own app scope instead."""
    env = os.environ if environ is None else environ
    runner = which("systemd-run") if env.get("WAYSTONE_GAME_SCOPES") == "1" else None
    if not runner:
        return argv
    return [runner, "--user", "--scope", "--collect", "--quiet",
            f"--unit=app-waystone-{provider}-{uuid.uuid4().hex[:12]}", "--", *argv]


def monitor_launch(*, provider: str, launcher: str, launch_args: list[str], game_id: str,
                   status_file: str | Path, game_executable: str | None = None,
                   game_folder: str | None = None, proc_root: str | Path = "/proc",
                   windows=None, clock: Callable[[], float] = time.monotonic,
                   sleep: Callable[[float], None] = time.sleep, popen: Callable = subprocess.Popen,
                   terminate: Callable | None = None, attention_after: float = 240,
                   startup_timeout: float = 600, poll_interval: float = 0.5,
                   attention_check: Callable[[dict, str], bool] = window_needs_attention,
                   scope_argv: Callable[[list[str], str], list[str]] = separate_scope_argv) -> int:
    """Launch a registered URI and supervise it without closing Steam or unrelated launchers."""
    if provider not in ("steam", "heroic"):
        raise ValueError("provider must be steam or heroic")
    if not launcher or not game_id or not launch_args:
        raise ValueError("launcher, game id and launch arguments are required")
    status_path = Path(status_file).expanduser()
    session_id = uuid.uuid4().hex
    started = clock()
    last_game_seen = 0.0
    game_seen = False
    attention_shown = False
    attention_reason = ""
    stage = "starting"
    stop_requested = False

    def status(new_stage: str, text: str, **extra):
        nonlocal stage
        stage = new_stage
        atomic_status_write(status_path, {"session_id": session_id, "provider": provider,
                                          "stage": stage, "text": text, "time": time.time(),
                                          "launcher_pid": os.getpid(), **extra})

    owner = windows or WindowOwnership(Hyprland())
    self_pid = os.getpid()
    before = set(process_snapshot(proc_root))
    # Windows that were already open (Steam library, friends chat, an open Heroic) are the user's: never hide them.
    preexisting = {str(c.get("address")) for c in owner.backend.clients() if c.get("address")}
    def preexisting_window(client: dict) -> bool:
        return str(client.get("address") or "") in preexisting
    # Hide launcher windows the instant they open (no visible flash), not only on the next poll.
    attention = {"shown": False}
    window_classes = {"steam": {"steam"}, "heroic": {"heroic", "heroic games launcher"}}[provider]
    def on_window_opened(address: str, window_class: str, workspace: str) -> None:
        if (not attention["shown"] and address not in preexisting
                and window_class.casefold() in window_classes):
            owner.hide_new(address, workspace)
    watch = getattr(owner.backend, "watch_new_windows", None)
    stop_watching = watch(on_window_opened) if callable(watch) and hasattr(owner, "hide_new") else None

    argv = [launcher, *launch_args]
    if provider == "steam" and not provider_processes(process_records(proc_root), provider):
        argv = scope_argv(argv, provider)
    proc = popen(argv, start_new_session=True)
    owned_launcher_pid = None
    owned_launcher_starttime = None
    status("starting", f"Starting {provider.title()}")

    old_handlers = {}
    def request_stop(signum, _frame):
        nonlocal stop_requested
        stop_requested = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        old_handlers[sig] = signal.getsignal(sig)
        signal.signal(sig, request_stop)
    try:
        while True:
            now = clock()
            if stop_requested:
                owner.restore(owner.backend.clients())
                if provider == "heroic" and owned_launcher_pid is not None and owned_launcher_starttime is not None:
                    if terminate is not None:
                        terminate(owned_launcher_pid, owned_launcher_starttime, provider)
                    else:
                        _terminate_owned(owned_launcher_pid, owned_launcher_starttime, provider, proc_root=proc_root)
                status("done", "Stopped by Waystone")
                return 0
            records = process_records(proc_root)
            launcher_pids = provider_processes(records, provider)
            if provider == "heroic":
                candidates = sorted(pid for pid in launcher_pids if pid not in before)
                if owned_launcher_pid is None:
                    # A still-live new Heroic process is ours; a short-lived URI forwarder is not.
                    owned_launcher_pid = next((pid for pid in candidates if pid != proc.pid), None)
                    if owned_launcher_pid is not None:
                        owned_launcher_starttime = _starttime(owned_launcher_pid, proc_root)
                    elif proc.pid in candidates and _starttime(proc.pid, proc_root) is not None:
                        owned_launcher_pid = proc.pid
                        owned_launcher_starttime = _starttime(proc.pid, proc_root)

            games = matching_game_processes(records, provider=provider, game_id=game_id,
                                            game_executable=game_executable, game_folder=game_folder,
                                            exclude_pids=ancestors(self_pid, records) | before)
            clients = owner.backend.clients()
            if games or not attention_shown:
                owner.hide(clients, launcher_pids, exclude_window=preexisting_window)
            if games:
                game_seen = True
                last_game_seen = now
                # Proton/Steam runtime helpers carry the game's SteamAppId seconds before the game is up: say
                # "Playing" only once a game process has a window (or when windows cannot be seen at all).
                has_window = any(c.get("pid") in games for c in clients)
                if has_window or not getattr(owner.backend, "enabled", True):
                    if stage != "playing":
                        status("playing", "Playing", game_pids=sorted(games))
                elif stage not in ("playing", "game-starting"):
                    status("game-starting", "Starting the game", game_pids=sorted(games))
            elif game_seen and now - last_game_seen >= 3:
                status("closing", "Game closed")
                if provider == "heroic" and owned_launcher_pid is not None and owned_launcher_starttime is not None:
                    if terminate is not None:
                        terminate(owned_launcher_pid, owned_launcher_starttime, provider)
                    else:
                        _terminate_owned(owned_launcher_pid, owned_launcher_starttime, provider, proc_root=proc_root)
                owner.restore(owner.backend.clients())
                status("done", "Game closed")
                return 0
            elif not game_seen:
                elapsed = now - started
                attention_windows = [c for c in clients if c.get("pid") in launcher_pids
                                     and not preexisting_window(c) and attention_check(c, provider)]
                if elapsed >= startup_timeout:
                    # Never leave Play stuck on "Starting": the launcher stays open (it may be showing the user
                    # something), Waystone just stops waiting.
                    owner.restore(clients)
                    status("failed", "The game did not start", reason="startup-timeout")
                    return 1
                if attention_windows or elapsed >= attention_after:
                    if not attention_shown:
                        attention["shown"] = True
                        owner.reveal(clients, destination=owner.backend.active_workspace())
                        attention_shown = True
                    reason = "dialog" if attention_windows else (
                        "startup-timeout" if elapsed >= startup_timeout else "game-start-timeout")
                    if reason != attention_reason or stage != "attention":
                        attention_reason = reason
                        status("attention", "Launcher needs your attention", reason=attention_reason)
                elif elapsed >= 2 and stage == "starting":
                    status("preparing", f"Preparing in {provider.title()}")

            # If the game never starts and the launched client exits, surface failure promptly.
            if not game_seen and proc.poll() is not None and not launcher_pids:
                owner.restore(clients)
                status("failed", f"{provider.title()} closed before game started")
                return 1
            sleep(poll_interval)
    except KeyboardInterrupt:
        status("closing", "Waystone launch monitor interrupted")
        owner.restore(owner.backend.clients())
        return 130
    finally:
        if stop_watching is not None:
            stop_watching()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("steam", "heroic"), required=True)
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--launch-args-json", required=True)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--game-executable", default="")
    parser.add_argument("--game-folder", default="")
    # Heroic is ready within seconds, so a game that has not started after 90 s means Heroic is showing something
    # (an update, a sign-in). Steam can spend minutes on shader pre-caching.
    parser.add_argument("--attention-after", type=float, default=None)
    parser.add_argument("--startup-timeout", type=float, default=600)
    args = parser.parse_args(argv)
    try:
        launch_args = json.loads(args.launch_args_json)
    except ValueError:
        parser.error("--launch-args-json must be a JSON array")
    if not isinstance(launch_args, list) or not all(isinstance(x, str) for x in launch_args):
        parser.error("--launch-args-json must be a JSON array of strings")
    return monitor_launch(provider=args.provider, launcher=args.launcher, launch_args=launch_args,
                          game_id=args.game_id, status_file=args.status_file,
                          game_executable=args.game_executable or None,
                          game_folder=args.game_folder or None,
                          attention_after=(args.attention_after if args.attention_after is not None
                                           else 90 if args.provider == "heroic" else 240),
                          startup_timeout=args.startup_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
