"""Explicit native observation snapshot. No scanner, launcher, URL or source writer."""
import json
import os
import re
import stat
from collections import defaultdict
from pathlib import Path
from PySide6.QtCore import QObject, Property, Signal, Slot

from native_services.installation_library import ALIASES as INSTALL_ALIASES
from native_services.install_receipts import PROVIDERS as RECEIPT_PROVIDERS
from native_services import waystone_paths as _wpaths

MAX_BYTES = 8 * 1024 * 1024
MAX_PROJECTION = 65536
PROVIDERS = {'steam': 'Steam', 'heroic-epic': 'Heroic / Epic',
             'xodus': 'Xodus / Xbox', 'lutris-battlenet': 'Lutris / Battle.net client'}
JOINS = {'matched', 'unmatched', 'ambiguous'}


def _text(value, maximum=512, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value):
        raise ValueError('Invalid bounded native identity/text')
    return value


def _aliases(row):
    provider, product = row['provider'], row['product_id']
    if provider == 'steam':
        if not re.fullmatch(r'[1-9][0-9]{0,15}', product):
            raise ValueError('Invalid exact Steam product ID')
        return {('Steam', product)}
    if provider == 'heroic-epic':
        return {('Epic', product)}
    if provider == 'xodus':
        if not re.fullmatch('[a-f0-9]{16}', product):
            raise ValueError('Invalid exact Xodus identity')
        return {('Xbox', value) for field in ('storeId', 'offerProductId')
                if isinstance((value := row['source_context'].get(field)), str) and value}
    return set()  # Client presence NEVER joins to installed Battle.net games.


def load_inventory(path, catalog, digest):
    """Validate detached data and independently reproduce every declared exact join."""
    fd = os.open(os.fspath(path), os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise ValueError('Native inventory must be a regular file within 8 MiB')
        chunks = []; count = 0
        while count <= MAX_BYTES:
            block = os.read(fd, min(65536, MAX_BYTES + 1 - count))
            if not block:
                break
            chunks.append(block); count += len(block)
        if count > MAX_BYTES:
            raise ValueError('Native inventory exceeds actual byte cap')
        after = os.fstat(fd)
        selected = os.stat(path, follow_symlinks=False)
        def fingerprint(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if count != after.st_size or fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(selected):
            raise ValueError('Native inventory changed during read')
    finally:
        os.close(fd)
    def no_constant(value):
        raise ValueError('Nonfinite native inventory number')
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate native inventory JSON key')
            result[key] = value
        return result
    try:
        data = json.loads(b''.join(chunks), parse_constant=no_constant, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('Expected bounded native inventory JSON') from error
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1 or data.get('inventory_kind') != 'native-connections-readonly':
        raise ValueError('Unsupported native inventory schema')
    native = catalog.get('catalog_kind') == 'native-library-sqlite-v1'
    anchor = (catalog.get('provenance', {}).get('canonical_catalog_sha256')
              if native and data.get('catalog_sha256') != digest else digest)
    if (not re.fullmatch('[a-f0-9]{64}', digest or '')
            or not re.fullmatch('[a-f0-9]{64}', anchor or '')
            or data.get('catalog_sha256') != anchor):
        raise ValueError('Native inventory catalog generation mismatch; no joins enabled')
    # Native DB provenance binds the historical alias generation ONLY. Every
    # declared product/canonical join below is independently reproduced against
    # CURRENT native records; this anchor is not a claim that DB bytes match JSON.
    if data.get('execution_supported') is not False:
        raise ValueError('Native observation UI never accepts an executor')
    _text(data.get('host'), 256); _text(data.get('observed_at'), 128)
    rows = data.get('connections')
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError('Expected at most 10000 native records')
    index = defaultdict(set)
    for game in catalog['games']:
        for variant in game['variants']:
            source, product = variant.get('source'), variant.get('provider_game_id')
            if source in ('Steam', 'Epic', 'Xbox') and isinstance(product, str) and product:
                index[(source, product)].add(game['id'])
    seen = set(); owners = defaultdict(set); aliases = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('provider'), str) or row['provider'] not in PROVIDERS:
            raise ValueError('Unsupported native provider')
        _text(row.get('product_id')); _text(row.get('title'), 4096)
        for field in ('record_key', 'variant_key'):
            if not isinstance(row.get(field), str) or not re.fullmatch('[a-f0-9]{64}', row[field]):
                raise ValueError('Invalid native record identity')
        if row['variant_key'] in seen:
            raise ValueError('Duplicate native variant identity')
        seen.add(row['variant_key'])
        if not isinstance(row.get('source_context'), dict) or not isinstance(row.get('evidence'), list):
            raise ValueError('Native evidence/source context required')
        _text(row['source_context'].get('host'), 256)
        if row.get('state') not in ('installed', 'unverified', 'client-only'):
            raise ValueError('Unknown native observation state')
        client = row['state'] == 'client-only'
        if (row['provider'] == 'lutris-battlenet' and row['state'] == 'installed') or (row['provider'] != 'lutris-battlenet' and client):
            raise ValueError('Client-only presence cannot be installed-game evidence')
        claims = row.get('current_claims')
        if not isinstance(claims, dict) or claims.get('installation') != row['state'] or claims.get('client_only') is not client or claims.get('gameplay_tested') is not False:
            raise ValueError('Inconsistent native verification scope')
        _text(claims.get('verification_scope'), 2048)
        intent = row.get('launch_intent')
        if not isinstance(intent, dict) or intent.get('dry_run') is not True or intent.get('execution_supported') is not False:
            raise ValueError('Only non-executable native observation intents accepted')
        if intent.get('provider') != row['provider'] or intent.get('variant_key') != row['variant_key']:
            raise ValueError('Native intent identity mismatch')
        aliases[row['variant_key']] = _aliases(row)
        for alias in aliases[row['variant_key']]:
            owners[alias].add(row['record_key'])
    links = {}; unmatched = set(); ambiguous = set()
    for row in rows:
        row_aliases = aliases[row['variant_key']]
        candidates = set().union(*(index[a] for a in row_aliases)) if row_aliases else set()
        collision = any(len(owners[a]) > 1 for a in row_aliases)
        join = 'ambiguous' if collision or len(candidates) > 1 else ('matched' if candidates else 'unmatched')
        canonical = sorted(candidates) if join == 'matched' else []
        if row.get('join_state') != join or row.get('canonical_game_ids') != canonical:
            raise ValueError('Declared native join differs from exact source/product identity')
        expected_evidence = [{'source': a[0], 'provider_game_id': a[1]} for a in sorted(row_aliases)]
        if row.get('join_evidence') != expected_evidence:
            raise ValueError('Native join evidence mismatch')
        for game_id in canonical:
            links.setdefault(game_id, []).append(row['variant_key'])
        if join == 'unmatched': unmatched.add(row['variant_key'])
        if join == 'ambiguous': ambiguous.add(row['variant_key'])
    declared = data.get('canonical_links')
    if not isinstance(declared, dict) or any(not isinstance(v, list) or any(not isinstance(k, str) or not re.fullmatch('[a-f0-9]{64}', k) for k in v) or len(v) != len(set(v)) for v in declared.values()):
        raise ValueError('Invalid canonical native link index')
    if {k: sorted(v) for k, v in declared.items()} != {k: sorted(v) for k, v in links.items()}:
        raise ValueError('Canonical native link index mismatch')
    for field, expected in (('unmatched_native_variant_keys', unmatched), ('ambiguous_native_variant_keys', ambiguous)):
        value = data.get(field)
        if not isinstance(value, list) or any(not isinstance(k, str) or not re.fullmatch('[a-f0-9]{64}', k) for k in value) or len(value) != len(set(value)) or set(value) != expected:
            raise ValueError('Independent native observations were lost or reassigned')
    return data


def _clip(value, count=256):
    return value[:count] + ('…' if len(value) > count else '')


def _bound_text(text, cap=MAX_PROJECTION):
    encoded = text.encode('utf-8')
    return encoded[:cap - 48].decode('utf-8', errors='ignore') + '\n[Display truncated]' if len(encoded) > cap else text


def _json_text(value):
    encoded = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    if len(encoded) > MAX_PROJECTION:
        return encoded[:MAX_PROJECTION - 48].decode('utf-8', errors='ignore') + '\n[Display truncated; no data executed]'
    return encoded.decode('utf-8')


class ConnectionView(QObject):
    changed = Signal()

    def __init__(self, path=None, catalog=None, catalog_sha256=None, parent=None):
        super().__init__(parent)
        self._data = load_inventory(path, catalog, catalog_sha256) if path is not None else None
        self._records = self._data['connections'] if self._data else []
        self._index = {r['variant_key']: r for r in self._records}
        self._matches = list(self._records); self._page = 0; self._page_history = []; self._detail = ''
        self._receipt_registry=None;self._receipt_links=set();self._receipt_present=set()

    def configureReceipts(self, registry, catalog):
        from native_services.installation_library import catalog_install_choices
        self._receipt_registry=registry
        self._receipt_links={(g,r['provider'],r['product_id']) for r in catalog_install_choices(catalog)
                             if r['provider'] in RECEIPT_PROVIDERS for g in r['game_ids']}
        return self.refreshReceipts()

    def refreshReceipts(self):
        """One host-local snapshot; never rescan/catalog-match by title."""
        present = set()
        if self._receipt_registry is not None:
            try:
                products = {(r['provider'], r['product_id']) for r in self._receipt_registry.snapshot()
                           if r.get('provider') in RECEIPT_PROVIDERS and r.get('is_present')}
                present |= {(g, provider, p) for g, provider, p in self._receipt_links if (provider, p) in products}
            except (ValueError, OSError, RuntimeError):
                pass  # Unreadable history is not evidence that files are present.
        # Ingest Epic Legendary installed.json
        try:
            epic_path = _wpaths.store('providers-v1', 'epic', 'legendary', 'installed.json')
            if epic_path.exists():
                data = json.loads(epic_path.read_text(encoding='utf-8', errors='replace'))
                if isinstance(data, dict):
                    for k, v in data.items():
                        app_id = k
                        if isinstance(v, dict):
                            app_id = v.get('app_name') or v.get('appName') or v.get('id') or k
                        if app_id:
                            for (gid, lp, lpid) in self._receipt_links:
                                if lp == 'epic' and str(lpid) == str(app_id):
                                    present.add((gid, 'epic', str(app_id)))
        except Exception:
            pass
        # Ingest Nile installed.json (Amazon)
        try:
            nile_paths = [
                _wpaths.store('providers-v1', 'amazon', 'nile', 'installed.json'),
                _wpaths.store('providers-v1', 'nile_store', 'installed.json'),
                _wpaths.store('providers-v1', 'nile_config', 'installed.json'),
            ]
            for np in nile_paths:
                if np.exists():
                    data = json.loads(np.read_text(encoding='utf-8', errors='replace'))
                    items = []
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, dict):
                                items.append(v)
                            elif isinstance(v, list):
                                items.extend([x for x in v if isinstance(x, dict)])
                        # also if the dict itself contains product entries directly, try values that look like records
                        if not items and data:
                            items = [v for v in data.values() if isinstance(v, dict)]
                    elif isinstance(data, list):
                        items = [x for x in data if isinstance(x, dict)]
                    for it in items:
                        pid = it.get('id') or it.get('product_id') or it.get('app_name') or it.get('appName')
                        if pid:
                            for (gid, lp, lpid) in self._receipt_links:
                                if lp == 'amazon' and str(lpid) == str(pid):
                                    present.add((gid, 'amazon', str(pid)))
                    break
        except Exception:
            pass
        changed = present != self._receipt_present
        self._receipt_present = present
        if changed:
            self.changed.emit()
        return changed

    def receipt_for_variant(self, game_id, variant):
        product=variant.get('provider_game_id')
        provider=INSTALL_ALIASES.get(str(variant.get('source','')).casefold())
        return isinstance(product,str) and (game_id,provider,product) in self._receipt_present

    def installation_basis_for_variant(self, game_id, variant):
        if self.receipt_for_variant(game_id,variant):
            return 'Verified provider files on this device; launch/runtime compatibility is separate'
        return 'Native manifest + directory observation; payload/gameplay not tested'

    def replaceFrom(self, other):
        """Swap an already-validated snapshot, retaining this QML object identity."""
        if not isinstance(other, ConnectionView) or other._data is None:
            raise ValueError('A validated native snapshot is required')
        self._data, self._records, self._index = other._data, other._records, other._index
        self._matches = list(self._records)
        self._page = 0; self._page_history = []; self._detail = ''
        self.changed.emit()

    @Property(bool, notify=changed)
    def available(self):
        return self._data is not None

    @Property(str, notify=changed)
    def notice(self):
        if not self._data:
            return 'Native connections not selected. Supply an explicit --connections snapshot; no discovery or launching.'
        return (f"Native observation snapshot • {self._data['host']} • {self._data['observed_at']}\n"
                f"{len(self._records)} native records; {len(self._data['canonical_links'])} exact canonical links; "
                f"{len(self._data['unmatched_native_variant_keys'])} unmatched; {len(self._data['ambiguous_native_variant_keys'])} ambiguous.\n"
                'Installed observation = declaration + directory only, not payload integrity, Linux compatibility or gameplay. '
                'Battle.net is client-only. Imported install claims remain separate. No executor or launch action exists.')

    @Property('QStringList', notify=changed)
    def providerKeys(self):
        return ['All native providers'] + sorted({r['provider'] for r in self._records})

    @Property('QStringList', notify=changed)
    def providerLabels(self):
        return ['All native providers'] + [PROVIDERS[k] for k in self.providerKeys[1:]]

    @Property('QVariantList', notify=changed)
    def rows(self):
        rows = []
        for record in self._matches[self._page:self._page + 50]:
            row = {'variantKey': record['variant_key'], 'title': _clip(record['title'], 128),
                   'provider': PROVIDERS[record['provider']], 'productId': _clip(record['product_id'], 128),
                   'joinState': record['join_state'], 'observation': self._state(record)}
            if len(json.dumps(rows + [row], ensure_ascii=False).encode()) > MAX_PROJECTION:
                break
            rows.append(row)
        return rows

    @staticmethod
    def _state(record):
        return {'installed': 'Declared + directory present', 'client-only': 'Client only — no game inventory',
                'unverified': 'Unverified native observation'}[record['state']]

    @Property(str, notify=changed)
    def pageLabel(self):
        return f'{self._page + 1 if self._matches else 0}–{min(self._page + len(self.rows), len(self._matches))} of {len(self._matches)} native observations'

    @Property(bool, notify=changed)
    def canPrevious(self):
        return self._page > 0

    @Property(bool, notify=changed)
    def canNext(self):
        return self._page + len(self.rows) < len(self._matches)

    @Property(str, notify=changed)
    def detail(self):
        return self._detail

    @Slot(str, str, str)
    def setFilters(self, provider, join, query):
        query = query[:256].casefold()
        self._matches = [r for r in self._records if (provider == 'All native providers' or r['provider'] == provider)
                         and (join == 'All join states' or r['join_state'] == join)
                         and (not query or query in r['title'].casefold() or query in r['product_id'].casefold())]
        self._page = 0; self._page_history = []; self._detail = ''; self.changed.emit()

    @Slot(int)
    def page(self, direction):
        if direction == -1 and self.canPrevious:
            self._page = self._page_history.pop() if self._page_history else 0
        elif direction == 1 and self.canNext:
            self._page_history.append(self._page)
            self._page += len(self.rows)
        self.changed.emit()

    @Slot(str)
    def select(self, variant_key):
        record = self._index.get(variant_key)
        self._detail = self._detail_for(record) if record else ''
        self.changed.emit()

    def _detail_for(self, record):
        # Never surface arbitrary metadata recipes/argv as a verified launch plan.
        fields = ('provider', 'product_id', 'record_key', 'variant_key', 'title', 'platform',
                  'canonical_game_ids', 'join_state', 'join_evidence', 'current_claims', 'evidence')
        facts = {key: record.get(key) for key in fields}
        facts['host'] = record['source_context'].get('host')
        facts['observed_at'] = self._data['observed_at']
        facts['launch_policy'] = 'No executor. Source-reported argv/recipes are intentionally not exposed.'
        return _json_text(facts)

    def summary_for(self, game_id):
        records = [self._index[key] for key in self._data['canonical_links'].get(game_id, [])] if self._data else []
        return {'nativeConnectionCount': len(records),
                'nativeConnections': _bound_text('\n'.join(f"{PROVIDERS[r['provider']]} · {r['product_id']} · {self._state(r)}" for r in records), 8192),
                'nativeConnectionEvidence': _bound_text('\n\n'.join(self._detail_for(r) for r in records)),
                'nativeConnectionNotice': ('Exact source/product-ID link to a separate native observation snapshot. '
                                           'Not a replacement for imported install status; gameplay not tested.') if records else ''}

    def installed_for_variant(self, game_id, variant):
        """Positive native observation only; absence never means uninstalled."""
        if self.receipt_for_variant(game_id,variant):
            return True
        if not self._data:
            return False
        source, product = variant.get('source'), variant.get('provider_game_id')
        if not isinstance(product, str):
            return False
        return any(record['state'] == 'installed' and record['join_state'] == 'matched'
                   and (source, product) in _aliases(record)
                   for key in self._data['canonical_links'].get(game_id, [])
                   for record in [self._index[key]])

    def matches(self, game_id, provider):
        if provider == 'All native providers':
            return True
        if not self._data:
            return False
        records = [self._index[key] for key in self._data['canonical_links'].get(game_id, [])]
        if provider == 'No exact native link':
            return not records
        return bool(records) if provider == 'Any exact native link' else any(r['provider'] == provider for r in records)
