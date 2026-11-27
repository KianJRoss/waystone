"""App-owned Linux SQLite library. No Wine, SDK, HTML resource loading or execution.

Imported BSON is immutable provenance; native metadata is independently editable.
Only the segregated one-off migration tool requires LiteDB/.NET.
"""
from .platform import set_private_file_mode
from .platform import fsync_directory
from pathlib import Path
import base64
import collections
import hashlib
from html.parser import HTMLParser
import json
import os
import sqlite3
import tempfile


def scalar(value):
    if isinstance(value, dict):
        for key in ('$guid', '$date', '$numberLong', '$numberDecimal'):
            if key in value:
                return value[key]
    return value


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.block = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'iframe', 'object'): self.block += 1
        if not self.block and tag in ('p', 'br', 'div', 'li'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'iframe', 'object'): self.block = max(0, self.block - 1)
    def handle_data(self, data):
        if not self.block: self.parts.append(data)


def plain(value):
    p = _Text(); p.feed(value if isinstance(value, str) else ''); return ''.join(p.parts).strip()


TARGETS = {'DeveloperIds': 'Company', 'PublisherIds': 'Company', 'PlatformIds': 'Platform',
           'GenreIds': 'Genre', 'TagIds': 'Tag', 'CategoryIds': 'Category',
           'SeriesIds': 'Series', 'AgeRatingIds': 'AgeRating', 'RegionIds': 'Region',
           'FeatureIds': 'GameFeature', 'SourceId': 'GameSource',
           'CompletionStatusId': 'CompletionStatus', 'EmulatorId': 'Emulator'}
EMPTY_GUID = '00000000-0000-0000-0000-000000000000'


class LibraryRepository:
    """Runtime requires only Python stdlib and this NEW SQLite file."""
    def __init__(self, path, *, writable=False):
        self.path = Path(path).absolute()
        self.db = sqlite3.connect(self.path.as_uri() + ('?mode=rw' if writable else '?mode=ro'), uri=True)
        self.db.row_factory = sqlite3.Row
        if not writable: self.db.execute('PRAGMA query_only=ON')
        if self.db.execute('PRAGMA application_id').fetchone()[0] != 0x4B4C5231:
            self.close(); raise ValueError('Not a native library repository')
    def close(self): self.db.close()
    def games(self):
        return [self.game(r[0]) for r in self.db.execute('SELECT source_id FROM games ORDER BY title,source_id')]
    def game(self, source_id):
        row = self.db.execute('SELECT * FROM games WHERE source_id=?', (source_id,)).fetchone()
        if row is None: return None
        result = dict(row); result['metadata'] = json.loads(result.pop('metadata_json'))
        override = self.db.execute('SELECT metadata_json FROM native_metadata WHERE canonical_id=?', (row['canonical_id'],)).fetchone()
        if override: result['metadata'].update(json.loads(override[0]))
        return result
    def records(self, collection=None):
        query = 'SELECT * FROM records' + (' WHERE collection=?' if collection else '')
        for row in self.db.execute(query, (collection,) if collection else ()):
            r = dict(row); r['document'] = json.loads(r.pop('document_json')); r['types'] = json.loads(r.pop('types_json')); yield r
    def relationships(self, source_id):
        return [dict(r) for r in self.db.execute("SELECT * FROM relationships WHERE source_collection='Game' AND source_id=?", (source_id,))]
    def set_native_metadata(self, canonical_id, metadata):
        """Replace local metadata overrides; never execute imported actions or rewrite provenance."""
        if not isinstance(metadata, dict): raise ValueError('metadata must be an object')
        known = self.db.execute('SELECT 1 FROM games WHERE canonical_id=? LIMIT 1', (canonical_id,)).fetchone()
        if not known:
            table = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='launcher_records'").fetchone()
            known = self.db.execute('SELECT 1 FROM launcher_records WHERE ?=(\'launcher:\'||lower(provider)||\':\'||product_id) LIMIT 1',
                                    (canonical_id,)).fetchone() if table else None
        if not known: raise KeyError(canonical_id)
        with self.db:
            self.db.execute('INSERT INTO native_metadata VALUES (?,?) ON CONFLICT(canonical_id) DO UPDATE SET metadata_json=excluded.metadata_json', (canonical_id, dump(metadata)))

    @staticmethod
    def create_empty(path):
        """Create a private, app-owned native DB. Existing files are never replaced."""
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.waystone-library-', suffix='.sqlite3', dir=path.parent)
        set_private_file_mode(fd, 0o600); os.close(fd)
        db = sqlite3.connect(temporary)
        try:
            db.executescript('''PRAGMA application_id=1263292977;
            CREATE TABLE provenance(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE records(database_name TEXT,collection TEXT,ordinal INTEGER,source_id TEXT,document_json TEXT NOT NULL,bson BLOB NOT NULL,types_json TEXT NOT NULL,PRIMARY KEY(database_name,collection,ordinal));
            CREATE INDEX records_identity ON records(collection,source_id);
            CREATE TABLE relationships(source_collection TEXT,source_id TEXT,field TEXT,target_collection TEXT,target_id TEXT,status TEXT);
            CREATE TABLE games(source_id TEXT PRIMARY KEY,canonical_id TEXT NOT NULL,title TEXT,provider TEXT,provider_game_id TEXT,plugin_id TEXT,metadata_json TEXT NOT NULL);
            CREATE INDEX games_canonical ON games(canonical_id);
            CREATE TABLE aliases(source_id TEXT PRIMARY KEY,canonical_id TEXT NOT NULL);
            CREATE TABLE native_metadata(canonical_id TEXT PRIMARY KEY,metadata_json TEXT NOT NULL);
            CREATE TABLE launcher_records(source_key TEXT PRIMARY KEY,provider TEXT NOT NULL,product_id TEXT NOT NULL,title TEXT NOT NULL,platform TEXT NOT NULL,status TEXT NOT NULL,install_path TEXT NOT NULL,metadata_json TEXT NOT NULL,observed_at TEXT NOT NULL,seen INTEGER NOT NULL DEFAULT 1);
            CREATE INDEX launcher_records_provider ON launcher_records(provider,product_id);
            CREATE TABLE launcher_sync(provider TEXT PRIMARY KEY,installed_complete INTEGER NOT NULL,ownership_complete INTEGER NOT NULL,observed_at TEXT NOT NULL,notice TEXT NOT NULL DEFAULT '');
            ''')
            db.execute('INSERT INTO provenance VALUES (?,?)', ('kind', dump({'kind':'waystone-owned-library-v1'})))
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Native library integrity check failed')
        except BaseException:
            db.close()
            Path(temporary).unlink(missing_ok=True)
            raise
        finally:
            try: db.close()
            except Exception: pass
        try:
            with open(temporary, 'rb') as stream:
                os.fsync(stream.fileno())
            os.link(temporary, path)  # atomic no-overwrite publication
            Path(temporary).unlink()
            fsync_directory(path.parent)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return path

    def ensure_launcher_schema(self):
        """Additive schema migration; does not rewrite imported Playnite records."""
        # Deliberately do not enter the sqlite connection context here. This
        # helper is called from sync_launcher_records()'s transaction; a nested
        # Connection context commits on exit and would split the snapshot into
        # multiple transactions.
        self.db.execute('''CREATE TABLE IF NOT EXISTS launcher_records(
            source_key TEXT PRIMARY KEY,provider TEXT NOT NULL,product_id TEXT NOT NULL,
            title TEXT NOT NULL,platform TEXT NOT NULL,status TEXT NOT NULL,
            install_path TEXT NOT NULL,metadata_json TEXT NOT NULL,observed_at TEXT NOT NULL,
            seen INTEGER NOT NULL DEFAULT 1)''')
        self.db.execute('CREATE INDEX IF NOT EXISTS launcher_records_provider ON launcher_records(provider,product_id)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS launcher_sync(
            provider TEXT PRIMARY KEY,installed_complete INTEGER NOT NULL DEFAULT 0,
            ownership_complete INTEGER NOT NULL DEFAULT 0,observed_at TEXT NOT NULL,notice TEXT NOT NULL DEFAULT '')''')

    def sync_launcher_records(self, records, source_states, observed_at):
        """Apply a non-destructive, per-source snapshot. Only complete sources get absence deltas."""
        allowed = {'installed', 'not-installed', 'registered', 'not-owned', 'unknown'}
        normalized = []
        seen = set()
        for item in records:
            if not isinstance(item, dict):
                raise ValueError('Launcher records must be dictionaries')
            provider, product = item.get('provider'), item.get('product_id')
            title, platform, status = item.get('title'), item.get('platform'), item.get('status')
            if (not all(isinstance(value, str) and value and len(value) <= 512
                        for value in (provider, product, title, platform))
                    or status not in allowed):
                raise ValueError('Invalid launcher record')
            source_key = item.get('source_key') or f'{provider}:{product}'
            if not isinstance(source_key, str) or not source_key or len(source_key) > 1024 or source_key in seen:
                raise ValueError('Invalid or duplicate launcher source key')
            seen.add(source_key)
            install_path = item.get('install_path') or ''
            if not isinstance(install_path, str) or len(install_path) > 4096:
                raise ValueError('Invalid launcher installation path')
            metadata = item.get('metadata') or {}
            if not isinstance(metadata, dict) or len(dump(metadata).encode()) > 256 * 1024:
                raise ValueError('Invalid launcher metadata')
            normalized.append((source_key, provider, product, title, platform, status,
                               install_path, dump(metadata), observed_at))
        with self.db:
            self.ensure_launcher_schema()
            self.db.execute('UPDATE launcher_records SET seen=0')
            for row in normalized:
                self.db.execute('''INSERT INTO launcher_records
                    (source_key,provider,product_id,title,platform,status,install_path,metadata_json,observed_at,seen)
                    VALUES (?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(source_key) DO UPDATE SET provider=excluded.provider,product_id=excluded.product_id,
                    title=excluded.title,platform=excluded.platform,status=excluded.status,
                    install_path=excluded.install_path,metadata_json=excluded.metadata_json,
                    observed_at=excluded.observed_at,seen=1''', row)
            for provider, state in source_states.items():
                if not isinstance(provider, str) or not isinstance(state, dict):
                    raise ValueError('Invalid source state')
                installed_complete = bool(state.get('installed_complete'))
                ownership_complete = bool(state.get('ownership_complete'))
                # Source-specific absence semantics. Incomplete/unavailable caches never create deltas.
                if installed_complete:
                    self.db.execute('''UPDATE launcher_records SET status='not-installed',install_path='',observed_at=?
                        WHERE provider=? AND seen=0 AND status='installed' ''', (observed_at, provider))
                if ownership_complete:
                    self.db.execute('''UPDATE launcher_records SET status='not-owned',install_path='',observed_at=?
                        WHERE provider=? AND seen=0 AND (metadata_json LIKE '%"ownership":"cached_entitlement"%'
                          OR metadata_json LIKE '%"ownership":"steam_webapi"%') ''',
                        (observed_at, provider))
                self.db.execute('''INSERT INTO launcher_sync(provider,installed_complete,ownership_complete,observed_at,notice) VALUES(?,?,?,?,?)
                    ON CONFLICT(provider) DO UPDATE SET installed_complete=excluded.installed_complete,
                    ownership_complete=excluded.ownership_complete,observed_at=excluded.observed_at,notice=excluded.notice''',
                    (provider, int(installed_complete), int(ownership_complete), observed_at,
                     str(state.get('notice') or '')[:1000]))


def migrate(records_path, manifest_path, canonical_catalog_path, new_database):
    """All records retained; failures abort, never silently skip malformed records.

    Canonical IDs/explicit source labels come only from exact existing variant IDs.
    PluginId is preserved separately and NEVER mapped to a store or platform.
    """
    completion_path = Path(str(records_path) + '.complete.json')
    completion = json.loads(completion_path.read_text())
    records_digest = hashlib.sha256(Path(records_path).read_bytes()).hexdigest()
    if records_digest != completion['records_sha256']: raise ValueError('Export checksum mismatch/incomplete export')
    out = Path(new_database); out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('xb'): pass
    out.chmod(0o600)
    db = sqlite3.connect(out)
    try:
        db.executescript('''PRAGMA application_id=1263292977;
        CREATE TABLE provenance(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE records(database_name TEXT,collection TEXT,ordinal INTEGER,source_id TEXT,document_json TEXT NOT NULL,bson BLOB NOT NULL,types_json TEXT NOT NULL,PRIMARY KEY(database_name,collection,ordinal));
        CREATE INDEX records_identity ON records(collection,source_id);
        CREATE TABLE relationships(source_collection TEXT,source_id TEXT,field TEXT,target_collection TEXT,target_id TEXT,status TEXT);
        CREATE TABLE games(source_id TEXT PRIMARY KEY,canonical_id TEXT NOT NULL,title TEXT,provider TEXT,provider_game_id TEXT,plugin_id TEXT,metadata_json TEXT NOT NULL);
        CREATE TABLE aliases(source_id TEXT PRIMARY KEY,canonical_id TEXT NOT NULL);
        CREATE TABLE native_metadata(canonical_id TEXT PRIMARY KEY,metadata_json TEXT NOT NULL);
        ''')
        aliases = {}; catalog = json.loads(Path(canonical_catalog_path).read_text())
        for game in catalog['games']:
            for variant in game.get('variants', []):
                ident = variant.get('source_id'); value = (game['id'], variant.get('source', 'Unassigned'))
                if ident in aliases and aliases[ident] != value: raise ValueError('Conflicting exact canonical alias')
                aliases[ident] = value
        counts = collections.Counter(); fields = collections.Counter(); docs = {}; game_rows = []
        with Path(records_path).open() as source:
            for line in source:
                row = json.loads(line); doc = json.loads(row['document_json'])
                if not isinstance(doc, dict): raise ValueError('Non-document source record')
                ident = scalar(doc.get('_id')); ident = str(ident) if ident is not None else None
                key = (row['collection'], ident)
                if ident is not None and key in docs: raise ValueError('Duplicate source entity ID')
                docs[key] = doc
                db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?)', (row['database'], row['collection'], row['ordinal'], ident, row['document_json'], base64.b64decode(row['bson_base64'], validate=True), dump(row['types'])))
                counts[row['database'] + '/' + row['collection']] += 1
                for field, value in doc.items():
                    fields[row['collection'] + '.' + field + ':present'] += 1
                    if value is not None: fields[row['collection'] + '.' + field + ':nonnull'] += 1
                if row['collection'] == 'Game': game_rows.append((ident, doc))
        refs = collections.Counter()
        for (collection, ident), doc in docs.items():
            for field, value in doc.items():
                if not (field.endswith('Id') or field.endswith('Ids')) or field == '_id': continue
                for target in value if isinstance(value, list) else [value]:
                    target = scalar(target)
                    if target is None or target == EMPTY_GUID: continue
                    target_collection = TARGETS.get(field)
                    status = ('resolved' if (target_collection, str(target)) in docs else 'unresolved') if target_collection else 'unknown_relationship_type'
                    db.execute('INSERT INTO relationships VALUES (?,?,?,?,?,?)', (collection, ident, field, target_collection, str(target), status)); refs[collection + '.' + field + ':' + status] += 1
        for ident, doc in game_rows:
            if ident is None: raise ValueError('Game without identity')
            canonical, provider = aliases.get(ident, ('playnite:' + ident, 'Unassigned'))
            if not provider or provider in ('Manual', 'Manual/Unassigned'): provider = 'Unassigned'
            metadata = dict(doc) # ALL fields retained, including inert actions/custom fields.
            metadata['description_text'] = plain(doc.get('Description'))
            for field, target in TARGETS.items():
                value = doc.get(field); ids = value if isinstance(value, list) else [value]
                metadata[field + '_resolved_names'] = [docs[(target, str(scalar(i)))].get('Name') for i in ids if (target, str(scalar(i))) in docs]
            db.execute('INSERT INTO games VALUES (?,?,?,?,?,?,?)', (ident, canonical, doc.get('Name'), provider, str(scalar(doc['GameId'])) if doc.get('GameId') is not None else None, scalar(doc.get('PluginId')), dump(metadata)))
            db.execute('INSERT INTO aliases VALUES (?,?)', (ident, canonical))
        manifest = json.loads(Path(manifest_path).read_text())
        database_counts = {name: sum(v for k, v in counts.items() if k.startswith(name + '/')) for name in manifest.get('before', {})}
        if dict(counts) != completion['collections'] or database_counts != completion['databases']:
            raise ValueError('Export database/collection count mismatch')
        summary = {'databases': database_counts, 'collections': dict(counts), 'fields': dict(fields), 'foreign_references': dict(refs), 'games': len(game_rows), 'records': sum(counts.values()), 'canonical_aliases_matched': sum(i in aliases for i, _ in game_rows), 'canonical_aliases_missing': sum(i not in aliases for i, _ in game_rows)}
        for key, value in {'manifest': json.loads(Path(manifest_path).read_text()), 'reconciliation': summary, 'records_sha256': records_digest, 'export_completion': completion, 'canonical_catalog_sha256': hashlib.sha256(Path(canonical_catalog_path).read_bytes()).hexdigest()}.items():
            db.execute('INSERT INTO provenance VALUES (?,?)', (key, dump(value)))
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ValueError('SQLite integrity failure')
        return summary
    except BaseException:
        db.close(); out.unlink(); raise
    finally: db.close()
