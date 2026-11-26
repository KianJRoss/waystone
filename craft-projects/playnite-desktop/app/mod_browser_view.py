"""Mods > Browse: search Nexus, GameBanana and Modrinth for the selected game (W7 phase 1).

Network calls run on a worker thread (the UI never waits on HTTP). The match between a library game and a
platform's game (Nexus domain, GameBanana game id) is never guessed silently: an exact name match is used,
otherwise the user picks from the candidates and the choice is remembered per library game.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QFileSystemWatcher, QObject, Property, QThread, Signal, Slot

from native_services import mod_catalog

try:
    from private_addons import EXTRA_PC_SOURCES as _EXTRA_PC_SOURCES
except ImportError:
    _EXTRA_PC_SOURCES = set()

PLATFORM_EMULATORS = [  # first installed one wins
    ("Nintendo Switch", ["eden", "ryujinx"]), ("Nintendo 3DS", ["azahar"]), ("3DS", ["azahar"]),
    ("Wii U", ["cemu"]), ("GameCube", ["dolphin"]), ("Wii", ["dolphin"]),
]
EMULATOR_CONFIG_DIRS = {"eden": ".local/share/eden", "ryujinx": ".config/Ryujinx", "azahar": ".local/share/azahar-emu",
                        "cemu": ".local/share/Cemu", "dolphin": ".local/share/dolphin-emu"}

PLATFORM_LABELS = {"nexus": "Nexus Mods", "gamebanana": "GameBanana", "modrinth": "Modrinth"}


def default_store_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "waystone/mods/catalog-games.json"


class _Job(QThread):
    done = Signal(int)

    def __init__(self, ticket: int, fn: Callable[[], Any]):
        super().__init__()
        self.ticket, self._fn = ticket, fn
        self.result: Any = None
        self.error: str = ""

    def run(self) -> None:  # pragma: no cover - thread body; behaviour tested through the view
        try:
            self.result = self._fn()
        except Exception as exc:  # network/catalog errors become a status line, never a crash
            self.error = str(exc) or type(exc).__name__
        self.done.emit(self.ticket)


PC_SOURCES = {"steam", "epic", "gog", "xbox", "ea app", "ubisoft connect", "battle.net", "amazon",
              "itch.io", "humble", "legendary", "heroic", "lutris"} | {x.casefold() for x in _EXTRA_PC_SOURCES}


def rank_mod_games(games: list[dict], title_of=lambda g: g.get("title", "")) -> list[dict]:
    """Mods game list: PC games first, then the ones you know best (installed, favourite, most played,
    most recent); hidden games left out. Each entry carries what the browser needs to pick a mod site."""
    out = []
    for g in games:
        variants = list(g.get("variants") or [])
        metas = [v.get("metadata") or {} for v in variants]
        if any(m.get("hidden") is True for m in metas) and not any(m.get("hidden") is False for m in metas):
            continue
        platforms = sorted({p.strip() for v in variants for p in str(v.get("platform") or "").split(" / ")
                            if p.strip() and p.strip() != "unknown"})
        sources = [str(v.get("source") or "") for v in variants]
        pc = any("PC" in p for p in platforms) or (not platforms and any(s.lower() in PC_SOURCES for s in sources))
        playtime = max([m.get("playtime_seconds") or 0 for m in metas if isinstance(m.get("playtime_seconds"), (int, float))] or [0])
        last = max([str(m.get("last_activity") or "") for m in metas] or [""])
        out.append({
            "game_id": g["id"], "title": title_of(g), "platforms": platforms,
            "source": next((s for s in sources if s), ""), "pc": pc,
            "installed": any(v.get("status") in ("installed", "declared-installed") for v in variants),
            "favorite": any(m.get("favorite") is True for m in metas), "playtime": int(playtime), "last_played": last,
        })
    def recency(e):
        from datetime import datetime
        try:
            return -datetime.fromisoformat(e["last_played"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    out.sort(key=lambda e: (not e["pc"], not e["installed"], not e["favorite"], -e["playtime"], recency(e),
                            e["title"].casefold()))
    return out


def nxm_inbox() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "waystone/mods/nxm-inbox"


class ModBrowserView(QObject):
    changed = Signal()
    nexusLinkReceived = Signal()  # QML switches to Mods > Browse so the user sees the download
    _progressed = Signal(float)  # emitted from the download thread; Qt queues it onto the UI thread

    def __init__(self, games: list[dict] | None = None, *, store_path: Path | None = None,
                 catalog=mod_catalog, synchronous: bool = False, home: str | Path | None = None,
                 downloads=None, installer=None, nexus=None, game_mods=None, inbox=None,
                 watch_inbox: bool = False, offline: bool = False, parent=None):
        super().__init__(parent)
        self._games = {str(g.get("game_id") or g.get("id")): g for g in (games or [])}
        self._store_path = Path(store_path) if store_path else default_store_path()
        self._catalog = catalog
        self._sync = synchronous
        self._jobs: dict[int, tuple[_Job, Callable[[Any, str], None]]] = {}
        self._ticket = 0
        self._search_ticket = 0
        self._game: dict = {}
        self._platforms: list[str] = []
        self._platform = ""
        self._query = ""
        self._items: list[dict] = []
        self._total = 0
        self._page = 0
        self._busy = False
        self._status = "Choose a game, or search Modrinth for Minecraft."
        self._candidates: list[dict] = []
        self._recipe: dict = {}
        self._home = Path(home) if home else Path.home()
        self._downloads = downloads
        self._installer = installer
        self._files: list[dict] = []
        self._files_for: dict = {}
        self._download_path = ""
        self._progress = 0.0
        self._install_plan: dict = {}
        self._last_journal = ""
        self._progressed.connect(self._on_progress)
        self._offline = offline  # headless checks/tests: never touch the network
        self._nexus = nexus
        self._game_mods = game_mods
        self._inbox = Path(inbox) if inbox else None
        self._watcher = None
        if watch_inbox:
            self._inbox = self._inbox or nxm_inbox()
            self._inbox.mkdir(parents=True, exist_ok=True)
            self._watcher = QFileSystemWatcher([str(self._inbox)], self)
            self._watcher.directoryChanged.connect(lambda _p: self.processInbox())

    # ---------- persistence of game matches ----------
    def _load_matches(self) -> dict:
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_match(self, game_id: str, platform: str, value: dict) -> None:
        data = self._load_matches()
        data.setdefault(game_id, {})[platform] = value
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._store_path)

    def _match(self) -> dict | None:
        gid = str(self._game.get("game_id") or "")
        return self._load_matches().get(gid, {}).get(self._platform) if gid else None

    # ---------- threading ----------
    def _run(self, fn: Callable[[], Any], on_done: Callable[[Any, str], None]) -> int:
        self._ticket += 1
        ticket = self._ticket
        if self._offline:
            on_done(None, "offline (headless check)")
            return ticket
        if self._sync:
            try:
                on_done(fn(), "")
            except Exception as exc:
                on_done(None, str(exc) or type(exc).__name__)
            return ticket
        job = _Job(ticket, fn)
        self._jobs[ticket] = (job, on_done)
        job.done.connect(self._finished)
        job.start()
        return ticket

    @Slot(int)
    def _finished(self, ticket: int) -> None:
        job, on_done = self._jobs.pop(ticket, (None, None))
        if job is None:
            return
        job.wait()
        on_done(job.result, job.error)
        job.deleteLater()

    def _set(self, **values) -> None:
        for key, value in values.items():
            setattr(self, "_" + key, value)
        self.changed.emit()

    # ---------- properties ----------
    @Property("QVariantList", notify=changed)
    def items(self):
        return self._items

    @Property(int, notify=changed)
    def total(self):
        return self._total

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property("QVariantList", notify=changed)
    def platforms(self):
        return [{"id": p, "label": PLATFORM_LABELS[p]} for p in self._platforms]

    @Property(str, notify=changed)
    def platform(self):
        return self._platform

    @Property(str, notify=changed)
    def gameTitle(self):
        return str(self._game.get("title") or "")

    @Property(str, notify=changed)
    def matchedGame(self):
        """The platform's game this search is scoped to, as shown to the user."""
        if self._platform == "modrinth":
            return "Minecraft"
        m = self._match()
        return str(m.get("name") or "") if m else ""

    @Property("QVariantList", notify=changed)
    def candidates(self):
        return self._candidates

    @Property(bool, notify=changed)
    def canLoadMore(self):
        return len(self._items) < self._total and not self._busy

    # ---------- actions ----------
    @Slot(str)
    def selectGame(self, game_id: str) -> None:
        game = dict(self._games.get(str(game_id)) or {})
        game.setdefault("game_id", str(game_id))
        platforms = mod_catalog.platforms_for(game) if game.get("title") else ["modrinth"]
        try:
            from native_services.game_mod_recipes import recipe_for
            recipe = recipe_for(str(game.get("title") or "")) or {}
        except Exception:
            recipe = {}
        if recipe.get("nexus_domain") and "nexus" in platforms:
            # Known game: its Nexus site is certain, no lookup or question needed.
            gid = str(game.get("game_id") or "")
            if gid and not self._load_matches().get(gid, {}).get("nexus"):
                self._save_match(gid, "nexus", {"domain": recipe["nexus_domain"], "name": recipe["titles"][0]})
        self._set(game=game, platforms=platforms, items=[], total=0, page=0, candidates=[], recipe=recipe)
        if game.get("pc") and not self.gameFolder:
            self._detect_game_folder()
        self.selectPlatform(platforms[0])

    @Slot(str)
    def selectPlatform(self, platform: str) -> None:
        if platform not in PLATFORM_LABELS:
            return
        if not self._platforms:
            self._platforms = ["modrinth"]
        self._set(platform=platform, items=[], total=0, page=0, candidates=[])
        if platform != "modrinth" and self._game.get("title") and self._match() is None:
            self._lookup_game()
        else:
            self.search(self._query)

    def _lookup_game(self) -> None:
        title, platform = str(self._game.get("title") or ""), self._platform
        finder = self._catalog.find_nexus_games if platform == "nexus" else self._catalog.find_gamebanana_games
        self._set(busy=True, status=f"Finding {title} on {PLATFORM_LABELS[platform]}…")

        def done(found, error):
            if error:
                self._set(busy=False, status=f"{PLATFORM_LABELS[platform]} is unreachable: {error}")
                return
            found = list(found or [])
            exact = [g for g in found if str(g.get("name", "")).casefold() == title.casefold()]
            if len(exact) == 1:
                self._remember(exact[0])
                return
            self._set(busy=False, candidates=found[:10],
                      status=(f"Which {PLATFORM_LABELS[platform]} game is {title}?" if found
                              else f"{title} was not found on {PLATFORM_LABELS[platform]}. Search all of it below."))
            if not found:
                self.search(self._query)
        self._run(lambda: finder(title), done)

    def _remember(self, candidate: dict) -> None:
        gid = str(self._game.get("game_id") or "")
        value = ({"domain": candidate.get("domain"), "name": candidate.get("name")} if self._platform == "nexus"
                 else {"id": candidate.get("id"), "name": candidate.get("name")})
        if gid:
            self._save_match(gid, self._platform, value)
        self._set(candidates=[])
        self.search(self._query)

    @Slot(int)
    def chooseCandidate(self, index: int) -> None:
        if 0 <= index < len(self._candidates):
            self._remember(self._candidates[index])

    @Slot()
    def changeGameMatch(self) -> None:
        gid = str(self._game.get("game_id") or "")
        data = self._load_matches()
        if gid in data and self._platform in data[gid]:
            del data[gid][self._platform]
            self._store_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self._set(items=[], total=0)
        self._lookup_game()

    @Slot(str)
    def search(self, query: str) -> None:
        self._query = str(query or "").strip()
        self._fetch(page=1)

    @Slot()
    def loadMore(self) -> None:
        if self.canLoadMore:
            self._fetch(page=self._page + 1)

    def _fetch(self, page: int) -> None:
        platform, query, match = self._platform, self._query, self._match() or {}
        cat = self._catalog
        if platform == "modrinth":
            fn = lambda: cat.search_modrinth(query, limit=20, offset=(page - 1) * 20)  # noqa: E731
        elif platform == "nexus":
            fn = lambda: cat.search_nexus(query, game_domain=match.get("domain"), count=20, offset=(page - 1) * 20)  # noqa: E731
        elif platform == "gamebanana":
            if not query and not match.get("id"):
                self._set(busy=False, status="Type a search to look through all of GameBanana.")
                return
            fn = lambda: cat.search_gamebanana(query, game_id=match.get("id"), page=page)  # noqa: E731
        else:
            return
        self._search_ticket += 1
        mine = self._search_ticket
        self._set(busy=True, status=f"Searching {PLATFORM_LABELS[platform]}…")

        def done(result, error):
            if mine != self._search_ticket:
                return  # a newer search replaced this one
            if error:
                self._set(busy=False, status=f"{PLATFORM_LABELS[platform]} search failed: {error}")
                return
            items = list((result or {}).get("items") or [])
            merged = (self._items + items) if page > 1 else items
            total = int((result or {}).get("total") or 0)
            where = self.matchedGame or PLATFORM_LABELS[platform]
            self._set(busy=False, items=merged, total=total, page=page,
                      status=(f"{total:,} mod{'s' if total != 1 else ''} for {where}" if total else f"No mods found on {PLATFORM_LABELS[platform]}."))
        self._run(fn, done)

    # ---------- phase 2: files, download, install ----------
    def _dl(self):
        if self._downloads is None:
            from native_services import mod_downloads
            self._downloads = mod_downloads
        return self._downloads

    def _inst(self):
        if self._installer is None:
            from native_services import emulator_mods
            self._installer = emulator_mods
        return self._installer

    def _cache_dir(self) -> Path:
        base = os.environ.get("XDG_CACHE_HOME") or str(self._home / ".cache")
        return Path(base) / "waystone/mods/downloads"

    def _journal_dir(self) -> Path:
        return self._store_path.parent / "install-journal"

    @Property("QVariantList", notify=changed)
    def files(self):
        return self._files

    @Property("QVariantMap", notify=changed)
    def filesFor(self):
        return self._files_for

    @Property(str, notify=changed)
    def downloadedPath(self):
        return self._download_path

    @Property(float, notify=changed)
    def progress(self):
        return self._progress

    @Property("QVariantMap", notify=changed)
    def installPlan(self):
        return self._install_plan

    @Property(bool, notify=changed)
    def canUndo(self):
        return bool(self._last_journal)

    @Property("QVariantList", notify=changed)
    def emulators(self):
        """Installed emulators that fit the selected game's platform (or all installed ones)."""
        platforms = " ".join(self._game.get("platforms") or [])
        wanted = [e for key, ems in PLATFORM_EMULATORS if key in platforms for e in ems] or list(EMULATOR_CONFIG_DIRS)
        seen, out = set(), []
        for e in wanted:
            if e not in seen and (self._home / EMULATOR_CONFIG_DIRS[e]).is_dir():
                seen.add(e)
                out.append({"id": e, "name": e.capitalize() if e != "azahar" else "Azahar"})
        return out

    @Slot(float)
    def _on_progress(self, value: float) -> None:
        self._set(progress=value)

    @Slot(int)
    def showFiles(self, index: int) -> None:
        if not 0 <= index < len(self._items):
            return
        item = self._items[index]
        dl = self._dl()
        if item.get("platform") == "nexus":
            nx = self._nx()
            key = nx.api_key()
            if not key:
                self._set(files=[], files_for=item, status="Add your Nexus API key first (Settings > Accounts).")
                return
            domain = str(item.get("url", "")).split("nexusmods.com/")[-1].split("/")[0]
            item = {**item, "domain": domain}
            fn = lambda: nx.mod_files(domain, item["mod_id"], key)  # noqa: E731
        elif item["platform"] == "gamebanana":
            fn = lambda: dl.gamebanana_files(item["mod_id"])  # noqa: E731
        else:
            fn = lambda: dl.modrinth_files(item["mod_id"])  # noqa: E731
        self._set(busy=True, files=[], files_for=item, install_plan={}, status=f"Reading files for {item['title']}…")

        def done(result, error):
            if error:
                self._set(busy=False, status=f"Could not list files: {error}")
                return
            files = list(result or [])
            self._set(busy=False, files=files, status=(f"{len(files)} file(s) for {item['title']}" if files else "This mod has no downloadable files."))
        self._run(fn, done)

    @Slot(int)
    def download(self, index: int) -> None:
        if not 0 <= index < len(self._files):
            return
        f = self._files[index]
        item = self._files_for
        dest = self._cache_dir() / str(item.get("platform", "mod")) / str(item.get("mod_id", "x"))
        dl = self._dl()

        def progress(done_bytes, total):
            self._progressed.emit((done_bytes / total) if total else 0.0)

        self._set(busy=True, progress=0.0, install_plan={}, status=f"Downloading {f['name']}…")

        def done(path, error):
            if error:
                self._set(busy=False, status=f"Download failed: {error}")
                return
            self._set(busy=False, download_path=str(path), progress=1.0, status=f"Downloaded {f['name']}. Choose where to install it.")
        self._run(lambda: dl.download(f["url"], dest, filename=f["name"], md5=f.get("md5", ""), sha1=f.get("sha1", ""), progress=progress), done)

    @Slot(str, str)
    def previewInstall(self, emulator: str, title_id: str) -> None:
        if not self._download_path:
            return
        name = str(self._files_for.get("title") or Path(self._download_path).stem)
        try:
            plan = self._inst().plan_install(self._download_path, emulator, title_id=title_id.strip(), mod_name=name, home=self._home)
        except Exception as exc:
            self._set(install_plan={}, status=f"Cannot install this into {emulator}: {exc}")
            return
        conflicts = len(plan.get("conflicts") or [])
        self._set(install_plan=plan, status=(f"{len(plan['files'])} file(s) -> {plan['target_root']}"
                                             + (f" · {conflicts} existing file(s) would be replaced: not installing" if conflicts else "")))

    @Slot()
    def applyInstall(self) -> None:
        plan = self._install_plan
        if not plan or plan.get("conflicts"):
            return
        try:
            result = self._inst().apply_install(plan, journal_dir=self._journal_dir())
        except Exception as exc:
            self._set(status=f"Install failed, nothing kept: {exc}")
            return
        self._set(install_plan={}, last_journal=result["journal"], status=f"Installed {result['written']} file(s). Undo is available.")

    @Slot()
    def undoInstall(self) -> None:
        if not self._last_journal:
            return
        try:
            result = self._inst().undo_install(self._last_journal)
        except Exception as exc:
            self._set(status=f"Undo failed: {exc}")
            return
        kept = result.get("kept_changed") or []
        self._set(last_journal="", status=f"Removed {result['removed']} file(s)" + (f"; kept {len(kept)} you changed" if kept else "."))

    # ---------- Nexus: nxm:// links (free accounts download only through these) ----------
    def _nx(self):
        if self._nexus is None:
            from native_services import nexus_client
            self._nexus = nexus_client
        return self._nexus

    def _gm(self):
        if self._game_mods is None:
            from native_services import game_mods
            self._game_mods = game_mods
        return self._game_mods

    @Slot(int)
    def openNexusDownload(self, index: int) -> None:
        """Premium accounts: download straight through the API. Otherwise (or if Nexus refuses) open the file on
        nexusmods.com; its 'Mod manager download' button sends an nxm:// link back here."""
        if not 0 <= index < len(self._files):
            return
        item, f = self._files_for, self._files[index]
        domain, mod_id, file_id = str(item.get("domain")), item.get("mod_id"), f["file_id"]
        if not self._nx().api_key():
            self._open_nexus_page(domain, mod_id, file_id)
            return
        self._fetch_nexus(domain, mod_id, file_id, title=str(item.get("title") or ""),
                          on_refused=lambda: self._open_nexus_page(domain, mod_id, file_id))

    def _open_nexus_page(self, domain, mod_id, file_id) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        url = f"https://www.nexusmods.com/{domain}/mods/{mod_id}?tab=files&file_id={file_id}&nmm=1"
        QDesktopServices.openUrl(QUrl(url))
        self._set(busy=False, status="On Nexus, press 'Mod manager download'; Waystone picks it up from there.")

    def _fetch_nexus(self, domain, mod_id, file_id, *, nxm_key: str = "", expires: str = "", title: str = "",
                     on_refused=None) -> None:
        nx = self._nx()
        key = nx.api_key()
        self._set(busy=True, install_plan={}, download_path="", progress=0.0, status="Nexus download starting…")
        dest = self._cache_dir() / "nexus" / f"{domain}-{mod_id}"
        dl = self._dl()

        def progress(done_bytes, total):
            self._progressed.emit((done_bytes / total) if total else 0.0)

        def work():
            files = nx.mod_files(domain, mod_id, key)
            meta = next((f for f in files if str(f["file_id"]) == str(file_id)), {"name": f"nexus-{file_id}.zip", "description": ""})
            try:
                cdn = nx.download_url(domain, mod_id, file_id, key, nxm_key=nxm_key, expires=expires)
            except Exception as exc:
                if on_refused is not None:      # free account: Nexus only hands out links via 'Mod manager download'
                    return None, {"refused": str(exc)}
                raise
            return dl.download(cdn, dest, filename=meta["name"], progress=progress), meta

        def done(result, error):
            if error:
                self._set(busy=False, status=f"Nexus download failed: {error}")
                return
            path, meta = result
            if path is None:
                self._set(busy=False)
                on_refused()
                return
            item = {"platform": "nexus", "mod_id": str(mod_id), "domain": domain,
                    "title": title or (meta.get("description") or meta["name"]).split(" · ")[0]}
            self._set(busy=False, files_for=item, files=[], download_path=str(path), progress=1.0,
                      status=f"Downloaded {meta['name']} from Nexus. Choose the game folder to install into.")
        self._run(work, done)

    @Slot()
    def processInbox(self) -> None:
        if not self._inbox or not self._inbox.is_dir():
            return
        for entry in sorted(self._inbox.glob("*.nxm")):
            try:
                url = entry.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            entry.unlink(missing_ok=True)
            self.handleNxm(url)

    @Slot(str)
    def handleNxm(self, url: str) -> None:
        nx = self._nx()
        try:
            link = nx.parse_nxm(url)
        except Exception as exc:
            self._set(status=f"Ignored a Nexus link: {exc}")
            return
        key = nx.api_key()
        if not key:
            self._set(status="A Nexus download arrived, but no Nexus API key is set (Settings > Accounts).")
            return
        self.nexusLinkReceived.emit()
        self._fetch_nexus(link["domain"], link["mod_id"], link["file_id"], nxm_key=link["key"], expires=link["expires"])

    @Property(str, notify=changed)
    def gameFolder(self):
        gid = str(self._game.get("game_id") or "")
        return str(self._load_matches().get(gid, {}).get("game_dir", {}).get("path", "")) if gid else ""

    @Slot(str)
    def setGameFolder(self, url_or_path: str) -> None:
        from PySide6.QtCore import QUrl
        path = QUrl(url_or_path).toLocalFile() if url_or_path.startswith("file:") else url_or_path
        gid = str(self._game.get("game_id") or "")
        if gid and path:
            self._save_match(gid, "game_dir", {"path": path})
            self.changed.emit()

    @Slot(str)
    def previewGameInstall(self, game_dir: str) -> None:
        if not self._download_path:
            return
        try:
            plan = self._gm().plan_game_install(self._download_path, game_dir or self.gameFolder,
                                                mod_name=str(self._files_for.get("title") or ""))
        except Exception as exc:
            self._set(install_plan={}, status=f"Cannot install this here: {exc}")
            return
        conflicts = len(plan.get("conflicts") or [])
        self._set(install_plan=plan, status=(f"{len(plan['files'])} file(s) -> {plan['target_root']}"
                                             + (f" · {conflicts} existing file(s) would be replaced: not installing" if conflicts else "")))

    @Slot()
    def shutdown(self) -> None:
        """Wait for in-flight network jobs so the app never exits with a running QThread."""
        for job, _ in list(self._jobs.values()):
            job.wait(5000)
        self._jobs.clear()

    # ---------- finding where the game is installed ----------
    def _find_installs(self) -> list[dict]:
        if getattr(self, "_installs", None) is None:
            from native_services import game_installs
            root = ""
            try:
                root = json.loads((self._home / ".local/share/waystone/install-settings.json").read_text()).get("install_root", "")
            except (OSError, ValueError):
                pass
            self._installs = game_installs.find_installs(self._home, waystone_root=root or None, scan_mounts=True)
        return self._installs

    def _detect_game_folder(self) -> None:
        game = dict(self._game)
        gid, title = str(game.get("game_id") or ""), str(game.get("title") or "")
        if not gid or not title:
            return

        def find():
            from native_services import game_installs
            return game_installs.match_install(title, self._find_installs())

        def done(matches, error):
            if error or not matches or str(self._game.get("game_id")) != gid:
                return
            # Prefer a copy Proton has already set up (it has a prefix): that is the one the user plays.
            best = sorted(matches, key=lambda m: (not m.get("installed"), not m.get("prefix")))[0]
            self._save_match(gid, "game_dir", {"path": best["install_dir"], "prefix": best.get("prefix", ""),
                                               "store": best.get("store", ""), "detected": True})
            self._set(status=self._status + f"  ·  found {title} in {best['install_dir']}")
        self._run(find, done)

    @Property(str, notify=changed)
    def gamePrefix(self):
        gid = str(self._game.get("game_id") or "")
        return str(self._load_matches().get(gid, {}).get("game_dir", {}).get("prefix", "")) if gid else ""

    # ---------- Nexus account (Settings > Accounts) ----------
    @Property(str, notify=changed)
    def nexusAccount(self):
        return getattr(self, "_nexus_account", "")

    @Slot()
    def refreshNexusAccount(self) -> None:
        nx = self._nx()

        def check():
            key = nx.api_key()
            return nx.validate(key) if key else None

        def done(info, error):
            if error:
                text = f"Key saved, but Nexus did not answer: {error}" if nx.api_key() else "Not connected"
            elif not info:
                text = "Not connected"
            else:
                text = f"Connected as {info['name']}" + (" (Premium: Nexus files download directly)" if info.get("premium") else " (free: downloads go through 'Mod manager download')")
            self._nexus_account = text
            self.changed.emit()
        self._run(check, done)

    @Slot(str)
    def setNexusKey(self, key: str) -> None:
        nx = self._nx()
        key = (key or "").strip()

        def work():
            info = nx.validate(key)        # only keep keys Nexus accepts
            nx.store_api_key(key)
            return info

        def done(info, error):
            self._nexus_account = (f"Connected as {info['name']}" + (" (Premium)" if info.get("premium") else "")) if not error else f"Not saved: {error}"
            self.changed.emit()
        self._nexus_account = "Checking the key with Nexus…"
        self.changed.emit()
        self._run(work, done)

    @Slot()
    def removeNexusKey(self) -> None:
        self._nx().clear_api_key()
        self._nexus_account = "Not connected"
        self.changed.emit()

    @Property("QVariantMap", notify=changed)
    def recipe(self):
        """What this game needs to be modded (frameworks, launch option, which manager)."""
        return self._recipe

    @Slot(str)
    def copyText(self, text: str) -> None:
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)
        self._set(status="Copied. In Steam: right-click the game > Properties > Launch options, paste.")
