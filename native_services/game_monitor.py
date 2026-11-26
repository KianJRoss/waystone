"""Which monitor games open on: a default plus per-game choices, applied at launch.

Monitors are remembered by their description ("AOC 32G2WG3 0x00000370"), never by connector name, because
DP-2 today can be DP-1 after a cable swap. At launch the description is resolved to the current connector.

Applying it (Hyprland only; elsewhere this is a quiet no-op):
1. Just before launching, focus the chosen monitor: new windows, including fullscreen games, open on the focused
   monitor.
2. Once the game's window exists, move it there if it still opened elsewhere (once per window).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Iterable

ANYWHERE = "any"   # per-game choice: no preference, wherever the game opens


def _settings_path() -> Path:
    return Path.home() / ".local/share/waystone/settings.json"


class GameMonitorSettings:
    """`games.monitor` (default description or "") and `games.monitor_overrides` {game id: description | "any"}
    in Waystone's settings.json. Other categories in the file are preserved."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _settings_path()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _games(self) -> dict:
        games = self._load().get("games")
        return games if isinstance(games, dict) else {}

    def _save_games(self, games: dict) -> None:
        data = self._load()
        data["games"] = games
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def default(self) -> str:
        value = self._games().get("monitor")
        return value if isinstance(value, str) else ""

    def set_default(self, description: str) -> None:
        games = self._games()
        games["monitor"] = str(description or "")
        self._save_games(games)

    def override(self, game_id: str) -> str:
        overrides = self._games().get("monitor_overrides")
        value = overrides.get(str(game_id)) if isinstance(overrides, dict) and game_id else None
        return value if isinstance(value, str) else ""

    def set_override(self, game_id: str, choice: str) -> None:
        """choice: "" = use the default, "any" = wherever it opens, else a monitor description."""
        if not game_id:
            return
        games = self._games()
        overrides = games.get("monitor_overrides")
        overrides = dict(overrides) if isinstance(overrides, dict) else {}
        if choice:
            overrides[str(game_id)] = str(choice)
        else:
            overrides.pop(str(game_id), None)
        games["monitor_overrides"] = overrides
        self._save_games(games)

    def effective(self, game_id: str | None) -> str:
        """The monitor description a game should open on, or "" for no preference."""
        choice = self.override(game_id) if game_id else ""
        if choice == ANYWHERE:
            return ""
        return choice or self.default


def resolve(description: str, monitors: Iterable[dict]) -> str | None:
    """Connector name for a remembered description: exact match first, then same make + model (a monitor whose
    serial reads differently after a firmware/driver change)."""
    if not description:
        return None
    monitors = list(monitors)
    for m in monitors:
        if m.get("description") == description:
            return m.get("name")
    want = description.casefold()
    for m in monitors:
        make_model = f"{m.get('make', '')} {m.get('model', '')}".strip().casefold()
        if make_model and want.startswith(make_model):
            return m.get("name")
    return None


class HyprlandMonitors:
    """Monitor listing, focus and window moves. Supports Hyprland's new `hl.dsp.*` dispatch syntax with the classic
    dispatchers as fallback. Tests inject `run`."""

    def __init__(self, *, run: Callable | None = None, environ: dict | None = None, which: Callable = shutil.which):
        env = os.environ if environ is None else environ
        self.enabled = bool(which("hyprctl")) and bool(env.get("HYPRLAND_INSTANCE_SIGNATURE"))
        self._run = run or subprocess.run

    def _call(self, *args: str) -> str:
        if not self.enabled:
            return ""
        try:
            result = self._run(["hyprctl", *args], capture_output=True, text=True, timeout=3)
            return result.stdout or ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _json(self, what: str):
        try:
            return json.loads(self._call("-j", what) or "[]")
        except ValueError:
            return []

    def monitors(self) -> list[dict]:
        out = []
        for m in self._json("monitors"):
            if not isinstance(m, dict) or m.get("disabled"):
                continue
            out.append({"name": str(m.get("name") or ""), "description": str(m.get("description") or "").strip(),
                        "make": str(m.get("make") or ""), "model": str(m.get("model") or ""),
                        "width": int(m.get("width") or 0), "height": int(m.get("height") or 0),
                        "refresh": round(float(m.get("refreshRate") or 0)), "focused": bool(m.get("focused"))})
        return out

    def clients(self) -> list[dict]:
        value = self._json("clients")
        return value if isinstance(value, list) else []

    def _dispatch(self, new: str, *classic: str) -> bool:
        if self._call("dispatch", new).strip() == "ok":
            return True
        return bool(classic) and all(self._call("dispatch", *c.split(" ", 1)).strip() == "ok" for c in classic)

    def focus_monitor(self, name: str) -> bool:
        return self._dispatch(f'hl.dsp.focus({{ monitor = "{name}" }})', f"focusmonitor {name}")

    def move_window(self, address: str, name: str) -> bool:
        return self._dispatch(
            f'hl.dsp.window.move({{ monitor = "{name}", follow = true, window = "address:{address}" }})',
            f"focuswindow address:{address}", f"movewindow mon:{name}")


def label(monitor: dict) -> str:
    """Short UI label: "AOC 32G2WG3 (DP-2, 144 Hz)"."""
    make_model = f"{monitor.get('make', '')} {monitor.get('model', '')}".strip() or monitor.get("description") or monitor.get("name", "")
    extra = ", ".join(x for x in (monitor.get("name"), f"{monitor.get('refresh')} Hz" if monitor.get("refresh") else "") if x)
    return f"{make_model} ({extra})" if extra else make_model


class GameWindowPlacer:
    """Per launch: focus the target monitor before launch, then move the game's windows there once they appear."""

    def __init__(self, backend: HyprlandMonitors, *, give_up_after: float = 180.0):
        self.backend = backend
        self.give_up_after = give_up_after
        self._pending: dict[str, dict] = {}   # session id -> {monitor, placed: set, since}

    def prepare(self, description: str) -> str | None:
        """Resolve and focus the monitor before launch; returns the connector name or None."""
        if not description or not self.backend.enabled:
            return None
        name = resolve(description, self.backend.monitors())
        if name:
            self.backend.focus_monitor(name)
        return name

    def track(self, session_id: str, monitor_name: str | None, now: float) -> None:
        if session_id and monitor_name:
            self._pending[session_id] = {"monitor": monitor_name, "placed": set(), "since": now}

    @property
    def pending(self) -> bool:
        return bool(self._pending)

    def poll(self, session_pids: Callable[[str], Iterable[int] | None], now: float) -> None:
        """session_pids(sid) -> the game's pids, or None once the session is over."""
        if not self._pending:
            return
        clients = None
        for sid, info in list(self._pending.items()):
            pids = session_pids(sid)
            if pids is None or now - info["since"] > self.give_up_after:
                del self._pending[sid]
                continue
            pids = set(pids)
            if not pids:
                continue
            if clients is None:
                clients = self.backend.clients()
                ids = {m.get("id"): m.get("name") for m in self.backend._json("monitors") if isinstance(m, dict)}
            for c in clients:
                address = str(c.get("address") or "")
                if c.get("pid") not in pids or not address or address in info["placed"]:
                    continue
                if (c.get("workspace") or {}).get("name", "").startswith("special:"):
                    continue   # a launcher window Waystone hid on purpose
                monitor = c.get("monitor")
                if (monitor if isinstance(monitor, str) else ids.get(monitor)) == info["monitor"]:
                    info["placed"].add(address)
                    continue
                if self.backend.move_window(address, info["monitor"]):
                    info["placed"].add(address)
