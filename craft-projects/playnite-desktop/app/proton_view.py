"""Settings > Games > Proton (W19): installed Proton versions, who uses them, install/switch/remove. Network and disk
work runs on a worker thread; results come back through queued signals."""
from __future__ import annotations

import threading

from PySide6.QtCore import Property, QObject, Signal, Slot

from native_services import proton_manager as pm


class ProtonView(QObject):
    changed = Signal()
    _done = Signal(str, object)          # worker -> UI thread
    _progressed = Signal(str, int, int)

    def __init__(self, paths: pm.Paths | None = None, *, get_latest=None, install=None, get_releases=None,
                 steam_running=None, close_steam=None, on_launch_profiles_changed=None, parent=None):
        super().__init__(parent)
        self._paths = paths or pm.Paths()
        self._get_latest = get_latest or pm.latest_ge
        self._get_releases = get_releases or pm.ge_releases
        self._install = install or pm.install_ge
        self._steam_running = steam_running or pm.steam_running
        self._close_steam = close_steam or pm.close_steam
        self._on_launch_profiles_changed = on_launch_profiles_changed
        self._releases: list[dict] = []
        self._game_status = ""
        self._game_needs_steam_close = False
        self._revision = 0
        self._rows: list[dict] = []
        self._latest: dict = {}
        self._busy = ""
        self._status = ""
        self._progress = (0, 0)
        self._cancel = threading.Event()
        self._done.connect(self._on_done)
        self._progressed.connect(self._on_progress)
        _orig_emit = self._progressed.emit
        def _safe_progress(*args):
            try:
                _orig_emit(*args)
            except RuntimeError:
                pass
        self._emit_progress = _safe_progress

    # ---- properties ----
    @Property("QVariantList", notify=changed)
    def runtimes(self):
        return [dict(r, heroicCount=len(r["heroic"]), waystoneCount=len(r["waystone"])) for r in self._rows]

    @Property("QVariantMap", notify=changed)
    def latest(self):
        if not self._latest:
            return {}
        installed = any(pm.ge_version(r["name"]) == pm.ge_version(self._latest["tag"]) for r in self._rows
                        if r["kind"] == "ge")
        return {"tag": self._latest["tag"], "sizeMb": self._latest.get("size", 0) // 1024**2, "installed": installed}

    @Property(str, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(float, notify=changed)
    def progress(self):
        done, total = self._progress
        return done / total if total else 0.0

    @Property("QVariantList", notify=changed)
    def releases(self):
        """Recent GE-Proton releases for "Install another version" (fetched on request)."""
        have = {pm.ge_version(r["name"]) for r in self._rows if r["kind"] == "ge"}
        return [{"tag": r["tag"], "sizeMb": r.get("size", 0) // 1024**2, "installed": pm.ge_version(r["tag"]) in have}
                for r in self._releases]

    @Property(str, notify=changed)
    def gameStatus(self):
        """Result of the last per-game change, for the game's Proton row."""
        return self._game_status

    @Property(bool, notify=changed)
    def gameNeedsSteamClose(self):
        return self._game_needs_steam_close

    @Property(int, notify=changed)
    def revision(self):
        """Bumps after anything changes, so per-game rows re-read their state."""
        return self._revision

    @Property(str, notify=changed)
    def newestInstalled(self):
        return next((r["name"] for r in self._rows if r.get("newestInstalled")), "")

    # ---- actions ----
    @Slot()
    def refresh(self):
        try:
            self._rows = pm.overview(self._paths)
        except Exception as exc:          # unreadable configs must not break Settings
            self._status = f"Could not read Proton versions: {exc}"
        self.changed.emit()

    @Slot()
    def checkLatest(self):
        self._run("check", lambda: self._get_latest())

    @Slot()
    def installLatest(self):
        if not self._latest:
            self._status = "Check for the latest version first."
            self.changed.emit()
            return
        release = dict(self._latest)
        self._cancel.clear()
        self._run("install", lambda: self._install(
            release, self._paths, progress=lambda stage, d, t: self._emit_progress(stage, d, t),
            cancelled=self._cancel.is_set))

    @Slot()
    def checkReleases(self):
        self._run("releases", lambda: self._get_releases())

    @Slot(str)
    def installRelease(self, tag):
        release = next((r for r in self._releases if r["tag"] == tag), None)
        if release is None:
            self._status = "Load the list of versions first."
            self.changed.emit()
            return
        self._cancel.clear()
        self._run("install", lambda: self._install(
            dict(release), self._paths, progress=lambda stage, d, t: self._emit_progress(stage, d, t),
            cancelled=self._cancel.is_set))

    # ---- one game's version (W19b) ----
    @Slot(str, str, str, result="QVariantMap")
    def gameRuntime(self, provider, product_id, target_id):
        """The Proton row for one library entry: {supported, store, gameId, current, default, hasDefault, options}.
        Reads a few small config files; never writes."""
        try:
            ref = pm.ref_for_launch(self._paths, provider, product_id, target_id)
            if ref is None:
                return {"supported": False}
            info = pm.game_runtime(self._paths, *ref)
        except Exception as exc:          # unreadable launcher configs must not break the game page
            return {"supported": False, "error": str(exc)}
        return dict(info, supported=True, gameId=ref[1])

    @Slot(str, str, str)
    def setGameRuntime(self, store, game_id, name):
        """name '' = follow the default. Applied on the worker thread."""
        self._game_needs_steam_close = False
        self._run("game", lambda: (store, pm.set_game_runtime(self._paths, store, game_id, name or None,
                                                               is_steam_running=self._steam_running)))

    @Slot(str, str, str)
    def closeSteamAndApply(self, store, game_id, name):
        self._game_needs_steam_close = False
        def work():
            self._close_steam()
            return store, pm.set_game_runtime(self._paths, store, game_id, name or None,
                                              is_steam_running=self._steam_running)
        self._run("closeSteam", work)

    @Slot()
    def cancel(self):
        self._cancel.set()

    @Slot(str, str)
    def switchGames(self, old, new):
        self._run("switch", lambda: pm.switch_games(self._paths, old, new))

    @Slot(str)
    def remove(self, name):
        self._run("remove", lambda: pm.remove_runtime(self._paths, name))

    # ---- worker plumbing ----
    def _run(self, kind, work):
        if self._busy:
            if kind in ("game", "closeSteam"):
                self._game_status = "Wait for the current Proton task to finish, then try again."
                self.changed.emit()
            return
        self._busy = kind
        self._status = {"check": "Checking for the latest GE-Proton…", "install": "Downloading…",
                        "switch": "Switching games…", "remove": "Removing…", "releases": "Loading versions…",
                        "game": "", "closeSteam": ""}[kind]
        if kind in ("game", "closeSteam"):
            self._game_status = "Closing Steam…" if kind == "closeSteam" else "Saving…"
        self._progress = (0, 0)
        self.changed.emit()

        def target():
            try:
                result = ("ok", work())
            except Exception as exc:
                result = ("error", str(exc))
            try:
                self._done.emit(kind, result)
            except RuntimeError:
                pass            # the view was closed while this ran
        threading.Thread(target=target, name=f"waystone-proton-{kind}", daemon=True).start()

    def _on_progress(self, stage, done, total):
        self._progress = (done, total)
        self._status = {"download": f"Downloading… {done // 1024**2} / {total // 1024**2} MB" if total else "Downloading…",
                        "verify": "Checking the download…", "unpack": "Unpacking…"}.get(stage, self._status)
        self.changed.emit()

    def _on_done(self, kind, result):
        outcome, value = result
        self._busy = ""
        self._revision += 1
        if kind in ("game", "closeSteam"):
            if outcome == "error":
                self._game_status = value
                self._game_needs_steam_close = "Close Steam first" in value
            else:
                store, used = value
                self._game_status = f"Saved: the next launch uses {used}."
                if store == "waystone":
                    self._launch_profiles_changed()
            self.refresh()
            return
        if outcome == "error":
            self._status = (f"GitHub did not answer ({value}). Try “Check for updates” again in a minute."
                            if kind == "check" else value)
        elif kind == "releases":
            self._releases = value or []
            self._status = "" if value else "No GE-Proton releases found."
        elif kind == "check":
            self._latest = value or {}
            self._status = "" if value else "No GE-Proton release found."
        elif kind == "install":
            self._status = f"Installed {value}. Restart Steam to see it in Steam's compatibility list."
        elif kind == "switch":
            if value["waystone"]:
                self._launch_profiles_changed()
            moved = len(value["heroic"]) + len(value["waystone"])
            steam = value.get("steam", 0)
            self._status = f"Switched {moved} game(s)." + (
                f" {steam} Steam game(s) still use it: change them in Steam (Properties > Compatibility)." if steam else "")
        elif kind == "remove":
            self._status = "Removed."
        self.refresh()

    def _launch_profiles_changed(self):
        """Waystone keeps launch entries in memory: reload them so the next Play uses the new Proton."""
        if self._on_launch_profiles_changed is not None:
            try:
                self._on_launch_profiles_changed()
            except Exception as exc:
                self._game_status += f" Restart Waystone to use it ({exc})."
