"""Deterministic, read-only duplicate suggestions and metadata collections.

Consumes only a caller-supplied loaded catalog. Suggestions never reconcile,
modify, merge, or delete records; no discovery, network, or AI is performed.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

SCHEMA = "library-collections-v1"
_ID_FIELDS = ("provider", "source", "source_id", "installation_id", "instance", "host", "record_key", "variant_key", "external_id", "provider_installation_id")
_GROUP_FIELDS = ("genre", "platform", "source", "favorite", "installed_state")


def _valid(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().casefold() not in {"unknown", "n/a", "none", "null"}
    return True


def _norm(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold()))


def _stable_id(kind: str, key: Any) -> str:
    encoded = json.dumps([kind, key], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{kind}-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


def suggest_library_organization(catalog: Any) -> dict[str, Any]:
    """Return stable-ID suggestions from JSON-compatible ``{'games': [...]}``.

    Exact full identity tuples (only when provider/source, source_id and
    installation_id are explicit) are strong duplicate candidates. Normalized
    titles are advisory and partitioned by edition/platform/source signature;
    neither type is an instruction to merge.
    """
    games = catalog.get("games") if isinstance(catalog, dict) else None
    if not isinstance(games, list):
        games = []
    entries: list[dict[str, Any]] = []
    exact: dict[str, list[int]] = defaultdict(list)
    titles: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for i, game in enumerate(games):
        if not isinstance(game, dict):
            continue
        gid = game.get("id")
        if not _valid(gid):
            gid = f"index:{i}"
        variants = game.get("variants") if isinstance(game.get("variants"), list) else []
        sigs = []
        source_labels = set()
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            source = variant.get("source")
            provider = variant.get("provider")
            source_key = provider if _valid(provider) else source
            sid, install = variant.get("source_id"), variant.get("installation_id")
            if _valid(source_key) and _valid(sid) and _valid(install):
                identity = [str(source_key).strip().casefold(), str(sid).strip(), str(install).strip()]
                exact[_stable_id("identity", identity)].append(i)
            for field in ("platform",):
                v = variant.get(field)
                if _valid(v): sigs.append((field, str(v).strip().casefold()))
            if _valid(source_key): source_labels.add(str(source_key).strip())
        edition = game.get("edition")
        if _valid(edition): sigs.append(("edition", str(edition).strip().casefold()))
        title = game.get("title")
        normalized = _norm(title) if isinstance(title, str) else ""
        entries.append({"index": i, "id": gid, "title": title if isinstance(title, str) else "", "variants": variants,
                        "sources": sorted(source_labels)})
        if normalized:
            # Source differences are exactly the useful cross-source review
            # signal, so display them per record instead of splitting candidates.
            titles[(normalized, tuple(sorted(set(sigs))))].append(i)

    groups = []
    for identity, indexes in sorted(exact.items()):
        unique = sorted(set(indexes))
        if len(unique) > 1:
            groups.append({"id": identity, "kind": "exact_identity", "confidence": "strong_candidate", "record_ids": [entries[i]["id"] for i in unique], "record_indexes": unique, "review_only": True})
    for (title, signature), indexes in sorted(titles.items(), key=lambda item: repr(item[0])):
        unique = sorted(set(indexes))
        if len(unique) > 1:
            groups.append({"id": _stable_id("title", [title, signature]), "kind": "normalized_title", "confidence": "advisory", "normalized_title": title, "distinguishing_metadata": [{"field": f, "value": v} for f, v in signature], "record_ids": [entries[i]["id"] for i in unique], "record_indexes": unique, "sources_by_record": [{"record_id": entries[i]["id"], "sources": entries[i]["sources"]} for i in unique], "review_only": True})

    collections: dict[tuple[str, str], list[int]] = defaultdict(list)
    for e in entries:
        game = games[e["index"]]
        candidates: list[tuple[str, Any]] = []
        for field in ("genre", "platform", "source"):
            value = game.get(field)
            if field == "platform" and not _valid(value):
                plats = sorted({str(v["platform"]).strip() for v in e["variants"] if isinstance(v, dict) and _valid(v.get("platform"))})
                value = plats[0] if len(plats) == 1 else None
            if field == "source" and not _valid(value):
                sources = sorted({str(v.get("source", v.get("provider"))).strip() for v in e["variants"] if isinstance(v, dict) and _valid(v.get("source", v.get("provider")))})
                value = sources[0] if len(sources) == 1 else None
            if _valid(value): candidates.append((field, str(value).strip()))
        favorite = game.get("favorite")
        if isinstance(favorite, bool): candidates.append(("favorite", "Favorite" if favorite else "Not favorite"))
        states = {str(v.get("status")).strip().casefold() for v in e["variants"] if isinstance(v, dict) and _valid(v.get("status"))}
        if states:
            candidates.append(("installed_state", "Installed" if states & {"installed", "present", "available"} else "Not installed" if states <= {"not-installed", "not installed", "uninstalled", "absent"} else "Mixed/other status"))
        for field, value in candidates:
            collections[(field, value)].append(e["index"])
    output = []
    for (field, value), indexes in sorted(collections.items()):
        unique = sorted(set(indexes))
        output.append({"id": _stable_id("collection", [field, value]), "field": field, "value": value, "record_ids": [entries[i]["id"] for i in unique], "record_indexes": unique})
    return {"schema": SCHEMA, "preview_only": True, "source_scan_performed": False, "duplicate_candidates": groups, "collections": output}
