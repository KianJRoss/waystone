"""Proton versions (W19): see what is installed and who uses it, install the latest GE-Proton, switch games over, remove.

Kian 2026-11-25: "I'd also like if we could update proton".

- Installed runtimes: Steam's compatibilitytools.d (GE-Proton and other custom tools) and Steam's own Proton builds.
- Users of each runtime: Steam's per-game CompatToolMapping (config.vdf), Heroic's per-game wineVersion, Waystone's
  launch profiles (argv/env/epic runtime paths). A runtime with users is never removed.
- Install: the official GitHub release of GloriousEggroll/proton-ge-custom, x86_64 tarball, verified against the
  release's .sha512sum, unpacked into a temporary folder in the same directory (only members under one top folder,
  no links outside it), then renamed into place in one step. Needs free space for download + unpack.
- Switch: Heroic game configs and Waystone launch profiles from one runtime to another, each file backed up first.
  Steam's own mapping is not edited (Steam rewrites config.vdf while it runs); Steam users are reported.
Every network and filesystem boundary is injectable for tests.
"""
from __future__ import annotations

import hashlib
from itertools import chain
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import time
from typing import Callable, Iterable
from urllib.parse import urlsplit
from native_services import waystone_paths as _wpaths

GE_RELEASES = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases?per_page=30"
ALLOWED_DOWNLOAD_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com",
                          "release-assets.githubusercontent.com"}
_GE_NAME = re.compile(r"GE-Proton(\d+)-(\d+)")


def ge_version(name: str) -> tuple[int, int] | None:
    m = _GE_NAME.search(name or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


class Paths:
    def __init__(self, home: str | Path | None = None):
        h = Path(home).expanduser() if home else Path.home()
        self.home = h
        self.steam = h / ".local/share/Steam"
        self.compat = self.steam / "compatibilitytools.d"
        self.steam_config = self.steam / "config/config.vdf"
        self.heroic_games = h / ".config/heroic/GamesConfig"
        self.heroic_config = h / ".config/heroic/config.json"
        self.launch_profiles = _wpaths.store("launch-profiles-v1", "launch_profiles.json", home=h)


# ---------------- what is installed, and who uses it ----------------

_COMPAT_NAME = re.compile(r'"compat_tools"\s*\{\s*"([^"]+)"')


def steam_tool_name(tool_dir: Path) -> str:
    """The name Steam stores in CompatToolMapping: the key under compat_tools in compatibilitytool.vdf (often, but
    not always, the folder name)."""
    try:
        text = (tool_dir / "compatibilitytool.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return tool_dir.name
    m = _COMPAT_NAME.search(text)
    return m.group(1) if m else tool_dir.name


def steam_builtin_name(folder: str) -> str:
    """Steam's internal name for its own Proton builds: 'Proton - Experimental' -> proton_experimental,
    'Proton Hotfix' -> proton_hotfix, 'Proton 9.0 (Beta)' -> proton_9, 'Proton 6.3' -> proton_63."""
    low = folder.casefold()
    if "experimental" in low:
        return "proton_experimental"
    if "hotfix" in low:
        return "proton_hotfix"
    m = re.search(r"proton\s+(\d+)\.(\d+)", low)
    if m:
        return f"proton_{m.group(1)}" if m.group(2) == "0" else f"proton_{m.group(1)}{m.group(2)}"
    return folder


def installed_runtimes(paths: Paths) -> list[dict]:
    out = []
    if paths.compat.is_dir():
        for d in sorted(paths.compat.iterdir()):
            if d.is_dir() and (d / "proton").exists():
                out.append({"name": d.name, "path": str(d), "kind": "ge" if ge_version(d.name) else "custom",
                            "steamName": steam_tool_name(d)})
    common = paths.steam / "steamapps/common"
    if common.is_dir():
        for d in sorted(common.glob("Proton*")):
            if (d / "proton").exists():
                out.append({"name": d.name, "path": str(d), "kind": "steam", "steamName": steam_builtin_name(d.name)})
    return out


def steam_users(paths: Paths) -> dict[str, int]:
    """Count Steam games per compat tool name in config.vdf's CompatToolMapping (read-only)."""
    try:
        text = paths.steam_config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    start = text.find('"CompatToolMapping"')
    if start < 0:
        return {}
    depth, i, end = 0, text.find("{", start), None
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                end = j
                break
    block = text[i:end or len(text)]
    counts: dict[str, int] = {}
    for name in re.findall(r'"name"\s+"([^"]*)"', block):
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def heroic_users(paths: Paths) -> dict[str, list[str]]:
    users: dict[str, list[str]] = {}
    try:
        default = json.loads(paths.heroic_config.read_text(encoding="utf-8")).get("defaultSettings", {}).get("wineVersion")
    except (OSError, ValueError, AttributeError):
        default = None
    if isinstance(default, dict) and default.get("bin"):
        users.setdefault(Path(default["bin"]).parent.name, []).append("Heroic default for new games")
    for f in sorted(paths.heroic_games.glob("*.json")) if paths.heroic_games.is_dir() else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for game, cfg in data.items() if isinstance(data, dict) else []:
            wine = cfg.get("wineVersion") if isinstance(cfg, dict) else None
            if isinstance(wine, dict) and wine.get("bin"):
                name = Path(wine["bin"]).parent.name
                users.setdefault(name, []).append(game)
    return users


def waystone_users(paths: Paths) -> dict[str, list[str]]:
    users: dict[str, list[str]] = {}
    try:
        data = json.loads(paths.launch_profiles.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return users
    for prof in data.get("profiles", []):
        blob = json.dumps(prof)
        for m in set(re.findall(r"compatibilitytools\.d/([^/\"\\]+)", blob)) | set(re.findall(r"steamapps/common/(Proton[^/\"\\]*)", blob)):
            users.setdefault(m, []).append(prof.get("display_name") or prof.get("profile_id", "?"))
    return users


def overview(paths: Paths) -> list[dict]:
    steam, heroic, waystone = steam_users(paths), heroic_users(paths), waystone_users(paths)
    rows = []
    for r in installed_runtimes(paths):
        n = r["name"]
        steam_name = r.pop("steamName", n)
        steam_count = steam.get(n, 0) + (steam.get(steam_name, 0) if steam_name != n else 0)
        r = dict(r, steam=steam_count, heroic=heroic.get(n, []), waystone=waystone.get(n, []))
        r["used"] = bool(r["steam"] or r["heroic"] or r["waystone"])
        rows.append(r)
    ge = [r for r in rows if r["kind"] == "ge"]
    newest = max((ge_version(r["name"]) for r in ge), default=None)
    for r in rows:
        r["newestInstalled"] = r["kind"] == "ge" and ge_version(r["name"]) == newest
    return rows


# ---------------- the latest release ----------------

def _get_json(url: str, timeout: float = 15):
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Waystone"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2_000_000))


def latest_ge(get_json: Callable[[str], object] = _get_json) -> dict | None:
    """{'tag', 'tarball', 'size', 'sha512_url'} for the newest x86_64 GE-Proton release."""
    releases = ge_releases(get_json, limit=1)
    return releases[0] if releases else None


def ge_releases(get_json: Callable[[str], object] = _get_json, limit: int = 15) -> list[dict]:
    """Recent installable GE-Proton releases, newest first (for rolling a game back to an older version)."""
    out = []
    for release in get_json(GE_RELEASES) or []:
        if len(out) >= limit:
            break
        if release.get("draft") or release.get("prerelease"):
            continue
        assets = {a.get("name"): a for a in release.get("assets", [])}
        tar = next((a for n, a in assets.items() if n and n.endswith("x86_64.tar.gz")), None) or \
            next((a for n, a in assets.items() if n and n.endswith(".tar.gz") and "aarch64" not in n), None)
        if not tar:
            continue
        sha = assets.get(tar["name"].replace(".tar.gz", ".sha512sum"))
        if not sha:
            continue
        if ge_version(release.get("tag_name", "")) is None:
            continue
        out.append({"tag": release.get("tag_name", ""), "tarball": tar["browser_download_url"],
                    "size": int(tar.get("size", 0)), "sha512_url": sha["browser_download_url"]})
    return out


# ---------------- install ----------------

class InstallError(RuntimeError):
    pass


def _download(url: str, dest: Path, progress: Callable[[int, int], None], cancelled: Callable[[], bool],
              total: int = 0) -> None:
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise InstallError("Download host is not allowed.")
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Waystone"})
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        if urlsplit(r.geturl()).hostname not in ALLOWED_DOWNLOAD_HOSTS:
            raise InstallError("Download was redirected to a host that is not allowed.")
        total = total or int(r.headers.get("Content-Length") or 0)
        while True:
            if cancelled():
                raise InstallError("Cancelled.")
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            progress(done, total)


def _safe_extract(tar_path: Path, into: Path) -> str:
    """Extract members that all live under one top folder, refusing absolute paths, '..' and links that escape."""
    with tarfile.open(tar_path, "r:gz") as tf:
        members = tf.getmembers()
        tops = {Path(m.name).parts[0] for m in members if m.name and Path(m.name).parts}
        if len(tops) != 1:
            raise InstallError("Unexpected archive layout.")
        top = tops.pop()
        for m in members:
            p = Path(m.name)
            if p.is_absolute() or ".." in p.parts:
                raise InstallError("Unsafe path in archive.")
            if m.issym() or m.islnk():
                target = (into / p.parent / m.linkname) if m.issym() else (into / m.linkname)
                if not str(target.resolve()).startswith(str(into.resolve())):
                    raise InstallError("Link in archive points outside it.")
            if m.isdev():
                raise InstallError("Device file in archive.")
        tf.extractall(into, filter="tar") if hasattr(tarfile, "data_filter") else tf.extractall(into)
    return top


def install_ge(release: dict, paths: Paths, *, progress: Callable[[str, int, int], None] = lambda *a: None,
               cancelled: Callable[[], bool] = lambda: False, download: Callable = _download,
               fetch_text: Callable[[str], str] | None = None, free_bytes: Callable[[Path], int] | None = None) -> str:
    """Install a GE-Proton release into compatibilitytools.d. Returns the installed folder name."""
    paths.compat.mkdir(parents=True, exist_ok=True)
    tag = release["tag"]
    if ge_version(tag) is None:
        raise InstallError("Not a GE-Proton release.")
    existing = [r["name"] for r in installed_runtimes(paths) if r["kind"] == "ge" and ge_version(r["name"]) == ge_version(tag)]
    if existing:
        return existing[0]
    need = release.get("size", 0) * 4          # tarball + unpacked (~3x) with margin
    free = (free_bytes or (lambda p: shutil.disk_usage(p).free))(paths.compat)
    if need and free < need:
        raise InstallError(f"Not enough free space: needs about {need // 1024**3 + 1} GB.")
    fetch_text = fetch_text or (lambda url: _fetch_small(url))
    expected = fetch_text(release["sha512_url"]).split()[0].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{128}", expected):
        raise InstallError("The release checksum could not be read.")
    with tempfile.TemporaryDirectory(prefix=".waystone-proton-", dir=paths.compat) as tmp:
        tmpdir = Path(tmp)
        tarball = tmpdir / "proton.tar.gz"
        download(release["tarball"], tarball, lambda d, t: progress("download", d, t), cancelled, release.get("size", 0))
        progress("verify", 0, 0)
        h = hashlib.sha512()
        with open(tarball, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != expected:
            raise InstallError("Checksum mismatch: the download is not the official release. Nothing was installed.")
        if cancelled():
            raise InstallError("Cancelled.")
        progress("unpack", 0, 0)
        unpack = tmpdir / "unpack"
        unpack.mkdir()
        top = _safe_extract(tarball, unpack)
        if not (unpack / top / "proton").exists():
            raise InstallError("The archive does not contain a Proton build.")
        final = paths.compat / top
        if final.exists():
            return top
        os.replace(unpack / top, final)
    return top


def _fetch_small(url: str) -> str:
    if urlsplit(url).hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise InstallError("Checksum host is not allowed.")
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Waystone"}), timeout=30) as r:
        return r.read(4096).decode("utf-8", "replace")


# ---------------- switch games and remove ----------------

def _backup(path: Path) -> None:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}-proton-switch"))


def switch_games(paths: Paths, old: str, new: str) -> dict:
    """Point Heroic games and Waystone launch profiles that use `old` at `new`. Steam mapping is left to Steam."""
    new_dir = paths.compat / new
    if not (new_dir / "proton").exists():
        raise InstallError(f"{new} is not installed.")
    changed = {"heroic": [], "waystone": []}
    if paths.heroic_games.is_dir():
        for f in sorted(paths.heroic_games.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            touched = False
            for game, cfg in data.items() if isinstance(data, dict) else []:
                wine = cfg.get("wineVersion") if isinstance(cfg, dict) else None
                if isinstance(wine, dict) and wine.get("bin") and Path(wine["bin"]).parent.name == old:
                    wine.update({"bin": str(new_dir / "proton"), "name": new, "type": "proton"})
                    changed["heroic"].append(game)
                    touched = True
            if touched:
                _backup(f)
                f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        heroic_config = json.loads(paths.heroic_config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        heroic_config = None
    default = heroic_config.get("defaultSettings", {}).get("wineVersion") if isinstance(heroic_config, dict) else None
    if isinstance(default, dict) and default.get("bin") and Path(default["bin"]).parent.name == old:
        default.update({"bin": str(new_dir / "proton"), "name": new, "type": "proton"})
        _backup(paths.heroic_config)
        paths.heroic_config.write_text(json.dumps(heroic_config, indent=2), encoding="utf-8")
        changed["heroic"].append("Heroic default for new games")
    if paths.launch_profiles.is_file():
        text = paths.launch_profiles.read_text(encoding="utf-8")
        old_path = str(paths.compat / old)
        if old_path in text:
            data = json.loads(text)
            for prof in data.get("profiles", []):
                blob = json.dumps(prof)
                if old_path in blob:
                    changed["waystone"].append(prof.get("display_name") or prof.get("profile_id"))
            _backup(paths.launch_profiles)
            paths.launch_profiles.write_text(text.replace(old_path, str(new_dir)), encoding="utf-8")
    steam, steam_name = steam_users(paths), steam_tool_name(paths.compat / old)
    changed["steam"] = steam.get(old, 0) + (steam.get(steam_name, 0) if steam_name != old else 0)
    return changed


def remove_runtime(paths: Paths, name: str) -> None:
    rows = {r["name"]: r for r in overview(paths)}
    row = rows.get(name)
    if row is None or row["kind"] == "steam":
        raise InstallError("Only Proton versions in Steam's compatibility tools folder can be removed here.")
    if row["used"]:
        raise InstallError("Games still use this version; switch them first.")
    target = Path(row["path"]).resolve()
    if target.parent != paths.compat.resolve():
        raise InstallError("Refusing to remove a folder outside compatibilitytools.d.")
    shutil.rmtree(target)


# ---------------- per-game choice (W19b) ----------------
# Kian 2026-11-25: "a version picker in case one version breaks it for whatever reason".
# A game's Proton lives in a different place per launcher:
#   steam    -> config.vdf CompatToolMapping["<appid>"]["name"] (Steam's internal tool name; no entry = Steam default)
#   heroic   -> GamesConfig/<appName>.json [appName]["wineVersion"] (Epic/GOG/Amazon through Heroic)
#   waystone -> the Proton path inside a Waystone launch profile (e.g. the Nolvus MO2 launcher)

STORES = ("steam", "heroic", "waystone")


def _match_brace(text: str, open_idx: int) -> int:
    """Index of the '}' closing the '{' at open_idx, skipping quoted strings."""
    depth, i, quoted = 0, open_idx, False
    while i < len(text):
        c = text[i]
        if quoted:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("Unbalanced braces in config.vdf")


def _block_entries(text: str, open_idx: int, close_idx: int) -> dict[str, tuple[int, int, int]]:
    """Direct children of a VDF block: key -> (key quote start, '{' index, '}' index)."""
    entries, i = {}, open_idx + 1
    key_re = re.compile(r'"((?:[^"\\]|\\.)*)"\s*')
    while i < close_idx:
        m = key_re.search(text, i, close_idx)
        if not m:
            break
        after = m.end()
        if after < close_idx and text[after] == "{":
            end = _match_brace(text, after)
            entries[m.group(1)] = (m.start(), after, end)
            i = end + 1
        else:
            value = key_re.match(text, after)      # "key" "value" pair: skip the value
            i = value.end() if value else after
    return entries


def _compat_block(text: str) -> tuple[int, int] | None:
    start = text.find('"CompatToolMapping"')
    if start < 0:
        return None
    open_idx = text.find("{", start)
    return open_idx, _match_brace(text, open_idx)


def steam_mapping(paths: Paths) -> dict[str, str]:
    """app id -> Steam internal tool name from CompatToolMapping ('0' is Steam's global default)."""
    try:
        text = paths.steam_config.read_text(encoding="utf-8", errors="replace")
        block = _compat_block(text)
        if block is None:
            return {}
        out = {}
        for appid, (_k, o, c) in _block_entries(text, *block).items():
            m = re.search(r'"name"\s+"([^"]*)"', text[o:c])
            if m and m.group(1):
                out[appid] = m.group(1)
        return out
    except (OSError, ValueError):
        return {}


def set_steam_mapping_text(text: str, appid: str, tool: str | None) -> str:
    """Return config.vdf text with only this app's CompatToolMapping entry set (or removed when tool is None)."""
    if not str(appid).isdigit():
        raise InstallError("Not a Steam app id.")
    block = _compat_block(text)
    if block is None:
        raise InstallError("Steam has no compatibility settings yet: pick a Proton for any game in Steam once, "
                           "then try again.")
    open_idx, close_idx = block
    entry = _block_entries(text, open_idx, close_idx).get(str(appid))
    if entry is not None:
        key_start, o, c = entry
        if tool is None:
            line_start = text.rfind("\n", 0, key_start) + 1
            line_end = text.find("\n", c)
            line_end = len(text) if line_end < 0 else line_end + 1
            return text[:line_start] + text[line_end:]
        body = text[o:c]
        m = re.search(r'("name"\s+")([^"]*)(")', body)
        if m:
            body = body[:m.start(2)] + tool + body[m.end(2):]
        else:
            body = '{\n\t"name"\t\t"%s"%s' % (tool, body[1:])
        return text[:o] + body + text[c:]
    if tool is None:
        return text
    line_start = text.rfind("\n", 0, close_idx) + 1
    indent = text[line_start:close_idx] if not text[line_start:close_idx].strip() else ""
    inner = indent + "\t"
    entry_text = (f'{inner}"{appid}"\n{inner}{{\n{inner}\t"name"\t\t"{tool}"\n{inner}\t"config"\t\t""\n'
                  f'{inner}\t"priority"\t\t"250"\n{inner}}}\n')
    return text[:line_start] + entry_text + text[line_start:]


def _procs() -> Iterable[tuple[str, str, str]]:
    """(comm, command line, executable path) for every process we can see."""
    proc = Path("/proc")
    # This adapter is specifically for Linux procfs. Other platforms have no
    # /proc tree; an unavailable snapshot is empty rather than an install crash.
    try:
        processes = proc.iterdir()
        first = next(processes, None)
    except OSError:
        return
    for d in (chain(()) if first is None else chain((first,), processes)):
        if not d.name.isdigit():
            continue
        try:
            comm = (d / "comm").read_text().strip()
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        try:
            exe = os.readlink(d / "exe")
        except OSError:
            exe = ""
        yield comm, cmd, exe


def steam_running(procs: Callable[[], Iterable[tuple[str, str, str]]] = _procs) -> bool:
    return any(comm == "steam" for comm, _cmd, _exe in procs())


def game_running(procs: Callable[[], Iterable[tuple[str, str, str]]] = _procs) -> bool:
    """A game is running: a Windows program under Wine/Proton (its process runs Wine's preloader), or anything
    Steam launched (its reaper carries SteamLaunch). A lingering wineserver alone does not count."""
    return any(Path(exe).name in ("wine64-preloader", "wine-preloader", "wine64", "wine") or "SteamLaunch" in cmd
               for _comm, cmd, exe in procs())


def close_steam(*, run: Callable[[list[str]], object] | None = None, is_running: Callable[[], bool] = steam_running,
                game_is_running: Callable[[], bool] = game_running, timeout: float = 60,
                sleep: Callable[[float], None] = time.sleep) -> None:
    """Ask Steam to quit and wait for it. Refuses while a game runs."""
    if game_is_running():
        raise InstallError("A game is running. Close it first.")
    if not is_running():
        return
    steam = shutil.which("steam") or str(Path.home() / ".local/bin/steam")
    if run is None:
        import subprocess
        run = lambda argv: subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, start_new_session=True)
    run([steam, "-shutdown"])
    waited = 0.0
    while is_running():
        if waited >= timeout:
            raise InstallError("Steam did not close. Close it yourself, then try again.")
        sleep(0.5)
        waited += 0.5


def _runtime_by_dir(paths: Paths) -> dict[str, dict]:
    return {r["name"]: r for r in installed_runtimes(paths)}


def _name_for_steam_tool(paths: Paths, tool: str) -> str:
    for r in installed_runtimes(paths):
        if r.get("steamName", r["name"]) == tool or r["name"] == tool:
            return r["name"]
    return tool


def _heroic_game_file(paths: Paths, app_name: str) -> Path:
    if not app_name or "/" in app_name or app_name.startswith("."):
        raise InstallError("Not a Heroic game id.")
    return paths.heroic_games / f"{app_name}.json"


def _heroic_default(paths: Paths) -> dict | None:
    try:
        wine = json.loads(paths.heroic_config.read_text(encoding="utf-8")).get("defaultSettings", {}).get("wineVersion")
    except (OSError, ValueError, AttributeError):
        return None
    return wine if isinstance(wine, dict) and wine.get("bin") else None


def _profile(paths: Paths, profile_id: str) -> tuple[dict, dict]:
    try:
        data = json.loads(paths.launch_profiles.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InstallError("Waystone's launch settings could not be read.") from None
    prof = next((p for p in data.get("profiles", []) if p.get("profile_id") == profile_id), None)
    if prof is None:
        raise InstallError("This game's launch entry was not found.")
    return data, prof


def _profile_strings(prof: dict) -> list[str]:
    env = prof.get("env") if isinstance(prof.get("env"), dict) else {}
    return [str(a) for a in prof.get("argv_template") or []] + [str(v) for v in env.values()]


def _profile_runtime(paths: Paths, prof: dict) -> str | None:
    dirs = _runtime_by_dir(paths)
    for value in _profile_strings(prof):
        for name, r in dirs.items():
            if value == r["path"] or value.startswith(r["path"] + "/"):
                return name
        m = re.search(r"compatibilitytools\.d/([^/]+)", value) or re.search(r"steamapps/common/(Proton[^/]*)", value)
        if m:
            return m.group(1)
    return None


def profile_for_target(paths: Paths, target_id: str) -> str | None:
    """Waystone launch profile used by a library target (game_associations), if it runs a Proton."""
    try:
        data = json.loads(paths.launch_profiles.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = (data.get("game_associations") or {}).get(target_id)
    prof = next((p for p in data.get("profiles", []) if p.get("profile_id") == pid), None)
    return pid if prof is not None and _profile_runtime(paths, prof) else None


def heroic_installed(paths: Paths, app_name: str) -> bool:
    """Heroic runs this game (installed through Heroic's Legendary, gogdl or nile)."""
    h = paths.home / ".config/heroic"
    try:
        if app_name in json.loads((h / "legendaryConfig/legendary/installed.json").read_text(encoding="utf-8")):
            return True
    except (OSError, ValueError, TypeError):
        pass
    for f in (h / "gog_store/installed.json", h / "nile_config/nile/installed.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = data.get("installed", []) if isinstance(data, dict) else data
        if any(isinstance(i, dict) and app_name in (i.get("appName"), i.get("id")) for i in items or []):
            return True
    return False


def ref_for_launch(paths: Paths, provider: str, product_id: str, target_id: str = "") -> tuple[str, str] | None:
    """Which launcher setting decides this library entry's Proton: ('steam', appid), ('heroic', appName) or
    ('waystone', profile_id); None when it does not run through Proton here."""
    prov = (provider or "").casefold()
    pid = str(product_id or "").strip()
    if "steam" in prov and pid.isdigit():
        return "steam", pid
    if prov in ("epic", "gog", "amazon", "heroic-epic", "heroic-gog", "heroic-amazon") and pid \
            and heroic_installed(paths, pid):
        return "heroic", pid
    if target_id:
        profile = profile_for_target(paths, target_id)
        if profile:
            return "waystone", profile
    return None


def game_runtime(paths: Paths, store: str, game_id: str) -> dict:
    """What a game runs with now: {store, current, default, hasDefault, options, note}. `current` is a runtime folder
    name, or '' when the game follows the default."""
    rows = installed_runtimes(paths)
    options = [r["name"] for r in rows]
    if store == "steam":
        mapping = steam_mapping(paths)
        tool = mapping.get(str(game_id), "")
        default = _name_for_steam_tool(paths, mapping["0"]) if mapping.get("0") else "Steam's choice"
        return {"store": store, "current": _name_for_steam_tool(paths, tool) if tool else "", "default": default,
                "hasDefault": True, "options": options, "note": ""}
    if store == "heroic":
        default = _heroic_default(paths)
        default_name = Path(default["bin"]).parent.name if default else ""
        current = ""
        try:
            data = json.loads(_heroic_game_file(paths, game_id).read_text(encoding="utf-8"))
            wine = (data.get(game_id) or {}).get("wineVersion") if isinstance(data, dict) else None
            if isinstance(wine, dict) and wine.get("bin"):
                current = Path(wine["bin"]).parent.name
        except (OSError, ValueError):
            pass
        if current and current == default_name:
            current = ""
        if default and default.get("type") not in ("proton", None) and default_name not in options:
            default_name = default.get("name") or default_name
        return {"store": store, "current": current, "default": default_name or "Heroic's default",
                "hasDefault": True, "options": options, "note": ""}
    if store == "waystone":
        _data, prof = _profile(paths, game_id)
        return {"store": store, "current": _profile_runtime(paths, prof) or "", "default": "", "hasDefault": False,
                "options": options, "note": ""}
    raise InstallError("This game's launcher does not use Proton.")


def set_game_runtime(paths: Paths, store: str, game_id: str, name: str | None, *,
                     is_steam_running: Callable[[], bool] = steam_running) -> str:
    """Point one game at runtime folder `name` (None = follow the default). Backs the file up first.
    Returns the runtime the next launch uses, for the status line."""
    rows = _runtime_by_dir(paths)
    if name is not None and name not in rows:
        raise InstallError(f"{name} is not installed.")
    target = rows.get(name) if name else None
    if store == "steam":
        if is_steam_running():
            raise SteamRunning("Steam is open and would undo this. Close Steam first.")
        text = paths.steam_config.read_text(encoding="utf-8")
        new_text = set_steam_mapping_text(text, game_id, target.get("steamName", name) if target else None)
        if new_text != text:
            _backup(paths.steam_config)
            _atomic_write(paths.steam_config, new_text)
        return name or game_runtime(paths, store, game_id)["default"]
    if store == "heroic":
        f = _heroic_game_file(paths, game_id)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except ValueError:
            raise InstallError("Heroic's settings for this game could not be read.") from None
        if not isinstance(data, dict):
            raise InstallError("Heroic's settings for this game could not be read.")
        cfg = data.setdefault(game_id, {})
        if target:
            wine = {"bin": str(Path(target["path"]) / "proton"), "name": name, "type": "proton"}
        else:
            wine = _heroic_default(paths)
            if wine is None:
                raise InstallError("Heroic has no default runtime set.")
            wine = dict(wine)
        cfg["wineVersion"] = wine
        if f.exists():
            _backup(f)
        f.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(f, json.dumps(data, indent=2))
        return Path(wine["bin"]).parent.name
    if store == "waystone":
        if target is None:
            raise InstallError("Pick a Proton version for this game.")
        data, prof = _profile(paths, game_id)
        current = _profile_runtime(paths, prof)
        old = rows.get(current)
        if old is None:
            raise InstallError("This launch entry's current Proton is not installed; edit it in launch settings.")
        if current != name:
            old_path, new_path = old["path"], target["path"]
            swap = lambda v: new_path + v[len(old_path):] if isinstance(v, str) and (
                v == old_path or v.startswith(old_path + "/")) else v
            prof["argv_template"] = [swap(a) for a in prof.get("argv_template") or []]
            if isinstance(prof.get("env"), dict):
                prof["env"] = {k: swap(v) for k, v in prof["env"].items()}
            _backup(paths.launch_profiles)
            _atomic_write(paths.launch_profiles, json.dumps(data, indent=2, sort_keys=True) + "\n")
        return name
    raise InstallError("This game's launcher does not use Proton.")


class SteamRunning(InstallError):
    pass


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.waystone-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
