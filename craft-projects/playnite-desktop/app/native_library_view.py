"""Detached native-library projection of an already validated ConnectionView.

No discovery, migration-title matching, service/model calls or execution. Strings
are bounded literal text; QML consumers must use Text.PlainText. Native variant
keys, not titles/products/canonical IDs, address distinct installations/accounts.
"""
import json
import copy
import re

from PySide6.QtCore import QObject, Property, Signal, Slot

from connection_view import ConnectionView, PROVIDERS

MAX_PROJECTION = 65536
ALL_PROVIDERS = 'All native providers'
STATES = {
    'installed': 'Declared + directory present',
    'unverified': 'Unverified native observation — installation unknown',
    'client-only': 'Client only — no game inventory',
}


def _required_text(value, maximum):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError('Invalid native projection text')
    # Reject broken Unicode without ever including source text in diagnostics.
    value.encode('utf-8')
    return value


def _display(value, maximum=256):
    """Bounded scalar only; never stringify arbitrary nested source metadata."""
    if not isinstance(value, str):
        return ''
    clipped = len(value) > maximum
    value = ''.join(c if c.isprintable() else ' ' for c in value[:maximum])
    if not clipped and len(json.dumps(value, ensure_ascii=True).encode()) <= maximum:
        return value
    # Budget escaped bytes too: astral Unicode must not overflow a Qt page or
    # detail. Identity keys and canonical IDs never pass through this function.
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(value[:middle] + '…', ensure_ascii=True).encode()) <= maximum:
            low = middle
        else:
            high = middle - 1
    return value[:low] + '…'


def _json(value):
    return json.dumps(value, ensure_ascii=True, indent=2)


def _snapshot(connection_view):
    """Check the projection boundary, not reimplement the catalog join validator.

    Only ConnectionView.load_inventory validates joins against the selected
    catalog. Never accept a raw inventory/dict/duck-typed migration view here.
    Copy only allowlisted fields; no recursive metadata/intent serialization.
    """
    if not isinstance(connection_view, ConnectionView):
        raise ValueError('Expected validated ConnectionView')
    data, records = connection_view._data, connection_view._records
    if data is None and records == []:
        return (), '', ''
    if (not isinstance(data, dict) or type(data.get('schema_version')) is not int
            or data['schema_version'] != 1
            or data.get('inventory_kind') != 'native-connections-readonly'
            or data.get('execution_supported') is not False
            or not isinstance(records, list) or len(records) > 10000
            or data.get('connections') is not records):
        raise ValueError('Invalid native snapshot shape')
    host = _required_text(data.get('host'), 256)
    observed = _required_text(data.get('observed_at'), 128)
    items, seen, links = [], set(), {}
    joins = {'unmatched': [], 'ambiguous': []}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Invalid native record')
        provider = _required_text(record.get('provider'), 64)
        state = _required_text(record.get('state'), 32)
        join = _required_text(record.get('join_state'), 32)
        if provider not in PROVIDERS or state not in STATES or join not in ('matched', 'unmatched', 'ambiguous'):
            raise ValueError('Invalid native classification')
        if ((provider == 'lutris-battlenet' and state == 'installed')
                or (provider != 'lutris-battlenet' and state == 'client-only')):
            raise ValueError('Client presence is not installed-game evidence')
        keys = {}
        for field in ('record_key', 'variant_key'):
            value = _required_text(record.get(field), 64)
            if not re.fullmatch('[a-f0-9]{64}', value):
                raise ValueError('Invalid native identity')
            keys[field] = value
        key = keys['variant_key']
        if key in seen:
            raise ValueError('Duplicate native variant identity')
        seen.add(key)
        canonical = record.get('canonical_game_ids')
        if not isinstance(canonical, list) or len(canonical) != (1 if join == 'matched' else 0):
            raise ValueError('Invalid native canonical identity shape')
        canonical = [_required_text(value, 4096) for value in canonical]
        for game_id in canonical:
            links.setdefault(game_id, []).append(key)
        if join in joins:
            joins[join].append(key)
        context = record.get('source_context')
        if not isinstance(context, dict):
            raise ValueError('Invalid native source context')
        _required_text(context.get('host'), 256)
        # Steam's source instance can be in snapshot_context.source. Account
        # claims are optional labels only; variant_key remains the identity.
        snapshot = context.get('snapshot_context')
        source = snapshot.get('source') if isinstance(snapshot, dict) else None
        source = source if isinstance(source, dict) else {}
        safe_context = {name: _display(context.get(name, source.get(name)), 512)
                        for name in ('host', 'instance', 'installation_id',
                                     'account_id', 'account', 'account_name',
                                     'installation_state', 'payload_presence')}
        items.append({**keys, 'provider': provider, 'state': state,
                      'title': _required_text(record.get('title'), 4096),
                      'product_id': _required_text(record.get('product_id'), 512),
                      'platform': _display(record.get('platform')),
                      'join_state': join, 'canonical_game_ids': canonical,
                      'source_context': safe_context})
    declared = data.get('canonical_links')
    if not isinstance(declared, dict) or any(not isinstance(v, list) or any(not isinstance(k, str) for k in v) for v in declared.values()):
        raise ValueError('Invalid native link index')
    if {k: sorted(v) for k, v in declared.items()} != {k: sorted(v) for k, v in links.items()}:
        raise ValueError('Inconsistent native link index')
    for join, expected in joins.items():
        actual = data.get(join + '_native_variant_keys')
        if not isinstance(actual, list) or any(not isinstance(k, str) for k in actual) or sorted(actual) != sorted(expected):
            raise ValueError('Inconsistent independent native record index')
    return tuple(items), host, observed


class NativeLibraryView(QObject):
    changed = Signal()
    PAGE_SIZE = 50

    def __init__(self, connection_view, parent=None):
        super().__init__(parent)
        self._provider, self._query, self._installed_only = ALL_PROVIDERS, '', True
        self._items = self._matches = ()
        self._index = {}
        self._page, self._history, self._detail = 0, [], ''
        self._notice = ''
        self.refreshFrom(connection_view)

    @Slot(QObject)
    def refreshFrom(self, connection_view):
        """Replace, never merge; retain filters but discard stale selection/page."""
        try:
            items, host, observed = _snapshot(connection_view)
            notice = ('Native connections not selected. No discovery or launching.'
                      if not host else f'Native observation snapshot • {_display(host)} • {_display(observed)}')
        except (ValueError, TypeError, AttributeError, UnicodeError):
            items = ()
            notice = 'Native library unavailable: invalid ConnectionView snapshot. No records displayed.'
        self._items = items
        self._index = {r['variant_key']: r for r in items}
        installed = sum(r['state'] == 'installed' for r in items)
        client = sum(r['state'] == 'client-only' for r in items)
        unverified = sum(r['state'] == 'unverified' for r in items)
        unmatched_installed = sum(r['state'] == 'installed' and r['join_state'] == 'unmatched' for r in items)
        self._notice = (f'{notice}\n{installed} installed / {len(items)} total native records; '
                        f'{client} client-only; {unverified} unverified; '
                        f'{unmatched_installed} unmatched installed (retained).\n'
                        'Installed = declaration + directory presence, not payload integrity, '
                        'Linux compatibility or gameplay. Unknown and absent joins do not mean '
                        'uninstalled. Imported install claims remain separate. No execution or launches.')
        self._filter()

    @Slot(str, result='QVariantList')
    def recordsForGame(self, canonical_id):
        """Exact canonical joins, independent of browser filters; detached values."""
        if not isinstance(canonical_id, str) or not canonical_id:
            return []
        return [copy.deepcopy(row) for row in self._items
                if row['join_state'] == 'matched' and canonical_id in row['canonical_game_ids']
                and row['state'] == 'installed']

    def _filter(self):
        query = self._query.casefold()
        self._matches = tuple(sorted((r for r in self._items
                              if (self._provider == ALL_PROVIDERS or r['provider'] == self._provider)
                              and (not self._installed_only or r['state'] == 'installed')
                              and (not query or any(query in r[f].casefold() for f in ('title', 'product_id')))),
                                    key=lambda r: (r['title'].casefold(), r['variant_key'])))
        self._page, self._history, self._detail = 0, [], ''
        self.changed.emit()

    @Slot(str, str, bool)
    def setFilters(self, provider, query, installed_only):
        # Accept provider keys from providers, and existing human display labels.
        labels = {label: key for key, label in PROVIDERS.items()}
        self._provider = labels.get(provider, provider) if isinstance(provider, str) and provider else ALL_PROVIDERS
        self._query = query[:256] if isinstance(query, str) else ''
        self._installed_only = installed_only is True
        self._filter()

    @Property('QStringList', notify=changed)
    def providers(self):
        return [ALL_PROVIDERS] + sorted({r['provider'] for r in self._items})

    @Property('QVariantList', notify=changed)
    def rows(self):
        result = []
        for record in self._matches[self._page:self._page + self.PAGE_SIZE]:
            row = {'variantKey': record['variant_key'], 'recordKey': record['record_key'],
                   'title': _display(record['title'], 128),
                   'provider': PROVIDERS[record['provider']], 'providerKey': record['provider'],
                   'productId': _display(record['product_id'], 128),
                   'state': record['state'], 'observation': STATES[record['state']],
                   'joinState': record['join_state'],
                   'canonicalGameIds': list(record['canonical_game_ids'])}
            if len(_json(result + [row]).encode('utf-8')) > MAX_PROJECTION:
                break
            result.append(row)
        return result

    @Property(int, notify=changed)
    def count(self):
        """Matching native records, independent of the current page."""
        return len(self._matches)

    @Property(str, notify=changed)
    def notice(self):
        return self._notice

    @Property(str, notify=changed)
    def pageLabel(self):
        end = self._page + len(self.rows)
        return f'{self._page + 1 if end else 0}–{end} of {self.count} native records'

    @Property(bool, notify=changed)
    def canNext(self):
        return self._page + len(self.rows) < self.count

    @Property(bool, notify=changed)
    def canPrevious(self):
        return self._page > 0

    @Slot(int)
    def page(self, direction):
        if direction == 1 and self.canNext:
            self._history.append(self._page)
            self._page += len(self.rows)
        elif direction == -1 and self.canPrevious:
            self._page = self._history.pop() if self._history else 0
        self.changed.emit()

    @Property(str, notify=changed)
    def detail(self):
        return self._detail

    @Slot(str)
    def select(self, variant_key):
        record = self._index.get(variant_key) if isinstance(variant_key, str) else None
        if record is None:
            self._detail = ''
        else:
            facts = {name: _display(record[name], 512) for name in ('provider', 'product_id', 'title', 'platform')}
            facts.update(record_key=record['record_key'], variant_key=record['variant_key'],
                         state=record['state'], observation=STATES[record['state']],
                         join_state=record['join_state'],
                         canonical_game_ids=list(record['canonical_game_ids']),
                         source_context=dict(record['source_context']),
                         verification_scope='Metadata plus directory presence only; not payload integrity or gameplay',
                         launch_policy='No execution or launches')
            self._detail = _json(facts)
        self.changed.emit()
