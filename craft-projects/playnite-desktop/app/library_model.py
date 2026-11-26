"""Fixture-only library model. Intentionally contains no launch or config discovery API."""
import json
import re
import uuid
from html.parser import HTMLParser
from pathlib import Path
from PySide6.QtCore import QUrl, QAbstractListModel, QModelIndex, Qt, Slot, Property, Signal

try:
    from private_addons import SOURCE_DISPLAY_ALIASES as _SOURCE_DISPLAY_ALIASES
except ImportError:
    _SOURCE_DISPLAY_ALIASES = {}


def source_display_name(value):
    return _SOURCE_DISPLAY_ALIASES.get(value, value)


STATUSES = {'installed', 'not-installed', 'not-owned', 'unknown', 'registered', 'declared-installed'}
ROLES = ['workId', 'title', 'platforms', 'sources', 'installState', 'variantCount', 'stateLabel', 'coverUrl', 'backgroundUrl', 'iconUrl', 'heroUrl', 'logoUrl', 'hasUpdate', 'updateLabel']

def load_fixture(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('Expected fixture schema_version 1')
    return validate_catalog(data)


def validate_catalog(data):
    """Validate the existing fixture-compatible shape in memory; never rewrite IDs."""
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] not in (1, 2) or not isinstance(data.get('games'), list):
        raise ValueError('Expected catalog schema_version 1 or 2 and games array')
    works, variants = set(), set()
    for game in data['games']:
        if not isinstance(game, dict):
            raise ValueError('Each game must be an object')
        if not isinstance(game.get('id'), str) or not game['id'] or game['id'] in works:
            raise ValueError('Work IDs must be distinct nonempty strings')
        works.add(game['id'])
        if not isinstance(game.get('title'), str) or not game['title'].strip() or not isinstance(game.get('variants'), list) or not game['variants']:
            raise ValueError('Title and source variants required')
        for v in game['variants']:
            if not isinstance(v, dict):
                raise ValueError('Each variant must be an object')
            for field in ('source_context', 'observation'):
                if field in v and not isinstance(v[field], dict):
                    raise ValueError('Source context and observation claims must be objects')
            for field, text in (('source_context', 'host'), ('observation', 'at')):
                if text in v.get(field, {}) and not isinstance(v[field][text], str):
                    raise ValueError('Host and observation display claims must be text')
            for field in ('provider_installation_id', 'external_id', 'title_observation', 'record_kind', 'payload_presence'):
                if field in v and not isinstance(v[field], str):
                    raise ValueError('Optional display text must be strings')
            if 'evidence' in v and not isinstance(v['evidence'], (dict, str)):
                raise ValueError('Evidence must be text or a structured object')
            if not all(isinstance(v.get(k), str) and v[k] for k in ['source', 'source_id', 'platform']):
                raise ValueError('Each variant needs source, source_id and platform')
            installation_id = v.get('installation_id', 'fixture-default')
            if not isinstance(installation_id, str) or not installation_id:
                raise ValueError('Installation identity must be a nonempty string')
            key = (v['source'], v['source_id'], installation_id)
            if key in variants:
                raise ValueError('Source/record/installation identity must be distinct, independently of title')
            variants.add(key)
            if not isinstance(v.get('status'), str) or v['status'] not in STATUSES:
                raise ValueError('Unknown install-state value')
            recipe = v.get('recipe', [])
            if not isinstance(recipe, list) or any(not isinstance(arg, str) for arg in recipe):
                raise ValueError('Recipe is display-only argv strings, not a shell command')
    return data['games']


class _PlainDescription(HTMLParser):
    """Resource-free HTML-to-text projection; never instantiate a rich text document."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'iframe', 'object'}:
            self.blocked += 1
        elif not self.blocked and tag in {'br', 'p', 'div', 'li', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'iframe', 'object'}:
            self.blocked = max(0, self.blocked - 1)
        elif not self.blocked and tag in {'p', 'div', 'li'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(data)


def plain_description(value):
    if not isinstance(value, str):
        return ''
    parser = _PlainDescription()
    parser.feed(value[:200000])
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def metadata_overview(variants):
    """Display explicit imported values only, keeping zero/False distinct from absent."""
    metadata = [v.get('metadata', {}) for v in variants if isinstance(v.get('metadata'), dict)]
    def project(field, convert=str, fallback='Not supplied'):
        values = []
        for item in metadata:
            value = item.get(field)
            if value is None or value == '' or value == []:
                continue
            rendered = convert(value)
            if rendered and rendered not in values:
                values.append(rendered)
        return ' / '.join(values) if values else ('None' if any(field in item.get('available_fields', []) and item.get(field) == [] for item in metadata) else fallback)
    def names(value):
        return ', '.join(v for v in value if isinstance(v, str)) if isinstance(value, list) else ''
    def seconds(value):
        if type(value) not in (int, float) or value < 0:
            return ''
        return f'{value:g} seconds' if value < 60 else f'{value / 3600:.1f} hours ({value:g} seconds)'
    def count(value):
        return str(value) if type(value) in (int, float) and value >= 0 else ''
    def boolean(value):
        return ('Yes' if value else 'No') if type(value) is bool else ''
    result = {field: project(field, names) for field in ('genres', 'tags', 'categories', 'developers', 'publishers')}
    result.update({field: project(field) for field in ('release_date', 'last_activity', 'completion', 'added', 'modified')})
    result.update(playtime=project('playtime_seconds', seconds), playCount=project('play_count', count),
                  favorite=project('favorite', boolean), hidden=project('hidden', boolean))
    descriptions = []
    for item in metadata:
        value = plain_description(item.get('description'))
        if value and value not in descriptions:
            descriptions.append(value)
    result['description'] = '\n\n'.join(descriptions) or 'No description supplied in the selected import.'
    result['metadataNotice'] = 'Imported metadata claims; not native installation verification. Field provenance is available below.' if metadata else ''
    return result


COLLECTION_FIELDS = {'genres', 'categories', 'libraries', 'publishers', 'developers', 'tags', 'favorite', 'completion',
                     'hidden', 'release_decade', 'playtime_bucket', 'play_count'}


def collection_values_for_variant(variant, field):
    """Project explicitly imported metadata into safe, deterministic facet labels."""
    metadata = variant.get('metadata', {})
    if not isinstance(metadata, dict):
        return []
    if field in {'favorite', 'hidden'}:
        value = metadata.get(field)
        return [('Favorite' if value else 'Not favorite') if field == 'favorite' else
                ('Hidden' if value else 'Not hidden')] if type(value) is bool else []
    if field == 'release_decade':
        raw = metadata.get('release_date')
        match = re.match(r'^(\d{4})(?:$|[-/])', raw) if isinstance(raw, str) else None
        return [f"{int(match.group(1)) // 10 * 10}s"] if match else []
    if field == 'playtime_bucket':
        raw = metadata.get('playtime_seconds')
        if type(raw) is not int or raw < 0:
            return []
        return ['0 minutes' if raw == 0 else 'Under 1 hour' if raw < 3600 else
                '1–10 hours' if raw < 36000 else '10–50 hours' if raw < 180000 else '50+ hours']
    if field == 'play_count':
        raw = metadata.get('play_count')
        return [str(raw)] if type(raw) is int and raw >= 0 else []
    raw = metadata.get(field, [])
    values = raw if isinstance(raw, list) else [raw]
    return [item.strip() for item in values if isinstance(item, str) and item.strip()]

class LibraryModel(QAbstractListModel):
    def __init__(self, games, parent=None, media=None, catalog_mode=False, connections=None, artwork_view=None):
        super().__init__(parent)
        # Imported records remain immutable evidence. Identity links only build
        # a detached presentation grouping and never rewrite source variants.
        self._source_games = games
        self._identity_resolution = {}
        self._representative_for = {g['id']: g['id'] for g in games}
        self.games = list(games)
        self._connections = connections
        self._native_filter = 'All native providers'
        # Browsing collections is a read-only projection of imported metadata. It
        # never creates a user collection or merges catalog identities.
        self._collection_values = None
        self._collection_filters = {}
        self._last_filters = ('', 'All sources', 'All platforms', 'All states')
        self._organizer = None
        self._overrides = {}
        self._library_assignments = {}
        self._organizer_revision = 0
        self._organizer_message = 'Organizer edits disabled; select an explicit app-owned state path.'
        self._organizer_preview = ''
        self._organizer_plan = None
        self._organizer_game = ''
        self._organizer_request = ''
        self._organizer_audit = ''
        self._organizer_latest = None
        self._catalog_mode = catalog_mode
        self._media = media
        self._artwork_view = artwork_view
        if self._artwork_view is not None:
            self._artwork_view.operationFinished.connect(self._on_artwork_changed)
        self._media_urls = {}
        self._game_updates = {}
        self.visible = list(games)
        self._sort_field = ''
        self._descending = False
        self._roles = {Qt.UserRole + i + 1: r.encode() for i, r in enumerate(ROLES)}

    organizerChanged = Signal()
    organizerOverlayChanged = Signal()
    updatesChanged = Signal()
    # Ribbon art (W18): emitted on the UI thread when a fetched hero/logo for a game becomes available.
    artworkUpdated = Signal(str)
    _ribbonReady = Signal(str, str)

    def setGameUpdates(self, updates_by_game):
        """Update read-only pending game update map {game_id: dict} and notify QML views."""
        normalized = {}
        if isinstance(updates_by_game, dict):
            for gid, info in updates_by_game.items():
                if isinstance(gid, str) and gid and isinstance(info, dict):
                    normalized[gid] = dict(info)
        if normalized == self._game_updates:
            return
        self._game_updates = normalized
        if self.visible:
            role_ids = [k for k, v in self._roles.items() if v in (b'hasUpdate', b'updateLabel')]
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.visible) - 1, 0), role_ids)
        self.organizerOverlayChanged.emit()
        self.updatesChanged.emit()

    def _update_entry_for_game(self, game):
        if not isinstance(game, dict) or not self._game_updates:
            return None
        for member_id in [game.get('id', ''), *game.get('linked_record_ids', [])]:
            if member_id and member_id in self._game_updates:
                return self._game_updates[member_id]
        return None

    @Slot(str, result=bool)
    def hasUpdateFor(self, work_id):
        representative = self._representative_for.get(work_id, work_id)
        game = next((g for g in self.games if g['id'] == representative), None)
        if game is not None:
            return self._update_entry_for_game(game) is not None
        return bool(work_id and work_id in self._game_updates)

    @Slot(str, result=str)
    def updateLabelFor(self, work_id):
        representative = self._representative_for.get(work_id, work_id)
        game = next((g for g in self.games if g['id'] == representative), None)
        entry = self._update_entry_for_game(game) if game is not None else self._game_updates.get(work_id)
        if not isinstance(entry, dict):
            return ''
        return str(entry.get('statusLabel') or 'Update available')

    def configure_organizer(self, path):
        from native_services.organizer_store import OrganizerStore
        path = Path(path)
        ids = [game['id'] for game in self._source_games]
        if not path.is_absolute():
            raise ValueError('Organizer state requires an absolute app-owned path')
        store = OrganizerStore(path) if path.exists() or path.is_symlink() else OrganizerStore.create(path, ids)
        store.register_ids(ids)  # the library grew: add new games, keep every saved edit
        self._organizer = store
        self._refresh_organizer()
        self._organizer_message = 'Local native overrides only; imported source records remain unchanged.'
        self.organizerChanged.emit()

    @Slot('QVariantMap')
    def setIdentityLinks(self, resolution):
        """Project exact user-approved canonical IDs together, keeping originals."""
        if not isinstance(resolution, dict):
            raise ValueError('Identity resolution must be an ID map')
        originals = {game['id']: game for game in self._source_games}
        if any(not isinstance(member, str) or not isinstance(rep, str)
               or member not in originals or rep not in originals or member == rep
               for member, rep in resolution.items()):
            raise ValueError('Identity link references an unknown or self ID')
        if set(resolution).intersection(resolution.values()):
            raise ValueError('Identity links cannot contain chains or cycles')
        self._identity_resolution = dict(resolution)
        self._representative_for = {ident: resolution.get(ident, ident) for ident in originals}
        groups = {}
        for game in self._source_games:
            representative = self._representative_for[game['id']]
            if representative not in groups:
                groups[representative] = {**originals[representative], 'variants': [], 'linked_record_ids': []}
            groups[representative]['variants'].extend(
                {**variant, '_source_record_id': game['id']}
                for variant in game['variants'])
            groups[representative]['linked_record_ids'].append(game['id'])
        for group in groups.values():
            if len(group['linked_record_ids']) > 1:
                group['linked_member_artwork'] = [originals[ident].get('artwork', {})
                                                  for ident in sorted(group['linked_record_ids'])]
        self.games = [groups[key] for key in sorted(groups)]
        self._collection_values = None
        self._media_urls = {}
        self.setFilters(*self._last_filters)

    def replaceCatalog(self, games):
        """Replace source rows after background sync while retaining filters and links."""
        if not isinstance(games, list):
            raise ValueError('Catalog games must be a list')
        self._source_games = games
        valid_links = {member: representative for member, representative in self._identity_resolution.items()
                       if any(g.get('id') == member for g in games)
                       and any(g.get('id') == representative for g in games)}
        self._identity_resolution = valid_links
        self._representative_for = {g['id']: valid_links.get(g['id'], g['id']) for g in games}
        self.setIdentityLinks(valid_links)
        self.organizerOverlayChanged.emit()

    @Slot(str, result=str)
    def representativeFor(self, work_id):
        return self._representative_for.get(work_id, '')

    def _refresh_organizer(self):
        snapshot = self._organizer.snapshot()
        self._overrides = snapshot['overrides']
        self._collection_values = None
        self._organizer_revision = snapshot['revision']
        audit = self._organizer.audit()
        self._organizer_latest = audit[0]['audit_id'] if audit and audit[0]['kind'] == 'apply' else None
        self._organizer_audit = json.dumps(audit[:20], ensure_ascii=False, indent=2)
        self.organizerOverlayChanged.emit()
        self.organizerChanged.emit()
        self.setFilters(*self._last_filters)

    def configure_reconciliation(self, path):
        from native_services.catalog_reconciliation import load_assignments
        self._library_assignments = load_assignments(path, [g['id'] for g in self._source_games])
        self.setFilters(*self._last_filters)

    def library_sources(self):
        return ['All sources'] + sorted({source_display_name(v['source'])
            for g in self._source_games for v in self._effective_variants(g)})

    def _display_title(self, game):
        return self._overrides.get(game['id'], {}).get('title', game['title'])

    def _effective_variants(self, game):
        projected = []
        for variant in game['variants']:
            ident = variant.get('_source_record_id', game['id'])
            metadata = variant.get('metadata')
            value = {**variant, 'metadata': {**(metadata if isinstance(metadata, dict) else {}),
                                           **self._overrides.get(ident, {})}}
            if self._overrides.get(ident):
                value['metadata']['available_fields'] = sorted(set(value['metadata'].get('available_fields', [])) | set(self._overrides[ident]))
            if 'platform' in self._overrides.get(ident, {}):
                value['platform'] = self._overrides[ident]['platform']
            if self._connections is not None and self._connections.installed_for_variant(ident, variant):
                value['imported_status'] = variant['status']
                value['status'] = 'installed'
                value['installation_basis'] = self._connections.installation_basis_for_variant(ident,variant) if hasattr(self._connections,'installation_basis_for_variant') else 'Native manifest + directory observation; payload/gameplay not tested'
            if variant['source'] == 'Unassigned' and ident in self._library_assignments:
                value['imported_source'] = variant['source']
                value['source'] = self._library_assignments[ident]
                value['library_attribution'] = 'Exact package catalog identity or explicit emulator action'
            projected.append(value)
        return projected

    @Property(bool, notify=organizerChanged)
    def organizerEnabled(self):
        return self._organizer is not None

    @Property(int, notify=organizerOverlayChanged)
    def organizerRevision(self):
        return self._organizer_revision

    @Property(str, notify=organizerChanged)
    def organizerMessage(self):
        return self._organizer_message

    @Property(str, notify=organizerChanged)
    def organizerPreview(self):
        return self._organizer_preview

    @Property(str, notify=organizerChanged)
    def organizerPendingGame(self):
        return self._organizer_game if self._organizer_plan is not None else ''

    @Property(str, notify=organizerChanged)
    def organizerAudit(self):
        return self._organizer_audit

    @Property(bool, notify=organizerChanged)
    def organizerCanUndo(self):
        return self._organizer_latest is not None

    @Slot(str, result='QVariantMap')
    def organizerEditValues(self, work_id):
        game = next((g for g in self._source_games if g['id'] == work_id), None)
        if game is None:
            return {'tags': '[]', 'favorite': False}
        # Variant conflicts are not silently merged into an edit baseline.
        metadata = game['variants'][0].get('metadata', {})
        values = {**metadata, **self._overrides.get(work_id, {})}
        return {'tags': json.dumps(values.get('tags', []), ensure_ascii=False),
                'title': self._overrides.get(work_id, {}).get('title', game['title']),
                'platform': self._overrides.get(work_id, {}).get('platform', game['variants'][0].get('platform', 'Unknown')),
                'categories': json.dumps(values.get('categories', []), ensure_ascii=False),
                'libraries': json.dumps(values.get('libraries', []), ensure_ascii=False),
                'description': values.get('description', ''),
                'favorite': values.get('favorite') is True}

    def _organizer_error(self, error):
        self._organizer_plan = None
        self._organizer_game = ''
        self._organizer_preview = ''
        self._organizer_message = str(error)
        if self._organizer is not None:
            try:
                self._refresh_organizer()
            except Exception:
                pass
        self.organizerChanged.emit()

    @Slot()
    def discardOrganizerPreview(self):
        self._organizer_plan = None
        self._organizer_game = ''
        self._organizer_preview = ''
        self._organizer_message = 'Edit changed — preview again before applying.'
        self.organizerChanged.emit()

    @Slot(str, str, bool)
    def previewOrganizer(self, work_id, tags_json, favorite):
        try:
            if self._organizer is None:
                raise ValueError('Organizer state is not enabled')
            if work_id not in {g['id'] for g in self._source_games}:
                raise ValueError('Select an exact catalog identity')
            if len(tags_json) > 16384:
                raise ValueError('Tags input too long')
            tags = json.loads(tags_json)
            self._set_organizer_plan(work_id, {'tags': tags, 'favorite': favorite})
        except Exception as error:
            self._organizer_error(error)

    def _set_organizer_plan(self, work_id, changes):
        self._organizer_plan = self._organizer.preview([{'game_id': work_id, 'changes': changes}])
        self._organizer_game = work_id
        self._organizer_request = str(uuid.uuid4())
        self._organizer_preview = json.dumps(self._organizer_plan.as_dict(), ensure_ascii=False, indent=2)
        self._organizer_message = 'Preview only — nothing saved. Apply explicitly to persist local overrides.'
        self.organizerChanged.emit()

    @Slot(str, str)
    def previewMetadata(self, work_id, changes_json):
        """Preview explicit field edits; use the same revision/undo path as tags."""
        try:
            if self._organizer is None or work_id not in {g['id'] for g in self._source_games}:
                raise ValueError('Select an enabled exact catalog identity')
            if not isinstance(changes_json, str) or len(changes_json) > 65536:
                raise ValueError('Metadata input too long')
            self._set_organizer_plan(work_id, json.loads(changes_json))
        except Exception as error:
            self._organizer_error(error)

    @Slot(str)
    def previewOrganizerReset(self, work_id):
        try:
            if self._organizer is None or work_id not in {g['id'] for g in self._source_games}:
                raise ValueError('Select an enabled exact catalog identity')
            self._set_organizer_plan(work_id, {'tags': None, 'favorite': None})
        except Exception as error:
            self._organizer_error(error)

    @Slot(str)
    def applyOrganizer(self, work_id):
        try:
            if self._organizer_plan is None or work_id != self._organizer_game:
                raise ValueError('Preview this selected game before applying')
            receipt = self._organizer.apply(self._organizer_plan, self._organizer_request)
            self._organizer_plan = None
            self._organizer_game = ''
            self._organizer_preview = ''
            self._refresh_organizer()
            self._organizer_message = 'Saved native override; audit ' + str(receipt['audit_id']) + '. Imported metadata unchanged.'
            self.organizerChanged.emit()
        except Exception as error:
            self._organizer_error(error)

    @Slot()
    def undoOrganizer(self):
        try:
            if self._organizer is None or self._organizer_latest is None:
                raise ValueError('No latest unchanged apply to undo')
            receipt = self._organizer.undo(self._organizer_latest, str(uuid.uuid4()))
            self._organizer_plan = None
            self._organizer_game = ''
            self._organizer_preview = ''
            self._refresh_organizer()
            self._organizer_message = 'Undone local apply; audit ' + str(receipt['audit_id']) + '. Imported metadata unchanged.'
            self.organizerChanged.emit()
        except Exception as error:
            self._organizer_error(error)

    def roleNames(self):
        return self._roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.visible)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.visible):
            return None
        g = self.visible[index.row()]
        if role == Qt.DisplayRole:
            return g['title']
        name = self._roles.get(role, b'').decode()
        if name in ('coverUrl', 'backgroundUrl', 'iconUrl', 'heroUrl', 'logoUrl'):
            return self._urls(g).get(name, '')
        effective = self._effective_variants(g)
        statuses = {v['status'] for v in effective}
        upd_entry = self._update_entry_for_game(g)
        values = {
            'workId': g['id'], 'title': self._display_title(g),
            'platforms': ', '.join(sorted({v['platform'] for v in effective})),
            'sources': ', '.join(sorted({source_display_name(v['source']) for v in effective})),
            'installState': next(iter(statuses)) if len(statuses) == 1 else 'mixed',
            'variantCount': len(g['variants']),
            'stateLabel': ('Installed · native observation' if any(v.get('installation_basis') for v in effective)
                           else state_label(next(iter(statuses)) if len(statuses) == 1 else 'mixed')),
            'hasUpdate': upd_entry is not None,
            'updateLabel': str(upd_entry.get('statusLabel') or 'Update available') if isinstance(upd_entry, dict) else '',
        }
        return values.get(name)

    def _on_artwork_changed(self, operation: str = ''):
        self._media_urls.clear()
        if self.visible:
            role_ids = [k for k, v in self._roles.items() if v in (b'coverUrl', b'backgroundUrl', b'iconUrl', b'heroUrl', b'logoUrl')]
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.visible) - 1, 0), role_ids)
        self.organizerOverlayChanged.emit()

    def _urls(self, game):
        if game['id'] not in self._media_urls:
            base = self._media.urls_for(game) if self._media is not None else {
                'coverUrl': '', 'backgroundUrl': '', 'iconUrl': ''}
            urls = {'coverUrl': '', 'backgroundUrl': '', 'iconUrl': '', 'heroUrl': '', 'logoUrl': '', **base}
            if self._artwork_view is not None:
                for role_name in ('cover', 'background', 'icon', 'hero', 'logo'):
                    url_key = f'{role_name}Url'
                    try:
                        override = self._artwork_view.artworkUrl(game['id'], role_name)
                    except Exception:
                        override = ''
                    if not override and 'linked_record_ids' in game:
                        for member_id in game['linked_record_ids']:
                            try:
                                override = self._artwork_view.artworkUrl(member_id, role_name)
                            except Exception:
                                override = ''
                            if override:
                                break
                    if override:
                        urls[url_key] = override
            # Cached provider artwork: manual choices and selected media are stronger.
            if (not urls['heroUrl'] or not urls['logoUrl'] or not urls['coverUrl']) and getattr(self, '_ribbon_cache', None) is not None:
                for provider, product_id in self._ribbon_keys(game):
                    cached = self._ribbon_cache.lookup(provider, product_id)
                    for role in ('hero', 'logo', 'cover'):
                        if not urls[f'{role}Url'] and cached.get(role):
                            urls[f'{role}Url'] = QUrl.fromLocalFile(cached[role]).toString()
            self._media_urls[game['id']] = urls
        return self._media_urls[game['id']]

    # ---- Ribbon art (W18) ----
    def configureRibbons(self, cache, fetcher) -> None:
        self._ribbon_cache = cache
        self._ribbon_fetcher = fetcher
        self._ribbonReady.connect(self._on_ribbon_ready)

    def ribbonReadyFromWorker(self, provider: str, product_id: str, _paths=None) -> None:
        """Called on the fetcher's thread; the signal hops to the UI thread. Ignored once the model is gone."""
        try:
            self._ribbonReady.emit(provider, product_id)
        except RuntimeError:
            pass

    def _ribbon_keys(self, game):
        keys = []
        steam_app_id = ''
        # Native store artwork first; SteamGridDB is an exact-title fallback only.
        for v in self._effective_variants(game):
            context = v.get('source_context') if isinstance(v.get('source_context'), dict) else {}
            metadata = v.get('metadata') if isinstance(v.get('metadata'), dict) else {}
            source = ' '.join(str(value) for value in (
                v.get('source', ''), v.get('provider', ''), v.get('store', ''),
                context.get('provider', ''), context.get('store', ''), metadata.get('store', ''),
            )).casefold()
            product = str(v.get('provider_game_id') or '').strip()
            provider = ('steam' if 'steam' in source else
                        'epic' if 'epic' in source else
                        'gog' if 'gog' in source else '')
            if provider and product and (provider, product) not in keys:
                if provider != 'steam' or product.isdigit():
                    keys.append((provider, product))
                    if provider == 'steam' and product.isdigit() and not steam_app_id:
                        steam_app_id = product
        title = self._display_title(game).strip()
        if title:
            request = json.dumps({'title': title[:180], 'steam_app_id': steam_app_id},
                                 ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            keys.append(('steamgriddb', request))
        return keys

    @Slot(str)
    def requestRibbon(self, work_id: str) -> None:
        """Big Screen asks for a game's ribbon when it shows it; fetched in the background, cached."""
        fetcher = getattr(self, '_ribbon_fetcher', None)
        if fetcher is None or not work_id:
            return
        game = next((g for g in self.games if g['id'] == work_id), None)
        if game is None:
            return
        effective_art = self._urls(game)
        if effective_art.get('coverUrl') and effective_art.get('heroUrl') and effective_art.get('logoUrl'):
            return  # All roles are satisfied, including manual overrides.
        for provider, product_id in self._ribbon_keys(game):
            fetcher.request(provider, product_id)

    def _on_ribbon_ready(self, provider: str, product_id: str) -> None:
        for row, game in enumerate(self.visible):
            if (provider, product_id) in self._ribbon_keys(game):
                self._media_urls.pop(game['id'], None)
                roles = [k for k, v in self._roles.items() if v in (b'coverUrl', b'heroUrl', b'logoUrl')]
                self.dataChanged.emit(self.index(row, 0), self.index(row, 0), roles)
        for game in self.games:
            if (provider, product_id) in self._ribbon_keys(game):
                self._media_urls.pop(game['id'], None)
                self.artworkUpdated.emit(game['id'])

    @Slot(str, str, str, str)
    def setFilters(self, query, source, platform, status):
        self._last_filters = (query, source, platform, status)
        query = query.casefold().strip()
        def matches(g):
            # A single variant must satisfy all active variant filters, not different copies.
            if self._connections is not None and not any(
                    self._connections.matches(member_id, self._native_filter)
                    for member_id in g.get('linked_record_ids', [g['id']])):
                return False
            if query and query not in self._display_title(g).casefold():
                return False
            def in_collections(variant):
                metadata = variant.get('metadata', {})
                if not self._collection_filters:
                    return True
                for field, chosen_values in self._collection_filters.items():
                    present = collection_values_for_variant(variant, field)
                    if not any(chosen in present for chosen in chosen_values):
                        return False
                return True
            return any((source == 'All sources' or v['source'] == source or source_display_name(v['source']) == source)
                       and (platform == 'All platforms' or v['platform'] == platform)
                       and (status == 'All states' or v['status'] == status
                            or (status == 'declared-installed' and v.get('imported_status') == status))
                       and in_collections(v)
                       for v in self._effective_variants(g))
        self.beginResetModel()
        self.visible = [g for g in self.games if matches(g)]
        self._sort_visible()
        self.endResetModel()
        self.countChanged.emit()

    @Slot(str)
    def setNativeFilter(self, provider):
        self._native_filter = provider
        self.setFilters(*self._last_filters)

    @Slot(str, str)
    def setCollectionFilter(self, field, value):
        """Compatibility shim for a single exact metadata filter."""
        self.setCollectionFilters({field: [value]} if field and value else {})

    @Slot('QVariantMap')
    def setCollectionFilters(self, filters):
        """Apply exact imported metadata facets on one matching source variant.

        Values within one field are ORed; different fields are ANDed. No
        metadata is inferred and filters cannot be satisfied by different copies.
        """
        if not isinstance(filters, dict):
            raise ValueError('Collection filters must be a mapping')
        normalized = {}
        for field, values in filters.items():
            if field not in COLLECTION_FIELDS:
                raise ValueError('Unsupported collection metadata field')
            if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) for value in values):
                raise ValueError('Collection filter values must be text lists')
            unique = list(dict.fromkeys(value for value in values if value))
            if unique:
                normalized[field] = unique
        self._collection_filters = normalized
        self.setFilters(*self._last_filters)

    @Slot(str, result='QVariantList')
    def collectionValues(self, field):
        """Sorted, unique imported labels; do not infer missing metadata."""
        if field not in COLLECTION_FIELDS:
            return []
        # Facets describe the full catalog, not the current search result. QML
        # re-reads all twelve on modelReset/countChanged; scan metadata only once
        # per identity/organizer revision rather than once per field per reset.
        if self._collection_values is None:
            labels = {name: set() for name in COLLECTION_FIELDS}
            for game in self.games:
                for variant in self._effective_variants(game):
                    for name, values in labels.items():
                        values.update(collection_values_for_variant(variant, name))
            projected = {}
            for name, values in labels.items():
                if name == 'favorite':
                    projected[name] = [value for value in ('Favorite', 'Not favorite') if value in values]
                elif name == 'hidden':
                    projected[name] = [value for value in ('Hidden', 'Not hidden') if value in values]
                elif name == 'playtime_bucket':
                    order = {'0 minutes': 0, 'Under 1 hour': 1, '1–10 hours': 2, '10–50 hours': 3, '50+ hours': 4}
                    projected[name] = sorted(values, key=order.__getitem__)
                else:
                    projected[name] = sorted(values, key=lambda item: (item.casefold(), item))
            self._collection_values = projected
        return list(self._collection_values[field])

    @Slot(str, result=str)
    def details(self, work_id):
        representative = self._representative_for.get(work_id, work_id)
        game = next((g for g in self.games if g['id'] == representative), None)
        if not game:
            return 'Select a game to inspect its source variants.'
        lines = [game['title'], 'Work ID: ' + game['id'], '',
                 'Imported recipe evidence (text only). Play uses separately configured native launch profiles.']
        linked_ids = game.get('linked_record_ids', [])
        if len(linked_ids) > 1:
            lines.append('User-approved linked source records (all original variants retained): ' + ', '.join(linked_ids))
        for ident in linked_ids or [game['id']]:
            if ident in self._overrides:
                lines.append('Native local overrides for ' + ident + ': ' + json.dumps(self._overrides[ident], ensure_ascii=False, sort_keys=True))
        if 'artwork' in game:
            lines.append('Game artwork role claims (unverified): ' + json.dumps(game['artwork'], ensure_ascii=False, sort_keys=True))
        for v in game['variants']:
            lines += ['', f"{v['source']} / {v['source_id']}",
                      'Installation ID: ' + v.get('installation_id', 'fixture-default'),
                      'Provider installation ID: ' + v.get('provider_installation_id', 'Unknown / not supplied'),
                      'External ID: ' + v.get('external_id', 'Unknown / not supplied'),
                      'Cached provider game ID (not native verification): ' + json.dumps(v.get('provider_game_id'), ensure_ascii=False),
                      f"Platform: {v['platform']} | State: {v['status']}",
                      'Evidence: ' + (json.dumps(v['evidence'], ensure_ascii=False, sort_keys=True) if isinstance(v.get('evidence'), dict) else v.get('evidence', 'Fixture; no live verification')),
                      'Source context: ' + json.dumps(v.get('source_context', {}), ensure_ascii=False, sort_keys=True),
                      'Observation: ' + json.dumps(v.get('observation', {}), ensure_ascii=False, sort_keys=True),
                      'Title observation: ' + v.get('title_observation', game['title']),
                      'Record kind: ' + v.get('record_kind', 'fixture game'),
                      'Payload presence: ' + v.get('payload_presence', 'unverified'),
                      'Display-only argv: ' + json.dumps(v.get('recipe', []), ensure_ascii=False)]
            if isinstance(v.get('metadata'), dict):
                if v['metadata'].get('provenance', {}).get('kind') == 'native-library-sqlite-v1':
                    core = {k: value for k, value in v['metadata'].items() if k not in ('description', 'provenance')}
                    lines.append('Native core metadata and exact relationships: ' + json.dumps(core, ensure_ascii=False, sort_keys=True)[:32768])
                lines.append('Imported metadata provenance: ' + json.dumps(v['metadata'].get('provenance', {}), ensure_ascii=False, sort_keys=True))
            if 'artwork' in v:
                lines.append('Variant artwork role claims (unverified): ' + json.dumps(v['artwork'], ensure_ascii=False, sort_keys=True))
        text = '\n'.join(lines)
        encoded = text.encode('utf-8')
        return encoded[:65500].decode('utf-8', errors='ignore') + '\n[Display truncated; full metadata remains queryable in LibraryRepository]' if len(encoded) > 65536 else text

    @Slot(str, result=str)
    def titleFor(self, work_id):
        representative = self._representative_for.get(work_id, work_id)
        game = next((g for g in self.games if g['id'] == representative), None)
        return game['title'] if game else ''

    countChanged = Signal()

    @Property(int, notify=countChanged)
    def visibleCount(self):
        return len(self.visible)

    @Property(int, constant=True)
    def totalCount(self):
        return len(self.games)

    @Property(int, constant=True)
    def sourceRecordCount(self):
        return len(self._source_games)

    def _sort_visible(self):
        if not self._sort_field:
            return
        def value(game):
            field = self._sort_field
            if field == 'title':
                return self._display_title(game).casefold()
            if field == 'variantCount':
                return len(game['variants'])
            if field == 'installState':
                return ', '.join(sorted({v['status'] for v in self._effective_variants(game)}))
            key = {'sources': 'source', 'platforms': 'platform'}[field]
            return ', '.join(sorted({v[key] for v in game['variants']})).casefold()
        # Stable identity tie-breaker, never title-based merging.
        self.visible.sort(key=lambda game: game['id'])
        self.visible.sort(key=value, reverse=self._descending)

    @Slot(str, bool)
    def setSort(self, field, descending):
        if field not in {'title', 'sources', 'platforms', 'installState', 'variantCount'}:
            raise ValueError('Unsupported sort field')
        self.beginResetModel()
        self._sort_field, self._descending = field, descending
        self._sort_visible()
        self.endResetModel()

    @Slot(str, result=int)
    def rowOf(self, work_id):
        representative = self._representative_for.get(work_id, work_id)
        return next((i for i, game in enumerate(self.visible) if game['id'] == representative), -1)

    @Slot(int, result=str)
    def idAt(self, row):
        return self.visible[row]['id'] if 0 <= row < len(self.visible) else ''

    @Slot(str, result='QVariantMap')
    def overview(self, work_id):
        representative = self._representative_for.get(work_id, work_id)
        game = next((g for g in self.games if g['id'] == representative), None)
        if game is None:
            return {}
        variants = self._effective_variants(game)
        def join(key):
            return ', '.join(sorted({v.get(key, 'unknown') for v in variants}))
        def display_claim(owner, field, fallback):
            value = owner.get(field)
            return value if isinstance(value, str) and value.strip() else fallback

        def observed(variant):
            observation = variant.get('observation', {})
            if not self._catalog_mode:
                return display_claim(observation, 'at', 'Unknown / fixture')
            labels = [('at', 'Record observed'),
                      ('cached_source_modified_at', 'Cached source modified'),
                      ('role_observed_at', 'Selected-copy roles observed')]
            claims = [label + ': ' + value for field, label in labels
                      if (value := display_claim(observation, field, ''))]
            return '; '.join(claims) or 'Not supplied (cached export)'

        host_fallback = 'Not supplied (cached export)' if self._catalog_mode else 'Unknown / fixture'
        states = {v['status'] for v in variants}
        metadata = metadata_overview(variants)
        edit_targets = [{'id': ident, 'label': ident + ' · ' + ', '.join(sorted({source_display_name(v['source']) for v in original['variants']}))}
                        for ident in game.get('linked_record_ids', [game['id']])
                        for original in self._source_games if original['id'] == ident]
        if any(target['id'] in self._overrides for target in edit_targets):
            metadata['metadataNotice'] += f' Native local overrides included per-record (revision {self._organizer_revision}); conflicting values are retained.'
        installed_variants = [
            {
                "source": v.get("source", ""),
                "provider": v.get("source", ""),
                "provider_game_id": v.get("provider_game_id", ""),
                "product_id": v.get("provider_game_id", ""),
                "title": self._display_title(game),
                "state": v.get("status", "installed"),
                "targetId": v.get("_source_record_id", game["id"]),
                "variant_key": v.get("_source_record_id", game["id"]),
            }
            for v in variants if v.get("status") == "installed" or v.get("installation_basis")
        ]
        upd_entry = self._update_entry_for_game(game)
        return {
            'id': game['id'], 'title': self._display_title(game),
            'sources': ', '.join(sorted({source_display_name(v['source']) for v in variants})), 'platforms': join('platform'),
            'state': ('Installed · native observation' if any(v.get('installation_basis') for v in variants)
                      else state_label(next(iter(states)) if len(states) == 1 else 'mixed')),
            'variantCount': len(variants),
            'editTargets': edit_targets,
            'hosts': ', '.join(sorted({display_claim(v.get('source_context', {}), 'host', host_fallback) for v in variants})),
            'observed': ', '.join(sorted({observed(v) for v in variants})),
            'provenance': self.details(work_id),
            'installedVariants': installed_variants,
            'hasUpdate': upd_entry is not None,
            'updateLabel': str(upd_entry.get('statusLabel') or 'Update available') if isinstance(upd_entry, dict) else '',
            'updateInfo': dict(upd_entry) if isinstance(upd_entry, dict) else {},
            **metadata,
            **(self._connections.summary_for(work_id) if self._connections is not None else {}),
            **self._urls(game),
        }

    @Slot(str, str, result=int)
    def facetCount(self, field, value):
        # Counts are explicitly full-catalog counts, not current-filter counts.
        if field in COLLECTION_FIELDS:
            def has_value(game):
                for variant in self._effective_variants(game):
                    if value in collection_values_for_variant(variant, field):
                        return True
                return False
            return sum(has_value(game) for game in self.games)
        if field not in {'source', 'platform', 'status'}:
            return 0
        return sum(any(v.get(field) == value for v in self._effective_variants(game)) for game in self.games)


def state_label(state):
    return {
        'installed': 'Installed',
        'declared-installed': 'Needs local check',
        'not-installed': 'Not installed',
        'not-owned': 'No longer in source library',
        'registered': 'In library',
        'unknown': 'Not checked',
        'mixed': 'Mixed observations',
    }.get(state, 'Unknown')
