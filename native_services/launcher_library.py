"""Offline first-party library discovery and sync for Waystone.

The scanner reads a deliberately small set of local launcher inventories. It
never reads credential files, invokes clients, scans arbitrary disks, or makes
network requests. SQLite owns the resulting catalog; source snapshots only add
or update rows, while absence is interpreted only for independently complete
installed/entitlement feeds.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from native_services.library_repository import LibraryRepository
from native_services.waystone_paths import xdg_paths
from native_services.platform import is_windows

MAX_SOURCE_FILE = 10 * 1024 * 1024
MAX_SOURCE_RECORDS = 30000
MAX_TITLE = 512


def default_library_path(home=None, environ=None):
    return xdg_paths(home, environ)['data'] / 'library.sqlite3'


def _read_vdf(path):
    from native_services.game_installs import parse_vdf
    try:
        st = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_SOURCE_FILE:
            return None
        return parse_vdf(path.read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        return None


def _steam_localconfig(home):
    """Return explicitly configured app IDs; this is NOT proof of ownership."""
    bases = (Path(home) / '.local/share/Steam/userdata',
             Path(home) / '.steam/steam/userdata',
             Path(home) / '.var/app/com.valvesoftware.Steam/.local/share/Steam/userdata')
    ids = set()
    for base in bases:
        if not base.is_dir():
            continue
        try:
            users = sorted(p for p in base.iterdir() if p.is_dir() and p.name.isdigit())[:64]
        except OSError:
            continue
        for user in users:
            data = _read_vdf(user / 'config/localconfig.vdf')
            try:
                app_rows = data['UserLocalConfigStore']['Software']['Valve']['Steam']['apps']
            except (TypeError, KeyError):
                continue
            if isinstance(app_rows, dict):
                ids.update(str(key) for key in app_rows if re.fullmatch(r'[1-9][0-9]{0,11}', str(key)))
    return ids


def _entry(provider, product_id, title, platform, status, *, path='', metadata=None, source_key=None):
    if not all(isinstance(x, str) and x.strip() for x in (provider, str(product_id), title, platform)):
        return None
    title = title.strip()[:MAX_TITLE]
    product_id = str(product_id).strip()
    if not title or not product_id or len(product_id) > 512:
        return None
    return {'source_key': source_key or f'{provider.casefold()}:{product_id}',
            'provider': provider, 'product_id': product_id, 'title': title,
            'platform': platform, 'status': status, 'install_path': str(path or '')[:4096],
            'metadata': metadata if isinstance(metadata, dict) else {}}


def _source_cache_complete(root, provider, *, installed=False):
    """True only when an approved cache of the requested kind parses.

    Installed snapshots cannot prove the complete entitlement set. Conversely,
    a complete library feed is not proof that installed-state data was scanned.
    Keep these two absence-delta authorities separate.
    """
    from native_services.provider_entitlements import ALLOWED_CACHE_TARGETS
    root = Path(root).resolve()
    for rel in ALLOWED_CACHE_TARGETS.get(provider, ()):
        is_installed_cache = 'installed' in rel.name.casefold()
        if is_installed_cache != installed:
            continue
        candidate = root / rel
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if resolved.name.casefold() in {
                    'auth.json', 'user.json', 'tokens.json', 'token.json', 'credentials.json'}:
                continue
            st = resolved.stat()
            if not stat.S_ISREG(st.st_mode) or st.st_size <= 0 or st.st_size > MAX_SOURCE_FILE:
                continue
            data = resolved.read_text(encoding='utf-8-sig')
            if len(data.encode('utf-8')) > MAX_SOURCE_FILE:
                continue
            data = json.loads(data)
        except (OSError, ValueError, UnicodeError):
            continue
        if isinstance(data, (dict, list)):
            return True
    return False


def _lutris_inventory_complete(path):
    try:
        uri = Path(path).resolve().as_uri() + '?mode=ro'
        connection = sqlite3.connect(uri, uri=True)
        try:
            return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='games'").fetchone()
                        and connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok')
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return False


def collect_launcher_library(home=None, *, data_dir=None, config_dir=None, rom_config_dir=None, local_games_path=None,
                             steam_session_store=None, steam_web_factory=None):
    """Read local sources into normalized rows; network and client launches are impossible."""
    requested_home = Path(home or Path.home()).expanduser().absolute()
    may_use_default_keyring = requested_home == Path.home().expanduser().absolute()
    home = requested_home
    roots = xdg_paths(home)
    data_dir = Path(data_dir or roots['data'])
    config_dir = Path(config_dir or roots['config'])
    if is_windows():
        from native_services.windows_catalog import collect_windows_launcher_library
        return collect_windows_launcher_library(home=home, data_dir=data_dir, config_dir=config_dir,
                                                rom_config_dir=rom_config_dir,
                                                local_games_path=local_games_path)
    rows = {}
    states = {}

    # Steam: appmanifest is positive installation evidence. localconfig.vdf can
    # contain stale IDs and no dependable title/ownership proof, so never turn
    # it into synthetic "Steam app <id>" placeholder rows.
    from native_services.game_installs import steam_games, steam_libraries
    steam_libs = steam_libraries(home)
    steam_complete = bool(steam_libs)
    steam_installed = {}
    for item in steam_games(home):
        app_id = str(item.get('id') or '')
        if not app_id.isdigit():
            continue
        steam_installed[app_id] = item
        metadata = {'discovery': 'steam-appmanifest', 'installation_verified': bool(item.get('installed')),
                    'ownership': 'not-asserted'}
        row = _entry('Steam', app_id, str(item.get('name') or f'Steam app {app_id}'), 'PC',
                     'installed' if item.get('installed') else 'not-installed',
                     path=item.get('install_dir') if item.get('installed') else '', metadata=metadata)
        if row:
            rows[row['source_key']] = row
    steam_ownership_complete = False
    steam_notice = 'Installed games only: existing Steam appmanifest files. Stale localconfig IDs are not imported as games.'
    # Optional account-owned library; only use an explicit Waystone keyring
    # session. Never scrape Steam login cookies or browser state.
    try:
        if os.environ.get('WAYSTONE_DISABLE_STEAM_API') == '1':
            raise RuntimeError('Steam API disabled by explicit environment setting')
        if steam_session_store is None and not may_use_default_keyring:
            raise RuntimeError('No real-user keyring is used for fixture homes')
        if steam_session_store is None:
            from native_services.provider_sessions import ProviderSessionStore
            steam_session_store = ProviderSessionStore()
        session = steam_session_store.load('steam')
        if session and session.get('kind') == 'steam_web':
            from native_services.steam_web import SteamWebAdapter
            factory = steam_web_factory or SteamWebAdapter
            snapshot = factory(session['account_id'], session['api_key']).fetch_owned_games()
            owned_games = snapshot.get('games') if isinstance(snapshot, dict) else None
            if (isinstance(snapshot, dict) and snapshot.get('complete') is True
                    and isinstance(owned_games, list) and len(owned_games) <= MAX_SOURCE_RECORDS):
                from native_services.steam_web import app_id as validate_steam_app_id
                staged = []
                ids = set()
                for owned in owned_games:
                    if not isinstance(owned, dict): raise ValueError('Invalid Steam library row')
                    product = validate_steam_app_id(str(owned.get('product_id') or ''))
                    title = owned.get('title')
                    playtime = owned.get('playtime_seconds')
                    if (product in ids or not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE
                            or type(playtime) is not int or playtime < 0):
                        raise ValueError('Invalid Steam library row')
                    ids.add(product)
                    staged.append((product, title.strip(), playtime))
                steam_ownership_complete = True
                for app_id, title, playtime in staged:
                    installed = steam_installed.get(app_id)
                    row = _entry('Steam', app_id, title, 'PC',
                                 'installed' if installed and installed.get('installed') else 'registered',
                                 path=installed.get('install_dir') if installed and installed.get('installed') else '',
                                 metadata={'discovery': 'steam-webapi-owned-games',
                                           'ownership': 'steam_webapi',
                                           'playtime_seconds': playtime})
                    if row:
                        rows[row['source_key']] = row
                steam_notice = f"Steam account library checked: {len(snapshot['games'])} games visible; installed state still comes from local appmanifests."
            else:
                steam_notice = 'Steam account is saved, but its library was incomplete; previous ownership rows were kept.'
    except Exception:
        # Private profiles, keyring outages, and network errors are unavailable,
        # not an empty library and never a reason to revoke prior ownership.
        steam_notice = 'Steam account library unavailable (privacy, keyring, or network); previous ownership rows were kept.'
    states['Steam'] = {'installed_complete': steam_complete,
                       'ownership_complete': steam_ownership_complete,
                       'notice': steam_notice}

    # Heroic install metadata and allowlisted library caches. The reader never
    # opens auth.json/user.json/token files.
    from native_services.game_installs import (battlenet_installed_games, heroic_games,
                                               lutris_database_path, lutris_games, waystone_games)
    installed = {}
    for item in heroic_games(home):
        provider = {'epic': 'Epic', 'gog': 'GOG', 'amazon': 'Amazon',
                    'sideload': 'heroic-sideload'}.get(item.get('store'))
        product = str(item.get('id') or '')
        if provider and product:
            installed[(provider, product)] = item
    heroic_root = home / '.config/heroic'
    waystone_provider_root = data_dir / 'providers-v1'
    from native_services.provider_entitlements import ProviderEntitlementsReader
    reader = ProviderEntitlementsReader()
    for provider_name, source_provider in (('epic', 'Epic'), ('gog', 'GOG'), ('amazon', 'Amazon')):
        gathered = {}
        complete = False
        for root in (heroic_root, waystone_provider_root):
            if not root.is_dir():
                continue
            complete = complete or _source_cache_complete(root, provider_name)
            try:
                for record in reader.load_entitlements(provider_name, config_root=root):
                    key = str(record.product_id)
                    prior = gathered.get(key)
                    if prior is None or record.is_installed:
                        gathered[key] = record
            except (OSError, ValueError):
                continue
        for product, record in gathered.items():
            installed_row = installed.get((source_provider, product))
            is_installed = bool(installed_row and installed_row.get('installed'))
            from_installed_cache = 'installed' in Path(record.cache_provenance).name.casefold()
            metadata = {'discovery': ('heroic-installed-cache' if from_installed_cache
                                      else 'launcher-entitlement-cache'),
                        'ownership': ('not-asserted' if from_installed_cache
                                      else 'cached_entitlement'),
                        'cache_provenance': record.cache_provenance}
            if not from_installed_cache:
                metadata['ownership_confidence'] = record.ownership_confidence
            row = _entry(source_provider, product, record.title,
                         'PC' if source_provider != 'Amazon' else 'PC',
                         'installed' if is_installed else 'registered',
                         path=(installed_row or {}).get('install_dir') if is_installed else '',
                         metadata=metadata)
            if row:
                rows[row['source_key']] = row
        # Installed cache can still establish present games when ownership feed
        # is absent; mark only appmanifest disappearance, not ownership changes.
        for (installed_provider, product), item in installed.items():
            if installed_provider != source_provider or f'{source_provider.casefold()}:{product}' in rows:
                continue
            row = _entry(source_provider, product, str(item.get('name') or product), 'PC',
                         'installed' if item.get('installed') else 'not-installed',
                         path=item.get('install_dir') if item.get('installed') else '',
                         metadata={'discovery': 'heroic-installed-cache', 'ownership': 'not-asserted'})
            if row:
                rows[row['source_key']] = row
        states[source_provider] = {'installed_complete': any(
            _source_cache_complete(root, provider_name, installed=True)
            for root in (heroic_root, waystone_provider_root)),
            'ownership_complete': complete,
            'notice': 'Local Heroic/Waystone JSON caches only; cached ownership may be stale.'}

    # Heroic sideload rows are user-added app records, not store entitlements.
    # Preserve their exact app_name identity and only claim installed when
    # Heroic declares it and its recorded folder exists.
    sideload_count = 0
    for item in installed.values():
        if item.get('store') != 'sideload':
            continue
        product = str(item.get('id') or '')
        row = _entry('heroic-sideload', product, str(item.get('name') or product), 'PC',
                     'installed' if item.get('installed') else 'not-installed',
                     path=item.get('install_dir') if item.get('installed') else '',
                     metadata={'discovery': 'heroic-sideload-library',
                               'installation_verified': bool(item.get('installed')),
                               'game_executable': str(item.get('executable') or ''),
                               'wine_prefix': str(item.get('prefix') or '')})
        if row:
            rows[row['source_key']] = row
            sideload_count += 1
    states['heroic-sideload'] = {
        'installed_complete': False, 'ownership_complete': False, 'count': sideload_count,
        'notice': 'Heroic sideload library only; exact app_name retained, no ownership implied.'}

    # Lutris' documented local SQLite inventory is safe to query through its existing read-only reader.
    lutris_db = lutris_database_path(home)
    lutris_complete = bool(lutris_db and _lutris_inventory_complete(lutris_db))
    lutris_services = {'ea_app': 'EA app', 'uplay': 'Ubisoft Connect',
                       'ubisoft': 'Ubisoft Connect', 'battlenet': 'Battle.net'}
    service_counts = {provider: 0 for provider in lutris_services.values()}
    lutris_rows = lutris_games(home)
    battlenet_prefixes = []
    for item in lutris_rows:
        identity = ' '.join(str(item.get(key) or '') for key in ('name', 'slug', 'configpath')).casefold()
        if (item.get('runner') == 'wine' and 'battlenet' in identity.replace('.', '')
                and item.get('install_dir')):
            battlenet_prefixes.append(item['install_dir'])
        service = str(item.get('service') or '').casefold()
        if service in lutris_services:
            provider = lutris_services[service]
            lutris_id = str(item.get('lutris_game_id') or '')
            # Lutris' own primary key is the exact target for its rungameid URI.
            # Do not use a title, a product code, or a game-folder guess as the
            # launch identifier. The source service ID remains provenance only.
            if not re.fullmatch(r'[1-9][0-9]{0,11}', lutris_id) or not item.get('configpath'):
                continue
            service_id = str(item.get('service_id') or '')
            metadata = {'discovery': 'lutris-pga-service-row',
                        'installation_verified': bool(item.get('installed')),
                        'lutris_game_id': lutris_id,
                        'lutris_slug': str(item.get('slug') or ''),
                        'lutris_service': service,
                        'store_id': service_id,
                        'runner': str(item.get('runner') or ''),
                        'lutris_configured': True}
            row = _entry(provider, lutris_id, str(item.get('name') or lutris_id), 'PC',
                         'installed' if item.get('installed') else 'not-installed',
                         metadata=metadata)
            if row:
                rows[row['source_key']] = row
                service_counts[provider] += 1
            continue
        product = str(item.get('id') or '')
        row = _entry('Lutris', product, str(item.get('name') or product), 'PC',
                     'installed' if item.get('installed') else 'not-installed',
                     path=item.get('install_dir'),
                     metadata={'discovery': 'lutris-pga.db', 'installation_verified': bool(item.get('installed'))})
        if row:
            rows[row['source_key']] = row
    battlenet_count = 0
    for item in battlenet_installed_games(home, battlenet_prefixes):
        product = str(item.get('id') or '')
        row = _entry('Battle.net', product, str(item.get('name') or product), 'PC', 'installed',
                     path=item.get('install_dir'),
                     metadata={'discovery': 'lutris-registered-battlenet-product-db',
                               'installation_verified': True,
                               'product_code': str(item.get('product_code') or ''),
                               'wine_prefix': str(item.get('prefix') or '')})
        if row:
            rows[row['source_key']] = row
            battlenet_count += 1
    states['Lutris'] = {'installed_complete': lutris_complete, 'ownership_complete': False,
                        'notice': 'Installed rows from local Lutris pga.db.'}
    for provider, count in service_counts.items():
        states[provider] = {'installed_complete': False, 'ownership_complete': False,
                            'count': count,
                            'notice': 'Installed service rows explicitly imported by Lutris; no account or entitlement scan.'}
    states['Battle.net']['count'] = max(states['Battle.net'].get('count', 0), battlenet_count)
    states['Battle.net']['notice'] += ' Lutris-registered Battle.net prefixes may add exact locally installed product.db rows.'

    # Waystone's own installs are app-owned and local. No directory-wide disk scan.
    own_root = data_dir / 'games'
    for item in waystone_games(own_root):
        product = str(item.get('id') or '')
        row = _entry('Waystone', product, str(item.get('name') or product), 'PC', 'installed',
                     path=item.get('install_dir'), metadata={'discovery': 'waystone-managed-install'})
        if row:
            rows[row['source_key']] = row
    states['Waystone'] = {'installed_complete': own_root.is_dir(), 'ownership_complete': False,
                          'notice': 'Waystone-managed game directories only.'}

    # Configured ROM roots are explicit; no default whole-disk search. Paths are
    # exact source IDs and never merged by display title.
    from native_services.rom_inventory import RomInventoryStore
    rom_store = RomInventoryStore(rom_config_dir or config_dir)
    rom_records = rom_store.scan_inventory() if rom_store.roots else []
    for rom in rom_records:
        product = str(rom.rom_id)
        row = _entry('Emulation', product, Path(rom.file_name).stem or Path(rom.file_path).stem,
                     str(rom.platform or 'unknown'), 'installed' if rom.local_presence else 'not-installed',
                     path=rom.file_path if rom.local_presence else '',
                     metadata={'discovery': 'configured-rom-root', 'rom_id': product,
                               'emulator': rom.emulator_name or '', 'launchable': bool(rom.launchable)})
        if row:
            rows[row['source_key']] = row
    states['Emulation'] = {'installed_complete': False, 'ownership_complete': False,
                           'configured_roots': len(rom_store.roots),
                           'notice': 'Only explicitly configured ROM roots were scanned; removals are not inferred.'}

    # Explicit app-owned local games; do not infer or add placeholder titles.
    local_path = Path(local_games_path) if local_games_path is not None else data_dir / 'local_games.json'
    try:
        from native_services.local_games import load as load_local_games
        local_entries = load_local_games(local_path)
    except (OSError, ValueError):
        local_entries = []
    for entry in local_entries:
        product = str(entry['id'])
        row = _entry('Local', product, entry['title'], entry.get('platform') or 'PC', 'registered',
                     metadata={'discovery': 'waystone-local-entry', 'tags': entry.get('tags', []),
                               'notes': entry.get('notes', ''), 'based_on': entry.get('based_on', '')})
        if row:
            rows[row['source_key']] = row
    states['Local'] = {'installed_complete': False, 'ownership_complete': False,
                       'notice': 'Explicit Waystone local entries; never removed by sync.'}

    if len(rows) > MAX_SOURCE_RECORDS:
        raise ValueError('Launcher discovery exceeded the safe 30,000-record limit')
    from collections import Counter
    counts = Counter(row['provider'] for row in rows.values())
    for provider in states:
        states[provider]['count'] = counts.get(provider, 0)
    return sorted(rows.values(), key=lambda x: (x['provider'].casefold(), x['title'].casefold(), x['source_key'])), states


def sync_library(path, *, home=None, data_dir=None, config_dir=None, rom_config_dir=None,
                 local_games_path=None, observed_at=None, steam_session_store=None, steam_web_factory=None):
    """Run bounded local discovery and atomically update the app-owned DB."""
    path = Path(path)
    if not path.exists():
        LibraryRepository.create_empty(path)
    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir,
                                               rom_config_dir=rom_config_dir,
                                               local_games_path=local_games_path,
                                               steam_session_store=steam_session_store,
                                               steam_web_factory=steam_web_factory)
    timestamp = observed_at or datetime.now(timezone.utc).isoformat()
    repo = LibraryRepository(path, writable=True)
    try:
        repo.sync_launcher_records(records, states, timestamp)
    finally:
        repo.close()
    from collections import Counter
    counts = Counter(row['provider'] for row in records)
    return {'records': len(records), 'providers': len(states), 'source_counts': dict(counts),
            'source_states': states, 'observed_at': timestamp,
            'path': str(path)}


if __name__ == '__main__':  # small explicit local diagnostic; never prints account data
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--home', type=Path, default=Path.home())
    args = parser.parse_args()
    result = sync_library(args.database, home=args.home)
    print(json.dumps({k: v for k, v in result.items() if k != 'path'}, sort_keys=True))
