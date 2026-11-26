"""Overlay Tools bridge for Waystone overlay v2 slice 3.

Provides compact overlay tool hub data and operations:
1. Downloads: active installs/downloads, progress, cancel, pause/resume where supported.
2. Controller profile: runtime profile info, bindings to ControllerProfilePicker.
3. Volume: master, game, and chat sliders via pactl with stream discovery; hidden when unavailable.
4. Screenshot: one-click capture to ~/Pictures/Waystone, last shot thumbnail path and time.
5. Saves quick backup: backs up the currently running game's save via SaveManagementView / Ludusavi.
6. Performance: FPS / frametime telemetry from MangoHud / gamescope; hidden when unavailable.

Strictly adheres to test isolation: accepts injected runners and mocks to avoid real system mutations.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Dict, List, Optional, Set

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QStandardPaths
from PySide6.QtGui import QGuiApplication


TERMINAL_DOWNLOAD_PHASES = {'completed', 'failed', 'cancelled', 'handed_off', 'invalid', 'unsupported'}


def _process_tree(root: int, proc: Path = Path("/proc")) -> Set[int]:
    """PIDs of `root` and all its descendants (read from /proc; empty if it is gone)."""
    children: Dict[int, List[int]] = {}
    try:
        entries = [p for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        return set()
    for entry in entries:
        try:
            stat = (entry / "stat").read_text()
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(entry.name))
    if not (proc / str(root)).exists():
        return set()
    seen, stack = set(), [root]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, []))
    return seen


def _grim_capture(target: Path, run=None) -> bool:
    """Wayland screenshot of the focused output with grim (Hyprland); False when unavailable."""
    import subprocess
    if not os.environ.get("WAYLAND_DISPLAY") or not shutil.which("grim"):
        return False
    run = run or subprocess.run
    args = ["grim"]
    if shutil.which("hyprctl"):
        try:
            out = run(["hyprctl", "-j", "monitors"], capture_output=True, text=True, timeout=2)
            focused = next((m.get("name") for m in json.loads(out.stdout or "[]") if m.get("focused")), None)
            if focused:
                args += ["-o", focused]
        except Exception:
            pass
    try:
        return run(args + [str(target)], capture_output=True, timeout=10).returncode == 0 and target.is_file()
    except Exception:
        return False


class OverlayToolsBridge(QObject):
    """Presentation bridge exposing compact overlay tools to QML."""

    changed = Signal()
    downloadsChanged = Signal()
    volumeChanged = Signal()
    screenshotTaken = Signal(str)
    saveStatusChanged = Signal()
    performanceChanged = Signal()

    def __init__(
        self,
        game_installs: Optional[Any] = None,
        controller_profiles: Optional[Any] = None,
        audio_routes: Optional[Any] = None,
        save_management: Optional[Any] = None,
        launch_view: Optional[Any] = None,
        *,
        audio_runner: Optional[Callable[[List[str]], Any]] = None,
        capturer: Optional[Callable[[Path], bool]] = None,
        screenshots_dir: Optional[Path] = None,
        save_backup_runner: Optional[Callable[[str, str], bool]] = None,
        stats_reader: Optional[Callable[[], Optional[Dict[str, float]]]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._game_installs = game_installs
        self._controller_profiles = controller_profiles
        self._audio_routes = audio_routes
        self._save_management = save_management
        self._launch_view = launch_view

        # Injected test / backend overrides
        self._audio_runner = audio_runner
        self._capturer = capturer
        self._screenshots_dir = screenshots_dir or (
            Path(QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)) / "Waystone"
            if QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
            else Path.home() / "Pictures" / "Waystone"
        )
        self._save_backup_runner = save_backup_runner
        self._stats_reader = stats_reader

        # Downloads state
        if self._game_installs is not None and hasattr(self._game_installs, 'changed'):
            self._game_installs.changed.connect(self._on_downloads_changed)

        # Volume state
        self._master_volume: int = 100
        self._master_muted: bool = False
        self._master_available: bool = False
        self._game_volume: int = 100
        self._game_muted: bool = False
        self._game_available: bool = False
        self._game_stream_idx: Optional[int] = None
        self._chat_volume: int = 100
        self._chat_muted: bool = False
        self._chat_available: bool = False
        self._chat_stream_idx: Optional[int] = None

        # Screenshot state
        self._last_screenshot: str = ""
        self._last_screenshot_time: str = ""
        self._find_existing_screenshot()

        # Saves backup state
        self._save_backup_status: str = "Ready"
        self._save_backup_running: bool = False
        self._last_backup_time: str = ""
        if self._save_management is not None:
            if hasattr(self._save_management, 'jobStateChanged'):
                self._save_management.jobStateChanged.connect(self._on_save_job_state_changed)
            if hasattr(self._save_management, 'operationCompleted'):
                self._save_management.operationCompleted.connect(self._on_save_operation_completed)

        # Performance state
        self._performance_available: bool = False
        self._fps: float = 0.0
        self._frametime_ms: float = 0.0
        self._performance_source: str = ""

        # Initialize telemetry and volumes
        self.refreshVolume()
        self.refreshPerformance()

    # -------------------------------------------------------------------------
    # 1. Downloads
    # -------------------------------------------------------------------------

    def _on_downloads_changed(self) -> None:
        self.downloadsChanged.emit()
        self.changed.emit()

    @Property('QVariant', notify=downloadsChanged)
    def downloadsTracker(self) -> Any:
        return self._game_installs

    @downloadsTracker.setter
    def downloadsTracker(self, tracker: Any) -> None:
        if self._game_installs != tracker:
            if self._game_installs is not None and hasattr(self._game_installs, 'changed'):
                try: self._game_installs.changed.disconnect(self._on_downloads_changed)
                except (RuntimeError, TypeError): pass
            self._game_installs = tracker
            if self._game_installs is not None and hasattr(self._game_installs, 'changed'):
                self._game_installs.changed.connect(self._on_downloads_changed)
            self.downloadsChanged.emit()

    @Property('QVariantList', notify=downloadsChanged)
    def activeDownloads(self) -> List[Dict[str, Any]]:
        if self._game_installs is None:
            return []
        raw_jobs = getattr(self._game_installs, 'jobs', [])
        return [
            dict(r) for r in raw_jobs
            if r.get('phase') not in TERMINAL_DOWNLOAD_PHASES
        ]

    @Property(int, notify=downloadsChanged)
    def activeDownloadCount(self) -> int:
        return len(self.activeDownloads)

    @Property(bool, notify=downloadsChanged)
    def hasActiveDownloads(self) -> bool:
        return self.activeDownloadCount > 0

    @Slot(str, result=bool)
    def cancelDownload(self, job_id: str) -> bool:
        if self._game_installs is not None and hasattr(self._game_installs, 'cancel'):
            self._game_installs.cancel(job_id)
            return True
        return False

    @Slot(str, result=bool)
    def pauseDownload(self, job_id: str) -> bool:
        if self._game_installs is not None and hasattr(self._game_installs, 'pause'):
            return bool(self._game_installs.pause(job_id))
        return False

    @Slot(str, result=bool)
    def resumeDownload(self, job_id: str) -> bool:
        if self._game_installs is not None and hasattr(self._game_installs, 'resume'):
            return bool(self._game_installs.resume(job_id))
        return False

    # -------------------------------------------------------------------------
    # 2. Controller profile
    # -------------------------------------------------------------------------

    @Property(str, notify=changed)
    def runtimeGameId(self) -> str:
        if self._controller_profiles is not None:
            gid = getattr(self._controller_profiles, 'runtimeGameId', '')
            if gid:
                return str(gid)
        if self._launch_view is not None and getattr(self._launch_view, 'gameState', '') == 'running':
            return str(getattr(self._launch_view, 'selectedId', ''))
        return ""

    @Property(str, notify=changed)
    def currentRunningTitle(self) -> str:
        if self._launch_view is not None and getattr(self._launch_view, 'gameState', '') == 'running':
            return str(getattr(self._launch_view, 'selectedTitle', ''))
        return ""

    # -------------------------------------------------------------------------
    # 3. Volume
    # -------------------------------------------------------------------------

    @Property(int, notify=volumeChanged)
    def masterVolume(self) -> int:
        return self._master_volume

    @Property(bool, notify=volumeChanged)
    def masterMuted(self) -> bool:
        return self._master_muted

    @Property(bool, notify=volumeChanged)
    def masterAvailable(self) -> bool:
        return self._master_available

    @Property(int, notify=volumeChanged)
    def gameVolume(self) -> int:
        return self._game_volume

    @Property(bool, notify=volumeChanged)
    def gameMuted(self) -> bool:
        return self._game_muted

    @Property(bool, notify=volumeChanged)
    def gameAvailable(self) -> bool:
        return self._game_available

    @Property(int, notify=volumeChanged)
    def chatVolume(self) -> int:
        return self._chat_volume

    @Property(bool, notify=volumeChanged)
    def chatMuted(self) -> bool:
        return self._chat_muted

    @Property(bool, notify=volumeChanged)
    def chatAvailable(self) -> bool:
        return self._chat_available

    def _exec_pactl(self, args: List[str]) -> Tuple[int, str]:
        if self._audio_runner is not None:
            try:
                res = self._audio_runner(args)
                return getattr(res, 'returncode', -1), getattr(res, 'stdout', '') or ""
            except Exception:
                return -1, ""
        # If in headless/offscreen test mode and no runner is provided, do NOT run real pactl!
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return -1, ""
        if not shutil.which("pactl"):
            return -1, ""
        try:
            import subprocess
            res = subprocess.run(args, capture_output=True, text=True, timeout=2.0, check=False)
            return res.returncode, res.stdout or ""
        except Exception:
            return -1, ""

    @Slot()
    def refreshVolume(self) -> None:
        # 1. Query sinks for default sink volume
        code, out = self._exec_pactl(["pactl", "-f", "json", "list", "sinks"])
        if code == 0 and out.strip():
            try:
                sinks = json.loads(out)
                if isinstance(sinks, list) and sinks:
                    # Find default sink or first sink
                    code_def, out_def = self._exec_pactl(["pactl", "get-default-sink"])
                    def_name = out_def.strip() if code_def == 0 else ""
                    target_sink = next((s for s in sinks if s.get("name") == def_name), sinks[0])
                    vol_obj = target_sink.get("volume", {})
                    # pactl JSON volume has channel keys with "value_percent"
                    percent = 100
                    for ch, data in vol_obj.items():
                        if isinstance(data, dict) and "value_percent" in data:
                            pct_str = data["value_percent"].replace("%", "").strip()
                            if pct_str.isdigit():
                                percent = int(pct_str)
                                break
                    self._master_volume = max(0, min(100, percent))
                    self._master_muted = bool(target_sink.get("mute", False))
                    self._master_available = True
            except Exception:
                self._master_available = False
        else:
            self._master_available = False

        # 2. Query sink inputs for game and chat streams
        code_si, out_si = self._exec_pactl(["pactl", "-f", "json", "list", "sink-inputs"])
        self._game_available = False
        self._game_stream_idx = None
        self._chat_available = False
        self._chat_stream_idx = None

        if code_si == 0 and out_si.strip():
            try:
                inputs = json.loads(out_si)
                if isinstance(inputs, list):
                    # Find voice PIDs if any
                    voice_pids: Set[int] = set()
                    try:
                        from voice_audio_routes_view import owned_voice_pids
                        voice_pids = owned_voice_pids()
                    except Exception:
                        pass

                    # Running game: the launched process and all its descendants (Proton games play audio
                    # from wine child processes, never from the launcher PID itself).
                    game_pids: Set[int] = set()
                    if self._launch_view is not None:
                        root_pid = int(getattr(self._launch_view, 'lastPid', 0) or 0)
                        if root_pid > 0:
                            game_pids = _process_tree(root_pid)

                    for item in inputs:
                        idx = item.get("index")
                        props = item.get("properties", {})
                        pid_raw = props.get("application.process.id")
                        pid = int(pid_raw) if (isinstance(pid_raw, (int, str)) and str(pid_raw).isdigit()) else None

                        # Check chat
                        if pid and pid in voice_pids and not self._chat_available:
                            self._chat_available = True
                            self._chat_stream_idx = idx
                            vol_obj = item.get("volume", {})
                            for ch, data in vol_obj.items():
                                if isinstance(data, dict) and "value_percent" in data:
                                    pct = data["value_percent"].replace("%", "").strip()
                                    if pct.isdigit():
                                        self._chat_volume = max(0, min(100, int(pct)))
                                        break
                            self._chat_muted = bool(item.get("mute", False))

                        # Check game
                        if pid and pid in game_pids and not self._game_available:
                            self._game_available = True
                            self._game_stream_idx = idx
                            vol_obj = item.get("volume", {})
                            for ch, data in vol_obj.items():
                                if isinstance(data, dict) and "value_percent" in data:
                                    pct = data["value_percent"].replace("%", "").strip()
                                    if pct.isdigit():
                                        self._game_volume = max(0, min(100, int(pct)))
                                        break
                            self._game_muted = bool(item.get("mute", False))
            except Exception:
                pass

        self.volumeChanged.emit()

    @Slot(int)
    def setMasterVolume(self, vol: int) -> None:
        self._master_volume = max(0, min(100, int(vol)))
        self._exec_pactl(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{self._master_volume}%"])
        self.volumeChanged.emit()

    @Slot(bool)
    def setMasterMuted(self, muted: bool) -> None:
        self._master_muted = bool(muted)
        self._exec_pactl(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if self._master_muted else "0"])
        self.volumeChanged.emit()

    @Slot(int)
    def setGameVolume(self, vol: int) -> None:
        self._game_volume = max(0, min(100, int(vol)))
        if self._game_stream_idx is not None:
            self._exec_pactl(["pactl", "set-sink-input-volume", str(self._game_stream_idx), f"{self._game_volume}%"])
        self.volumeChanged.emit()

    @Slot(bool)
    def setGameMuted(self, muted: bool) -> None:
        self._game_muted = bool(muted)
        if self._game_stream_idx is not None:
            self._exec_pactl(["pactl", "set-sink-input-mute", str(self._game_stream_idx), "1" if self._game_muted else "0"])
        self.volumeChanged.emit()

    @Slot(int)
    def setChatVolume(self, vol: int) -> None:
        self._chat_volume = max(0, min(100, int(vol)))
        if self._chat_stream_idx is not None:
            self._exec_pactl(["pactl", "set-sink-input-volume", str(self._chat_stream_idx), f"{self._chat_volume}%"])
        self.volumeChanged.emit()

    @Slot(bool)
    def setChatMuted(self, muted: bool) -> None:
        self._chat_muted = bool(muted)
        if self._chat_stream_idx is not None:
            self._exec_pactl(["pactl", "set-sink-input-mute", str(self._chat_stream_idx), "1" if self._chat_muted else "0"])
        self.volumeChanged.emit()

    # -------------------------------------------------------------------------
    # 4. Screenshot
    # -------------------------------------------------------------------------

    def _find_existing_screenshot(self) -> None:
        if self._screenshots_dir.is_dir():
            files = sorted(
                self._screenshots_dir.glob("*.png"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            if files:
                self._last_screenshot = str(files[0])
                mtime = datetime.fromtimestamp(files[0].stat().st_mtime)
                self._last_screenshot_time = mtime.strftime("%b %d, %H:%M")

    @Property(str, notify=changed)
    def lastScreenshot(self) -> str:
        return self._last_screenshot

    @Property(str, notify=changed)
    def lastScreenshotUrl(self) -> str:
        if self._last_screenshot:
            return "file://" + self._last_screenshot
        return ""

    @Property(str, notify=changed)
    def lastScreenshotTime(self) -> str:
        return self._last_screenshot_time

    @Property(str, constant=True)
    def screenshotsFolder(self) -> str:
        return str(self._screenshots_dir)

    @Property(bool, notify=changed)
    def hasScreenshot(self) -> bool:
        return bool(self._last_screenshot)

    @Slot(result=str)
    @Slot('QVariant', result=str)
    def takeScreenshot(self, surface_window: Optional[Any] = None) -> str:
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        filename = f"waystone_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        target_path = self._screenshots_dir / filename

        success = False
        if self._capturer is not None:
            success = bool(self._capturer(target_path))
        else:
            # Capture the game, not the overlay: hide the overlay surface for a moment, then grab the screen
            # (grim on Wayland/Hyprland; Qt's screen grab only as a fallback, it is black for Wayland windows).
            was_visible = bool(surface_window is not None and hasattr(surface_window, 'isVisible')
                               and surface_window.isVisible())
            if was_visible:
                surface_window.hide()
                QGuiApplication.processEvents()
                import time
                time.sleep(0.15)
            try:
                success = _grim_capture(target_path)
                if not success:
                    screen = QGuiApplication.primaryScreen()
                    if screen is not None:
                        pix = screen.grabWindow(0)
                        if not pix.isNull():
                            success = pix.save(str(target_path))
            finally:
                if was_visible:
                    surface_window.show()

        if success or target_path.is_file():
            self._last_screenshot = str(target_path)
            self._last_screenshot_time = datetime.now().strftime("%b %d, %H:%M")
            self.screenshotTaken.emit(self._last_screenshot)
            self.changed.emit()
            return self._last_screenshot
        return ""

    # -------------------------------------------------------------------------
    # 5. Saves quick backup
    # -------------------------------------------------------------------------

    def _on_save_job_state_changed(self) -> None:
        if self._save_management is not None:
            self._save_backup_running = bool(getattr(self._save_management, 'isJobRunning', False))
            self.saveStatusChanged.emit()

    def _on_save_operation_completed(self, job_type: str, success: bool, message: str) -> None:
        self._save_backup_running = False
        if success:
            self._last_backup_time = datetime.now().strftime("%H:%M")
            self._save_backup_status = f"Saved successfully ({self._last_backup_time})"
        else:
            self._save_backup_status = message or "Save backup failed."
        self.saveStatusChanged.emit()

    @Property(bool, notify=saveStatusChanged)
    def saveBackupAvailable(self) -> bool:
        if self._save_backup_runner is not None:
            return True
        if self._save_management is not None:
            return bool(getattr(self._save_management, 'toolAvailable', False))
        return False

    @Property(bool, notify=saveStatusChanged)
    def saveBackupRunning(self) -> bool:
        return self._save_backup_running

    @Property(str, notify=saveStatusChanged)
    def saveBackupStatus(self) -> str:
        return self._save_backup_status

    @Property(str, notify=saveStatusChanged)
    def lastBackupTime(self) -> str:
        return self._last_backup_time

    @Slot(result=bool)
    @Slot(str, str, result=bool)
    def backupCurrentGame(self, game_id: str = "", title: str = "") -> bool:
        target_id = game_id or self.runtimeGameId
        target_title = title or self.currentRunningTitle
        if not target_id:
            self._save_backup_status = "No running game detected to back up."
            self.saveStatusChanged.emit()
            return False

        if self._save_backup_runner is not None:
            self._save_backup_running = True
            self._save_backup_status = f"Backing up saves for {target_title or target_id}…"
            self.saveStatusChanged.emit()
            ok = self._save_backup_runner(target_id, target_title)
            self._save_backup_running = False
            if ok:
                self._last_backup_time = datetime.now().strftime("%H:%M")
                self._save_backup_status = f"Saved successfully ({self._last_backup_time})"
            else:
                self._save_backup_status = "Backup failed."
            self.saveStatusChanged.emit()
            return ok

        if self._save_management is not None:
            if not self.saveBackupAvailable:
                self._save_backup_status = getattr(self._save_management, 'toolStatus', 'Ludusavi unavailable')
                self.saveStatusChanged.emit()
                return False
            self._save_backup_running = True
            self._save_backup_status = f"Backing up {target_title or target_id}…"
            self.saveStatusChanged.emit()
            ok = self._save_management.backupGameAsync(target_id, target_title)
            if not ok:
                self._save_backup_running = False
                self._save_backup_status = getattr(self._save_management, 'error', 'Backup failed to dispatch')
                self.saveStatusChanged.emit()
            return ok

        self._save_backup_status = "Save management service not connected."
        self.saveStatusChanged.emit()
        return False

    # -------------------------------------------------------------------------
    # 6. Performance telemetry
    # -------------------------------------------------------------------------

    @Property(bool, notify=performanceChanged)
    def performanceAvailable(self) -> bool:
        return self._performance_available

    @Property(float, notify=performanceChanged)
    def fps(self) -> float:
        return self._fps

    @Property(float, notify=performanceChanged)
    def frametimeMs(self) -> float:
        return self._frametime_ms

    @Property(str, notify=performanceChanged)
    def performanceSource(self) -> str:
        return self._performance_source

    @Slot()
    def refreshPerformance(self) -> None:
        if self._stats_reader is not None:
            stats = self._stats_reader()
            if stats is not None and isinstance(stats, dict) and 'fps' in stats:
                self._performance_available = True
                self._fps = round(float(stats.get('fps', 0.0)), 1)
                self._frametime_ms = round(float(stats.get('frametime_ms', 1000.0 / max(1.0, self._fps))), 1)
                self._performance_source = str(stats.get('source', 'reader'))
            else:
                self._performance_available = False
            self.performanceChanged.emit()
            return

        # Automatic detection of MangoHud / gamescope stats files
        self._performance_available = False
        candidates = []
        uid = os.getuid()
        candidates.append(Path(f"/run/user/{uid}/gamescope-stats"))
        candidates.append(Path("/tmp/gamescope-stats"))
        candidates.append(Path(f"/run/user/{uid}/mangohud.json"))
        candidates.append(Path("/tmp/mangohud.json"))

        for path in candidates:
            if path.is_file():
                try:
                    data = json.loads(path.read_text())
                    if isinstance(data, dict) and ('fps' in data or 'FPS' in data):
                        fps_val = float(data.get('fps', data.get('FPS', 0.0)))
                        self._fps = round(fps_val, 1)
                        ft_val = float(data.get('frametime_ms', data.get('frametime', 1000.0 / max(1.0, self._fps))))
                        self._frametime_ms = round(ft_val, 1)
                        self._performance_available = True
                        self._performance_source = path.name
                        break
                except Exception:
                    continue

        self.performanceChanged.emit()
