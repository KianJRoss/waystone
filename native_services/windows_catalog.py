"""Read-only Windows installed-game discovery for official PC clients.

Only local install evidence is imported; it never proves account ownership. The
readers are fixtureable and do not launch clients or inspect auth/session stores.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import xml.etree.ElementTree as ET

from native_services.game_installs import parse_vdf, _protobuf_fields, _read_bytes_bounded

MAX_MANIFEST = 4 * 1024 * 1024
MAX_RECORDS = 30_000


def _read_json(path: Path):
    payload = _read_bytes_bounded(path, max_bytes=MAX_MANIFEST)
    if payload is None:
        return None
    try:
        return json.loads(payload.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        return None


def _valid_dir(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        return None
    path = Path(value)
    try:
        return path if path.is_absolute() and path.is_dir() else None
    except (OSError, ValueError):
        return None


def _read_xml(path):
    """Parse bounded UTF-8 XML with DTD/entity declarations rejected before parsing."""
    payload = _read_bytes_bounded(path, max_bytes=MAX_MANIFEST)
    if payload is None:
        return None
    try:
        source = payload.decode('utf-8-sig')
    except UnicodeError:
        return None
    normalized = source.casefold()
    if '<!doctype' in normalized or '<!entity' in normalized:
        return None
    try:
        return ET.fromstring(source)
    except (ET.ParseError, ValueError):
        return None


def _record(provider, product_id, title, install_path, source, metadata=None):
    if not (isinstance(product_id, str) and product_id.strip() and len(product_id) <= 512
            and isinstance(title, str) and title.strip() and len(title) <= 512):
        return None
    path = _valid_dir(str(install_path))
    if path is None:
        return None
    return {'source_key': f'{provider.casefold()}:{product_id}', 'provider': provider,
            'product_id': product_id, 'title': title.strip(), 'platform': 'PC',
            'status': 'installed', 'install_path': str(path),
            'metadata': {'discovery': source, 'installation_verified': True,
                         'ownership': 'not-asserted', **(metadata or {})}}


def steam_rows(steam_roots):
    """Parse Steam library VDF and exact appmanifest IDs; path presence proves installed."""
    rows, seen = [], set()
    for root in steam_roots:
        root = Path(root)
        primary = root / 'steamapps'
        libraries = [primary]
        data = _read_vdf_bounded(primary / 'libraryfolders.vdf')
        folders = data.get('libraryfolders', {}) if isinstance(data, dict) else {}
        if isinstance(folders, dict):
            for key, value in folders.items():
                if not str(key).isdigit():
                    continue
                path = value.get('path') if isinstance(value, dict) else value
                if isinstance(path, str) and path:
                    candidate = Path(path) / 'steamapps'
                    if candidate not in libraries:
                        libraries.append(candidate)
        for steamapps in libraries[:128]:
            try:
                manifests = sorted(steamapps.glob('appmanifest_*.acf'))[:MAX_RECORDS]
            except OSError:
                continue
            for manifest in manifests:
                data = _read_vdf_bounded(manifest)
                state = data.get('AppState') if isinstance(data, dict) else None
                if not isinstance(state, dict):
                    continue
                appid = str(state.get('appid', '')).strip()
                title = str(state.get('name', '')).strip()
                folder = str(state.get('installdir', state.get('installDir', ''))).strip()
                app_type = str(state.get('type', '')).strip().casefold()
                if not re.fullmatch(r'[1-9][0-9]{0,11}', appid) or appid in seen:
                    continue
                if app_type and app_type != 'game':
                    continue
                # installDir is a single directory component, never a manifest path.
                if not folder or folder in {'.', '..'} or any(c in folder for c in ('/', '\\', ':')):
                    continue
                flags = str(state.get('StateFlags', ''))
                if not flags.isdigit() or not (int(flags) & 4):
                    continue
                install = steamapps / 'common' / folder
                row = _record('Steam', appid, title, install, 'steam-appmanifest',
                              {'library_root': str(steamapps.parent)})
                if row:
                    rows.append(row); seen.add(appid)
    return rows


def _read_vdf_bounded(path):
    payload = _read_bytes_bounded(path, max_bytes=MAX_MANIFEST)
    if payload is None:
        return None
    try:
        return parse_vdf(payload.decode('utf-8', errors='replace'))
    except (ValueError, RecursionError):
        return None


def epic_rows(manifest_roots):
    """Read Epic's official .item manifests, excluding incomplete/DLC/UE entries."""
    rows, seen = [], set()
    for root in manifest_roots:
        try:
            manifests = sorted(Path(root).glob('*.item'))[:MAX_RECORDS]
        except OSError:
            continue
        for manifest in manifests:
            data = _read_json(manifest)
            if not isinstance(data, dict):
                continue
            app = data.get('AppName')
            if (not isinstance(app, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}', app)
                    or app in seen or data.get('bIsIncompleteInstall') is True):
                continue
            parent = data.get('MainGameAppName')
            if isinstance(parent, str) and parent and parent.casefold() != app.casefold():
                continue
            if str(data.get('CatalogNamespace') or '').casefold() == 'ue':
                continue
            location = _valid_dir(data.get('InstallLocation'))
            title = data.get('DisplayName')
            row = _record('Epic', app, title, location, 'epic-item-manifest', {
                'catalog_namespace': str(data.get('CatalogNamespace') or '')[:200],
                'catalog_item_id': str(data.get('CatalogItemId') or '')[:200],
            }) if location else None
            if row:
                rows.append(row); seen.add(app)
    return rows


def gog_rows(uninstall_entries):
    rows, seen = [], set()
    for entry in uninstall_entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get('key') or '')
        match = re.match(r'^(\d+)_is1$', key, re.I)
        if (not match or key.upper().startswith('GOGPACK')
                or str(entry.get('publisher') or '').casefold() != 'gog.com'):
            continue
        game_id = match.group(1)
        if game_id in seen:
            continue
        path = _valid_dir(entry.get('install_location'))
        if not path:
            continue
        info = _read_json(path / f'goggame-{game_id}.info')
        if isinstance(info, dict):
            root_id = str(info.get('rootGameId') or '')
            if root_id and root_id != game_id:
                continue
        row = _record('GOG', game_id, entry.get('display_name'), path, 'gog-uninstall-manifest',
                      {'game_manifest_present': isinstance(info, dict)})
        if row:
            rows.append(row); seen.add(game_id)
    return rows


def ubisoft_rows(installs):
    rows, seen = [], set()
    for entry in installs:
        if not isinstance(entry, dict):
            continue
        product_id = str(entry.get('key') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', product_id) or product_id.casefold() in seen:
            continue
        path = _valid_dir(entry.get('install_dir'))
        if not path:
            continue
        # The official registry reliably identifies the install ID/path; the folder
        # basename is the only local title source when the configuration cache is absent.
        row = _record('Ubisoft Connect', product_id, str(entry.get('display_name') or path.name), path,
                      'ubisoft-installs-registry')
        if row:
            rows.append(row); seen.add(product_id.casefold())
    return rows


def ea_rows(installs):
    """Conservative offline EA install rows: require EA registry identity and installerdata.xml."""
    rows, seen = [], set()
    for entry in installs:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get('key') or '')
        publisher = str(entry.get('publisher') or '').casefold()
        # EA Games registry roots do not carry Publisher; their location is an
        # explicit provider boundary. Uninstall entries still need publisher.
        provider_root = entry.get('provider_root') == 'ea-games'
        if (not key or not (provider_root or publisher.startswith('electronic arts') or publisher == 'ea')
                or key.casefold() in seen):
            continue
        path = _valid_dir(entry.get('install_location'))
        if not path:
            continue
        manifest = path / '__Installer' / 'installerdata.xml'
        content = _read_xml(manifest)
        if content is None:
            continue
        content_ids = sorted({el.text.strip() for el in content.iter()
                              if el.tag.rsplit('}', 1)[-1].casefold() == 'contentid'
                              and el.text and len(el.text.strip()) <= 200})
        row = _record('EA app', key, entry.get('display_name') or path.name, path, 'ea-installerdata-registry',
                      {'content_ids': content_ids})
        if row:
            rows.append(row); seen.add(key.casefold())
    return rows


_BNET_TITLES = {
    'wow': ('wow', 'World of Warcraft'), 'd3': ('d3', 'Diablo III'),
    'd4': ('d4', 'Diablo IV'), 'fenris': ('fenris', 'Diablo IV'), 's2': ('s2', 'StarCraft II'),
    'hero': ('hero', 'Heroes of the Storm'), 'pro': ('pro', 'Overwatch 2'),
    'vipr': ('vipr', 'Call of Duty'), 'w3': ('w3', 'Warcraft III'),
}


def battlenet_rows(product_db):
    """Decode only known installed product.db rows; omit launcher/agent/unknown products."""
    path = Path(product_db)
    payload = _read_bytes_bounded(path, max_bytes=MAX_MANIFEST)
    if payload is None:
        return []
    try:
        installs = [value for number, wire, value in _protobuf_fields(payload) if number == 1 and wire == 2]
    except ValueError:
        return []
    rows, seen = [], set()
    for message in installs[:1024]:
        try:
            fields = list(_protobuf_fields(message))
            code = next((value.decode('utf-8') for number, wire, value in fields
                         if number == 2 and wire == 2), '').casefold()
            mapped = _BNET_TITLES.get(code)
            settings = next((value for number, wire, value in fields if number == 3 and wire == 2), b'')
            state = next((value for number, wire, value in fields if number == 4 and wire == 2), b'')
            settings_fields = list(_protobuf_fields(settings)) if settings else []
            state_fields = list(_protobuf_fields(state)) if state else []
            path_value = next((v.decode('utf-8') for n, w, v in settings_fields if n == 1 and w == 2), '')
            base_state = next((v for n, w, v in state_fields if n == 1 and w == 2), b'')
            installed = any(v == 1 for n, w, v in _protobuf_fields(base_state) if n == 1 and w == 0)
        except (ValueError, UnicodeError):
            continue
        if not mapped or not installed or code in seen:
            continue
        path = _valid_dir(path_value)
        row = _record('Battle.net', mapped[0], mapped[1], path, 'battlenet-product-db',
                      {'product_code': code}) if path else None
        if row:
            rows.append(row); seen.add(code)
    return rows


def xbox_package_rows(packages):
    """Import only MSIX packages containing MicrosoftGame.config, not every UWP app."""
    rows, seen = [], set()
    for package in packages:
        if not isinstance(package, dict):
            continue
        root = _valid_dir(package.get('install_location'))
        pfn = str(package.get('package_family_name') or '')
        if not root or not re.fullmatch(r'[A-Za-z0-9.-]{1,150}_[A-Za-z0-9]{8,20}', pfn) or pfn.casefold() in seen:
            continue
        marker = root / 'MicrosoftGame.config'
        manifest = root / 'AppxManifest.xml'
        try:
            if not stat.S_ISREG(marker.stat(follow_symlinks=False).st_mode):
                continue
        except OSError:
            continue
        xml = _read_xml(manifest)
        if xml is None:
            continue
        app = next((node for node in xml.iter() if node.tag.rsplit('}', 1)[-1] == 'Application'), None)
        app_id = str(app.get('Id') or '') if app is not None else ''
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', app_id):
            continue
        props = next((node for node in xml.iter() if node.tag.rsplit('}', 1)[-1] == 'Properties'), None)
        display = next((node for node in list(props) if node.tag.rsplit('}', 1)[-1] == 'DisplayName'), None) if props is not None else None
        title = (display.text or '').strip() if display is not None else ''
        if title.startswith('ms-resource:') or not title:
            title = str(package.get('name') or '').strip()
        product = pfn + '!' + app_id
        row = _record('Xbox', product, title, root, 'xbox-msix-game-manifest',
                      {'package_family_name': pfn, 'app_id': app_id, 'ownership': 'not-asserted'})
        if row:
            rows.append(row); seen.add(pfn.casefold())
    return rows


def _registry_snapshot():
    """Read only narrowly allowlisted registry values used by the local scanners."""
    import winreg
    roots = {
        'hklm': winreg.HKEY_LOCAL_MACHINE,
        'hkcu': winreg.HKEY_CURRENT_USER,
    }
    def subkeys(root, path, view):
        base = roots[root]
        try:
            with winreg.OpenKey(base, path, 0, winreg.KEY_READ | view) as key:
                names = []
                for index in range(MAX_RECORDS):
                    try: names.append(winreg.EnumKey(key, index))
                    except OSError: break
                rows = []
                for name in names:
                    try:
                        with winreg.OpenKey(key, name, 0, winreg.KEY_READ | view) as child:
                            values = {}
                            for value_name in ('DisplayName', 'Publisher', 'InstallLocation',
                                               'InstallDir', 'InstallPath', 'SteamPath'):
                                try:
                                    value, _ = winreg.QueryValueEx(child, value_name)
                                    if isinstance(value, str): values[value_name.casefold()] = value[:4096]
                                except OSError:
                                    pass
                            rows.append({'key': name, **values})
                    except OSError:
                        continue
                return rows
        except OSError:
            return []
    def values(root, path, view):
        try:
            with winreg.OpenKey(roots[root], path, 0, winreg.KEY_READ | view) as key:
                result = {}
                for name in ('SteamPath', 'InstallPath'):
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                        if isinstance(value, str): result[name.casefold()] = value[:4096]
                    except OSError:
                        pass
                return result
        except OSError:
            return {}
    views = (getattr(winreg, 'KEY_WOW64_64KEY', 0), getattr(winreg, 'KEY_WOW64_32KEY', 0))
    data = {'uninstall': [], 'steam_roots': [], 'ubisoft': [], 'ea': []}
    for view in views:
        data['uninstall'] += subkeys('hklm', r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', view)
        data['uninstall'] += subkeys('hkcu', r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', view)
        steam = values('hkcu', r'Software\Valve\Steam', view).get('steampath')
        if steam: data['steam_roots'].append(steam)
        data['ubisoft'] += subkeys('hklm', r'SOFTWARE\Ubisoft\Launcher\Installs', view)
        data['ea'] += [{**row, 'provider_root': 'ea-games', 'publisher': 'Electronic Arts',
                        'install_location': row.get('installdir') or row.get('installpath')}
                       for row in subkeys('hklm', r'SOFTWARE\EA Games', view)]
        data['ea'] += [{**row, 'provider_root': 'ea-games', 'publisher': 'Electronic Arts',
                        'install_location': row.get('installdir') or row.get('installpath')}
                       for row in subkeys('hklm', r'SOFTWARE\WOW6432Node\EA Games', view)]
    return data


def _appx_packages():
    """Query package identity/location only; static read-only PowerShell, bounded output/time."""
    script = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
              "$ProgressPreference='SilentlyContinue'; Get-AppxPackage | "
              "Select-Object Name,PackageFamilyName,InstallLocation | ConvertTo-Json -Compress")
    completed = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                '-Command', script], capture_output=True, timeout=20, check=True)
    if len(completed.stdout) > MAX_MANIFEST:
        return []
    if not completed.stdout.strip():
        return []
    value = json.loads(completed.stdout.decode('utf-8-sig'))
    values = value if isinstance(value, list) else [value]
    return [{'name': item.get('Name'), 'package_family_name': item.get('PackageFamilyName'),
             'install_location': item.get('InstallLocation')}
            for item in values if isinstance(item, dict)][:MAX_RECORDS]


def collect_windows_game_installs(*, environ=None, registry=None, appx_packages=None):
    """Discover installed games without API auth, client launches, or disk-wide scans."""
    env = os.environ if environ is None else environ
    reg = registry if registry is not None else _registry_snapshot()
    program_data = Path(env.get('PROGRAMDATA', r'C:\ProgramData'))
    roots = []
    for row in reg.get('steam_roots', ()):
        if row:
            roots.append(Path(str(row)))
    for key in ('PROGRAMFILES(X86)', 'PROGRAMFILES'):
        if env.get(key): roots.append(Path(env[key]) / 'Steam')
    epic_root = program_data / 'Epic' / 'EpicGamesLauncher' / 'Data' / 'Manifests'
    rows = steam_rows(roots) + epic_rows([epic_root])
    rows += gog_rows(reg.get('uninstall', ()))
    rows += ubisoft_rows(reg.get('ubisoft', ()))
    # EA's games are only included when its exact installer metadata exists;
    # registered names alone do not prove a playable payload or ownership.
    rows += ea_rows(list(reg.get('ea', ())) + [r for r in reg.get('uninstall', ())
                                              if 'electronic arts' in str(r.get('publisher', '')).casefold()])
    product_db = program_data / 'Battle.net' / 'Agent' / 'product.db'
    rows += battlenet_rows(product_db)
    try:
        packages = appx_packages if appx_packages is not None else _appx_packages()
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
        packages = []
    rows += xbox_package_rows(packages)
    # Exact stable source IDs are unique per provider; first valid evidence wins.
    unique = {}
    for row in rows[:MAX_RECORDS]:
        unique.setdefault(row['source_key'], row)
    counts = {}
    for row in unique.values():
        counts[row['provider']] = counts.get(row['provider'], 0) + 1
    states = {name: {'installed_complete': False, 'ownership_complete': False, 'count': counts.get(name, 0),
                     'notice': 'No credentials or account entitlements read; local install evidence only.'}
              for name in ('Steam', 'Epic', 'GOG', 'EA app', 'Ubisoft Connect', 'Battle.net', 'Xbox')}
    return sorted(unique.values(), key=lambda row: (row['provider'].casefold(), row['title'].casefold(), row['source_key'])), states


def collect_windows_launcher_library(*, home, data_dir, config_dir, rom_config_dir=None, local_games_path=None,
                                     environ=None, registry=None, appx_packages=None):
    """Windows branch of launcher_library.collect_launcher_library, including common app-owned rows."""
    records, states = collect_windows_game_installs(environ=environ, registry=registry,
                                                    appx_packages=appx_packages)
    data_dir, config_dir = Path(data_dir), Path(config_dir)
    own_root = data_dir / 'games'
    from native_services.game_installs import waystone_games
    for item in waystone_games(own_root):
        product = str(item.get('id') or '')
        row = _record('Waystone', product, str(item.get('name') or product), item.get('install_dir'),
                      'waystone-managed-install')
        if row:
            records.append(row)
    states['Waystone'] = {'installed_complete': own_root.is_dir(), 'ownership_complete': False,
                          'notice': 'Waystone-managed game directories only.'}
    from native_services.rom_inventory import RomInventoryStore
    roms = RomInventoryStore(rom_config_dir or config_dir).scan_inventory()
    for rom in roms:
        path = Path(rom.file_path)
        row = {'source_key': f'emulation:{rom.rom_id}', 'provider': 'Emulation',
               'product_id': str(rom.rom_id), 'title': path.stem or path.name,
               'platform': str(rom.platform or 'unknown'),
               'status': 'installed' if rom.local_presence else 'not-installed',
               'install_path': str(path) if rom.local_presence else '',
               'metadata': {'discovery': 'configured-rom-root', 'rom_id': str(rom.rom_id),
                            'emulator': rom.emulator_name or '', 'launchable': bool(rom.launchable)}}
        records.append(row)
    states['Emulation'] = {'installed_complete': False, 'ownership_complete': False,
                           'notice': 'Only explicitly configured ROM roots were scanned; removals are not inferred.'}
    local_path = Path(local_games_path) if local_games_path is not None else data_dir / 'local_games.json'
    try:
        from native_services.local_games import load as load_local_games
        for entry in load_local_games(local_path):
            product = str(entry['id'])
            records.append({'source_key': f'local:{product}', 'provider': 'Local', 'product_id': product,
                            'title': entry['title'], 'platform': entry.get('platform') or 'PC',
                            'status': 'registered', 'install_path': '',
                            'metadata': {'discovery': 'waystone-local-entry', 'tags': entry.get('tags', []),
                                         'notes': entry.get('notes', ''), 'based_on': entry.get('based_on', '')}})
    except (OSError, ValueError, KeyError):
        pass
    states['Local'] = {'installed_complete': False, 'ownership_complete': False,
                       'notice': 'Explicit Waystone local entries; never removed by sync.'}
    from collections import Counter
    counts = Counter(row['provider'] for row in records)
    for provider in states:
        states[provider]['count'] = counts.get(provider, 0)
    return sorted(records, key=lambda row: (row['provider'].casefold(), row['title'].casefold(), row['source_key'])), states
