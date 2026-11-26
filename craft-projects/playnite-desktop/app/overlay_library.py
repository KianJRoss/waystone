"""Waystone Overlay Quick Library Bridge.

Provides Xbox-guide style compact library integration inside the in-game overlay:
1. Now Playing card: cover art, title, current session playtime, Resume (close overlay),
   and Quit game (with confirmation, using launch view's stop/terminate path without killing
   unrelated processes).
2. Recent games row: sorted newest first by play session store and library last_activity.
3. Pinned / favorites row: quick access to starred titles.
4. All installed games: virtualized/lazy list with real-time title search and controller Y-key search.
5. Game card details: cover, title, platform, last played, total playtime, and Play action.
6. Safe game switcher: "Switch to X? Y will be closed." - asks LaunchView to stop Y (only that
   session's processes), waits until the tracker sees Y gone, then asks the app to launch X through the
   same launch options as the Library Play button (launchRequested). If Y does not close, X is not started.
"""
from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QObject, Property, Qt, Signal, Slot, QTimer


class _RowsModel(QAbstractListModel):
    """List model for the (possibly thousands of) installed games: QML converts only the rows it shows.
    The single role is named modelData so delegates keep `required property var modelData`."""

    ROLE = Qt.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        return self._rows[index.row()] if role in (self.ROLE, Qt.DisplayRole) else None

    def roleNames(self):
        return {self.ROLE: QByteArray(b"modelData")}


def _format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string (e.g. '45m', '2h 15m')."""
    if seconds <= 0:
        return "0m"
    secs = int(seconds)
    hours = secs // 3600
    minutes = (secs % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m" if minutes > 0 else "< 1m"


def _format_total_playtime(seconds: float) -> str:
    """Format total playtime (e.g. '14.5 hours' or '45 mins')."""
    if seconds <= 0:
        return "Never played"
    secs = int(seconds)
    hours = secs / 3600.0
    if hours >= 1.0:
        return f"{hours:.1f} hours"
    minutes = max(1, secs // 60)
    return f"{minutes} mins"


def _format_last_played(iso_str: str) -> str:
    """Format ISO timestamp to friendly relative/short string."""
    if not iso_str:
        return ""
    clean = iso_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean)
        now = datetime.now(timezone.utc)
        diff = now - dt
        days = diff.days
        if days == 0:
            return "Today"
        elif days == 1:
            return "Yesterday"
        elif days < 7:
            return f"{days} days ago"
        elif days < 30:
            weeks = max(1, days // 7)
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        else:
            return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return iso_str[:10] if len(iso_str) >= 10 else iso_str


class OverlayLibraryBridge(QObject):
    """Presentation bridge exposing Xbox-guide quick library data and operations to QML."""

    changed = Signal()
    nowPlayingChanged = Signal()
    recentGamesChanged = Signal()
    pinnedGamesChanged = Signal()
    installedGamesChanged = Signal()
    searchQueryChanged = Signal()
    selectedGameChanged = Signal()
    switchConfirmationChanged = Signal()
    quitConfirmationChanged = Signal()
    closeOverlayRequested = Signal()
    launchRequested = Signal(str)       # canonical game id; Main.qml launches it like the Library Play button
    launchError = Signal(str)

    SWITCH_TIMEOUT_SECONDS = 90.0       # Proton games can take a while to shut down (wineserver)
    RECENT_LIMIT = 15

    def __init__(
        self,
        library_model: Optional[Any] = None,
        launch_view: Optional[Any] = None,
        play_session_store: Optional[Any] = None,
        parent: Optional[QObject] = None,
        clock=time.monotonic,
    ) -> None:
        super().__init__(parent)
        self._library_model = library_model
        self._launch_view = launch_view
        self._play_session_store = play_session_store
        self._clock = clock

        self._search_query = ""
        self._selected_game_id = ""
        self._selected_game: Dict[str, Any] = {}

        self._is_switching = False
        self._switch_closing = False
        self._switching_target_id = ""
        self._switching_target_title = ""
        self._switching_current_title = ""
        self._switch_status = ""
        self._switch_started = 0.0
        self._is_quitting = False

        # Cached presentation rows; rebuilt only when the library changes (the library has thousands of games).
        self._cache: Optional[Dict[str, Any]] = None
        self._search_cache: Optional[List[Dict[str, Any]]] = None
        self._search_model = _RowsModel(self)
        self._search_model_source: Optional[List[Dict[str, Any]]] = None

        self._switch_timer = QTimer(self)
        self._switch_timer.setInterval(250)
        self._switch_timer.timeout.connect(self._poll_switch_progress)

        # Library filters reset the model often; coalesce those into one rebuild.
        self._library_debounce = QTimer(self)
        self._library_debounce.setSingleShot(True)
        self._library_debounce.setInterval(300)
        self._library_debounce.timeout.connect(self._on_library_changed)
        self._had_running_game = False
        if self._library_model is not None:
            for name in ("modelReset", "organizerChanged"):
                signal = getattr(self._library_model, name, None)
                if signal is not None and hasattr(signal, "connect"):
                    signal.connect(self._library_debounce.start)
        if self._launch_view is not None and hasattr(self._launch_view, "changed"):
            self._launch_view.changed.connect(self._on_launch_view_changed)

    # --- Now Playing ---

    def _active(self) -> Dict[str, Any]:
        if self._launch_view is None:
            return {}
        active = getattr(self._launch_view, "activeGame", None)
        return dict(active) if isinstance(active, dict) else {}

    @Property(bool, notify=nowPlayingChanged)
    def hasRunningGame(self) -> bool:
        """True while a game Waystone launched is starting or running (whichever game the Library has selected)."""
        return bool(self._active())

    @Property("QVariantMap", notify=nowPlayingChanged)
    def runningGame(self) -> Dict[str, Any]:
        active = self._active()
        if not active:
            return {}
        game_id = str(active.get("gameId") or "")
        row = self._row_for(game_id) if game_id else None
        since = float(active.get("runningSince") or 0) or float(active.get("launchTime") or 0)
        seconds = max(0.0, time.time() - since) if since else 0.0
        return {
            "id": row["id"] if row else game_id,
            "title": active.get("title") or (row["title"] if row else ""),
            "initials": row["initials"] if row else "".join(w[0] for w in str(active.get("title") or "").split()[:2]).upper(),
            "coverUrl": row["coverUrl"] if row else "",
            "state": active.get("state", ""),
            "sessionSeconds": seconds,
            "sessionDuration": _format_duration(seconds) if active.get("state") == "running" else "Starting…",
        }

    # --- Recent, Pinned, Installed Games ---

    def _rows(self) -> Dict[str, Any]:
        if self._cache is not None:
            return self._cache
        rows: List[Dict[str, Any]] = []
        games = getattr(self._library_model, "games", None) if self._library_model is not None else None
        for game in games or []:
            try:
                rows.append(self._game_to_dict(game))
            except Exception:
                continue
        by_id = {row["id"]: row for row in rows}
        installed = sorted((r for r in rows if r["isInstalled"]), key=lambda r: r["title"].casefold())
        pinned = sorted((r for r in rows if r["isFavorite"]), key=lambda r: r["title"].casefold())
        self._cache = {"by_id": by_id, "installed": installed, "pinned": pinned, "recent": self._compute_recent(rows)}
        return self._cache

    def _row_for(self, game_id: str) -> Optional[Dict[str, Any]]:
        rows = self._rows()["by_id"]
        if game_id in rows:
            return rows[game_id]
        rep = getattr(self._library_model, "_representative_for", {}) if self._library_model is not None else {}
        return rows.get(rep.get(game_id, ""))

    def _compute_recent(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        store_recents: Dict[str, float] = {}
        db = getattr(self._play_session_store, "db", None) if self._play_session_store is not None else None
        if db is not None:
            try:
                for game_id, last_seen in db.execute(
                    "SELECT game_id, MAX(updated_at) FROM play_sessions WHERE ever_running = 1 "
                    "GROUP BY game_id ORDER BY 2 DESC LIMIT 50"
                ).fetchall():
                    store_recents[str(game_id)] = float(last_seen)
            except Exception:
                pass
        annotated = []
        for item in rows:
            ts = store_recents.get(item["id"], 0.0)
            if not ts and item["lastPlayedIso"]:
                try:
                    parsed = datetime.fromisoformat(item["lastPlayedIso"].replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    ts = parsed.timestamp()
                except (TypeError, ValueError):
                    ts = 0.0
            if ts > 0:
                annotated.append((ts, item["playtimeSeconds"], item))
        annotated.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [entry[2] for entry in annotated[: self.RECENT_LIMIT]]

    @Property("QVariantList", notify=recentGamesChanged)
    def recentGames(self) -> List[Dict[str, Any]]:
        """Last played games, newest first (Waystone play sessions, then the library's last activity)."""
        return self._rows()["recent"]

    @Property("QVariantList", notify=pinnedGamesChanged)
    def pinnedGames(self) -> List[Dict[str, Any]]:
        return self._rows()["pinned"]

    @Property("QVariantList", notify=installedGamesChanged)
    def installedGames(self) -> List[Dict[str, Any]]:
        return self._rows()["installed"]

    @Property(int, notify=installedGamesChanged)
    def installedCount(self) -> int:
        return len(self._rows()["installed"])

    # --- Search ---

    @Property(str, notify=searchQueryChanged)
    def searchQuery(self) -> str:
        return self._search_query

    @Property("QVariantList", notify=searchQueryChanged)
    def searchResults(self) -> List[Dict[str, Any]]:
        """Installed games matching the search (substring, or initials such as 'mkw' for Mario Kart Wii)."""
        if self._search_cache is not None:
            return self._search_cache
        installed = self._rows()["installed"]
        query = self._search_query.strip().casefold()
        if not query:
            self._search_cache = installed
            return installed
        filtered = []
        for item in installed:
            title = item["title"].casefold()
            if query in title or query in "".join(word[0] for word in title.split() if word):
                filtered.append(item)
        self._search_cache = filtered
        return filtered

    @Property(QObject, constant=True)
    def searchModel(self) -> QObject:
        """searchResults as a list model (what the QML list uses)."""
        self._sync_search_model()
        return self._search_model

    @Property(int, notify=searchQueryChanged)
    def searchResultCount(self) -> int:
        return len(self.searchResults)

    @Slot(int, result=str)
    def searchResultId(self, index: int) -> str:
        rows = self.searchResults
        return rows[index]["id"] if 0 <= index < len(rows) else ""

    def _sync_search_model(self) -> None:
        rows = self.searchResults
        if rows is not self._search_model_source:
            self._search_model_source = rows
            self._search_model.set_rows(rows)

    @Slot(str)
    def setSearchQuery(self, query: str) -> None:
        clean = (query or "").strip()
        if self._search_query != clean:
            self._search_query = clean
            self._search_cache = None
            self._sync_search_model()
            self.searchQueryChanged.emit()

    # --- Game Selection & Detail Card ---

    @Property("QVariantMap", notify=selectedGameChanged)
    def selectedGame(self) -> Dict[str, Any]:
        return self._selected_game

    @Property(str, notify=selectedGameChanged)
    def selectedGameId(self) -> str:
        return self._selected_game_id

    @Slot(str)
    def selectGame(self, game_id: str) -> None:
        row = self._row_for(game_id) if game_id else None
        if row is None:
            self.clearSelectedGame()
            return
        self._selected_game_id = row["id"]
        self._selected_game = dict(row)
        self.selectedGameChanged.emit()

    @Slot()
    def clearSelectedGame(self) -> None:
        if self._selected_game_id:
            self._selected_game_id = ""
            self._selected_game = {}
            self.selectedGameChanged.emit()

    # --- Play & Safe Switching ---

    @Property(bool, notify=switchConfirmationChanged)
    def isSwitching(self) -> bool:
        return self._is_switching

    @Property(bool, notify=switchConfirmationChanged)
    def switchClosing(self) -> bool:
        """True after the switch was confirmed, while waiting for the running game to close."""
        return self._switch_closing

    @Property(str, notify=switchConfirmationChanged)
    def switchingTargetTitle(self) -> str:
        return self._switching_target_title

    @Property(str, notify=switchConfirmationChanged)
    def switchingCurrentTitle(self) -> str:
        return self._switching_current_title

    @Property(str, notify=switchConfirmationChanged)
    def switchStatus(self) -> str:
        return self._switch_status

    @Property(bool, notify=quitConfirmationChanged)
    def isQuitting(self) -> bool:
        return self._is_quitting

    @Slot(str)
    def playGame(self, game_id: str) -> None:
        """Play a game; if another one is running, ask first ("Switch to X? Y will be closed.")."""
        row = self._row_for(game_id)
        if row is None:
            self.launchError.emit(f"Game not found: {game_id}")
            return
        running = self.runningGame
        if running:
            if running.get("id") == row["id"]:
                self.resumeGame()
                return
            self._is_switching = True
            self._switch_closing = False
            self._switching_target_id = row["id"]
            self._switching_target_title = row["title"]
            self._switching_current_title = running.get("title") or "The running game"
            self._switch_status = f"Switch to {row['title']}? {self._switching_current_title} will be closed."
            self.switchConfirmationChanged.emit()
            return
        self._request_launch(row["id"])

    @Slot()
    def confirmSwitch(self) -> None:
        """Close the running game, wait until its session has closed, then launch the chosen game."""
        if not self._is_switching or self._switch_closing or not self._switching_target_id:
            return
        if self.hasRunningGame and not self._stop_running_game():
            self._switch_status = f"Waystone could not ask {self._switching_current_title} to close."
            self.switchConfirmationChanged.emit()
            self.launchError.emit(self._switch_status)
            return
        self._switch_closing = True
        self._switch_started = self._clock()
        self._switch_status = f"Closing {self._switching_current_title}…"
        self.switchConfirmationChanged.emit()
        self._switch_timer.start()
        self._poll_switch_progress()

    @Slot()
    def cancelSwitch(self) -> None:
        self._switch_timer.stop()
        self._reset_switch()

    def _reset_switch(self) -> None:
        self._is_switching = False
        self._switch_closing = False
        self._switching_target_id = ""
        self._switching_target_title = ""
        self._switching_current_title = ""
        self._switch_status = ""
        self.switchConfirmationChanged.emit()

    def _poll_switch_progress(self) -> None:
        if not self._switch_closing:
            self._switch_timer.stop()
            return
        if self._launch_view is not None and hasattr(self._launch_view, "pollGameState"):
            self._launch_view.pollGameState()
        if not self.hasRunningGame:
            self._switch_timer.stop()
            target_id = self._switching_target_id
            self._reset_switch()
            self._request_launch(target_id)
            return
        if self._clock() - self._switch_started > self.SWITCH_TIMEOUT_SECONDS:
            # Never start a second game while the first one is still running.
            self._switch_timer.stop()
            self._switch_closing = False
            self._switch_status = (f"{self._switching_current_title} is still running, so "
                                   f"{self._switching_target_title} was not started.")
            self.switchConfirmationChanged.emit()
            self.launchError.emit(self._switch_status)

    @Slot()
    def resumeGame(self) -> None:
        self.closeOverlayRequested.emit()

    @Slot()
    def requestQuit(self) -> None:
        if not self.hasRunningGame:
            return
        self._is_quitting = True
        self.quitConfirmationChanged.emit()

    @Slot()
    def confirmQuit(self) -> None:
        self._is_quitting = False
        self.quitConfirmationChanged.emit()
        if not self._stop_running_game():
            self.launchError.emit("Waystone could not ask the game to close.")

    @Slot()
    def cancelQuit(self) -> None:
        self._is_quitting = False
        self.quitConfirmationChanged.emit()

    def _stop_running_game(self) -> bool:
        stop = getattr(self._launch_view, "stopActiveGame", None) if self._launch_view is not None else None
        return bool(stop()) if callable(stop) else False

    def _request_launch(self, game_id: str) -> None:
        self.clearSelectedGame()
        self.launchRequested.emit(game_id)
        self.closeOverlayRequested.emit()

    @Slot(str, str)
    def reportLaunchResult(self, game_id: str, error: str) -> None:
        """Main.qml reports a launch that could not be started."""
        if error:
            self.launchError.emit(error)

    # --- Helpers ---

    def _game_to_dict(self, game: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a LibraryModel game entry into a lightweight QML presentation dict."""
        model = self._library_model
        game_id = game.get("id", "")
        title = model._display_title(game) if model is not None and hasattr(model, "_display_title") else game.get("title", "")
        cover_url = ""
        if model is not None and hasattr(model, "_urls"):
            cover_url = str(model._urls(game).get("coverUrl", "") or "")
        variants = model._effective_variants(game) if model is not None and hasattr(model, "_effective_variants") else game.get("variants", [])
        variants = [v for v in variants if isinstance(v, dict)]
        metas = [v.get("metadata") or {} for v in variants]
        is_installed = any(v.get("status") in ("installed", "declared-installed") for v in variants)
        is_fav = any(m.get("favorite") is True for m in metas)
        overrides = getattr(model, "_overrides", {}) if model is not None else {}
        if isinstance(overrides, dict) and (overrides.get(game_id) or {}).get("favorite") is True:
            is_fav = True
        playtimes = [m.get("playtime_seconds") for m in metas if isinstance(m.get("playtime_seconds"), (int, float))]
        playtime_seconds = max(playtimes) if playtimes else 0
        activities = [str(m.get("last_activity")) for m in metas if m.get("last_activity")]
        last_activity_iso = max(activities) if activities else ""
        platforms = {str(v.get("platform") or "").strip() for v in variants}
        plat_str = ", ".join(sorted(p for p in platforms if p and p != "unknown")) or "PC"
        return {
            "id": game_id,
            "title": title,
            "initials": "".join(word[0] for word in str(title).split()[:2] if word).upper() or "?",
            "coverUrl": cover_url,
            "isInstalled": is_installed,
            "isFavorite": is_fav,
            "playtimeSeconds": int(playtime_seconds),
            "playtime": _format_total_playtime(playtime_seconds),
            "lastPlayedIso": last_activity_iso,
            "lastPlayed": _format_last_played(last_activity_iso),
            "platform": plat_str,
        }

    def _on_library_changed(self) -> None:
        self._cache = None
        self._search_cache = None
        self._sync_search_model()
        self.recentGamesChanged.emit()
        self.pinnedGamesChanged.emit()
        self.installedGamesChanged.emit()
        self.searchQueryChanged.emit()
        self.changed.emit()

    def _on_launch_view_changed(self) -> None:
        running = self.hasRunningGame
        if running != self._had_running_game:
            self._had_running_game = running
            if not running:
                self._on_library_changed()  # a session just ended: Recent and playtime changed
        self.nowPlayingChanged.emit()
        self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        """Rebuild the rows (e.g. after a game was played, so Recent is current)."""
        self._on_library_changed()
        self._on_launch_view_changed()
