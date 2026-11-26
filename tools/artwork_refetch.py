"""Re-fetch covers that tools/artwork_repair.py cleared, using EXACT identifiers only (never a title search).

Sources:
  rom   - libretro-thumbnails Named_Boxarts, matched by the game's own No-Intro/Redump ROM file name
          (case-insensitive, libretro's character substitution, '-decrypted' and trailing release-group suffixes
          dropped). If the ROM name has no region tag, it accepts the single '(USA)' entry with that exact base name.
  disc  - GameTDB front cover by the exact Wii/GameCube disc ID in the ROM name ('Metroid Other M [R3OP01]').
  xbox  - Microsoft Store catalog lookup by the exact Package Family Name; only a portrait Poster/BoxArt image.
Anything else (PC repacks with no store ID) is left without a cover for the per-game "choose artwork" path.

  python tools/artwork_refetch.py plan  --db LIVE_OR_COPY.sqlite3 --repair-plan artwork-plan.json --out refetch.json --cache DIR
  python tools/artwork_refetch.py fetch --plan refetch.json --files STAGING_DIR      (downloads <gid>/cover_exact.<ext>)
  python tools/artwork_refetch.py apply --db LIVE.sqlite3 --plan refetch.json        (backs up the DB; only fills EMPTY covers)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "waystone-artwork-refetch/1"}
SYSTEMS = {  # ROM folder under .../roms/<folder>/ -> libretro-thumbnails repo
    "gba": "Nintendo_-_Game_Boy_Advance", "gb": "Nintendo_-_Game_Boy", "gbc": "Nintendo_-_Game_Boy_Color",
    "n64": "Nintendo_-_Nintendo_64", "nds": "Nintendo_-_Nintendo_DS", "ds": "Nintendo_-_Nintendo_DS",
    "3ds": "Nintendo_-_Nintendo_3DS", "gamecube": "Nintendo_-_GameCube", "gc": "Nintendo_-_GameCube",
    "wii": "Nintendo_-_Wii", "wiiu": "Nintendo_-_Wii_U", "snes": "Nintendo_-_Super_Nintendo_Entertainment_System",
    "nes": "Nintendo_-_Nintendo_Entertainment_System", "megadrive": "Sega_-_Mega_Drive_-_Genesis",
    "genesis": "Sega_-_Mega_Drive_-_Genesis", "switch": None,
}
PLATFORM = {"Nintendo GameCube": "gamecube", "Nintendo 3DS": "3ds", "Nintendo 64": "n64", "Nintendo DS": "nds",
            "Nintendo Game Boy Advance": "gba", "Nintendo Game Boy": "gb", "Nintendo Game Boy Color": "gbc",
            "Nintendo Wii": "wii", "Nintendo Wii U": "wiiu"}


def _get(url: str, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def _key(name: str) -> str:
    """libretro file-name key: its &*/:`<>?\\| -> _ substitution, case-insensitive."""
    return re.sub(r"[&*/:`<>?\\|]", "_", name).strip().lower()


def _rom_name(name: str) -> str:
    n = re.sub(r"\.(3ds|cia|nds|gba|gbc|gb|n64|v64|z64|iso|rvz|wbfs|wua|zip|7z|nsp|xci)$", "", name.strip(), flags=re.I)
    n = re.sub(r"-decrypted.*$", "", n, flags=re.I)          # 'X (USA) (Rev 2)-decrypted - USA - En'
    n = re.sub(r"^(.*\))\s+-\s+[\w ,.-]+$", r"\1", n)       # Playnite's appended ' - USA - En - Fr - Es'
    n = re.sub(r"\s*\[[0-9a-z]{4,6}\]\s*$", "", n, flags=re.I)  # trailing disc ID '[rqoe69]'
    return n.strip()


class Thumbs:
    def __init__(self, cache: Path):
        self.cache, self.index = cache, {}
        cache.mkdir(parents=True, exist_ok=True)

    def names(self, repo: str) -> dict[str, str]:
        if repo not in self.index:
            f = self.cache / f"{repo}.json"
            if not f.exists():
                api = f"https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees"
                top = json.loads(_get(f"{api}/master"))["tree"]
                sha = next(t["sha"] for t in top if t["path"] == "Named_Boxarts")
                sub = json.loads(_get(f"{api}/{sha}", timeout=120))["tree"]
                f.write_text(json.dumps(["Named_Boxarts/" + t["path"] for t in sub]))
            paths = json.loads(f.read_text())
            self.index[repo] = {_key(p[len("Named_Boxarts/"):-4]): p for p in paths if p.endswith(".png")}
        return self.index[repo]

    def match(self, repo: str, rom: str) -> str | None:
        idx, k = self.names(repo), _key(_rom_name(rom))
        if k in idx:
            return idx[k]
        if "(" not in k:  # bare name: accept only a single exact '<name> (USA...)' entry
            hits = [p for kk, p in idx.items() if kk.startswith(k + " (usa")]
            if len(hits) == 1:
                return hits[0]
        return None


def _rom_candidates(provider: str, pgid: str, meta: dict) -> list[tuple[str, str]]:
    """(system folder, rom file name) pairs from the game's OWN records only."""
    out = []
    if pgid and ":" in pgid:
        path = pgid.split(":", 1)[1].replace("\\", "/")
        m = re.search(r"/roms/([^/]+)/", path, re.I)
        if m:
            out.append((m.group(1).lower(), path.rsplit("/", 1)[-1]))
    plats = meta.get("PlatformIds_resolved_names") or []
    plat = PLATFORM.get(plats[0]) if isinstance(plats, list) and len(plats) == 1 else None
    roms = meta.get("Roms")
    if isinstance(roms, list) and plat:
        for r in roms:
            if isinstance(r, dict) and r.get("Name"):
                out.append((plat, str(r["Name"])))
    return out


_TDB_REGION = {"E": "US", "P": "EN", "J": "JA", "K": "KO"}


def _gametdb(pgid: str, meta: dict) -> dict | None:
    names = [pgid] + [str(r.get("Name", "")) for r in (meta.get("Roms") or []) if isinstance(r, dict)]
    for n in names:
        m = re.search(r"\[([A-Z0-9]{6})\]", n, re.I)
        if not m:
            continue
        gid = m.group(1).upper()
        for region in dict.fromkeys([_TDB_REGION.get(gid[3], "EN"), "US", "EN"]):
            url = f"https://art.gametdb.com/wii/cover/{region}/{gid}.png"
            try:
                req = urllib.request.Request(url, headers=UA, method="HEAD")
                with urllib.request.urlopen(req, timeout=20) as r:
                    if r.status == 200 and r.headers.get("Content-Type", "").startswith("image/"):
                        return {"source": "gametdb", "evidence": f"disc ID {gid}", "url": url}
            except Exception:
                continue
    return None


def plan(db: str, repair_plan: str, cache: str) -> dict:
    cleared = {a["id"] for a in json.loads(Path(repair_plan).read_text())["actions"] if a["action"] == "clear"}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    thumbs, items = Thumbs(Path(cache)), []
    for sid, title, provider, pgid, meta_json in con.execute(
            "SELECT source_id, title, provider, provider_game_id, metadata_json FROM games"):
        if sid not in cleared:
            continue
        meta = json.loads(meta_json or "{}")
        item = {"id": sid, "title": title, "provider": provider, "source": None, "url": None, "evidence": None}
        for folder, rom in _rom_candidates(provider, pgid or "", meta):
            repo = SYSTEMS.get(folder)
            hit = repo and thumbs.match(repo, rom)
            if hit:
                item.update(source="libretro-thumbnails", evidence=f"{repo}: ROM '{rom}'",
                            url=f"https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/" + urllib.parse.quote(hit))
                break
        if not item["url"]:
            item.update(_gametdb(pgid or "", meta) or {})
        if not item["url"] and provider == "Xbox" and pgid and "_" in pgid:
            try:
                d = json.loads(_get("https://displaycatalog.mp.microsoft.com/v7.0/products/lookup?market=US&languages=en-US"
                                    f"&alternateId=PackageFamilyName&value={urllib.parse.quote(pgid)}"))
                imgs = [i for p in d.get("Products", [])[:1] for i in p["LocalizedProperties"][0]["Images"]
                        if i["ImagePurpose"] in ("Poster", "BoxArt") and i["Height"] > i["Width"]]
                if imgs:
                    best = max(imgs, key=lambda i: i["Height"])
                    item.update(source="microsoft-store", evidence=f"PackageFamilyName {pgid}",
                                url=("https:" + best["Uri"]) if best["Uri"].startswith("//") else best["Uri"])
            except Exception as e:  # network/catalog miss -> leave for manual choice
                item["evidence"] = f"microsoft-store lookup failed: {e}"
        items.append(item)
    con.close()
    found = sum(1 for i in items if i["url"])
    return {"created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "counts": {"cleared": len(items), "exact_match": found, "manual": len(items) - found}, "items": items}


def fetch(plan_doc: dict, files: str) -> dict:
    root, done = Path(files), {"ok": 0, "failed": 0}
    for it in plan_doc["items"]:
        if not it["url"]:
            continue
        try:
            raw = _get(it["url"])
            ext = "png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "jpg" if raw[:3] == b"\xff\xd8\xff" else None
            if not ext or len(raw) > 16 * 1024 * 1024:
                raise ValueError("not a PNG/JPEG under 16 MiB")
            (root / it["id"]).mkdir(parents=True, exist_ok=True)
            (root / it["id"] / f"cover_exact.{ext}").write_bytes(raw)
            it["file"] = f"{it['id']}\\cover_exact.{ext}"
            done["ok"] += 1
        except Exception as e:
            it["fetch_error"] = str(e)
            done["failed"] += 1
    return done


def apply(db: str, plan_doc: dict) -> dict:
    backup = f"{db}.bak-artwork-refetch-{int(time.time())}"
    shutil.copy2(db, backup)
    con, done = sqlite3.connect(db), {"set": 0, "skipped_has_cover": 0}
    try:
        for it in plan_doc["items"]:
            if not it.get("file"):
                continue
            row = con.execute("SELECT metadata_json FROM games WHERE source_id=?", (it["id"],)).fetchone()
            if row is None:
                continue
            meta = json.loads(row[0] or "{}")
            if meta.get("CoverImage"):  # never overwrite a cover set after the repair
                done["skipped_has_cover"] += 1
                continue
            meta["CoverImage"] = it["file"]
            meta["CoverSource"] = {"source": it["source"], "evidence": it["evidence"], "url": it["url"]}
            con.execute("UPDATE games SET metadata_json=? WHERE source_id=?", (json.dumps(meta, ensure_ascii=False), it["id"]))
            done["set"] += 1
        con.commit()
    finally:
        con.close()
    return {"backup": backup, **done}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "fetch", "apply"])
    ap.add_argument("--db")
    ap.add_argument("--repair-plan")
    ap.add_argument("--plan")
    ap.add_argument("--out")
    ap.add_argument("--cache", default=".artwork-refetch-cache")
    ap.add_argument("--files")
    a = ap.parse_args()
    if a.cmd == "plan":
        doc = plan(a.db, a.repair_plan, a.cache)
        Path(a.out).write_text(json.dumps(doc, indent=1, ensure_ascii=False))
        print(json.dumps(doc["counts"]))
    elif a.cmd == "fetch":
        doc = json.loads(Path(a.plan).read_text())
        print(json.dumps(fetch(doc, a.files)))
        Path(a.plan).write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    else:
        print(json.dumps(apply(a.db, json.loads(Path(a.plan).read_text()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
