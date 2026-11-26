"""Small, preserving JSON preferences for the optional assistant UI animation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot


class AssistantAnimationSettings(QObject):
    changed = Signal()

    def __init__(self, path: str | Path | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.path = Path(path) if path is not None else Path.home() / ".local/share/waystone/settings.json"
        self._show_animation = True
        self._speed = "Normal"
        self._pace = 1.0          # Kian 2026-11-24: the pace is a slider in Settings (0.5x quick .. 3x very slow)
        self._load_error = ""
        self._load()

    def _load(self) -> None:
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("settings path is not a regular file")
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("settings document must be an object")
            ui = doc.get("ui", {})
            if not isinstance(ui, dict):
                raise ValueError("settings ui section must be an object")
            show = ui.get("assistant_animation", True)
            speed = ui.get("assistant_speed", "Normal")
            self._show_animation = show if isinstance(show, bool) else True
            self._speed = speed if speed in {"Quick", "Normal", "Cinematic"} else "Normal"
            pace = ui.get("assistant_pace")
            if isinstance(pace, (int, float)) and not isinstance(pace, bool):
                self._pace = _clamp_pace(pace)
            else:   # older settings only had the preset
                self._pace = {"Quick": 0.5, "Normal": 1.0, "Cinematic": 1.8}[self._speed]
        except FileNotFoundError:
            return
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._load_error = type(exc).__name__

    def _write(self, *, show_animation: bool | None = None, speed: str | None = None,
               pace: float | None = None) -> bool:
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            try:
                info = self.path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise OSError("unsafe settings file")
                doc = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(doc, dict):
                    raise ValueError("settings document must be an object")
            except FileNotFoundError:
                doc = {}
            ui = doc.setdefault("ui", {})
            if not isinstance(ui, dict):
                raise ValueError("settings ui section must be an object")
            if show_animation is not None:
                ui["assistant_animation"] = show_animation
            if speed is not None:
                ui["assistant_speed"] = speed
            if pace is not None:
                ui["assistant_pace"] = pace
            payload = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            fd, tmp_name = tempfile.mkstemp(prefix=".waystone-settings-", dir=self.path.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb", closefd=True) as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp_name, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
            self._load_error = ""
            return True
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._load_error = type(exc).__name__
            return False

    @Property(bool, notify=changed)
    def showAnimation(self) -> bool:
        return self._show_animation

    @Property(str, notify=changed)
    def speed(self) -> str:
        return self._speed

    @Property(float, notify=changed)
    def pace(self) -> float:
        """Timing multiplier for the animation: 0.5 quick, 1 normal, 3 very slow."""
        return self._pace

    @Slot(float, result=bool)
    def setPace(self, pace: float) -> bool:
        pace = _clamp_pace(pace)
        if pace == self._pace:
            return True
        if not self._write(pace=pace):
            self.changed.emit()
            return False
        self._pace = pace
        self.changed.emit()
        return True

    @Property(str, notify=changed)
    def lastError(self) -> str:
        return self._load_error

    @Slot(bool, result=bool)
    def setShowAnimation(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool) or enabled == self._show_animation:
            return enabled == self._show_animation
        if not self._write(show_animation=enabled):
            self.changed.emit()
            return False
        self._show_animation = enabled
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def setSpeed(self, speed: str) -> bool:
        if speed not in {"Quick", "Normal", "Cinematic"}:
            return False
        if speed == self._speed:
            return True
        if not self._write(speed=speed):
            self.changed.emit()
            return False
        self._speed = speed
        self.changed.emit()
        return True


def _clamp_pace(value) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 1.0
    return round(min(3.0, max(0.5, value)), 1)
