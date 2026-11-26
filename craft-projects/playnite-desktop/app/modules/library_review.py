"""Read-only native review of an already-loaded ``{"games": [...]}`` catalog.

No adapters, filesystem access, provider calls, launch recipes, or apply operations.
The legacy health report is preserved verbatim; the surrounding contract adds
snapshot-local references, original identities, and explicit import coverage.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from modules.organizer_health import UNKNOWN_PLATFORM, health_report


SCHEMA = "library-review-v1"
GAME_FIELDS = ("title", "type", "artwork", "tags")
VARIANT_FIELDS = ("source", "source_id", "installation_id", "platform", "status")
TEXT_FIELDS = {"id", "title", "type", *VARIANT_FIELDS}
LIMITS = (
    "Only caller-supplied games and variants were reviewed; no sources were scanned.",
    "Absent fields mean not imported; empty/unknown fields do not prove upstream metadata is missing.",
    "Supplied provenance and installation status are unverified claims, not runtime or payload verification.",
    "Title matches and repeated source records are review candidates, not proof of duplicate editions or platforms.",
    "Original IDs are retained, not synthesized or reconciled across providers, accounts, or hosts.",
    "Record/variant indexes address this snapshot only; missing or colliding IDs cannot identify records across refreshes.",
    "No plugin parity, classification, repair, preferred-copy selection, hide, merge, delete, or launch capability is provided.",
)


def _field_state(record: dict[str, Any], field: str) -> str:
    if field not in record:
        return "not_imported"
    value = record[field]
    if value is None or (isinstance(value, str) and not value.strip()):
        return "unknown"
    if isinstance(value, (list, dict)) and not value:
        return "unknown"
    if field in TEXT_FIELDS and not isinstance(value, str):
        return "invalid"
    if field == "tags" and not isinstance(value, list):
        return "invalid"
    if field in {"source", "platform", "status", "type"} and value.strip().casefold() == "unknown":
        return "unknown"
    if field == "platform" and value.strip() == UNKNOWN_PLATFORM:
        return "unknown"
    return "supplied"


def _provenance(record: dict[str, Any]) -> dict[str, Any]:
    # Forward only explicit opaque claims, not unrelated recipe/command fields.
    return {
        key: copy.deepcopy(record[key])
        for key in ("evidence", "provenance", "source_context", "observation")
        if key in record
    }


def _title_key(record: Any) -> str | None:
    title = record.get("title") if isinstance(record, dict) else None
    return " ".join(title.strip().split()).casefold() if isinstance(title, str) else None


def review_catalog(catalog: Any) -> dict[str, Any]:
    """Return a deterministic, detached JSON review; never mutate the catalog.

    Accept a JSON-compatible catalog object with a list-valued ``games`` key.
    Invalid envelopes/non-JSON game data yield an empty health report plus input
    diagnostics. Malformed list records/variants remain visible to health_report.
    Extra fields are ignored, except explicit provenance/source/observation claims
    and the original identity fields listed below. Claims are not verified.
    """
    input_issues: list[dict[str, str]] = []
    games: list[Any] = []
    if not isinstance(catalog, dict):
        input_issues.append({"code": "invalid_catalog", "path": "", "message": "Expected a loaded catalog object."})
    elif "games" not in catalog:
        input_issues.append({"code": "missing_games", "path": "/games", "message": "Catalog did not supply games."})
    elif not isinstance(catalog["games"], list):
        input_issues.append({"code": "invalid_games", "path": "/games", "message": "Expected a list; no records were coerced."})
    else:
        try:
            # Validate JSON data before the legacy engine's deepcopy/equality check.
            json.dumps(catalog["games"], allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            input_issues.append({"code": "non_json_games", "path": "/games", "message": "Games must contain finite, acyclic JSON data."})
        else:
            games = catalog["games"]

    health = health_report(games)
    records: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    def field_coverage(raw: dict[str, Any], fields: tuple[str, ...], ref: dict[str, Any], path: str) -> dict[str, str]:
        states = {field: _field_state(raw, field) for field in fields}
        for field, state in states.items():
            if state != "supplied":
                gaps.append({**ref, "field": field, "path": f"{path}/{field}", "state": state})
        return states

    for index, game in enumerate(games):
        ref = {"record_index": index, "id": copy.deepcopy(game.get("id")) if isinstance(game, dict) else None}
        entry: dict[str, Any] = {**ref, "state": "supplied" if isinstance(game, dict) else "malformed", "variants": []}
        records.append(entry)
        if not isinstance(game, dict):
            continue
        path = f"/games/{index}"
        entry["id_state"] = _field_state(game, "id")
        entry["provenance"] = _provenance(game)
        entry["metadata_coverage"] = field_coverage(game, GAME_FIELDS, ref, path)
        variants = game.get("variants")
        entry["variants_state"] = "not_imported" if "variants" not in game else "supplied" if isinstance(variants, list) else "invalid"
        if not isinstance(variants, list):
            continue
        for v_index, variant in enumerate(variants):
            v_ref = {**ref, "variant_index": v_index}
            detail: dict[str, Any] = {**v_ref, "state": "supplied" if isinstance(variant, dict) else "malformed"}
            entry["variants"].append(detail)
            if not isinstance(variant, dict):
                continue
            # Opaque, exact values: no trimming, case folding, inference, or ID generation.
            detail["identity"] = {
                key: copy.deepcopy(variant[key])
                for key in ("source", "source_id", "installation_id", "provider", "instance", "host", "record_key", "variant_key", "external_id", "provider_installation_id")
                if key in variant
            }
            detail["provenance"] = _provenance(variant)
            detail["field_coverage"] = field_coverage(variant, VARIANT_FIELDS, v_ref, f"{path}/variants/{v_index}")

    proposals: list[dict[str, Any]] = []
    for candidate in health["duplicate_candidates"]:
        # Match title as well as ID so colliding IDs under other titles cannot
        # accidentally enter a proposal. Normalization is exactly the engine's.
        matching = [
            records[entry["index"]]
            for entry in health["entries"]
            if entry["id"] in candidate["ids"]
            and _title_key(games[entry["index"]]) == candidate["normalized_title"]
        ]
        proposals.append({"kind": "normalized_title", "basis": copy.deepcopy(candidate), "records": copy.deepcopy(matching), "review_only": True})
    for entry in health["entries"]:
        for issue in entry["issues"]:
            if issue["type"] == "multiple_install_records":
                proposals.append({"kind": "repeated_source_record", "basis": copy.deepcopy(issue), "records": [copy.deepcopy(records[entry["index"]])], "review_only": True})

    return {
        "schema": SCHEMA,
        "preview_only": True,
        "input_issues": input_issues,
        "coverage": {
            "scope": "caller_supplied_catalog",
            "input_state": "invalid" if input_issues else "accepted",
            "reviewed_records": len(records),
            "source_scan_performed": False,
            "source_completeness": "unknown",
            "provenance_verification": "not_performed",
            "limits": list(LIMITS),
        },
        "health_report": health,
        "records": records,
        "metadata_gaps": gaps,
        "duplicate_proposals": proposals,
    }
