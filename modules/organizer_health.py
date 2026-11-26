"""Deterministic read-only Organizer health/duplicate-candidate preview.

Native first pass: preview only. No AI calls, no writes, no process/network/path scans,
no game launching, and no merge/delete of IDs or installation variants.
Untrusted strings are treated as data only.
"""
from __future__ import annotations

import copy
import json
from typing import Any


IMPORTER_MISSING = "importer did not supply metadata"
UNKNOWN_PLATFORM = "unknown (not inferred)"
IMPORTER_ONLY_FIELDS = ("type", "artwork", "tags")


def _safe_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _display(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        text = value.strip()
        return text if text else fallback
    return fallback


def _issue(issue_type: str, field: str, message: str, **extra: Any) -> dict[str, Any]:
    data = {"type": issue_type, "field": field, "message": message}
    for key, val in extra.items():
        if val is not None:
            data[key] = val
    return data


def _normalize_title(title: Any) -> str | None:
    if not isinstance(title, str):
        return None
    norm = " ".join(title.strip().split()).casefold()
    return norm or None


def health_report(games: Any) -> dict[str, Any]:
    """Build a JSON-safe health and duplicate-candidate preview report.

    Input is expected to be a sequence of UI game records (read-only).
    Malformed records are reported, not raised on.
    """
    original = copy.deepcopy(games)
    try:
        records = list(games) if games is not None else []
    except TypeError:
        records = []

    entries: list[dict[str, Any]] = []
    grouped_issues: dict[str, list[dict[str, Any]]] = {}
    issue_totals: dict[str, int] = {}
    title_groups: dict[str, list[dict[str, str]]] = {}

    def add_grouped(issue: dict[str, Any], work_id: str) -> None:
        item = {"work_id": work_id, **issue}
        grouped_issues.setdefault(issue["type"], []).append(item)
        issue_totals[issue["type"]] = issue_totals.get(issue["type"], 0) + 1

    for index, game in enumerate(records):
        if not isinstance(game, dict):
            work_id = f"<malformed-record-{index}>"
            title = "<invalid record>"
            issues = [
                _issue(
                    "malformed_record",
                    "record",
                    "Record is not an object; reported as data without mutation.",
                    index=index,
                )
            ]
            entry = {
                "index": index,
                "id": work_id,
                "title": title,
                "issues": issues,
                "variant_count": 0,
            }
            entries.append(entry)
            for iss in issues:
                add_grouped(iss, work_id)
            continue

        raw_id = game.get("id")
        work_id = _display(raw_id, f"<missing-id-{index}>")
        raw_title = game.get("title")
        title = _display(raw_title, "<missing title>")
        issues: list[dict[str, Any]] = []

        # Title checks
        if not isinstance(raw_title, str) or not raw_title.strip():
            issues.append(
                _issue(
                    "missing_title",
                    "title",
                    f"Title missing: {IMPORTER_MISSING}.",
                )
            )

        # Importer-supplied metadata fields (absence is importer metadata gap, not corruption proof)
        for field in IMPORTER_ONLY_FIELDS:
            if field not in game:
                issues.append(
                    _issue(
                        "missing_metadata",
                        field,
                        f"Field '{field}' absent: {IMPORTER_MISSING}; not proof vendor data is corrupt or a game is broken.",
                    )
                )
            else:
                value = game.get(field)
                empty = value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, (list, dict)) and len(value) == 0)
                if empty:
                    issues.append(
                        _issue(
                            "missing_metadata",
                            field,
                            f"Field '{field}' empty/unknown: {IMPORTER_MISSING}; not proof vendor data is corrupt or a game is broken.",
                        )
                    )

        raw_variants = game.get("variants")
        variants: list[Any] = raw_variants if isinstance(raw_variants, list) else []
        if not isinstance(raw_variants, list):
            issues.append(
                _issue(
                    "missing_variants",
                    "variants",
                    "Variants missing or not a list: importer did not supply usable variant records.",
                )
            )

        variant_summaries: list[dict[str, Any]] = []
        source_install_counts: dict[tuple[str, str], int] = {}

        for v_index, variant in enumerate(variants):
            if not isinstance(variant, dict):
                issues.append(
                    _issue(
                        "malformed_variant",
                        "variants",
                        "Variant is not an object; reported as data without mutation.",
                        variant_index=v_index,
                    )
                )
                variant_summaries.append(
                    {"variant_index": v_index, "source": "<invalid>", "source_id": "<invalid>", "platform": "<invalid>"}
                )
                continue

            source = _display(variant.get("source"), "<missing source>")
            source_id = _display(variant.get("source_id"), "<missing source_id>")
            installation_id = _display(variant.get("installation_id"), "<missing installation_id>")
            raw_platform = variant.get("platform")
            platform = _display(raw_platform, UNKNOWN_PLATFORM)
            if platform == UNKNOWN_PLATFORM or (isinstance(raw_platform, str) and raw_platform.strip().casefold() == "unknown"):
                if isinstance(raw_platform, str) and raw_platform.strip().casefold() == "unknown":
                    platform = UNKNOWN_PLATFORM
                issues.append(
                    _issue(
                        "unknown_platform",
                        "platform",
                        f"Platform is {UNKNOWN_PLATFORM}; never inferred from title.",
                        variant_index=v_index,
                        source=source,
                        source_id=source_id,
                    )
                )
                platform = UNKNOWN_PLATFORM

            variant_summaries.append(
                {
                    "variant_index": v_index,
                    "source": source,
                    "source_id": source_id,
                    "installation_id": installation_id,
                    "platform": platform,
                    "status": _display(variant.get("status"), "<missing status>"),
                }
            )

            key = (source, source_id)
            source_install_counts[key] = source_install_counts.get(key, 0) + 1

        # Same source+source_id with multiple installs/variant records
        for (source, source_id), count in sorted(source_install_counts.items()):
            if count > 1:
                issues.append(
                    _issue(
                        "multiple_install_records",
                        "variants",
                        f"Same source record appears {count} times (source={source}, source_id={source_id}); candidates/records only, not merged or deleted.",
                        source=source,
                        source_id=source_id,
                        count=count,
                    )
                )

        norm_title = _normalize_title(raw_title)
        if norm_title is not None:
            title_groups.setdefault(norm_title, []).append({"id": work_id, "title": title})

        entry = {
            "index": index,
            "id": work_id,
            "title": title,
            "variant_count": len(variants) if isinstance(raw_variants, list) else 0,
            "variants": variant_summaries,
            "issues": issues,
        }
        entries.append(entry)
        for iss in issues:
            add_grouped(iss, work_id)

    duplicate_candidates: list[dict[str, Any]] = []
    for norm_title, refs in sorted(title_groups.items(), key=lambda kv: kv[0]):
        distinct_ids = sorted({r["id"] for r in refs})
        if len(distinct_ids) > 1:
            titles = sorted({r["title"] for r in refs})
            duplicate_candidates.append(
                {
                    "normalized_title": norm_title,
                    "titles": titles,
                    "ids": distinct_ids,
                    "candidate_count": len(distinct_ids),
                    "note": "Candidate duplicates only (same title across different IDs). Never merge/delete IDs or installation variants.",
                }
            )

    report = {
        "schema": "organizer-health-preview-v1",
        "preview_only": True,
        "entry_count": len(entries),
        "issue_totals": {k: issue_totals[k] for k in sorted(issue_totals)},
        "grouped_issues": {k: grouped_issues[k] for k in sorted(grouped_issues)},
        "duplicate_candidates": duplicate_candidates,
        "entries": entries,
    }

    # Ensure JSON-safe output and verify input immutability invariant for callers.
    json.dumps(report, ensure_ascii=False)
    if games != original:
        raise AssertionError("health_report must not mutate input")
    return report
