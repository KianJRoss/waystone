"""Repair the 2026-11-19 bulk cover scrape (scripts/scrape_and_populate_covers.py).

That pass replaced covers by approximate title search (its last fallback was the first Bing image result),
so sequels/series got one shared picture (e.g. every Mario Party shows the same box) and 733 games that
already had their real cover were overwritten.

Plan (per game, comparing with the pre-scrape backup ``.bak-covers-1795075204``):
  restore  - the game had a cover before the scrape and it was replaced: put the original back.
  clear    - the scrape filled a missing cover with a picture that is byte-identical to the picture it gave a
             DIFFERENT title: wrong for all but at most one of them, so clear it (no cover beats a wrong one;
             re-fetch by exact store ID later).
  keep     - the scrape filled a missing cover with a picture unique to this game.

Usage:
  python tools/artwork_repair.py plan  --db LIVE.sqlite3 --before BACKUP.sqlite3 --hashes HASHES.txt --out plan.json
  python tools/artwork_repair.py apply --db LIVE.sqlite3 --plan plan.json        (backs up the DB first)
HASHES.txt = ``sha1sum`` lines for ``<game id>/cover_*.jpg`` under the managed-assets root.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import sqlite3
import time
from pathlib import Path


def _covers(db: str) -> dict[str, tuple[str, str | None]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {sid: (title, json.loads(meta or "{}").get("CoverImage"))
                for sid, title, meta in con.execute("SELECT source_id, title, metadata_json FROM games")}
    finally:
        con.close()


_ROMAN = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
_NOISE = {"vr", "edition", "remastered", "remaster", "definitive", "deluxe", "goty", "complete", "ultimate",
          "enhanced", "hd", "demo", "beta", "version", "usa", "europe", "eur", "japan", "jpn", "world", "en",
          "the", "a", "for", "and", "incl", "mod", "windows", "pc", "nds", "friends", "frie", "video"}


def _norm(title: str | None) -> tuple[str, frozenset]:
    """Same-game family key: base words (minus edition/platform/region noise) + sequel numbers.
    'Athletics Games' == 'Athletics Games VR'; 'Mario Party 8' != 'Mario Party 9'."""
    t = re.sub(r"[\(\[].*?[\)\]]", " ", (title or "").lower())
    t = re.sub(r"\bv(?:beta|alpha)?\.?\d[\w.]*|\bv(?:beta|alpha)\b|\b\d+(?:\.\d+)+\w*", " ", t)  # v1.2, v.703, vBeta2.0
    t = t.replace("&", " and ")
    words = re.findall(r"[a-z]+|\d+", t)
    nums = frozenset(int(w) for w in words if w.isdigit() and int(w) < 1900) | frozenset(
        _ROMAN[w] for w in words if w in _ROMAN and w != "x")
    base = " ".join(w for w in words if not w.isdigit() and w not in _ROMAN and w not in _NOISE)
    return base, nums


def plan(db: str, before: str, hashes: str) -> dict:
    now, old = _covers(db), _covers(before)
    sha: dict[str, str] = {}
    for line in Path(hashes).read_text().splitlines():
        digest, path = line.split(None, 1)
        gid, name = path.strip().lstrip("./").split("/", 1)
        if name == "cover_scraped.jpg":
            sha[gid] = digest
    owners: dict[str, set] = collections.defaultdict(set)
    for gid, digest in sha.items():
        owners[digest].add(_norm(now.get(gid, ("", None))[0]))

    def conflicting(keys: set) -> bool:
        """Two titles are the same game when the sequel numbers match and one name starts with the other."""
        ks = list(keys)
        for i, (ba, na) in enumerate(ks):
            for bb, nb in ks[i + 1:]:
                wa, wb = ba.split(), bb.split()
                prefix = wa[:len(wb)] == wb or wb[:len(wa)] == wa
                if na != nb or not prefix:
                    return True
        return False
    actions = []
    for gid, (title, cover) in sorted(now.items(), key=lambda kv: (kv[1][0] or "").lower()):
        was = old.get(gid, (title, None))[1]
        if cover == was:
            continue
        if was:
            actions.append({"id": gid, "title": title, "action": "restore", "from": cover, "to": was})
        elif gid in sha and conflicting(owners[sha[gid]]):
            actions.append({"id": gid, "title": title, "action": "clear", "from": cover, "to": None})
        else:
            actions.append({"id": gid, "title": title, "action": "keep", "from": cover, "to": cover})
    counts = collections.Counter(a["action"] for a in actions)
    return {"created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "counts": dict(counts), "actions": actions}


def apply(db: str, plan_doc: dict) -> dict:
    backup = f"{db}.bak-artwork-repair-{int(time.time())}"
    shutil.copy2(db, backup)
    con = sqlite3.connect(db)
    done = collections.Counter()
    try:
        for a in plan_doc["actions"]:
            if a["action"] == "keep":
                continue
            row = con.execute("SELECT metadata_json FROM games WHERE source_id=?", (a["id"],)).fetchone()
            if row is None:
                continue
            meta = json.loads(row[0] or "{}")
            if meta.get("CoverImage") != a["from"]:
                done["skipped_changed_since_plan"] += 1  # never overwrite a newer choice
                continue
            if a["to"]:
                meta["CoverImage"] = a["to"]
            else:
                meta.pop("CoverImage", None)
            con.execute("UPDATE games SET metadata_json=? WHERE source_id=?", (json.dumps(meta, ensure_ascii=False), a["id"]))
            done[a["action"]] += 1
        con.commit()
    finally:
        con.close()
    return {"backup": backup, **done}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "apply"])
    ap.add_argument("--db", required=True)
    ap.add_argument("--before")
    ap.add_argument("--hashes")
    ap.add_argument("--plan")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "plan":
        doc = plan(a.db, a.before, a.hashes)
        Path(a.out).write_text(json.dumps(doc, indent=1, ensure_ascii=False))
        print(json.dumps(doc["counts"]))
    else:
        print(json.dumps(apply(a.db, json.loads(Path(a.plan).read_text()))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
