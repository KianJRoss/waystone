"""Presentation/service boundary for reviewable catalog identity links.

Candidate generation is display-only. Mutations require separate explicit
preview and apply calls against the app-owned CatalogLinkStore overlay.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))
from modules.catalog_link_candidates import generate_link_candidates
from native_services.catalog_link_store import CatalogLinkStore

MAX_PROJECTION = 65536
MAX_ROWS = 2000


def _safe_display(value, limit=120):
    """Render imported text conservatively; never expose path/command-shaped data."""
    if not isinstance(value, str):
        return 'Untitled record'
    value = value.strip()
    if (not value or re.search(r'(?:[\\/]|\b(?:https?|file)://|\b(?:sudo|bash|sh|python|powershell)\b|\$\(|[;&|`])', value, re.I)):
        return 'Imported text withheld'
    value = ''.join(c for c in value if c.isprintable())
    return value[:limit] + ('…' if len(value) > limit else '')


def _conflicts(records):
    """Block a proposal when any known platforms or editions disagree."""
    for field in ('platforms', 'editions'):
        known = {item.casefold() for row in records for item in row.get(field, [])
                 if isinstance(item, str) and item.strip()}
        if len(known) > 1:
            return True
    return False


class CatalogLinkView(QObject):
    changed = Signal()
    operationFinished = Signal(str)

    def __init__(self, catalog=None, link_state=None, enable_link_state=False, parent=None):
        super().__init__(parent)
        self._catalog = catalog if isinstance(catalog, dict) else {'games': []}
        result = generate_link_candidates(self._catalog)
        self._candidates = result['candidate_clusters'][:MAX_ROWS]
        self._blocked = {c['id'] for c in self._candidates if _conflicts(c['records'])}
        self._linked_ids = set()
        self._store = None
        self._plan = None
        self._last_result = ''
        self._error = ''
        self._state_error = ''
        self._selected = []
        # Opening state is strictly read/write to an existing app-owned DB.
        # This constructor never creates a file, regardless of opt-in flag.
        self._enabled = bool(enable_link_state)
        if link_state is not None and self._enabled:
            path = Path(link_state)
            if not path.is_absolute():
                raise ValueError('Catalog Link state must be an explicit absolute path')
            if path.exists():
                try:
                    store = CatalogLinkStore(path)
                    known, current = set(store.canonical_ids()), set(self._catalog_ids())
                    if len(known & current) * 2 < len(known):
                        # Not this library (most saved games are absent): refuse rather than mix two catalogs.
                        raise ValueError('Catalog generation differs')
                    store.register_ids(current)  # the library grew or changed a little: new games join, links kept
                    self._store = store
                    self._refresh_linked()
                except (ValueError, OSError, sqlite3.Error):
                    self._state_error = 'Saved link state does not match this catalog generation; preview/apply disabled.'
                    self._error = self._state_error
        self._publish()

    def _publish(self):
        self.changed.emit()

    def _refresh_linked(self):
        mapping = self._store.resolution() if self._store else {}
        self._linked_ids = set(mapping) | set(mapping.values())

    def replaceCatalog(self, catalog):
        """Refresh review candidates after a source sync; retain only valid saved links."""
        if not isinstance(catalog, dict) or not isinstance(catalog.get('games'), list):
            raise ValueError('Expected a catalog projection')
        self._catalog = catalog
        result = generate_link_candidates(catalog)
        self._candidates = result['candidate_clusters'][:MAX_ROWS]
        self._blocked = {c['id'] for c in self._candidates if _conflicts(c['records'])}
        if self._store is not None:
            self._store.register_ids(set(self._catalog_ids()))
            self._refresh_linked()
        self._selected = []
        self._plan = None
        self._publish()

    def _is_blocked(self, candidate):
        return (candidate['id'] in self._blocked or any(
            record.get('canonical_game_id') in self._linked_ids
            for record in candidate['records']))

    @Property(bool, constant=True)
    def available(self):
        return bool(self._candidates)

    @Property(bool, notify=changed)
    def storeEnabled(self):
        return self._store is not None

    @Property('QVariantList', notify=changed)
    def rows(self):
        rows = []
        for c in self._candidates:
            records = c['records']
            rows.append({'candidateId': c['id'],
                         'kind': c['kind'],
                         'confidence': c['confidence_tier'].replace('_', ' '),
                         'records': [{'title': _safe_display(r.get('title')),
                                      'sources': [_safe_display(s, 64) for s in r.get('sources', [])[:8]],
                                      'platforms': [_safe_display(s, 64).title() for s in r.get('platforms', [])[:8]],
                                      'editions': [_safe_display(s, 64).title() for s in r.get('editions', [])[:8]],
                                      'sourceCount': min(99, len(r.get('sources', [])))} for r in records],
                         'evidence': [self._evidence_label(e) for e in c.get('evidence', [])[:4]],
                         'blocked': self._is_blocked(c),
                         'linked': any(r.get('canonical_game_id') in self._linked_ids for r in records),
                         'selected': c['id'] in self._selected})
        return rows

    @staticmethod
    def _evidence_label(e):
        return {'exact_identifier': 'Exact identifier evidence (value hidden)',
                'normalized_title': 'Normalized title evidence',
                'explicit_platform': 'Matching explicit platform',
                'explicit_edition': 'Matching explicit edition',
                'edition': 'Edition unavailable on both records'}.get(e.get('type'), 'Review evidence')

    @Property(str, notify=changed)
    def previewText(self):
        return self._last_result

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property('QVariantMap', notify=changed)
    def resolution(self):
        """Current detached member-to-representative map, empty without valid state."""
        return self._store.resolution() if self._store else {}

    @Property('QVariantList', notify=changed)
    def history(self):
        if not self._store:
            return []
        return [{'auditId': row['audit_id'], 'kind': row['kind'], 'revision': row['revision']}
                for row in self._store.audit()[:8]]

    @Property(int, notify=changed)
    def undoableAuditId(self):
        if not self._store:
            return 0
        latest = self._store.audit()
        revision = self._store.snapshot()['revision']
        if latest and latest[0]['kind'] == 'apply' and latest[0]['revision'] == revision:
            return latest[0]['audit_id']
        return 0

    @Property(str, notify=changed)
    def latestAction(self):
        if not self._store:
            return 'No link changes have been recorded.'
        latest = self._store.audit()
        if not latest:
            return 'No link changes have been recorded.'
        row = latest[0]
        return f"Latest saved action: {row['kind']} · revision {row['revision']}" + (
            ' · can undo' if self.undoableAuditId else '')

    @Property(str, notify=changed)
    def status(self):
        if self._state_error:
            return self._state_error
        if not self._store:
            return 'Review only. Explicit --link-state and enablement are required for preview/apply.'
        return f'{len(self._candidates)} review candidates; no changes applied automatically.'

    @Slot('QVariantList')
    def selectCandidates(self, candidate_ids):
        """Save only display selection; it has no store effect."""
        allowed = {c['id'] for c in self._candidates if not self._is_blocked(c)}
        if not isinstance(candidate_ids, list) or len(candidate_ids) > MAX_ROWS:
            self._error = 'Invalid candidate selection'; self._publish(); return
        if any(not isinstance(i, str) or i not in allowed for i in candidate_ids):
            self._error = 'Unknown or blocked candidate'; self._publish(); return
        self._selected = list(dict.fromkeys(candidate_ids))
        self._plan = None
        self._error = ''
        self._last_result = ''
        self._publish()

    @Slot(str, bool, result=bool)
    def createLinkState(self, absolute_path, explicitly_enabled):
        """Create the separate overlay only through this explicit opt-in call."""
        self._error = ''
        try:
            path = Path(absolute_path)
            if explicitly_enabled is not True or not path.is_absolute():
                raise ValueError('Creation requires explicit enablement and an absolute --link-state path')
            if not path.parent.is_dir():
                raise ValueError('Link-state parent directory must already exist')
            exact_ids = self._catalog_ids()
            # Store validates uniqueness and bounds. No source DB is opened.
            self._store = CatalogLinkStore.create(path, list(exact_ids))
            if self._store.canonical_ids() != exact_ids:
                self._store = None
                raise ValueError('Created state catalog mismatch')
            self._state_error = ''
            self._refresh_linked()
            self._enabled = True
        except (ValueError, TypeError, OSError):
            self._error = 'Link state could not be created; verify the explicit app-owned path and catalog IDs.'
            self._publish()
            return False
        self._publish()
        return True

    def _catalog_ids(self):
        return tuple(sorted(game['id'] for game in self._catalog.get('games', [])
                            if isinstance(game, dict) and isinstance(game.get('id'), str)
                            and game['id']))

    def _groups(self, ids):
        by_id = {c['id']: c for c in self._candidates}
        groups = []
        for candidate_id in ids:
            candidate = by_id.get(candidate_id)
            if candidate is None or self._is_blocked(candidate):
                raise ValueError('Unknown or incompatible candidate')
            # Store receives exact canonical catalog IDs only. It never sees titles.
            members = candidate['records']
            exact_ids = [r.get('canonical_game_id') for r in members]
            if any(not isinstance(i, str) or not i for i in exact_ids) or len(set(exact_ids)) != len(exact_ids):
                raise ValueError('Candidate lacks distinct exact catalog IDs')
            ids_sorted = sorted(exact_ids)
            groups.append({'canonical_ids': ids_sorted, 'representative_id': ids_sorted[0]})
        return groups

    @Slot('QVariantList', result=str)
    def preview(self, candidate_ids):
        self._error = ''; self._last_result = ''; self._plan = None
        try:
            if not self._store:
                raise ValueError('Link state is not enabled')
            if not isinstance(candidate_ids, list) or not candidate_ids:
                raise ValueError('Select one or more candidates')
            self._plan = self._store.preview(self._groups(candidate_ids))
            plan = self._plan.as_dict()
            # Evidence output is intentionally summarized, never raw imported IDs.
            self._last_result = _bounded(json.dumps({'preview': True,
                'groups': len(plan['groups']), 'links_to_add': len(plan['after']) - len(plan['before']),
                'revision': plan['revision']}, ensure_ascii=False, indent=2))
        except (ValueError, TypeError):
            self._error = 'Preview unavailable; verify selection, exact IDs, and current link-state revision.'
        self._publish()
        return self._last_result

    @Slot(str, result=str)
    def apply(self, request_id):
        """Apply only a previously previewed plan, on explicit caller action."""
        self._error = ''; self._last_result = ''
        try:
            if not self._store or self._plan is None:
                raise ValueError('Preview must succeed before explicit apply')
            result = self._store.apply(self._plan, request_id)
            self._last_result = _bounded(json.dumps({'applied': True,
                'audit_id': result['audit_id'], 'revision': result['revision']}, indent=2))
            self._plan = None
            self._refresh_linked()
        except (ValueError, TypeError):
            self._error = 'Apply refused; preview again and verify the request ID and current revision.'
        self._publish()
        return self._last_result

    @Slot(int, str, result=str)
    def undo(self, audit_id, request_id):
        self._error = ''; self._last_result = ''; self._plan = None
        try:
            if not self._store:
                raise ValueError('Link state is not enabled')
            result = self._store.undo(audit_id, request_id)
            self._last_result = _bounded(json.dumps({'undone': True,
                'audit_id': result['audit_id'], 'revision': result['revision']}, indent=2))
            self._refresh_linked()
        except (ValueError, TypeError):
            self._error = 'Undo refused; verify the latest apply audit ID and request ID.'
        self._publish()
        return self._last_result


def _bounded(text):
    raw = text.encode('utf-8')
    return raw[:MAX_PROJECTION - 32].decode('utf-8', 'ignore') + '\n[Display truncated]' if len(raw) > MAX_PROJECTION else text
