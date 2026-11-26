#!/usr/bin/env python3
"""Launch an Android game in Waydroid and live exactly as long as it does, so Waystone shows Play -> Playing.

Kian 2026-11-24: "Can we also add a mobile game to the launcher? I want to add plants vs zombies reflourished".

    android_launch.py --package com.ea.game.pvz2_rfl --title "PvZ 2: Reflourished" \
        --status-file ~/.local/state/waystone/launch-status/pvz2-reflourished.json [--keep-session]

1. Starts a Waydroid session if none runs (with the system Python: Waydroid needs python-dbus, which a user
   Python on PATH may lack).
2. Opens the app (`waydroid app launch`).
3. "Playing" once the app's process exists; done when the app process ends or its Waydroid window is closed.
4. Stops the Waydroid session afterwards if this launch started it (Android keeps RAM otherwise), unless
   --keep-session. SIGTERM (Waystone Quit) does the same.
Stages go to the status file like tools/mo2_launch.py: starting, loading, playing, closing, done, failed.
"""
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

SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"


def waydroid_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = SYSTEM_PATH
    return env


def waydroid(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["waydroid", *args], env=waydroid_env(), capture_output=True, text=True, timeout=timeout)


def session_running() -> bool:
    try:
        out = waydroid("status", timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(line.startswith("Session:") and "RUNNING" in line for line in out.splitlines())


def start_session() -> None:
    runner = shutil.which("systemd-run", path=SYSTEM_PATH)
    if runner:
        subprocess.run([runner, "--user", "--unit=waystone-waydroid-session", "--collect",
                        f"--setenv=PATH={SYSTEM_PATH}", "waydroid", "session", "start"],
                       env=waydroid_env(), capture_output=True, timeout=15)
    else:
        subprocess.Popen(["waydroid", "session", "start"], env=waydroid_env(), start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def app_pids(package: str, proc: Path = Path("/proc")) -> list[int]:
    """Android app processes are host processes too: match the cmdline (package or package:subprocess)."""
    pids = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().split(b"\0", 1)[0].decode(errors="replace")
        except OSError:
            continue
        if cmd == package or cmd.startswith(package + ":"):
            pids.append(int(entry.name))
    return pids


def waydroid_window_open() -> bool | None:
    """True/False for a Waydroid window on Hyprland; None when that cannot be checked."""
    if not (shutil.which("hyprctl") and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")):
        return None
    try:
        clients = json.loads(subprocess.run(["hyprctl", "-j", "clients"], capture_output=True, text=True,
                                            timeout=3).stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return any("waydroid" in (str(c.get("class", "")) + str(c.get("initialClass", ""))).casefold() for c in clients)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--status-file", required=True)
    ap.add_argument("--keep-session", action="store_true")
    ap.add_argument("--start-timeout", type=float, default=90)
    a = ap.parse_args(argv)
    status_path = Path(a.status_file).expanduser()
    status_path.parent.mkdir(parents=True, exist_ok=True)
    title = a.title or a.package

    def status(stage: str, text: str, **extra) -> None:
        tmp = status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"stage": stage, "text": text, "time": time.time(), "launcher_pid": os.getpid(), **extra}))
        os.replace(tmp, status_path)

    started_session = False

    def finish(code: int, text: str) -> int:
        if started_session and not a.keep_session:
            status("closing", "Closing Android")
            try:
                waydroid("session", "stop", timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pass
        status("done" if code == 0 else "failed", text)
        return code

    stop = {"requested": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(requested=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(requested=True))

    status("starting", "Starting Android")
    if not session_running():
        start_session()
        started_session = True
        deadline = time.monotonic() + a.start_timeout
        while not session_running():
            if stop["requested"]:
                return finish(0, "Stopped")
            if time.monotonic() > deadline:
                return finish(1, "Android (Waydroid) did not start")
            time.sleep(1)

    status("loading", f"Opening {title}")
    try:
        waydroid("app", "launch", a.package, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return finish(1, f"Could not open {title}")

    deadline = time.monotonic() + a.start_timeout
    while not app_pids(a.package):
        if stop["requested"]:
            return finish(0, "Stopped")
        if time.monotonic() > deadline:
            return finish(1, f"{title} did not start")
        time.sleep(0.5)
    status("playing", "Playing")

    window_gone_since = None
    while True:
        if stop["requested"]:
            return finish(0, "Stopped")
        if not app_pids(a.package):
            return finish(0, "Game closed")
        window = waydroid_window_open()
        if window is False:
            window_gone_since = window_gone_since or time.monotonic()
            if time.monotonic() - window_gone_since > 3:
                return finish(0, "Game closed")
        else:
            window_gone_since = None
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
