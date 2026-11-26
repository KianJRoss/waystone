"""Read-only native catalog UI facade; store listings never enter the library model."""
import copy
import json
from pathlib import Path
import sys
from PySide6.QtCore import QObject, Property, Signal, Slot

# Import source services only, never the upstream executable/helper/Windows bridge.
SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

try:
    from private_addons import (
        AVAILABLE as _ADDONS_AVAILABLE,
        NATIVE_DIALOG_TABS as _NATIVE_DIALOG_TABS,
        STORE_CATALOG_KEY as _STORE_CATALOG_KEY,
        configure_native_provider_view as _configure_native_provider_view,
    )
except ImportError:
    _ADDONS_AVAILABLE = False
    _NATIVE_DIALOG_TABS = [
        'Store catalog — not owned library',
        'Emulation — game catalog',
        'Native read-only control API — no AI model configured',
    ]
    _STORE_CATALOG_KEY = ''
    _configure_native_provider_view = None


# Caps include JSON escaping (not just Unicode character count). A 50-row
# projection of the fixed fields below stays below 64KiB even for astral text.
MAX_DISPLAY_JSON_BYTES = 128
MAX_PAGE_JSON_BYTES = 65536


def display_claim(value):
    """Detached scalar display claim; nested untrusted data is never exposed."""
    if value is None or type(value) in (bool, int, float):
        if len(json.dumps(value, ensure_ascii=True).encode()) <= MAX_DISPLAY_JSON_BYTES:
            return value
        value = str(value)
    elif not isinstance(value, str):
        return '[non-scalar imported claim]'
    if len(json.dumps(value, ensure_ascii=True).encode()) <= MAX_DISPLAY_JSON_BYTES:
        return value
    marker = '…[display truncated]'
    low, high = 0, min(len(value), MAX_DISPLAY_JSON_BYTES)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(value[:middle] + marker, ensure_ascii=True).encode()) <= MAX_DISPLAY_JSON_BYTES:
            low = middle
        else:
            high = middle - 1
    return value[:low] + marker


def display_page(rows):
    projected = [{key: display_claim(value) for key, value in row.items()} for row in rows]
    # Fixed field counts and PAGE_SIZE guarantee this cap, without dropping rows
    # or changing pagination/selection addressing.
    if len(json.dumps(projected, ensure_ascii=True).encode()) > MAX_PAGE_JSON_BYTES:
        raise ValueError('Display projection exceeds page byte cap')
    return projected


class NativeProviderView(QObject):
    changed = Signal()
    PAGE_SIZE = 50

    def __init__(self, store_catalog=None, display_catalog=None, parent=None, emulation_db=None, **kwargs):
        super().__init__(parent)
        self._store_loaded = False
        self._items = ()
        self._matches = ()
        self._page = 0
        self._detail = ''
        self._retro = ()
        self._retro_matches = ()
        self._retro_page = 0
        self._retro_platform = 'All platforms'
        self._retro_detail = ''
        self._ai = None
        self._ai_result = 'Native AI control: no selected display catalog. No models or listeners are running.'
        self._notice = 'Catalog provider: source not selected.'
        self._database = None
        self._db_summary = None
        self._db_rows = []
        self._db_cursors = ['']
        self._db_next = None
        self._db_query = ''
        self._db_platform = ''
        self._db_notice = 'Selected export slice only; original database/live coverage unknown.'

        if _configure_native_provider_view is not None:
            _configure_native_provider_view(
                self,
                store_catalog=store_catalog,
                display_catalog=display_catalog,
                emulation_db=emulation_db,
                display_claim_fn=display_claim,
                **kwargs,
            )
        else:
            if kwargs:
                unexpected = ', '.join(sorted(kwargs))
                raise TypeError(f'Unexpected keyword arguments: {unexpected}')
            self._retro = tuple(copy.deepcopy(
                {'id': game.get('id'), 'title': game.get('title'),
                 'variant_index': index, 'source_id': variant.get('source_id'),
                 'platform': variant.get('platform'), 'imported_status_claim': variant.get('status'),
                 'installation_validation': 'unknown', 'launchability': 'unknown'})
                for game in (display_catalog or {}).get('games', [])
                for index, variant in enumerate(game.get('variants', []))
                if isinstance(variant.get('source_context'), dict)
                and variant['source_context'].get('provider') == 'Emulation')
            self._retro_matches = self._retro

        if display_catalog is not None:
            try:
                from native_services.ai_control import AIControlService
                self._ai = AIControlService(display_catalog)
                self._ai_result = 'Native read-only AI control ready for the selected display catalog. No model, listener, classification, writes or launch actions.'
            except (ImportError, OSError, ValueError, RuntimeError) as exc:
                self._ai_result = f'Native AI control unavailable: {type(exc).__name__}: {display_claim(str(exc))}. Library unchanged.'

    @Property(bool, constant=True)
    def addonsAvailable(self):
        return bool(_ADDONS_AVAILABLE)

    @Property(str, constant=True)
    def storeCatalogKey(self):
        return str(_STORE_CATALOG_KEY)

    @Property('QStringList', constant=True)
    def serviceTabs(self):
        return list(_NATIVE_DIALOG_TABS)

    def _load_db_page(self):
        result = self._database.page(cursor=self._db_cursors[-1], query=self._db_query, platform=self._db_platform)
        self._db_rows = [{'title':r['name'], 'platform':r['platform'], 'sourceId':r['sourceId']} for r in result['items']]
        self._db_next = result['next_cursor']

    @Property(str, notify=changed)
    def retroNotice(self):
        return self._db_notice

    @Property('QStringList', notify=changed)
    def retroPlatforms(self):
        return ['All platforms'] + (self._db_summary['platforms'] if self._database is not None else sorted({str(r['platform']) for r in self._retro}))

    @Slot(str)
    def setRetroPlatform(self, platform):
        if self._database is not None:
            self._db_platform = '' if platform == 'All platforms' else platform
            self.searchRetro(self._db_query)
        else:
            self._retro_platform = platform
            self.searchRetro('')

    @Property(str, notify=changed)
    def notice(self):
        return self._notice + f'\nEmulation catalog (imported records): {len(self._retro)} exact-provenance variants from the selected library export. Original database completeness and live service availability are unknown; no GameFlix web UI is used.'

    @Property('QVariantList', notify=changed)
    def rows(self):
        return display_page([{'sourceId': item.source_id, 'name': item.name, 'sourceKey': item.source_key}
                for item in self._matches[self._page:self._page + self.PAGE_SIZE]])

    @Property(str, notify=changed)
    def pageLabel(self):
        end = min(self._page + self.PAGE_SIZE, len(self._matches))
        return f'{self._page + 1 if end else 0}–{end} of {len(self._matches)} matching / {len(self._items)} store records'

    @Property(int, notify=changed)
    def totalCount(self):
        return len(self._items)

    @Property(bool, notify=changed)
    def canPrevious(self):
        return self._page > 0

    @Property(bool, notify=changed)
    def canNext(self):
        return self._page + self.PAGE_SIZE < len(self._matches)

    @Property(str, notify=changed)
    def detail(self):
        return self._detail

    @Property(str, notify=changed)
    def aiResult(self):
        return self._ai_result

    @Slot(str)
    def search(self, query):
        needle = query.strip().casefold()
        self._matches = tuple(i for i in self._items if needle in i.name.casefold() or needle in i.source_key.casefold())
        self._page = 0
        self._detail = ''
        self.changed.emit()

    @Slot(int)
    def turnPage(self, direction):
        if direction == -1 and self.canPrevious:
            self._page -= self.PAGE_SIZE
        elif direction == 1 and self.canNext:
            self._page += self.PAGE_SIZE
        self.changed.emit()

    @Slot(str)
    def select(self, source_id):
        item = next((i for i in self._matches if i.source_id == source_id), None)
        self._detail = (f'{display_claim(item.name)}\nProvider: {item.provider}\nNative ID: {item.source_id}\nSource key (display): {display_claim(item.source_key)}\n'
                        f'Record: {item.record_number}\nDownloaded: unknown · Playable: unknown\n'
                        'Store listing only; no ownership, installation or launchability inferred.') if item else ''
        self.changed.emit()

    @Property(int, notify=changed)
    def retroCount(self):
        return self._db_summary['total'] if self._database is not None else len(self._retro)

    @Property('QVariantList', notify=changed)
    def retroRows(self):
        if self._database is not None:
            return [dict(row) for row in self._db_rows]
        return display_page(self._retro_matches[self._retro_page:self._retro_page + self.PAGE_SIZE])

    @Property(str, notify=changed)
    def retroPageLabel(self):
        if self._database is not None:
            count = self._db_summary.get('platform_counts', {}).get(self._db_platform) if self._db_platform else self.retroCount
            return f'Page {len(self._db_cursors)} · {len(self._db_rows)} shown / {count if count is not None else "unknown"} records in {self._db_platform or "all systems"} · prefix search'
        end = min(self._retro_page + self.PAGE_SIZE, len(self._retro_matches))
        return f'{self._retro_page + 1 if end else 0}–{end} of {len(self._retro_matches)} matching / {len(self._retro)} migrated variants'

    @Property(str, notify=changed)
    def retroDetail(self):
        return self._retro_detail

    @Slot(str)
    def searchRetro(self, query):
        if self._database is not None:
            self._db_query = query
            self._db_cursors = ['']
            self._retro_detail = ''
            try:
                self._load_db_page()
            except ValueError as exc:
                self._db_rows = []
                self._db_next = None
                self._retro_detail = 'Database search unavailable: ' + str(display_claim(str(exc)))
            self.changed.emit()
            return
        needle = query.strip().casefold()
        self._retro_matches = tuple(row for row in self._retro if (self._retro_platform == 'All platforms' or str(row['platform']) == self._retro_platform) and (needle in str(row['title']).casefold() or needle in str(row['platform']).casefold()))
        self._retro_page = 0
        self._retro_detail = ''
        self.changed.emit()

    @Slot(int)
    def turnRetroPage(self, direction):
        if self._database is not None:
            if direction == -1 and len(self._db_cursors)>1:
                self._db_cursors.pop()
            elif direction == 1 and self._db_next is not None:
                self._db_cursors.append(self._db_next)
            else:
                return
            try:
                self._load_db_page()
            except ValueError as exc:
                self._retro_detail = 'Database page unavailable: ' + str(display_claim(str(exc)))
            self.changed.emit()
            return
        if direction == -1 and self._retro_page > 0:
            self._retro_page -= self.PAGE_SIZE
        elif direction == 1 and self._retro_page + self.PAGE_SIZE < len(self._retro_matches):
            self._retro_page += self.PAGE_SIZE
        self.changed.emit()

    @Slot(int)
    def selectRetro(self, index):
        if self._database is not None:
            try:
                self._retro_detail = json.dumps(self._database.detail(self._db_rows[index]['sourceId']),ensure_ascii=False,indent=2) if 0<=index<len(self._db_rows) else ''
            except ValueError as exc:
                self._retro_detail = 'Database detail unavailable: ' + str(display_claim(str(exc)))
            self.changed.emit()
            return
        rows = self.retroRows
        self._retro_detail = json.dumps(rows[index], ensure_ascii=False, indent=2) if 0 <= index < len(rows) else ''
        self.changed.emit()

    @Slot(str)
    def reviewLibrary(self, query):
        if self._ai is not None:
            try:
                action = 'search' if query.strip() else 'review'
                args = {'query': query.strip(), 'limit': 20} if query.strip() else {'limit': 20}
                self._ai_result = json.dumps(self._ai.execute(action, args), ensure_ascii=False, indent=2)
            except (ValueError, RuntimeError) as exc:
                self._ai_result = f'Read-only request unavailable: {type(exc).__name__}: {exc}'
        self.changed.emit()
