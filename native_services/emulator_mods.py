"""Emulator mod installation utilities.

Pure Python stdlib implementation for installing .zip mods into emulator mod folders
with preview, apply, and undo capabilities.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


EMULATORS = {
    "eden": {"name": "Eden", "platform": "Nintendo Switch", "kind": "switch-content"},
    "ryujinx": {"name": "Ryujinx", "platform": "Nintendo Switch", "kind": "switch-content"},
    "azahar": {"name": "Azahar", "platform": "Nintendo 3DS", "kind": "3ds-content"},
    "cemu": {"name": "Cemu", "platform": "Nintendo Wii U", "kind": "cemu-graphicpack"},
    "dolphin": {"name": "Dolphin", "platform": "Nintendo GameCube / Wii", "kind": "dolphin-textures"},
}


def _validate_title_id(emulator: str, title_id: str) -> str:
    """Validate and normalize title_id for the given emulator."""
    if emulator in ("eden", "ryujinx", "azahar"):
        if not title_id:
            raise ValueError("title id needed")
        # Must be 16 hex chars
        if not re.fullmatch(r"[0-9a-fA-F]{16}", title_id):
            raise ValueError(f"invalid title_id for {emulator}: must be 16 hex chars")
        return title_id.upper()
    elif emulator == "dolphin":
        if not title_id:
            raise ValueError("title id needed")
        # Must be 6 chars [A-Z0-9]
        if not re.fullmatch(r"[A-Z0-9]{6}", title_id):
            raise ValueError(f"invalid title_id for {emulator}: must be 6 chars [A-Z0-9]")
        return title_id
    elif emulator == "cemu":
        # cemu ignores title_id
        return ""
    else:
        raise KeyError(emulator)


def _sanitize_mod_name(mod_name: str) -> str:
    """Sanitize mod_name: basename, allowed chars, no leading dots, max 80 chars."""
    name = os.path.basename(mod_name)
    # Replace disallowed chars with underscore
    name = re.sub(r"[^A-Za-z0-9._ ()+-]", "_", name)
    # Remove leading dots
    name = name.lstrip(".")
    # Truncate to 80 chars
    if len(name) > 80:
        name = name[:80]
    if not name:
        raise ValueError("mod_name cannot be empty after sanitization")
    return name


def mod_root(emulator: str, *, title_id: str, mod_name: str, home: str | Path) -> Path:
    """Return the mod root directory for the given emulator."""
    if emulator not in EMULATORS:
        raise KeyError(emulator)

    home = Path(home).expanduser()
    title_id = _validate_title_id(emulator, title_id)
    mod_name = _sanitize_mod_name(mod_name)

    if emulator == "eden":
        return home / ".local/share/eden/load" / title_id / mod_name
    elif emulator == "ryujinx":
        return home / ".config/Ryujinx/mods/contents" / title_id.lower() / mod_name
    elif emulator == "azahar":
        return home / ".local/share/azahar-emu/load/mods" / title_id
    elif emulator == "cemu":
        return home / ".local/share/Cemu/graphicPacks" / mod_name
    elif emulator == "dolphin":
        return home / ".local/share/dolphin-emu/Load/Textures" / title_id
    else:
        raise KeyError(emulator)


def _find_title_id_in_paths(names: list[str]) -> str:
    """Find first 16-hex path segment in any member path."""
    for name in names:
        parts = name.split("/")
        for part in parts:
            if re.fullmatch(r"[0-9a-fA-F]{16}", part):
                return part.upper()
    return ""


def detect_layout(names: list[str]) -> dict:
    """Detect the content layout from zip member names.

    Returns dict with keys: kind, root, title_id
    """
    # Normalize names - ensure directories end with /
    normalized = []
    for n in names:
        if n.endswith("/"):
            normalized.append(n)
        else:
            normalized.append(n)

    # Find all directory prefixes (including empty string)
    dir_prefixes = set([""])
    for n in normalized:
        parts = n.split("/")
        for i in range(len(parts)):
            prefix = "/".join(parts[:i])
            if prefix:
                dir_prefixes.add(prefix + "/")
            else:
                dir_prefixes.add("")

    # Sort by length (shortest first) to find shortest satisfying prefix
    sorted_prefixes = sorted(dir_prefixes, key=len)

    # Check for switch-content / 3ds-content: P + "romfs/" or P + "exefs/" or P + "cheats/"
    for prefix in sorted_prefixes:
        for marker in ("romfs/", "exefs/", "cheats/"):
            target = prefix + marker
            if any(name.startswith(target) for name in normalized):
                title_id = _find_title_id_in_paths(names)
                return {"kind": "switch-content", "root": prefix, "title_id": title_id}

    # Check for cemu-graphicpack: P + "rules.txt" is a member
    for prefix in sorted_prefixes:
        target = prefix + "rules.txt"
        if target in normalized:
            title_id = _find_title_id_in_paths(names)
            return {"kind": "cemu-graphicpack", "root": prefix, "title_id": title_id}

    # Check for dolphin-textures: some member under P ends with .png/.dds
    # and no romfs/exefs/rules.txt exists anywhere in the archive
    has_romfs = any("romfs/" in n for n in normalized)
    has_exefs = any("exefs/" in n for n in normalized)
    has_rules = any(n == "rules.txt" or n.endswith("/rules.txt") for n in normalized)

    if not (has_romfs or has_exefs or has_rules):
        textures = [n for n in normalized if not n.endswith("/") and n.lower().endswith((".png", ".dds"))]
        if textures:
            # Root = the folder all textures share. Dolphin packs usually ship as "<GAMEID>/..." (6 chars), whose
            # contents belong directly in Load/Textures/<GAMEID>/, so that folder is the root and gives the id.
            parts = [t.split("/")[:-1] for t in textures]
            common = []
            for segs in zip(*parts):
                if len(set(segs)) != 1:
                    break
                common.append(segs[0])
            game_id = ""
            for i, seg in enumerate(common):
                if re.fullmatch(r"[A-Z0-9]{6}", seg):
                    game_id, common = seg, common[: i + 1]
                    break
            root = "/".join(common) + "/" if common else ""
            return {"kind": "dolphin-textures", "root": root, "title_id": game_id or _find_title_id_in_paths(names)}

    title_id = _find_title_id_in_paths(names)
    return {"kind": "unknown", "root": "", "title_id": title_id}


def _sha256_file(path: Path) -> str:
    """Compute SHA256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    """Compute SHA256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def plan_install(archive: str | Path, emulator: str, *, title_id: str = "", mod_name: str, home: str | Path) -> dict:
    """Plan a mod installation from a zip archive."""
    if emulator not in EMULATORS:
        raise KeyError(emulator)

    expected_kind = EMULATORS[emulator]["kind"]
    archive = Path(archive)

    # Compute archive SHA256
    archive_sha256 = _sha256_file(archive)

    with zipfile.ZipFile(archive, "r") as zf:
        members = zf.infolist()

        # Validate members
        for info in members:
            name = info.filename
            # Reject absolute paths
            if os.path.isabs(name):
                raise ValueError(f"absolute path in archive: {name}")
            # Reject paths with ..
            if ".." in name.split("/"):
                raise ValueError(f"path traversal in archive: {name}")
            # Reject backslashes
            if "\\" in name:
                raise ValueError(f"backslash in archive path: {name}")
            # Reject symlinks (external_attr >> 16 has S_IFLNK)
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise ValueError(f"symlink in archive: {name}")

        names = [info.filename for info in members]
        layout = detect_layout(names)

        # Check kind compatibility
        if expected_kind == "switch-content" and layout["kind"] not in ("switch-content", "3ds-content"):
            raise ValueError(f"{EMULATORS[emulator]['name']} needs a romfs/exefs mod; this zip looks like {layout['kind']}")
        elif expected_kind == "3ds-content" and layout["kind"] != "switch-content":
            raise ValueError(f"{EMULATORS[emulator]['name']} needs a romfs/exefs mod; this zip looks like {layout['kind']}")
        elif expected_kind == "cemu-graphicpack" and layout["kind"] != "cemu-graphicpack":
            raise ValueError(f"Cemu needs a graphic pack (rules.txt); this zip looks like {layout['kind']}")
        elif expected_kind == "dolphin-textures" and layout["kind"] != "dolphin-textures":
            raise ValueError(f"Dolphin needs a texture pack; this zip looks like {layout['kind']}")

        # Determine title_id
        final_title_id = title_id or layout["title_id"]
        if emulator in ("eden", "ryujinx", "azahar", "dolphin") and not final_title_id:
            raise ValueError("title id needed")

        target_root = mod_root(emulator, title_id=final_title_id, mod_name=mod_name, home=home)
        root = layout["root"]

        files = []
        conflicts = []

        for info in members:
            if info.is_dir():
                continue
            member_name = info.filename
            if not member_name.startswith(root):
                continue  # Skip files outside the content root

            rel_path = member_name[len(root):]
            if not rel_path:
                continue

            target = target_root / rel_path

            # Read member data for hashing
            with zf.open(info) as f:
                data = f.read()
            member_sha256 = _sha256_bytes(data)

            # Check for conflicts
            if target.exists():
                existing_sha256 = _sha256_file(target)
                if existing_sha256 != member_sha256:
                    conflicts.append(str(target))

            files.append({
                "member": member_name,
                "target": str(target),
                "sha256": member_sha256,
                "size": info.file_size,
            })

        # Compute plan_id: sha256 of (archive_sha256 + emulator + target_root) prefix 32 hex
        plan_id_input = archive_sha256 + emulator + str(target_root)
        plan_id = hashlib.sha256(plan_id_input.encode()).hexdigest()[:32]

        return {
            "plan_id": plan_id,
            "archive": str(archive),
            "archive_sha256": archive_sha256,
            "emulator": emulator,
            "target_root": str(target_root),
            "files": files,
            "conflicts": conflicts,
            "kind": layout["kind"],
            "title_id": final_title_id,
        }


def apply_install(plan: dict, *, journal_dir: str | Path) -> dict:
    """Apply an installation plan."""
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)

    # Re-hash archive
    archive_path = Path(plan["archive"])
    current_sha256 = _sha256_file(archive_path)
    if current_sha256 != plan["archive_sha256"]:
        raise ValueError("archive changed since preview")

    # Refuse if conflicts exist
    if plan["conflicts"]:
        raise ValueError("conflicts exist, cannot apply")

    target_root = Path(plan["target_root"])
    created_dirs = []
    written = []

    # Collect all parent directories needed for all files
    all_parents = set()
    for file_info in plan["files"]:
        target = Path(file_info["target"])
        parent = target.parent
        # Add all parents from target_root down to parent
        for p in [parent] + list(parent.parents):
            if p == target_root:
                all_parents.add(p)
                break
            if p in target_root.parents:
                break
            all_parents.add(p)

    # Sort by depth (shallowest first) so we create parents before children
    sorted_parents = sorted(all_parents, key=lambda p: len(p.relative_to(target_root).parts))

    # Create directories and track which ones are newly created
    for p in sorted_parents:
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created_dirs.append(p)

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for file_info in plan["files"]:
                target = Path(file_info["target"])
                member = file_info["member"]

                # Write to .part then atomic replace
                part_path = target.with_suffix(target.suffix + ".part")
                with zf.open(member) as src, part_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

                # Verify hash
                if _sha256_file(part_path) != file_info["sha256"]:
                    part_path.unlink(missing_ok=True)
                    raise ValueError(f"hash mismatch for {target}")

                os.replace(part_path, target)
                written.append({"target": str(target), "sha256": file_info["sha256"]})

        # Write journal
        journal_path = journal_dir / f"{plan['plan_id']}.json"
        journal_data = {
            "plan": plan,
            "written": written,
            "created_dirs": [str(d) for d in created_dirs],
            "applied_at": datetime.now().isoformat(),
        }
        with journal_path.open("w") as f:
            json.dump(journal_data, f, indent=2)

        return {"journal": str(journal_path), "written": len(written)}

    except Exception:
        # Cleanup on failure
        for w in written:
            Path(w["target"]).unlink(missing_ok=True)
        # Remove created dirs deepest first if empty
        for d in sorted(created_dirs, key=lambda p: len(str(p)), reverse=True):
            try:
                Path(d).rmdir()
            except OSError:
                pass
        raise


def undo_install(journal_path: str | Path) -> dict:
    """Undo an installation using its journal."""
    journal_path = Path(journal_path)
    with journal_path.open() as f:
        journal = json.load(f)

    written = journal["written"]
    created_dirs = journal["created_dirs"]

    removed = 0
    kept_changed = []

    for w in written:
        target = Path(w["target"])
        if target.exists():
            current_sha256 = _sha256_file(target)
            if current_sha256 == w["sha256"]:
                target.unlink()
                removed += 1
            else:
                kept_changed.append(str(target))

    # Remove created dirs deepest-first if empty
    for d in sorted(created_dirs, key=lambda p: len(str(p)), reverse=True):
        try:
            Path(d).rmdir()
        except OSError:
            pass

    # Rename journal to .undone
    undone_path = journal_path.with_name(journal_path.stem + ".undone")
    journal_path.rename(undone_path)

    return {"removed": removed, "kept_changed": kept_changed}