"""Qt view/controller for W20a Discord phase 1: Rich Presence, client hand-off, and Vesktop controller PTT."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, Property, Signal, Slot

from native_services.discord_integration import (
    DEFAULT_DISCORD_APP_ID,
    PTT_BEHAVIORS,
    DiscordClientInfo,
    DiscordIpcClient,
    choose_discord_client,
    detect_discord_clients,
    discover_discord_ipc_sockets,
    is_controller_source_pressed,
    open_or_focus_discord_client,
    send_vesktop_ptt,
)


class DiscordView(QObject):
    changed = Signal()

    def __init__(
        self,
        *,
        settings_path: Path | str | None = None,
        launch_view: Any = None,
        controller_view: Any = None,
        controller_live: Any = None,
        runtime_dir: Path | str | None = None,
        environ: dict[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        flatpak_installed: Callable[[str], bool] | None = None,
        spawn_fn: Callable[[list[str]], Any] | None = None,
        home_dir: Path | None = None,
        system_flatpak_root: Path | None = None,
        offline: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings_path = (
            Path(settings_path)
            if settings_path is not None
            else (Path.home() / ".local/share/waystone/settings.json")
        )
        self._launch_view = launch_view
        self._controller_view = controller_view
        self._controller_live = controller_live
        self._runtime_dir = Path(runtime_dir) if runtime_dir is not None else None
        self._environ = environ if environ is not None else ({} if offline and runtime_dir is None else os.environ)
        self._which = which
        self._flatpak_installed = flatpak_installed
        self._spawn_fn = spawn_fn
        self._home_dir = Path(home_dir) if home_dir is not None else None
        self._system_flatpak_root = Path(system_flatpak_root) if system_flatpak_root is not None else None
        self._offline = bool(offline)

        env_app_id = (self._environ.get("WAYSTONE_DISCORD_APP_ID", "") if isinstance(self._environ, dict) else "").strip()
        self._app_id = env_app_id or DEFAULT_DISCORD_APP_ID
        self._rich_presence_enabled = True
        self._preferred_client_id = "auto"
        self._ptt_source = ""
        self._ptt_behavior = "hold"
        self._last_notice = ""

        self._active_published_key: tuple[str, int] | None = None
        self._ptt_prev_pressed = False
        self._ptt_talking = False

        self._load_settings()

        self._ipc_client = DiscordIpcClient(
            app_id=self._app_id,
            runtime_dir=self._runtime_dir,
            environ=self._environ,
        )
        self._clients: list[DiscordClientInfo] = self._detect_clients()

        if self._launch_view is not None and hasattr(self._launch_view, "changed"):
            self._launch_view.changed.connect(self.syncRichPresence)

        if self._controller_view is not None and hasattr(self._controller_view, "changed"):
            self._controller_view.changed.connect(self._sync_ptt_from_controller_profile)

        if self._controller_live is not None and hasattr(self._controller_live, "changed"):
            self._controller_live.changed.connect(self._on_controller_frame)
            self._update_live_lease()

    def _detect_clients(self) -> list[DiscordClientInfo]:
        return detect_discord_clients(
            which=self._which,
            flatpak_installed=self._flatpak_installed,
            home_dir=self._home_dir,
            system_flatpak_root=self._system_flatpak_root,
        )

    def _selected_client(self) -> DiscordClientInfo | None:
        return choose_discord_client(self._clients, self._preferred_client_id)

    def _load_settings(self) -> None:
        try:
            info = self._settings_path.lstat()
            if not stat.S_ISREG(info.st_mode):
                return
            doc = json.loads(self._settings_path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                return
            section = doc.get("discord", {})
            if not isinstance(section, dict):
                return
            rp = section.get("rich_presence_enabled")
            if isinstance(rp, bool):
                self._rich_presence_enabled = rp
            pref = section.get("preferred_client")
            if isinstance(pref, str) and pref.strip():
                self._preferred_client_id = pref.strip()
            aid = section.get("app_id")
            if isinstance(aid, str) and aid.strip():
                self._app_id = aid.strip()
            ptt = section.get("push_to_talk")
            if isinstance(ptt, dict):
                src = ptt.get("source")
                beh = ptt.get("behavior")
                if isinstance(src, str):
                    self._ptt_source = src.strip()
                if isinstance(beh, str) and beh in PTT_BEHAVIORS:
                    self._ptt_behavior = beh
        except FileNotFoundError:
            return
        except Exception:
            return

    def _save_settings(self) -> bool:
        try:
            self._settings_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                doc = json.loads(self._settings_path.read_text(encoding="utf-8"))
                if not isinstance(doc, dict):
                    doc = {}
            except FileNotFoundError:
                doc = {}
            section = doc.setdefault("discord", {})
            if not isinstance(section, dict):
                section = {}
                doc["discord"] = section
            section["rich_presence_enabled"] = self._rich_presence_enabled
            section["preferred_client"] = self._preferred_client_id
            section["app_id"] = self._app_id
            section["push_to_talk"] = {
                "source": self._ptt_source,
                "behavior": self._ptt_behavior,
            }
            payload = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            fd, tmp_name = tempfile.mkstemp(prefix=".waystone-settings-", dir=self._settings_path.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb", closefd=True) as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp_name, self._settings_path)
            finally:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
            return True
        except Exception:
            return False

    def _sync_ptt_from_controller_profile(self) -> None:
        if self._controller_view is None:
            return
        try:
            raw = getattr(self._controller_view, "profileJson", "")
            if not raw:
                return
            data = json.loads(raw)
            if not isinstance(data, dict):
                return
            ptt = data.get("push_to_talk")
            if isinstance(ptt, dict):
                src = str(ptt.get("source") or "").strip()
                beh = str(ptt.get("behavior") or "hold").strip()
                if beh not in PTT_BEHAVIORS:
                    beh = "hold"
                if (src, beh) != (self._ptt_source, self._ptt_behavior):
                    self._ptt_source = src
                    self._ptt_behavior = beh
                    self._save_settings()
                    self._update_live_lease()
                    self.changed.emit()
        except Exception:
            return

    def _update_live_lease(self) -> None:
        if self._controller_live is None:
            return
        try:
            if self._ptt_source:
                self._controller_live.acquire("ptt")
            else:
                self._controller_live.release("ptt")
        except Exception:
            pass

    def _on_controller_frame(self) -> None:
        if not self._ptt_source or self._controller_live is None:
            self._ptt_prev_pressed = False
            return
        frame = getattr(self._controller_live, "frame", None)
        if not isinstance(frame, dict):
            self._ptt_prev_pressed = False
            return
        pressed = is_controller_source_pressed(frame, self._ptt_source)
        if pressed == self._ptt_prev_pressed:
            return
        self._ptt_prev_pressed = pressed
        if self._ptt_behavior == "toggle":
            if pressed:
                self._ptt_talking = not self._ptt_talking
                self.triggerPtt(self._ptt_talking)
        else:
            self._ptt_talking = pressed
            self.triggerPtt(pressed)

    @Property(bool, notify=changed)
    def richPresenceEnabled(self) -> bool:
        return self._rich_presence_enabled

    @Property(str, notify=changed)
    def appId(self) -> str:
        return self._app_id

    @Property(str, notify=changed)
    def preferredClientId(self) -> str:
        return self._preferred_client_id

    @Property("QVariantList", notify=changed)
    def installedClients(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self._clients]

    @Property("QVariantList", notify=changed)
    def clientChoices(self) -> list[dict[str, Any]]:
        choices = [{"id": "auto", "label": "Auto (prefer Vesktop)"}]
        for c in self._clients:
            choices.append({"id": c.client_id, "label": c.to_dict()["label"]})
        return choices

    @Property(bool, notify=changed)
    def hasClient(self) -> bool:
        return len(self._clients) > 0

    @Property(str, notify=changed)
    def activeClientName(self) -> str:
        chosen = self._selected_client()
        return chosen.to_dict()["label"] if chosen is not None else "None detected"

    @Property(bool, notify=changed)
    def activeClientSupportsPtt(self) -> bool:
        chosen = self._selected_client()
        return bool(chosen is not None and chosen.supports_ptt)

    @Property(bool, notify=changed)
    def ipcSocketAvailable(self) -> bool:
        return len(discover_discord_ipc_sockets(self._runtime_dir, environ=self._environ)) > 0

    @Property(str, notify=changed)
    def statusSummary(self) -> str:
        chosen = self._selected_client()
        ipc_live = self.ipcSocketAvailable
        if chosen is None and not ipc_live:
            return "No Discord client detected on this PC (looked for Vesktop, Discord, Legcord and WebCord)."
        client_part = (
            f"Client: {chosen.to_dict()['label']}"
            if chosen is not None
            else "Client: running (local IPC socket found)"
        )
        ipc_part = "Rich Presence IPC socket ready" if ipc_live else "Discord not currently running (Rich Presence stays silent until opened)"
        rp_part = "Rich Presence: On" if self._rich_presence_enabled else "Rich Presence: Off"
        return f"{client_part} · {ipc_part} · {rp_part}"

    @Property(str, notify=changed)
    def pttSource(self) -> str:
        return self._ptt_source

    @Property(str, notify=changed)
    def pttBehavior(self) -> str:
        return self._ptt_behavior

    @Property(str, notify=changed)
    def pttStatusMessage(self) -> str:
        chosen = self._selected_client()
        if chosen is None:
            if self._which("vesktop"):
                return "Ready · drives Vesktop Push-to-Talk via $XDG_RUNTIME_DIR/vesktop.sock (or vesktop --run-shortcut)."
            return "Vesktop is not installed. Controller Push-to-Talk requires Vesktop (official Discord, WebCord and Legcord do not expose a Wayland PTT socket)."
        if not chosen.supports_ptt:
            return (
                f"{chosen.name} does not support external Wayland Push-to-Talk. "
                "Switch your Discord client to Vesktop to use controller PTT."
            )
        return "Ready · drives Vesktop Push-to-Talk via $XDG_RUNTIME_DIR/vesktop.sock (or vesktop --run-shortcut)."

    @Property(str, notify=changed)
    def lastNotice(self) -> str:
        return self._last_notice

    @Slot()
    def refreshClients(self) -> None:
        self._clients = self._detect_clients()
        self.changed.emit()

    @Slot(bool, result=bool)
    def setRichPresenceEnabled(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        if enabled == self._rich_presence_enabled:
            return True
        self._rich_presence_enabled = enabled
        self._save_settings()
        if not enabled:
            self._ipc_client.clear_activity()
            self._active_published_key = None
            self._last_notice = "Discord Rich Presence turned off."
        else:
            self._last_notice = "Discord Rich Presence turned on."
            self.syncRichPresence()
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def setPreferredClient(self, client_id: str) -> bool:
        cleaned = str(client_id or "auto").strip() or "auto"
        self._preferred_client_id = cleaned
        self._save_settings()
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def setAppId(self, app_id: str) -> bool:
        cleaned = str(app_id or "").strip() or DEFAULT_DISCORD_APP_ID
        if cleaned and not cleaned.isdigit():
            self._last_notice = "Discord Application ID must be a numeric ID from discord.com/developers/applications."
            self.changed.emit()
            return False
        self._app_id = cleaned
        self._ipc_client.close()
        self._ipc_client = DiscordIpcClient(
            app_id=self._app_id,
            runtime_dir=self._runtime_dir,
            environ=self._environ,
        )
        self._active_published_key = None
        self._save_settings()
        self.syncRichPresence()
        self._last_notice = f"Saved Discord Application ID ({self._app_id})."
        self.changed.emit()
        return True

    @Slot(str, str, result=bool)
    def setControllerPtt(self, source: str, behavior: str = "hold") -> bool:
        clean_src = str(source or "").strip()
        clean_beh = str(behavior or "hold").strip()
        if clean_beh not in PTT_BEHAVIORS:
            clean_beh = "hold"
        self._ptt_source = clean_src
        self._ptt_behavior = clean_beh
        self._save_settings()
        self._update_live_lease()
        self.changed.emit()
        return True

    @Slot(bool, result=bool)
    def triggerPtt(self, down: bool) -> bool:
        chosen = self._selected_client()
        ok, msg = send_vesktop_ptt(
            bool(down),
            client=chosen,
            runtime_dir=self._runtime_dir,
            environ=self._environ,
            which=self._which,
            spawn_fn=self._spawn_fn,
        )
        self._last_notice = msg
        self.changed.emit()
        return ok

    @Slot(result=bool)
    def openDiscord(self) -> bool:
        self._clients = self._detect_clients()
        chosen = self._selected_client()
        if chosen is None:
            self._last_notice = "No Discord client found (install Vesktop, Discord, Legcord or WebCord)."
            self.changed.emit()
            return False
        ok = open_or_focus_discord_client(
            chosen,
            which=self._which,
            spawn_fn=self._spawn_fn,
            environ=self._environ,
        )
        self._last_notice = (
            f"Opened {chosen.to_dict()['label']}."
            if ok
            else f"Could not start {chosen.to_dict()['label']}."
        )
        self.changed.emit()
        return ok

    @Slot(result=bool)
    def syncRichPresence(self) -> bool:
        """Publish or clear 'Playing <title>' over local IPC based on LaunchView.activeGame."""
        if not self._rich_presence_enabled:
            if self._active_published_key is not None:
                self._ipc_client.clear_activity()
                self._active_published_key = None
            return False

        active = getattr(self._launch_view, "activeGame", None) if self._launch_view is not None else None
        # Do not advertise an attempted launch as a playing session. The session
        # monitor emits a later change when process tracking confirms it is running.
        if isinstance(active, dict) and active.get("state") == "running":
            title = str(active.get("title") or "").strip()
            if title:
                raw_ts = active.get("runningSince") or active.get("launchTime") or time.time()
                try:
                    numeric_ts = float(raw_ts)
                except (TypeError, ValueError):
                    numeric_ts = time.time()
                start_ts = int(numeric_ts) if numeric_ts > 0 else int(time.time())
                key = (title, start_ts)
                if key == self._active_published_key:
                    return True
                ok = self._ipc_client.set_activity(title, start_timestamp=start_ts)
                if ok:
                    self._active_published_key = key
                return ok

        if self._active_published_key is not None:
            self._ipc_client.clear_activity()
            self._active_published_key = None
        return False

    @Slot()
    def shutdown(self) -> None:
        if self._active_published_key is not None:
            try:
                self._ipc_client.clear_activity()
            except Exception:
                pass
            self._active_published_key = None
        self._ipc_client.close()
        if self._controller_live is not None:
            try:
                self._controller_live.release("ptt")
            except Exception:
                pass
