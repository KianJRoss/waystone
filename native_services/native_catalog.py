"""Detached display projection of an app-owned SQLite library; stdlib only.

The repository retains original BSON, extended JSON, types and unknown fields.
This bounded UI contract never exposes an executor or follows imported paths.
"""
import hashlib
import json
import os
import stat
import re
from pathlib import Path
from .library_repository import LibraryRepository, scalar

CATALOG_KIND = 'native-library-sqlite-v1'
MAX_DATABASE_BYTES = 512 * 1024 * 1024
MAX_PROJECTION_BYTES = 64 * 1024 * 1024
NAME_FIELDS = {'platforms': 'PlatformIds', 'genres': 'GenreIds', 'tags': 'TagIds',
               'categories': 'CategoryIds', 'developers': 'DeveloperIds',
               'publishers': 'PublisherIds', 'series': 'SeriesIds',
               'age_ratings': 'AgeRatingIds', 'regions': 'RegionIds',
               'features': 'FeatureIds', 'completion': 'CompletionStatusId'}
FIELDS = {'release_date': 'ReleaseDate', 'last_activity': 'LastActivity', 'added': 'Added',
          'modified': 'Modified', 'playtime_seconds': 'Playtime', 'play_count': 'PlayCount',
          'favorite': 'Favorite', 'hidden': 'Hidden', 'install_size': 'InstallSize',
          'sorting_name': 'SortingName', 'version': 'Version', 'user_score': 'UserScore',
          'critic_score': 'CriticScore', 'community_score': 'CommunityScore',
          'user_score_rating': 'UserScoreRating', 'critic_score_rating': 'CriticScoreRating',
          'community_score_rating': 'CommunityScoreRating', 'notes': 'Notes',
          'links': 'Links', 'last_size_scan_date': 'LastSizeScanDate'}


def _value(value):
    result = scalar(value)
    if isinstance(value, dict) and '$numberLong' in value:
        return int(result)
    if isinstance(result, dict):
        if all(key in result for key in ('Year', 'Month', 'Day')):
            return f"{result['Year']:04d}-{result['Month']:02d}-{result['Day']:02d}"
        return {k: _value(v) for k, v in result.items()}
    if isinstance(result, list): return [_value(v) for v in result]
    return result


def _art(value, field, source_id):
    reference = value.replace('\\', '/') if isinstance(value, str) else ''
    safe = (reference and len(reference) <= 4096 and not reference.startswith('/')
            and ':' not in reference and not any(ord(c) < 32 for c in reference)
            and all(p not in ('', '.', '..') for p in reference.split('/')))
    return {'status': 'local_present' if safe else 'unresolved',
            'relative_reference': reference if safe else None,
            'source_field': field, 'source_id': source_id,
            'provenance': {'kind': 'native_repository_role_reference',
                           'media_bytes_verified': False}}


def load_native_catalog(path, *, with_digest=False, opened_fd=None):
    path = Path(path)
    fd = os.dup(opened_fd) if opened_fd is not None else os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    repository = None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_DATABASE_BYTES:
            raise ValueError('Native library requires a regular SQLite file within 512 MiB')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            stream.seek(0)
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        repository = LibraryRepository('/proc/self/fd/' + str(fd))
    except BaseException:
        os.close(fd)
        raise
    try:
        provenance = {r[0]: json.loads(r[1]) for r in repository.db.execute('SELECT key,value FROM provenance')}
        games = {}
        relationships = {}
        for relation in repository.db.execute("SELECT * FROM relationships WHERE source_collection='Game'"):
            relationships.setdefault(relation['source_id'], []).append(dict(relation))
        rows = repository.games()
        if len(rows) > 10000: raise ValueError('Native display supports at most 10000 source games')
        # Reconcile exact Battle.net product IDs from the local Lutris-derived
        # installation reader onto the existing Playnite source variant. Do
        # not create a second game card or match these records by title.
        battlenet_candidates = {}
        consumed_launcher_keys = set()
        launcher_table = repository.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='launcher_records'").fetchone()
        if launcher_table:
            for local in repository.db.execute('''SELECT source_key,provider,product_id,status,
                    install_path,metadata_json,observed_at FROM launcher_records'''):
                if str(local['provider']).casefold() != 'battle.net' or local['status'] != 'installed':
                    continue
                try:
                    local_meta = json.loads(local['metadata_json'])
                except (TypeError, ValueError):
                    continue
                if not isinstance(local_meta, dict):
                    continue
                if local_meta.get('discovery') != 'lutris-registered-battlenet-product-db':
                    continue
                battlenet_candidates.setdefault(str(local['product_id']), []).append(dict(local))
        observed_battlenet = {product: candidates[0]
                              for product, candidates in battlenet_candidates.items()
                              if len(candidates) == 1}
        for row in rows:
            source_id = row['source_id']; raw = row['metadata']
            names = lambda field: [n for n in raw.get(field + '_resolved_names', []) if isinstance(n, str) and n]
            metadata = {key: names(field) for key, field in NAME_FIELDS.items()}
            metadata['completion'] = ' / '.join(metadata['completion']) or None
            for key, field in FIELDS.items(): metadata[key] = _value(raw.get(field))
            metadata['available_fields'] = [key for key, field in {**FIELDS, **NAME_FIELDS}.items() if field in raw]
            metadata['description'] = raw.get('description_text', '')
            metadata['developer_ids'] = _value(raw.get('DeveloperIds', []))
            metadata['publisher_ids'] = _value(raw.get('PublisherIds', []))
            metadata['completion_id'] = scalar(raw.get('CompletionStatusId'))
            metadata['relationships'] = relationships.get(source_id, [])
            metadata['provenance'] = {'kind': CATALOG_KIND, 'source_id': source_id,
                'raw_record_api': 'LibraryRepository.game/records',
                'description_format': 'plain_text_untrusted', 'playtime_unit': 'seconds',
                'company_names_available': True,
                'fields': {key: {'source_field': field, 'kind': 'native_repository'}
                           for key, field in {**FIELDS, **NAME_FIELDS}.items()}}
            # Source names are exact GameSource foreign-key joins, NEVER title/plugin guesses.
            source = (names('SourceId') or [row['provider'] or 'Unassigned'])[0]
            local_observation = (observed_battlenet.get(str(row['provider_game_id']))
                                 if source.casefold() == 'battle.net' else None)
            status = 'declared-installed' if raw.get('IsInstalled') is True else 'unknown'
            observation = {'kind': 'native_repository', 'installation_verified': False}
            if local_observation:
                status = 'installed'
                observation = {'kind': 'local_launcher_observation', 'installation_verified': True,
                               'observed_at': local_observation['observed_at']}
                metadata['provenance']['local_installation'] = {
                    'source_key': local_observation['source_key'],
                    'installation_path': local_observation['install_path'],
                    'discovery': 'lutris-registered-battlenet-product-db'}
                if 'installation_path' not in metadata['available_fields']:
                    metadata['available_fields'].append('installation_path')
                consumed_launcher_keys.add(local_observation['source_key'])
            variant = {'source': source, 'source_id': source_id,
                'provider_game_id': row['provider_game_id'],
                'installation_id': 'playnite-record:' + source_id,
                'platform': ' / '.join(metadata['platforms']) or 'unknown',
                'status': status,
                'recipe': [], 'metadata': metadata,
                'source_context': {'source': 'native-library', 'provider': source,
                                   'importer_plugin_id': row['plugin_id']},
                'observation': observation,
                'evidence': {'kind': 'native_sqlite_record', 'internal_guid': source_id},
                'artwork': {role: _art(raw.get(field), field, source_id) for role, field in
                            (('cover', 'CoverImage'), ('background', 'BackgroundImage'), ('icon', 'Icon'))}}
            game = games.setdefault(row['canonical_id'], {'id': row['canonical_id'],
                    'title': row['title'] or '[Untitled native record]', 'variants': []})
            game['variants'].append(variant)
        # First-party launcher observations are mutable app-owned rows kept
        # separately from the immutable Playnite migration archive. Join them
        # by exact source key/canonical ID, never by display title.
        table = launcher_table
        if table:
            for row in repository.db.execute('''SELECT source_key,provider,product_id,title,platform,status,
                    install_path,metadata_json,observed_at FROM launcher_records ORDER BY provider,title,source_key'''):
                source_id = row['source_key']
                if source_id in consumed_launcher_keys:
                    continue
                raw_meta = json.loads(row['metadata_json'])
                metadata = {key: [] for key in NAME_FIELDS}
                metadata.update({key: None for key in FIELDS})
                metadata.update({'available_fields': [], 'description': '', 'developer_ids': [],
                                 'publisher_ids': [], 'completion_id': None, 'relationships': [],
                                 'provenance': {'kind': 'launcher_source_observation',
                                                'source_key': source_id,
                                                'observed_at': row['observed_at'],
                                                'installation_path': row['install_path'] or None,
                                                'metadata': raw_meta}})
                override = repository.db.execute('SELECT metadata_json FROM native_metadata WHERE canonical_id=?',
                                                 ('launcher:' + row['provider'].casefold() + ':' + row['product_id'],)).fetchone()
                if override:
                    metadata.update(json.loads(override[0]))
                if row['platform']:
                    metadata['platforms'] = [row['platform']]
                    metadata['available_fields'].append('platforms')
                if isinstance(raw_meta.get('tags'), list):
                    metadata['tags'] = [v for v in raw_meta['tags'] if isinstance(v, str)]
                    metadata['available_fields'].append('tags')
                variant = {'source': row['provider'], 'source_id': source_id,
                    'provider_game_id': row['product_id'], 'installation_id': source_id,
                    'platform': row['platform'], 'status': row['status'], 'recipe': [],
                    'metadata': metadata,
                    'source_context': {'source': 'waystone-launcher-sync', 'provider': row['provider']},
                    'observation': {'kind': 'native_installation' if row['status'] == 'installed' else 'launcher_library_observation',
                                    'installation_verified': row['status'] == 'installed',
                                    'observed_at': row['observed_at']},
                    'evidence': {'kind': raw_meta.get('discovery', 'launcher_cache'),
                                 'ownership': raw_meta.get('ownership', 'not-asserted')},
                    'artwork': {}}
                canonical_id = 'launcher:' + row['provider'].casefold() + ':' + row['product_id']
                game = games.setdefault(canonical_id, {'id': canonical_id, 'title': row['title'], 'variants': []})
                # A Playnite import wins as the display title if the exact
                # canonical ID already exists; no cross-provider title guess.
                if not game.get('title') or game.get('title') == '[Untitled native record]':
                    game['title'] = row['title']
                game['variants'].append(variant)
        data = {'schema_version': 2, 'catalog_kind': CATALOG_KIND, 'games': list(games.values()),
                'provenance': {'kind': CATALOG_KIND, 'database_sha256': digest,
                   'canonical_catalog_sha256': provenance.get('canonical_catalog_sha256'),
                   'records_sha256': provenance.get('records_sha256'),
                   'reconciliation': provenance.get('reconciliation')}}
        if len(json.dumps(data, ensure_ascii=False).encode()) > MAX_PROJECTION_BYTES:
            raise ValueError('Native display projection exceeds 64 MiB')
        after = os.fstat(fd)
        selected = path.stat(follow_symlinks=False) if opened_fd is None else after
        if (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (selected.st_dev, selected.st_ino, selected.st_mode, selected.st_size, selected.st_mtime_ns, selected.st_ctime_ns):
            raise ValueError('Native library pathname changed during projection')
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Native library changed during projection')
        return (data, digest) if with_digest else data
    finally:
        repository.close()
        os.close(fd)
