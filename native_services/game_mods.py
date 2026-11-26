"""Install a downloaded mod into a PC game's folder (Nexus mods), reusing emulator_mods' journaled apply/undo.

Nexus archives are often .7z or .rar: `as_zip` unpacks them with bsdtar into a private staging folder (rejecting
absolute paths, '..' and links) and repacks a .zip next to the download, so planning/applying/undo only ever deal
with zip members and sha256s.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from native_services import emulator_mods

BETHESDA_DATA_DIRS = {"meshes", "textures", "scripts", "interface", "skse", "f4se", "sound", "music", "seq",
                      "strings", "materials", "shaders", "lodsettings", "dialogueviews", "fomod", "nemesis_engine",
                      "netscriptframework", "calientetools", "skyproc patchers", "source", "video", "grass"}
PLUGIN_EXT = (".esp", ".esm", ".esl", ".bsa", ".ba2")


def as_zip(archive: str | Path) -> Path:
    archive = Path(archive)
    if zipfile.is_zipfile(archive):
        return archive
    out = archive.with_name(archive.name + ".waystone.zip")
    if out.exists():
        return out
    listing = subprocess.run(["bsdtar", "-tf", str(archive)], capture_output=True, text=True, timeout=300)
    if listing.returncode != 0:
        raise ValueError(f"cannot read this archive ({archive.suffix}): {listing.stderr.strip()[:200]}")
    for name in listing.stdout.splitlines():
        p = PurePosixPath(name.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe path in archive: {name}")
    with tempfile.TemporaryDirectory(prefix="waystone-stage-", dir=archive.parent) as tmp:
        result = subprocess.run(["bsdtar", "-xf", str(archive), "-C", tmp, "--no-same-owner", "--no-same-permissions"],
                                capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            raise ValueError(f"cannot unpack this archive: {result.stderr.strip()[:200]}")
        root = Path(tmp)
        part = out.with_suffix(".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as zf:
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    raise ValueError(f"links are not allowed in mods: {path.relative_to(root)}")
                if path.is_file():
                    zf.write(path, path.relative_to(root).as_posix())
        os.replace(part, out)
    return out


def is_bethesda(game_dir: Path) -> bool:
    data = game_dir / "Data"
    return data.is_dir() and any(p.suffix.lower() in (".esm", ".esl") for p in data.iterdir())


def _layout(names: list[str], game_dir: Path | None = None) -> tuple[str, str]:
    """(root prefix inside the zip, sub-folder of the game dir) for a PC mod archive.

    Bethesda games (Data/*.esm): strip wrapper folders, then a Data/ folder -> game folder; data folders or
    loose plugins -> <game>/Data. Other games: the root is the shallowest level whose top folders already exist
    in the game folder (so 'r6/', 'bin/', 'archive/' land where they belong); otherwise the zip root."""
    files = [n for n in names if not n.endswith("/")]
    candidates = [""]
    for n in files:
        parts = n.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            p = "/".join(parts[:i]) + "/"
            if p not in candidates:
                candidates.append(p)
    candidates.sort(key=lambda p: (p.count("/"), p))

    def tops(prefix):
        below = [n[len(prefix):] for n in files if n.startswith(prefix)]
        return below, {b.split("/", 1)[0] for b in below if "/" in b}, [b for b in below if "/" not in b]

    if game_dir is None or is_bethesda(game_dir):
        root = ""
        while True:
            below, folders, loose = tops(root)
            low = {f.lower() for f in folders}
            if len(folders) == 1 and not loose and not low & (BETHESDA_DATA_DIRS | {"data", "bepinex"}):
                root += next(iter(folders)) + "/"
                continue
            break
        low = {f.lower() for f in folders}
        if "data" in low or "bepinex" in low:
            return root, ""
        if low & BETHESDA_DATA_DIRS or any(b.lower().endswith(PLUGIN_EXT) for b in loose):
            return root, "Data"
        return root, ""
    existing = {p.name.lower() for p in game_dir.iterdir()}
    for prefix in candidates:
        below, folders, loose = tops(prefix)
        if {f.lower() for f in folders} & existing or {b.lower() for b in loose} & existing:
            return prefix, ""
    return "", ""


def plan_game_install(archive: str | Path, game_dir: str | Path, *, mod_name: str = "") -> dict:
    zpath = as_zip(archive)
    game_dir = Path(game_dir)
    if not game_dir.is_dir():
        raise ValueError("choose the game's install folder first")
    with zipfile.ZipFile(zpath) as zf:
        infos = zf.infolist()
        for info in infos:
            name = info.filename
            if name.startswith("/") or "\\" in name or ".." in PurePosixPath(name).parts:
                raise ValueError(f"unsafe path in archive: {name}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"links are not allowed in mods: {name}")
        root, sub = _layout([i.filename for i in infos], game_dir)
        target_root = game_dir / sub if sub else game_dir
        files, conflicts = [], []
        for info in infos:
            if info.is_dir() or not info.filename.startswith(root):
                continue
            rel = info.filename[len(root):]
            if not rel:
                continue
            data = zf.read(info)
            digest = hashlib.sha256(data).hexdigest()
            target = target_root / rel
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    conflicts.append(str(target))
            files.append({"member": info.filename, "target": str(target), "sha256": digest, "size": info.file_size})
    archive_sha = hashlib.sha256(zpath.read_bytes()).hexdigest()
    plan_id = hashlib.sha256((archive_sha + "game" + str(target_root)).encode()).hexdigest()[:32]
    return {"plan_id": plan_id, "archive": str(zpath), "archive_sha256": archive_sha, "emulator": "game",
            "target_root": str(target_root), "files": files, "conflicts": conflicts, "kind": "game-folder",
            "mod_name": mod_name}


apply_install = emulator_mods.apply_install
undo_install = emulator_mods.undo_install
