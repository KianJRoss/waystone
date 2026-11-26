"""Waystone UI sounds: switchable sound packs, played from QML as `uiSounds.play("move")`.

A pack is a folder with `pack.json` ({name, description, version, sounds: {event: file.wav}}) and its WAV files.
Packs come from the app's own `assets/ui-sounds/` and the user's `~/.local/share/waystone/sound-packs/` (a user
pack with the same id replaces the built-in one). Settings live in Waystone's settings.json under `ui`:
`sounds` (on/off), `sound_pack` (id) and `sound_volume` (0-100).

While a game is running only overlay sounds play (`play(event, "overlay")`), so menus never beep over a game.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Callable

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

EVENTS = ("move", "select", "back", "open", "launch", "error", "notify")
DEFAULT_PACK = "console"
MOVE_INTERVAL = 0.035   # fast scrolling: at most ~28 move ticks per second
APP_PACKS = Path(__file__).resolve().parents[1] / "assets" / "ui-sounds"


def user_pack_root() -> Path:
    return Path.home() / ".local/share/waystone/sound-packs"


def discover_packs(roots: list[Path]) -> dict[str, dict[str, Any]]:
    """{pack id: {id, name, description, files: {event: absolute path}}}; later roots override earlier ones."""
    packs: dict[str, dict[str, Any]] = {}
    for root in roots:
        try:
            folders = sorted(p for p in Path(root).iterdir() if p.is_dir())
        except OSError:
            continue
        for folder in folders:
            try:
                meta = json.loads((folder / "pack.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            sounds = meta.get("sounds") if isinstance(meta, dict) else None
            if not isinstance(sounds, dict):
                continue
            files = {}
            for event in EVENTS:
                name = sounds.get(event)
                path = (folder / name).resolve() if isinstance(name, str) else None
                # files must stay inside the pack folder
                if path and path.is_file() and path.is_relative_to(folder.resolve()):
                    files[event] = path
            if files:
                packs[folder.name] = {"id": folder.name, "name": str(meta.get("name") or folder.name),
                                      "description": str(meta.get("description") or ""), "files": files}
    return packs


class _Settings:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ui = data.get("ui") if isinstance(data, dict) else None
            return ui if isinstance(ui, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, **values) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        ui = data.get("ui") if isinstance(data.get("ui"), dict) else {}
        ui.update(values)
        data["ui"] = ui
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def audio_allowed(environ=None) -> bool:
    """Offscreen runs (tests, screenshots, workers' suites) must never make noise on the machine running them."""
    env = os.environ if environ is None else environ
    if env.get("WAYSTONE_UI_SOUNDS", "").strip() in ("0", "off", "false"):
        return False
    return env.get("QT_QPA_PLATFORM", "").split(":")[0] not in ("offscreen", "minimal", "vnc")


class _SilentEffect:
    def __init__(self, *_):
        pass

    def play(self):
        pass

    def isPlaying(self):
        return False

    def setVolume(self, _volume):
        pass


def _qt_effect_factory(parent: QObject):
    if not audio_allowed():
        return lambda path, volume: _SilentEffect()
    try:
        from PySide6.QtMultimedia import QSoundEffect
    except ImportError:  # Qt Multimedia not installed: Waystone still runs, just without UI sounds
        return lambda path, volume: _SilentEffect()

    def make(path: Path, volume: float):
        effect = QSoundEffect(parent)
        effect.setSource(QUrl.fromLocalFile(str(path)))
        effect.setVolume(volume)
        return effect
    return make


class UiSounds(QObject):
    changed = Signal()

    def __init__(self, *, pack_roots: list[Path] | None = None, settings_path: str | Path | None = None,
                 effect_factory: Callable | None = None, quiet: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic, parent: QObject | None = None):
        super().__init__(parent)
        self._roots = pack_roots if pack_roots is not None else [APP_PACKS, user_pack_root()]
        self._settings = _Settings(Path(settings_path) if settings_path else Path.home() / ".local/share/waystone/settings.json")
        self._factory = effect_factory or _qt_effect_factory(self)
        self._quiet = quiet or (lambda: False)
        self._clock = clock
        self._packs = discover_packs(self._roots)
        ui = self._settings.load()
        self._enabled = bool(ui.get("sounds", True))
        self._volume = max(0, min(100, int(ui.get("sound_volume", 70)))) if isinstance(ui.get("sound_volume", 70), (int, float)) else 70
        pack = ui.get("sound_pack")
        self._pack = pack if isinstance(pack, str) and pack in self._packs else (DEFAULT_PACK if DEFAULT_PACK in self._packs else next(iter(self._packs), ""))
        self._effects: dict[tuple[str, str], list] = {}   # (pack, event) -> small pool so quick repeats overlap
        self._last_move = 0.0

    # ---- properties for Settings ----
    @Property("QVariantList", notify=changed)
    def packs(self) -> list:
        return [{"id": p["id"], "name": p["name"], "description": p["description"]} for p in self._packs.values()]

    @Property(str, notify=changed)
    def pack(self) -> str:
        return self._pack

    @Property(bool, notify=changed)
    def enabled(self) -> bool:
        return self._enabled

    @Property(int, notify=changed)
    def volume(self) -> int:
        return self._volume

    @Slot(str)
    def setPack(self, pack_id: str) -> None:
        if pack_id in self._packs and pack_id != self._pack:
            self._pack = pack_id
            self._settings.save(sound_pack=pack_id)
            self.changed.emit()

    @Slot(bool)
    def setEnabled(self, enabled: bool) -> None:
        if bool(enabled) != self._enabled:
            self._enabled = bool(enabled)
            self._settings.save(sounds=self._enabled)
            self.changed.emit()

    @Slot(int)
    def setVolume(self, volume: int) -> None:
        volume = max(0, min(100, int(volume)))
        if volume != self._volume:
            self._volume = volume
            for pool in self._effects.values():
                for effect in pool:
                    effect.setVolume(volume / 100)
            self._settings.save(sound_volume=volume)
            self.changed.emit()

    @Slot()
    def reloadPacks(self) -> None:
        self._packs = discover_packs(self._roots)
        self._effects.clear()
        if self._pack not in self._packs:
            self._pack = DEFAULT_PACK if DEFAULT_PACK in self._packs else next(iter(self._packs), "")
        self.changed.emit()

    # ---- playing ----
    @Slot(str)
    def play(self, event: str) -> None:
        self._play(self._pack, event, "")

    @Slot(str, str)
    def playIn(self, event: str, context: str) -> None:
        """context "overlay": still plays while a game runs (the overlay is on top of the game)."""
        self._play(self._pack, event, context)

    @Slot(str, str)
    def preview(self, pack_id: str, event: str) -> None:
        """Settings preview: plays even when sounds are switched off, never while a game runs."""
        if not self._quiet():
            self._emit(pack_id, event)

    def _play(self, pack_id: str, event: str, context: str) -> None:
        if not self._enabled or self._volume <= 0 or event not in EVENTS:
            return
        if context != "overlay" and self._quiet():
            return
        if event == "move":
            now = self._clock()
            if now - self._last_move < MOVE_INTERVAL:
                return
            self._last_move = now
        self._emit(pack_id, event)

    def _emit(self, pack_id: str, event: str) -> None:
        pack = self._packs.get(pack_id)
        path = pack["files"].get(event) if pack else None
        if path is None:
            return
        pool = self._effects.setdefault((pack_id, event), [])
        effect = next((e for e in pool if not e.isPlaying()), None)
        if effect is None and len(pool) < 3:
            try:
                effect = self._factory(path, self._volume / 100)
            except Exception:
                return
            pool.append(effect)
        if effect is None:
            effect = pool[0]
        effect.play()
