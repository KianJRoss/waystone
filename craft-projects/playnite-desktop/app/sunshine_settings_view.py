"""Qt view/controller for Settings > Streaming (Sunshine / Apollo host integration, W11a)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Callable

from PySide6.QtCore import QObject, Property, Signal, Slot

from native_services.sunshine_library import (
    FILTER_MODES,
    detect_sunshine_host,
    publish_to_sunshine,
    remove_from_sunshine,
    select_games_for_sunshine,
)


class SunshineSettingsView(QObject):
    changed = Signal()

    def __init__(
        self,
        *,
        settings_path: Path | str | None = None,
        config_dir: Path | str | None = None,
        library_model: Any = None,
        local_library: Any = None,
        artwork_view: Any = None,
        media_catalog: Any = None,
        ribbon_cache: Any = None,
        artwork_cache_dir: Path | None = None,
        waystone_command: str = "waystone",
        rest_client: Any = None,
        which: Callable[[str], str | None] = shutil.which,
        status_probe: Callable[[], dict[str, Any]] | None = None,
        home_dir: Path | None = None,
        xdg_config_home: Path | None = None,
        offline: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings_path = (
            Path(settings_path)
            if settings_path is not None
            else (Path.home() / ".local/share/waystone/settings.json")
        )
        self._explicit_config_dir = str(config_dir or "").strip()
        self._library_model = library_model
        self._local_library = local_library
        self._artwork_view = artwork_view
        self._media_catalog = media_catalog
        self._ribbon_cache = ribbon_cache
        self._artwork_cache_dir = Path(artwork_cache_dir) if artwork_cache_dir is not None else None
        self._waystone_command = waystone_command
        self._rest_client = rest_client
        self._which = which
        self._status_probe = status_probe
        self._home_dir = Path(home_dir) if home_dir is not None else None
        self._xdg_config_home = Path(xdg_config_home) if xdg_config_home is not None else None
        self._offline = bool(offline)

        self._publish_enabled = False
        self._filter_mode = "installed"
        self._filter_category = ""
        self._saved_config_dir = ""
        self._last_action_notice = ""

        self._load_settings()
        self._status = self._detect()

        if self._library_model is not None:
            for sig_name in ("countChanged", "organizerChanged", "organizerOverlayChanged", "artworkUpdated"):
                sig = getattr(self._library_model, sig_name, None)
                if sig is not None:
                    try:
                        sig.connect(self._on_library_changed)
                    except Exception:
                        pass

    def _on_library_changed(self, *_args: Any) -> None:
        self.changed.emit()

    def _active_config_dir(self) -> str | None:
        if self._explicit_config_dir:
            return self._explicit_config_dir
        if self._saved_config_dir:
            return self._saved_config_dir
        return None

    def _detect(self):
        return detect_sunshine_host(
            self._active_config_dir(),
            publish_enabled=self._publish_enabled,
            which=self._which,
            status_probe=self._status_probe,
            home_dir=self._home_dir,
            xdg_config_home=self._xdg_config_home,
            environ={} if self._offline and self._explicit_config_dir == "" else None,
            offline=self._offline,
        )

    def _load_settings(self) -> None:
        try:
            info = self._settings_path.lstat()
            if not stat.S_ISREG(info.st_mode):
                return
            doc = json.loads(self._settings_path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                return
            section = doc.get("streaming", {})
            if not isinstance(section, dict):
                return
            enabled = section.get("publish_enabled", False)
            if isinstance(enabled, bool):
                self._publish_enabled = enabled
            mode = section.get("filter_mode", "installed")
            if isinstance(mode, str) and mode in FILTER_MODES:
                self._filter_mode = mode
            cat = section.get("filter_category", "")
            if isinstance(cat, str):
                self._filter_category = cat
            cfg_dir = section.get("config_dir", "")
            if isinstance(cfg_dir, str) and cfg_dir.strip():
                self._saved_config_dir = cfg_dir.strip()
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
            section = doc.setdefault("streaming", {})
            if not isinstance(section, dict):
                section = {}
                doc["streaming"] = section
            section["publish_enabled"] = self._publish_enabled
            section["filter_mode"] = self._filter_mode
            section["filter_category"] = self._filter_category
            if self._saved_config_dir:
                section["config_dir"] = self._saved_config_dir
            else:
                section.pop("config_dir", None)
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

    def _matching_games(self) -> list[dict[str, Any]]:
        return select_games_for_sunshine(
            self._library_model,
            filter_mode=self._filter_mode,
            filter_category=self._filter_category,
            local_library=self._local_library,
            artwork_view=self._artwork_view,
            media_catalog=self._media_catalog,
            ribbon_cache=self._ribbon_cache,
            artwork_cache_dir=self._artwork_cache_dir,
        )

    @Property(bool, notify=changed)
    def installed(self) -> bool:
        return self._status.installed

    @Property(bool, notify=changed)
    def running(self) -> bool:
        return self._status.running

    @Property(bool, notify=changed)
    def clientConnected(self) -> bool:
        return self._status.client_connected

    @Property(str, notify=changed)
    def flavor(self) -> str:
        return self._status.flavor

    @Property(str, notify=changed)
    def configDir(self) -> str:
        return str(self._status.config_dir)

    @Property(str, notify=changed)
    def appsJsonPath(self) -> str:
        return str(self._status.apps_json_path)

    @Property(bool, notify=changed)
    def backupExists(self) -> bool:
        return self._status.backup_path.is_file()

    @Property(int, notify=changed)
    def publishedCount(self) -> int:
        return self._status.published_count

    @Property(str, notify=changed)
    def statusSummary(self) -> str:
        return self._status.summary

    @Property(str, notify=changed)
    def lastActionNotice(self) -> str:
        return self._last_action_notice

    @Property(bool, notify=changed)
    def publishEnabled(self) -> bool:
        return self._publish_enabled

    @Property(str, notify=changed)
    def filterMode(self) -> str:
        return self._filter_mode

    @Property(str, notify=changed)
    def filterCategory(self) -> str:
        return self._filter_category

    @Property("QVariantList", notify=changed)
    def categoryOptions(self) -> list[str]:
        if self._library_model is not None and hasattr(self._library_model, "collectionValues"):
            try:
                cats = list(self._library_model.collectionValues("categories") or [])
                return [str(c) for c in cats if isinstance(c, str) and c.strip()]
            except Exception:
                return []
        return []

    @Property(int, notify=changed)
    def matchingGameCount(self) -> int:
        return len(self._matching_games())

    @Property(str, notify=changed)
    def bigScreenStatus(self) -> str:
        return self._status.big_screen_label

    @Slot()
    def refreshStatus(self) -> None:
        self._status = self._detect()
        self.changed.emit()

    @Slot(bool, result=bool)
    def setPublishEnabled(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        self._publish_enabled = enabled
        self._save_settings()
        if enabled:
            return self.publishNow()
        self._status = self._detect()
        self._last_action_notice = "Automatic publishing to Moonlight turned off."
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def setFilterMode(self, mode: str) -> bool:
        if mode not in FILTER_MODES:
            return False
        if mode == "category" and not self._filter_category:
            cats = self.categoryOptions
            if cats:
                self._filter_category = cats[0]
        self._filter_mode = mode
        self._save_settings()
        if self._publish_enabled:
            return self.publishNow()
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def setFilterCategory(self, category: str) -> bool:
        self._filter_category = str(category or "").strip()
        self._save_settings()
        if self._publish_enabled and self._filter_mode == "category":
            return self.publishNow()
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def setConfigDir(self, path: str) -> bool:
        cleaned = str(path or "").strip()
        self._saved_config_dir = cleaned
        self._explicit_config_dir = cleaned
        self._save_settings()
        self._status = self._detect()
        self.changed.emit()
        return True

    @Slot(result=bool)
    def publishNow(self) -> bool:
        try:
            games = self._matching_games()
            result = publish_to_sunshine(
                self._status.config_dir,
                games,
                waystone_command=self._waystone_command,
                rest_client=self._rest_client,
            )
            self._status = self._detect()
            count = result["published_count"]
            backup_note = " (backed up original apps.json)" if result.get("backed_up") else ""
            rest_note = " and synced via Sunshine REST API" if result.get("rest_synced") else ""
            self._last_action_notice = (
                f"Published {count} Waystone game{'s' if count != 1 else ''} to Moonlight{backup_note}{rest_note}."
            )
            self.changed.emit()
            return True
        except Exception:
            self._last_action_notice = "Could not write Sunshine apps.json. Check permissions on the Sunshine config folder."
            self.changed.emit()
            return False

    @Slot(result=bool)
    def republishIfEnabled(self) -> bool:
        if not self._publish_enabled:
            return False
        return self.publishNow()

    @Slot(result=bool)
    def removePublishedGames(self) -> bool:
        try:
            result = remove_from_sunshine(
                self._status.config_dir,
                rest_client=self._rest_client,
            )
            self._publish_enabled = False
            self._save_settings()
            self._status = self._detect()
            removed = result["removed_count"]
            kept = result["user_count"]
            self._last_action_notice = (
                f"Removed {removed} Waystone game{'s' if removed != 1 else ''} from Moonlight "
                f"({kept} non-Waystone Sunshine app{'s' if kept != 1 else ''} kept intact)."
            )
            self.changed.emit()
            return True
        except Exception:
            self._last_action_notice = "Could not update Sunshine apps.json to remove Waystone games."
            self.changed.emit()
            return False
