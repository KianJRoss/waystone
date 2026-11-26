"""Bounded, offline, review-only account/source identity-link candidates.

Consumes a loaded ``{"games": [...]}`` catalog only. It never changes records,
scans sources, merges, or contacts providers.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

SCHEMA = "catalog-link-candidates-v1"
_IDENTITY_FIELDS = ("provider_game_id", "external_id", "provider_id")
_MISSING = {"", "unknown", "n/a", "none", "null"}


def _valid(value: Any) -> bool:
    return isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip().casefold() not in _MISSING


def _norm(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold()))


def _stable(kind: str, key: Any) -> str:
    raw = json.dumps([kind, key], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"link-{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def generate_link_candidates(catalog: Any) -> dict[str, Any]:
    """Return deterministic candidates from supplied games; output is detached.

    Eligible proposals require at least one Playnite ``Unassigned`` record and
    one record from another named source. Exact provider IDs outrank title
    matches. Title candidates require known, matching platforms and editions;
    contradictory known metadata blocks the candidate. No fuzzy matching.
    """
    games = catalog.get("games") if isinstance(catalog, dict) else None
    if not isinstance(games, list):
        games = []
    records: list[dict[str, Any]] = []
    id_groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    title_groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for i, game in enumerate(games):
        if not isinstance(game, dict):
            continue
        gid = game.get("id")
        variants = game.get("variants") if isinstance(game.get("variants"), list) else []
        sources: set[str] = set()
        platforms: set[str] = set()
        editions: set[str] = set()
        identities: set[tuple[str, str]] = set()
        for v in variants:
            if not isinstance(v, dict):
                continue
            src = v.get("source", v.get("provider"))
            if _valid(src):
                sources.add(str(src).strip())
            for field, target in (("platform", platforms),):
                val = v.get(field)
                if _valid(val): target.add(_norm(str(val)))
            for field in _IDENTITY_FIELDS:
                val = v.get(field)
                if _valid(val): identities.add((field, str(val).strip()))
            md = v.get("metadata") if isinstance(v.get("metadata"), dict) else {}
            for owner in (game, v, md):
                val = owner.get("edition")
                if _valid(val): editions.add(_norm(str(val)))
        rec = {"index": i, "id": gid, "title": game.get("title"), "sources": sorted(sources),
               "platforms": sorted(platforms), "editions": sorted(editions), "identities": sorted(identities)}
        records.append(rec)
        ridx = len(records) - 1
        for identity in identities: id_groups[identity].append(ridx)
        title = _norm(game["title"]) if isinstance(game.get("title"), str) else ""
        # Missing on both sides is explicitly unavailable, not inferred.
        # One-sided availability and conflicting per-record values stay blocked.
        if title and len(platforms) == 1 and len(editions) <= 1:
            edition_key = next(iter(editions)) if editions else "__edition_unavailable__"
            title_groups[(title, next(iter(platforms)), edition_key)].append(ridx)

    def eligible(group: list[int]) -> bool:
        srcs = {s.casefold() for j in group for s in records[j]["sources"]}
        return "unassigned" in srcs and bool(srcs - {"unassigned", "playnite"})

    proposals: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
    for (field, value), group in sorted(id_groups.items()):
        uniq = tuple(sorted(set(group)))
        if len(uniq) < 2 or not eligible(list(uniq)): continue
        proposals[("exact_provider_id", uniq)] = {"kind": "exact_provider_id", "confidence_tier": "strong_identity_candidate",
            "evidence": [{"type": "exact_identifier", "field": field, "value": value}], "indexes": uniq}
    for (title, platform, edition), group in sorted(title_groups.items()):
        uniq = tuple(sorted(set(group)))
        if len(uniq) < 2 or not eligible(list(uniq)): continue
        # Exact candidates already connect these same records; keep strongest evidence only.
        if any(set(uniq) <= set(p["indexes"]) for p in proposals.values()): continue
        proposals[("normalized_title_platform_edition", uniq)] = {"kind": "normalized_title_platform_edition",
            "confidence_tier": "conservative_review_candidate", "evidence": [
                {"type": "normalized_title", "value": title}, {"type": "explicit_platform", "value": platform},
                ({"type": "edition", "value": None, "state": "unavailable_on_both"} if edition == "__edition_unavailable__"
                 else {"type": "explicit_edition", "value": edition})], "indexes": uniq}

    clusters = []
    for (_, _), p in sorted(proposals.items(), key=lambda item: (item[0][0], item[0][1])):
        members = [records[j] for j in p.pop("indexes")]
        cluster_key = [p["kind"], sorted(str(r["id"]) for r in members), p["evidence"]]
        clusters.append({"id": _stable("candidate", cluster_key), "kind": p["kind"],
            "confidence_tier": p["confidence_tier"], "evidence": p["evidence"],
            "records": [{"record_index": r["index"], "canonical_game_id": r["id"], "title": r["title"],
                         "sources": r["sources"], "platforms": r["platforms"], "editions": r["editions"]} for r in members],
            "review_only": True})
    return {"schema": SCHEMA, "preview_only": True, "source_scan_performed": False,
            "record_mutation_performed": False, "candidate_clusters": clusters,
            "unresolved_identity_policy": "missing, ambiguous, or contradictory identity is not linked"}
