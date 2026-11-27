"""Explicit app-owned Steam export to UI projection; no scan, config lookup or launch."""
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOTS = 16
MAX_TOTAL_SNAPSHOT_BYTES = 32 * 1024 * 1024
STATES = {'registered', 'declared-installed', 'unknown'}
SOURCE_FIELDS = ['provider', 'instance', 'host', 'scope', 'root', 'adapter_version']
EVIDENCE_FIELDS = ['kind', 'locator', 'sha256', 'size_bytes', 'mtime_ns', 'mode', 'field_locators', 'verification_scope']

def key(parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def snapshot_games(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError('Snapshot must be a JSON object')
    if type(snapshot.get('schema_version')) is not int or snapshot['schema_version'] != 1:
        raise ValueError('Unsupported exported snapshot schema')
    source = snapshot.get('source', {})
    if not isinstance(source, dict) or source.get('provider') != 'steam' or any(not isinstance(source.get(k), str) or not source[k] for k in SOURCE_FIELDS):
        raise ValueError('Expected explicit Steam source context')
    availability = snapshot.get('availability')
    if not isinstance(availability, str) or availability not in {'online', 'partial', 'offline', 'unknown'} or type(snapshot.get('complete')) is not bool:
        raise ValueError('Snapshot availability/completeness required')
    observed_at = snapshot.get('observed_at')
    if not isinstance(observed_at, str) or not re.fullmatch(
        r'[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]'
        r'(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])', observed_at
    ):
        raise ValueError('Snapshot observed_at must be a timezone-aware ISO 8601 timestamp')
    try:
        datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Snapshot observed_at must be a valid timestamp') from exc
    if type(snapshot.get('fixture_mode', False)) is not bool:
        raise ValueError('Snapshot fixture_mode must be a boolean')
    if not isinstance(snapshot.get('records'), list):
        raise ValueError('Snapshot records must be a list')
    groups = {}; seen = set()
    for index, row in enumerate(snapshot['records']):
        if not isinstance(row, dict):
            raise ValueError(f'Snapshot records[{index}] must be an object')
        appid, installation = row.get('external_id'), row.get('installation_id')
        if not isinstance(appid, str) or not re.fullmatch(r'[1-9][0-9]{0,15}', appid) or not isinstance(installation, str) or not installation:
            raise ValueError('Explicit source record/installation IDs required')
        record_key = key(['steam', source['instance'], appid])
        variant_key = key([record_key, source['host'], installation])
        if row.get('record_key') != record_key or row.get('variant_key') != variant_key or variant_key in seen:
            raise ValueError('Invalid/duplicate stable source identity')
        seen.add(variant_key)
        state = row.get('installation_state')
        if not isinstance(state, str) or state not in STATES or not isinstance(row.get('title'), str) or not row['title']:
            raise ValueError('Unknown/invalid record observation')
        evidence = row.get('evidence')
        if not isinstance(evidence, dict) or not all(k in evidence for k in EVIDENCE_FIELDS):
            raise ValueError('Structured manifest provenance required')
        if evidence['kind'] != 'manifest-declaration' or not isinstance(evidence['locator'], str) or len(evidence['locator']) > 16384:
            raise ValueError('Invalid evidence locator/kind')
        if not isinstance(evidence['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', evidence['sha256']):
            raise ValueError('Invalid evidence hash')
        if any(type(evidence[k]) is not int or evidence[k] < 0 for k in ['size_bytes', 'mtime_ns', 'mode']):
            raise ValueError('Invalid evidence stat metadata')
        if not isinstance(evidence['field_locators'], list) or len(evidence['field_locators']) > 16 or any(not isinstance(p, str) or len(p) > 256 for p in evidence['field_locators']):
            raise ValueError('Invalid field provenance')
        if not isinstance(evidence['verification_scope'], str) or len(evidence['verification_scope']) > 2048:
            raise ValueError('Invalid verification scope')
        payload = row.get('payload_presence', 'unknown')
        kind = row.get('kind', 'unknown')
        if not isinstance(payload, str) or payload not in {'directory-present', 'missing', 'unknown'} or not isinstance(kind, str) or kind not in {'game', 'tool', 'unknown'}:
            raise ValueError('Invalid record classification/payload evidence')
        group = groups.setdefault(record_key, {'id': record_key, 'title': row['title'], 'variants': []})
        group['variants'].append({
            'source': 'Steam', 'source_id': record_key, 'external_id': appid,
            'installation_id': variant_key, 'provider_installation_id': installation,
            'platform': 'unknown', 'status': state, 'title_observation': row['title'],
            'record_kind': row.get('kind', 'unknown'), 'payload_presence': row.get('payload_presence', 'unknown'),
            'source_context': {k: source[k] for k in SOURCE_FIELDS},
            'evidence': {k: evidence[k] for k in EVIDENCE_FIELDS},
            'observation': {'at': snapshot['observed_at'], 'availability': snapshot['availability'], 'complete': snapshot['complete']},
            'recipe': [],
        })
    return sorted(groups.values(), key=lambda g: g['id'])

@contextmanager
def _read_export(path):
    """Open read-only without FIFO waits; validate the descriptor, not a prior stat.

    Explicit symlinks are allowed only when the opened target is a regular file.
    No paths from the JSON payload are opened. Unbuffered reads avoid read-ahead
    outside the caller's remaining byte budget.
    """
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOCTTY", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Snapshot must be a regular file')
        if info.st_size > MAX_SNAPSHOT_BYTES:
            raise ValueError('Snapshot size cap exceeded')
        with os.fdopen(fd, 'rb', buffering=0) as stream:
            fd = None  # The stream now owns (and closes) the descriptor.
            yield stream
    finally:
        if fd is not None:
            os.close(fd)


def _decode_export(path, total_bytes):
    """Charge actual reads against both caps, including one overflow probe byte."""
    chunks = []; file_bytes = 0
    with _read_export(path) as stream:
        while True:
            remaining = min(MAX_SNAPSHOT_BYTES - file_bytes, MAX_TOTAL_SNAPSHOT_BYTES - total_bytes)
            block = stream.read(min(64 * 1024, remaining + 1))
            if not block:
                break
            file_bytes += len(block)
            total_bytes += len(block)
            if file_bytes > MAX_SNAPSHOT_BYTES:
                raise ValueError('Snapshot grew beyond size cap')
            if total_bytes > MAX_TOTAL_SNAPSHOT_BYTES:
                raise ValueError('Combined snapshot size cap exceeded')
            chunks.append(block)
    snapshot = json.loads(b''.join(chunks))
    return snapshot, snapshot_games(snapshot), total_bytes

def load_exports(paths, *, with_context=False):
    """Combine only explicit exports; preserve variants and never fuzzy-merge titles."""
    paths = list(paths)
    if not paths or len(paths) > MAX_SNAPSHOTS:
        raise ValueError(f'Provide between 1 and {MAX_SNAPSHOTS} explicit snapshots')
    total_bytes = 0
    grouped = {}; seen_variants = set(); contexts = []
    for path in paths:
        try:
            snapshot, games, total_bytes = _decode_export(Path(path), total_bytes)
        except ValueError as exc:
            raise ValueError(f'{path}: {exc}') from exc
        source_context = snapshot['source']
        contexts.append({
            'provider': source_context['provider'],
            'instance': source_context['instance'],
            'host': source_context['host'],
            'availability': snapshot['availability'],
            'complete': snapshot['complete'],
            'observed_at': snapshot['observed_at'],
            'fixture_mode': snapshot.get('fixture_mode', False),
        })
        for game in games:
            target = grouped.setdefault(game['id'], {'id': game['id'], 'title': game['title'], 'variants': []})
            for variant in game['variants']:
                identity = variant['installation_id']
                if identity in seen_variants:
                    raise ValueError('Duplicate variant across selected snapshots')
                seen_variants.add(identity)
                target['variants'].append(variant)
    result = sorted(grouped.values(), key=lambda game: game['id'])
    if with_context:
        return result, contexts
    return result

def load_export(path, *, with_context=False):
    """Backward-compatible singular snapshot loader."""
    games, contexts = load_exports([path], with_context=True)
    if with_context:
        context = contexts[0]
        return games, context
    return games
