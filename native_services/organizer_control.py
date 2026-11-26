"""Native Organizer and Catalog Link Control Service for Waystone.

Provides high-level, live-updating metadata management and duplicate reconciliation:
- Atomic scoped metadata edits (title, platform, tags, categories, libraries, etc.)
- Optimistic concurrency control via expected_revision validation against shared UI state
- Request ID idempotency preserving retries while rejecting stale revisions
- Strict integration with catalog_reconciliation.load_assignments (no silent invalid manifest fallback)
- Duplicate grouping proposal and application via CatalogLinkStore
- 1-step audited rollback via OrganizerStore/CatalogLinkStore
- Read-only base catalog querying with active overrides and reconciliation attribution merged
- Strictly app-owned database writes; zero arbitrary shell or subprocess execution
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import sqlite3
from typing import Any

from native_services.organizer_store import (
    OrganizerStore,
    Conflict as OrganizerConflict,
    Plan as OrganizerPlan,
    FIELDS as ORGANIZER_FIELDS,
)
from native_services.catalog_link_store import (
    CatalogLinkStore,
    Conflict as CatalogLinkConflict,
    Plan as CatalogLinkPlan,
)
from native_services.catalog_reconciliation import load_assignments, KIND as RECONCILIATION_KIND


class ControlError(ValueError):
    """Base error for OrganizerControl operational failures."""


class ConcurrencyConflict(ControlError):
    """Raised when an edit plan is stale or a request ID is reused with different intent."""


@dataclass(frozen=True)
class GameRecord:
    canonical_id: str
    title: str | None = None
    platform: str | None = None
    tags: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    libraries: tuple[str, ...] = ()
    genres: tuple[str, ...] = ()
    developers: tuple[str, ...] = ()
    publishers: tuple[str, ...] = ()
    description: str | None = None
    favorite: bool | None = None
    hidden: bool | None = None
    completion: str | None = None
    has_overrides: bool = False
    is_duplicate: bool = False
    representative_id: str | None = None
    overrides: dict[str, Any] = None
    source: str | None = None  # Original raw provenance

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['tags'] = list(self.tags)
        d['categories'] = list(self.categories)
        d['libraries'] = list(self.libraries)
        d['genres'] = list(self.genres)
        d['developers'] = list(self.developers)
        d['publishers'] = list(self.publishers)
        return d


class OrganizerControl:
    """Cohesive management service wrapping OrganizerStore and CatalogLinkStore."""

    def __init__(
        self,
        organizer_store: OrganizerStore,
        catalog_link_store: CatalogLinkStore | None = None,
        library_db_path: str | Path | None = None,
        reconciliation_path: str | Path | None = None,
    ):
        self.organizer_store = organizer_store
        self.catalog_link_store = catalog_link_store
        self.library_db_path = Path(library_db_path).resolve() if library_db_path else None
        if self.library_db_path and (not self.library_db_path.is_file() or self.library_db_path.is_symlink()):
            raise ValueError(f"Library DB path '{self.library_db_path}' must be an existing non-symlink file")

        self.reconciliation_path = Path(reconciliation_path).resolve() if reconciliation_path else None
        if self.reconciliation_path and (not self.reconciliation_path.is_file() or self.reconciliation_path.is_symlink()):
            raise ValueError(f"Reconciliation path '{self.reconciliation_path}' must be an existing non-symlink file")

    def _load_reconciliation(self) -> dict[str, str]:
        """Load library attribution assignments using official load_assignments.

        Never silently swallows invalid reconciliation manifests or falls back to stale Unassigned.
        """
        if not self.reconciliation_path:
            return {}
        canonical_ids = self.organizer_store.canonical_ids()
        # Direct call to load_assignments; fails fast and visibly on corrupt or unsupported manifests
        return load_assignments(self.reconciliation_path, canonical_ids)

    def _read_base_metadata(self, canonical_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Query read-only base metadata from library_db_path if available."""
        if not self.library_db_path or not canonical_ids:
            return {}

        results: dict[str, dict[str, Any]] = {}
        uri = self.library_db_path.as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            has_games = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='games'"
            ).fetchone()
            if not has_games:
                return {}

            placeholders = ','.join('?' for _ in canonical_ids)
            query = f"SELECT canonical_id, title, provider, metadata_json FROM games WHERE canonical_id IN ({placeholders})"
            for row in db.execute(query, canonical_ids):
                cid = row['canonical_id']
                meta = {}
                if row['metadata_json']:
                    try:
                        meta = json.loads(row['metadata_json'])
                    except Exception:
                        pass
                meta['title'] = row['title'] or meta.get('title')
                meta['provider'] = row['provider']
                # The native migration retains Playnite field names with exact
                # resolved relationship names. Expose the same semantic fields
                # as the UI, not GUID arrays or title-based inference.
                for public, original in (('tags', 'TagIds'), ('categories', 'CategoryIds'),
                                         ('genres', 'GenreIds'), ('developers', 'DeveloperIds'),
                                         ('publishers', 'PublisherIds')):
                    key = original + '_resolved_names'
                    if key in meta:
                        names = meta[key]
                        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
                            raise ValueError('Invalid native relationship name projection')
                        meta[public] = list(names)
                if 'PlatformIds_resolved_names' in meta:
                    names = meta['PlatformIds_resolved_names']
                    if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
                        raise ValueError('Invalid native platform name projection')
                    meta['platform'] = ' / '.join(names) or None
                results[cid] = meta

        return results

    def list_games(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        platform: str | None = None,
        tag: str | None = None,
        category: str | None = None,
        library: str | None = None,
    ) -> list[GameRecord]:
        """List catalog games with active overrides, duplicate links, and reconciliation applied."""
        all_canonical_ids = self.organizer_store.canonical_ids()
        snapshot = self.organizer_store.snapshot()
        overrides_map = snapshot.get('overrides', {})
        links_map = self.catalog_link_store.resolution() if self.catalog_link_store else {}
        reconciled_map = self._load_reconciliation()
        base_meta = self._read_base_metadata(list(all_canonical_ids))

        records: list[GameRecord] = []
        for cid in all_canonical_ids:
            overrides = overrides_map.get(cid, {})
            base = base_meta.get(cid, {})
            source = base.get('provider')

            # Effective fields (overrides take precedence)
            title = overrides.get('title') if 'title' in overrides else base.get('title', cid)
            plat = overrides.get('platform') if 'platform' in overrides else base.get('platform')
            tags = tuple(overrides.get('tags') if 'tags' in overrides else base.get('tags', []))
            cats = tuple(overrides.get('categories') if 'categories' in overrides else base.get('categories', []))

            # Effective library attribution:
            # 1. OrganizerStore overrides
            # 2. Reconciliation overlay (resolves raw Unassigned to package catalog or Emulation)
            # 3. Base metadata libraries
            # 4. Base provider if not Unassigned
            if 'libraries' in overrides:
                libs = tuple(overrides['libraries'] or [])
            elif cid in reconciled_map:
                libs = (reconciled_map[cid],)
            elif base.get('libraries'):
                libs = tuple(base['libraries'])
            elif source and source != 'Unassigned':
                libs = (source,)
            else:
                libs = ()

            genres = tuple(overrides.get('genres') if 'genres' in overrides else base.get('genres', []))
            devs = tuple(overrides.get('developers') if 'developers' in overrides else base.get('developers', []))
            pubs = tuple(overrides.get('publishers') if 'publishers' in overrides else base.get('publishers', []))
            desc = overrides.get('description') if 'description' in overrides else base.get('description')
            fav = overrides.get('favorite') if 'favorite' in overrides else base.get('favorite', False)
            hid = overrides.get('hidden') if 'hidden' in overrides else base.get('hidden', False)
            comp = overrides.get('completion') if 'completion' in overrides else base.get('completion')

            rep_id = links_map.get(cid)
            is_dup = rep_id is not None

            # Filters
            if query and query.lower() not in (title or '').lower() and query.lower() not in cid.lower():
                continue
            if platform and (plat or '').lower() != platform.lower():
                continue
            if tag and tag not in tags:
                continue
            if category and category not in cats:
                continue
            if library and library.lower() not in [L.lower() for L in libs]:
                continue

            records.append(GameRecord(
                canonical_id=cid,
                title=title,
                platform=plat,
                tags=tags,
                categories=cats,
                libraries=libs,
                genres=genres,
                developers=devs,
                publishers=pubs,
                description=desc,
                favorite=fav,
                hidden=hid,
                completion=comp,
                has_overrides=bool(overrides),
                is_duplicate=is_dup,
                representative_id=rep_id,
                overrides=dict(overrides) if overrides else {},
                source=source,
            ))

        return records[offset: offset + limit]

    def get_game(self, canonical_id: str) -> GameRecord:
        """Get a single game record by canonical ID."""
        all_ids = self.organizer_store.canonical_ids()
        if canonical_id not in all_ids:
            raise ControlError(f"Game with canonical ID '{canonical_id}' not found in organizer catalog")

        snapshot = self.organizer_store.snapshot()
        overrides = snapshot.get('overrides', {}).get(canonical_id, {})
        base_meta = self._read_base_metadata([canonical_id]).get(canonical_id, {})
        links_map = self.catalog_link_store.resolution() if self.catalog_link_store else {}
        reconciled_map = self._load_reconciliation()

        source = base_meta.get('provider')
        title = overrides.get('title') if 'title' in overrides else base_meta.get('title', canonical_id)
        plat = overrides.get('platform') if 'platform' in overrides else base_meta.get('platform')
        tags = tuple(overrides.get('tags') if 'tags' in overrides else base_meta.get('tags', []))
        cats = tuple(overrides.get('categories') if 'categories' in overrides else base_meta.get('categories', []))

        if 'libraries' in overrides:
            libs = tuple(overrides['libraries'] or [])
        elif canonical_id in reconciled_map:
            libs = (reconciled_map[canonical_id],)
        elif base_meta.get('libraries'):
            libs = tuple(base_meta['libraries'])
        elif source and source != 'Unassigned':
            libs = (source,)
        else:
            libs = ()

        genres = tuple(overrides.get('genres') if 'genres' in overrides else base_meta.get('genres', []))
        devs = tuple(overrides.get('developers') if 'developers' in overrides else base_meta.get('developers', []))
        pubs = tuple(overrides.get('publishers') if 'publishers' in overrides else base_meta.get('publishers', []))
        desc = overrides.get('description') if 'description' in overrides else base_meta.get('description')
        fav = overrides.get('favorite') if 'favorite' in overrides else base_meta.get('favorite', False)
        hid = overrides.get('hidden') if 'hidden' in overrides else base_meta.get('hidden', False)
        comp = overrides.get('completion') if 'completion' in overrides else base_meta.get('completion')

        rep_id = links_map.get(canonical_id)

        return GameRecord(
            canonical_id=canonical_id,
            title=title,
            platform=plat,
            tags=tags,
            categories=cats,
            libraries=libs,
            genres=genres,
            developers=devs,
            publishers=pubs,
            description=desc,
            favorite=fav,
            hidden=hid,
            completion=comp,
            has_overrides=bool(overrides),
            is_duplicate=(rep_id is not None),
            representative_id=rep_id,
            overrides=dict(overrides) if overrides else {},
            source=source,
        )

    def update_metadata(
        self,
        canonical_id: str,
        changes: dict[str, Any],
        request_id: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Apply an atomic metadata change to a canonical game with optimistic revision check."""
        return self.batch_update_metadata(
            edits=[{'game_id': canonical_id, 'changes': changes}],
            request_id=request_id,
            expected_revision=expected_revision,
        )

    def batch_update_metadata(
        self,
        edits: list[dict[str, Any]],
        request_id: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Apply a batch of metadata edits (1..500) atomically with optimistic revision check."""
        if not edits:
            raise ControlError("Must supply at least one edit")

        # Validate fields
        for edit in edits:
            changes = edit.get('changes', {})
            invalid = set(changes.keys()) - ORGANIZER_FIELDS
            if invalid:
                raise ControlError(f"Unsupported metadata override fields: {sorted(invalid)}")

        # 1. Check existing audit for idempotent retry FIRST
        for audit_entry in self.organizer_store.audit():
            if audit_entry['request_id'] == request_id:
                with self.organizer_store._connect() as db:
                    row = db.execute("SELECT intent FROM audit WHERE request_id=?", (request_id,)).fetchone()
                    if row:
                        intent = json.loads(row['intent'])
                        recorded_edits = intent.get('plan', {}).get('edits', [])
                        norm_input = [{'game_id': e['game_id'], 'changes': e['changes']} for e in edits]
                        if recorded_edits == norm_input:
                            return audit_entry
                        raise ConcurrencyConflict("Request ID already used for a different operation")

        # 2. Validate caller's expected_revision against current shared state revision
        current_rev = self.organizer_store.snapshot()['revision']
        if expected_revision is not None and expected_revision != current_rev:
            raise ConcurrencyConflict(
                f"Stale revision: caller expected revision {expected_revision}, "
                f"but current database revision is {current_rev}. Refresh state before applying changes."
            )

        # 3. Preview and apply
        try:
            plan = self.organizer_store.preview(edits)
            result = self.organizer_store.apply(plan, request_id)
            return result
        except OrganizerConflict as e:
            raise ConcurrencyConflict(str(e)) from e
        except Exception as e:
            raise ControlError(str(e)) from e

    def propose_duplicates(self, groups: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate a dry-run duplicate resolution preview plan."""
        if not self.catalog_link_store:
            raise ControlError("CatalogLinkStore is not configured")

        try:
            plan = self.catalog_link_store.preview(groups)
            return plan.as_dict()
        except CatalogLinkConflict as e:
            raise ConcurrencyConflict(str(e)) from e
        except Exception as e:
            raise ControlError(str(e)) from e

    def apply_duplicates(
        self,
        groups: list[dict[str, Any]],
        request_id: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Commit duplicate representative links to CatalogLinkStore with optimistic revision check."""
        if not self.catalog_link_store:
            raise ControlError("CatalogLinkStore is not configured")

        # 1. Check existing audit for idempotent retry
        for audit_entry in self.catalog_link_store.audit():
            if audit_entry['request_id'] == request_id:
                return audit_entry

        # 2. Validate expected revision against CatalogLinkStore snapshot revision
        current_rev = self.catalog_link_store.snapshot()['revision']
        if expected_revision is not None and expected_revision != current_rev:
            raise ConcurrencyConflict(
                f"Stale link revision: caller expected revision {expected_revision}, "
                f"but current database revision is {current_rev}. Refresh state before applying changes."
            )

        try:
            plan = self.catalog_link_store.preview(groups)
            result = self.catalog_link_store.apply(plan, request_id)
            return result
        except CatalogLinkConflict as e:
            raise ConcurrencyConflict(str(e)) from e
        except Exception as e:
            raise ControlError(str(e)) from e

    def undo(
        self,
        store_type: str,
        audit_id: int,
        request_id: str,
    ) -> dict[str, Any]:
        """Undo the latest applied operation on the specified store."""
        st = store_type.lower().strip()
        if st in ('organizer', 'metadata'):
            try:
                return self.organizer_store.undo(audit_id, request_id)
            except OrganizerConflict as e:
                raise ConcurrencyConflict(str(e)) from e
            except Exception as e:
                raise ControlError(str(e)) from e
        elif st in ('catalog_link', 'links', 'duplicates'):
            if not self.catalog_link_store:
                raise ControlError("CatalogLinkStore is not configured")
            try:
                return self.catalog_link_store.undo(audit_id, request_id)
            except CatalogLinkConflict as e:
                raise ConcurrencyConflict(str(e)) from e
            except Exception as e:
                raise ControlError(str(e)) from e
        else:
            raise ControlError(f"Unknown store type '{store_type}'; choose 'organizer' or 'catalog_link'")

    def audit_log(self, store_type: str = 'all', limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent audit logs from stores."""
        entries = []
        st = store_type.lower().strip()
        if st in ('all', 'organizer', 'metadata'):
            for item in self.organizer_store.audit():
                item_copy = dict(item)
                item_copy['store'] = 'organizer'
                entries.append(item_copy)

        if st in ('all', 'catalog_link', 'links', 'duplicates') and self.catalog_link_store:
            for item in self.catalog_link_store.audit():
                item_copy = dict(item)
                item_copy['store'] = 'catalog_link'
                entries.append(item_copy)

        entries.sort(key=lambda x: x.get('audit_id', 0), reverse=True)
        return entries[:limit]
