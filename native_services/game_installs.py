"""Read-only discovery of locally installed games from Linux launcher metadata.

Pure standard library: no Qt, no subprocesses, no writes of any kind.  Every
reader only inspects files/directories that already exist and silently skips
missing or malformed metadata.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

__all__ = [
    'parse_vdf',
    'steam_libraries',
    'steam_games',
    'heroic_games',
    'lutris_games',
    'lutris_database_path',
    'folder_games',
    'find_installs',
    'normalize_title',
    'match_install',
]


# ---------------------------------------------------------------------------
# Minimal Valve KeyValues (VDF/ACF) reader
# ---------------------------------------------------------------------------

def _tokenize_vdf(text):
    """Turn VDF text into quoted strings, bare words and brace tokens."""
    tokens, i, n = [], 0, len(text)
    while i < n:
        char = text[i]
        if char.isspace() or char == '\ufeff':
            i += 1
            continue
        if char == '/' and i + 1 < n and text[i + 1] == '/':
            end = text.find('\n', i + 2)
            i = n if end < 0 else end + 1
            continue
        if char in '{}':
            tokens.append(char)
            i += 1
            continue
        if char == '"':
            i += 1
            buffer = []
            while i < n:
                current = text[i]
                if current == '\\' and i + 1 < n and text[i + 1] in ('"', '\\'):
                    buffer.append(text[i + 1])
                    i += 2
                elif current == '"':
                    i += 1
                    break
                else:
                    buffer.append(current)
                    i += 1
            tokens.append(''.join(buffer))
            continue
        start = i
        while i < n and not text[i].isspace() and text[i] not in '{}"':
            i += 1
        if i == start:  # defensive: never loop forever on an odd character
            i += 1
            continue
        tokens.append(text[start:i])
    return tokens


def _parse_vdf_block(tokens, pos):
    result = {}
    while pos < len(tokens) and tokens[pos] != '}':
        key = tokens[pos]
        pos += 1
        if pos >= len(tokens):
            result[key] = ''
            break
        value = tokens[pos]
        pos += 1
        if value == '{':
            child, pos = _parse_vdf_block(tokens, pos)
            result[key] = child  # duplicate keys: last wins
        else:
            result[key] = value
    if pos < len(tokens) and tokens[pos] == '}':
        pos += 1
    return result, pos


def parse_vdf(text: str) -> dict:
    """Parse Valve KeyValues text into nested dicts of str.

    Handles quoted keys/values, nested ``{ }`` blocks, ``//`` comments and
    escaped ``\\"`` / ``\\\\`` inside quotes.  Duplicate keys keep the last
    value encountered.
    """
    tokens = _tokenize_vdf(text or '')
    result, _ = _parse_vdf_block(tokens, 0)
    return result


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _read_json(path):
    try:
        with path.open('r', encoding='utf-8-sig') as handle:
            return json.load(handle)
    except (OSError, ValueError, UnicodeError):
        return None


def _read_bytes_bounded(path, *, max_bytes=10 * 1024 * 1024):
    """Read one bounded regular file without following a final symlink."""
    import stat
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            return None
        with os.fdopen(descriptor, 'rb') as handle:
            descriptor = None
            payload = handle.read(max_bytes + 1)
        if len(payload) > max_bytes:
            return None
        return payload
    except OSError:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_json_bounded(path, *, max_bytes=10 * 1024 * 1024):
    payload = _read_bytes_bounded(path, max_bytes=max_bytes)
    if payload is None:
        return None
    try:
        return json.loads(payload.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        return None


def _dedup_key(path):
    if not path:
        return None
    try:
        return str(Path(path).resolve())
    except (OSError, ValueError):
        return str(path)


def _wine_prefix(heroic_root, app_name):
    data = _read_json(heroic_root / 'GamesConfig' / (str(app_name) + '.json'))
    if isinstance(data, dict):
        row = data.get(str(app_name))
        if isinstance(row, dict) and isinstance(row.get('winePrefix'), str):
            return row['winePrefix']
    return ''


def _install_record(store, app_id, name, install_dir, prefix=''):
    install_dir = str(install_dir) if install_dir else ''
    return {
        'store': store,
        'id': app_id,
        'name': name,
        'install_dir': install_dir,
        'prefix': prefix,
        'installed': bool(install_dir) and Path(install_dir).is_dir(),
    }


# ---------------------------------------------------------------------------
# Steam
# ---------------------------------------------------------------------------

_STEAM_VDF_LOCATIONS = (
    ('.local', 'share', 'Steam', 'steamapps', 'libraryfolders.vdf'),
    ('.steam', 'steam', 'steamapps', 'libraryfolders.vdf'),
    ('.var', 'app', 'com.valvesoftware.Steam', '.local', 'share', 'Steam',
     'steamapps', 'libraryfolders.vdf'),
)


def steam_libraries(home) -> list[Path]:
    """Return existing Steam library folders from the first available VDF."""
    home = Path(home)
    source = None
    for parts in _STEAM_VDF_LOCATIONS:
        candidate = home.joinpath(*parts)
        if candidate.is_file():
            source = candidate
            break
    if source is None:
        return []
    try:
        data = parse_vdf(source.read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        return []
    folders = data.get('libraryfolders')
    if not isinstance(folders, dict):
        return []
    result, seen = [], set()
    for entry in folders.values():  # dict keeps file order
        if not isinstance(entry, dict):
            continue
        raw = entry.get('path')
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw)
        if not path.exists():
            continue
        key = _dedup_key(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def unregistered_steam_libraries(mount_roots=("/mnt", "/media", "/run/media")) -> list[Path]:
    """Steam libraries on other drives that this Steam does not list (e.g. a Windows-side SteamLibrary):
    <root>/<drive>/SteamLibrary and <root>/<user>/<drive>/SteamLibrary with a steamapps folder."""
    found = []
    for root in mount_roots:
        for pattern in ("*/SteamLibrary", "*/*/SteamLibrary", "*/Steam", "*/*/Steam"):
            try:
                for lib in Path(root).glob(pattern):
                    if (lib / "steamapps").is_dir():
                        found.append(lib)
            except OSError:
                continue
    return found


def steam_games(home, extra_libraries=()) -> list[dict]:
    """Read installed game manifests from every Steam library."""
    games = []
    known = {_dedup_key(p) for p in steam_libraries(home)}
    libraries = steam_libraries(home) + [p for p in extra_libraries if _dedup_key(p) not in known]
    for library in libraries:
        steamapps = library / 'steamapps'
        try:
            manifests = sorted(steamapps.glob('appmanifest_*.acf'))
        except OSError:
            continue
        for manifest in manifests:
            try:
                state = parse_vdf(manifest.read_text(encoding='utf-8',
                                                     errors='replace')).get('AppState')
                app_id = state['appid']
                name = state['name']
                installdir = state['installdir']
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if not isinstance(installdir, str) or not installdir:
                continue
            install_dir = steamapps / 'common' / installdir
            pfx = steamapps / 'compatdata' / str(app_id) / 'pfx'
            games.append({
                'store': 'steam',
                'id': app_id,
                'name': name,
                'install_dir': str(install_dir),
                'prefix': str(pfx) if pfx.is_dir() else '',
                'installed': install_dir.exists(),
            })
    return games


# ---------------------------------------------------------------------------
# Heroic (Legendary / Epic, GOG, Nile / Amazon)
# ---------------------------------------------------------------------------

def _heroic_title_map(path, key_name):
    titles = {}
    data = _read_json(path)
    if isinstance(data, dict) and isinstance(data.get(key_name), list):
        for row in data[key_name]:
            if isinstance(row, dict) and row.get('app_name') is not None:
                titles[str(row['app_name'])] = row.get('title')
    return titles


def heroic_games(home) -> list[dict]:
    """Read Heroic's per-store installed metadata (never accounts/auth)."""
    base = Path(home)
    roots = (base / '.config' / 'heroic',
             base / '.var/app/com.heroicgameslauncher.hgl/config/heroic')
    games = []

    # Heroic's manually-added games live in its own sideload library, separate
    # from Legendary/GOG/Nile install manifests. Read only this exact file in
    # the native and Flatpak config roots; do not walk arbitrary directories.
    for heroic in roots:
        sideload = _read_json_bounded(heroic / 'sideload_apps' / 'library.json')
        if not isinstance(sideload, dict) or not isinstance(sideload.get('games'), list):
            continue
        for row in sideload['games'][:30000]:
            if (not isinstance(row, dict) or row.get('runner') != 'sideload'
                    or not isinstance(row.get('app_name'), str)
                    or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', row['app_name'])):
                continue
            install = row.get('install') if isinstance(row.get('install'), dict) else {}
            folder = row.get('folder_name')
            # `is_installed` is Heroic's declaration; require the exact folder
            # to exist too, so stale entries are not presented as installed.
            folder_path = Path(folder) if isinstance(folder, str) and folder else Path('')
            folder_exists = folder_path.is_absolute() and folder_path.is_dir()
            executable = install.get('executable')
            executable_path = ''
            if isinstance(executable, str) and executable:
                candidate = Path(executable)
                if not candidate.is_absolute() and folder_exists:
                    candidate = folder_path / candidate
                if candidate.is_absolute() and candidate.is_file():
                    executable_path = str(candidate)
            games.append({
                'store': 'sideload', 'id': row['app_name'],
                'name': str(row.get('title') or row['app_name']),
                'install_dir': str(folder_path) if folder_exists else '',
                'prefix': _wine_prefix(heroic, row['app_name']),
                'executable': executable_path,
                'installed': row.get('is_installed') is True and folder_exists,
                'runner': 'sideload',
            })

    # Epic via Legendary's installed.json: appName -> {title, install_path, ...}
    for heroic in roots:
        epic = _read_json(heroic / 'legendaryConfig' / 'legendary' / 'installed.json')
        if isinstance(epic, dict):
            for app_name, row in epic.items():
                if not isinstance(row, dict):
                    continue
                app_id = str(app_name)
                name = str(row.get('title') or app_id)
                games.append(_install_record('epic', app_id, name,
                                             row.get('install_path', ''),
                                             _wine_prefix(heroic, app_id)))

    # GOG: installed list plus optional library.json title lookup
    for heroic in roots:
        gog = _read_json(heroic / 'gog_store' / 'installed.json')
        if isinstance(gog, dict) and isinstance(gog.get('installed'), list):
            titles = _heroic_title_map(heroic / 'gog_store' / 'library.json', 'games')
            for row in gog['installed']:
                if not isinstance(row, dict) or row.get('appName') is None:
                    continue
                app_id = str(row['appName'])
                name = str(titles.get(app_id) or app_id)
                games.append(_install_record('gog', app_id, name,
                                             row.get('install_path', ''),
                                             _wine_prefix(heroic, app_id)))

    # Amazon via Nile: list of {id, path}, optional store_cache title lookup
    for heroic in roots:
        nile = _read_json(heroic / 'nile_config' / 'nile' / 'installed.json')
        if isinstance(nile, list):
            titles = _heroic_title_map(heroic / 'store_cache' / 'nile_library.json',
                                       'library')
            for row in nile:
                if not isinstance(row, dict) or row.get('id') is None:
                    continue
                app_id = str(row['id'])
                name = str(titles.get(app_id) or app_id)
                games.append(_install_record('amazon', app_id, name,
                                             row.get('path', ''),
                                             _wine_prefix(heroic, app_id)))

    return games


# ---------------------------------------------------------------------------
# Lutris
# ---------------------------------------------------------------------------

def lutris_database_path(home) -> Path | None:
    """Return the first existing known native/Flatpak Lutris inventory path."""
    home = Path(home)
    for path in (home / '.local/share/lutris/pga.db',
                 home / '.var/app/net.lutris.Lutris/data/lutris/pga.db'):
        if path.is_file():
            return path
    return None

def lutris_games(home) -> list[dict]:
    """Query Lutris' pga.db through a read-only SQLite URI.

    Keep the base Lutris inventory contract stable, but expose the optional
    service fields Lutris itself writes for EA App / Ubisoft Connect /
    Battle.net imports.  These fields are absent in older Lutris databases.
    """
    database = lutris_database_path(home)
    if database is None:
        return []
    try:
        uri = database.resolve().as_uri() + '?mode=ro'
        connection = sqlite3.connect(uri, uri=True)
        try:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(games)')}
            selected = ['name', 'slug', 'directory', 'runner']
            optional = ('id', 'service', 'service_id', 'configpath', 'installer_slug')
            selected.extend(name for name in optional if name in columns)
            rows = connection.execute(
                'SELECT ' + ', '.join('"' + name + '"' for name in selected)
                + ' FROM games WHERE installed=1'
            ).fetchall()
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError):
        return []
    games = []
    for values in rows:
        record = dict(zip(selected, values))
        name, slug, directory, runner = (record.get(k) for k in ('name', 'slug', 'directory', 'runner'))
        directory = directory or ''
        game = {
            'store': 'lutris',
            'id': slug,
            'name': name,
            'install_dir': str(directory),
            'prefix': '',
            'installed': bool(directory) and Path(directory).is_dir(),
            'runner': runner,
        }
        for key in optional:
            if key in record:
                game['lutris_game_id' if key == 'id' else key] = record[key]
        games.append(game)
    return games


def _protobuf_fields(data):
    """Yield (field number, wire type, value) for a bounded protobuf message."""
    offset = 0
    while offset < len(data):
        tag = 0
        shift = 0
        while True:
            if offset >= len(data) or shift > 63:
                raise ValueError('Malformed protobuf varint')
            byte = data[offset]
            offset += 1
            tag |= (byte & 0x7f) << shift
            if byte < 0x80:
                break
            shift += 7
        field, wire = tag >> 3, tag & 7
        if field <= 0:
            raise ValueError('Invalid protobuf field')
        if wire == 0:
            value = 0
            shift = 0
            while True:
                if offset >= len(data) or shift > 63:
                    raise ValueError('Malformed protobuf varint')
                byte = data[offset]
                offset += 1
                value |= (byte & 0x7f) << shift
                if byte < 0x80:
                    break
                shift += 7
        elif wire == 1:
            if offset + 8 > len(data):
                raise ValueError('Truncated protobuf fixed64')
            value, offset = data[offset:offset + 8], offset + 8
        elif wire == 2:
            length = 0
            shift = 0
            while True:
                if offset >= len(data) or shift > 63:
                    raise ValueError('Malformed protobuf length')
                byte = data[offset]
                offset += 1
                length |= (byte & 0x7f) << shift
                if byte < 0x80:
                    break
                shift += 7
            if length > 10 * 1024 * 1024 or offset + length > len(data):
                raise ValueError('Invalid protobuf length')
            value, offset = data[offset:offset + length], offset + length
        elif wire == 5:
            if offset + 4 > len(data):
                raise ValueError('Truncated protobuf fixed32')
            value, offset = data[offset:offset + 4], offset + 4
        else:
            raise ValueError('Unsupported protobuf wire type')
        yield field, wire, value


def battlenet_installed_games(home, prefixes=None):
    """Read installed product rows from Lutris-registered Battle.net Wine prefixes.

    Only the Battle.net product.db schema fields used for product code, local
    install directory, and installed state are decoded. No credentials, region,
    account, or network data is read. Unknown products and unsafe paths are
    omitted rather than guessed.
    """
    # Lutris product.db product codes and display labels, from its public
    # GAME_IDS map. Keep this allowlist intentionally small; add reviewed IDs.
    titles = {'pro': ('Pro', 'Overwatch 2'), 'prometheus': ('Pro', 'Overwatch 2'),
              'w3': ('W3', 'Warcraft III')}
    result = []
    for prefix in prefixes or ():
        prefix = Path(prefix)
        if not prefix.is_absolute() or not prefix.is_dir():
            continue
        database = prefix / 'drive_c/ProgramData/Battle.net/Agent/product.db'
        try:
            payload = _read_bytes_bounded(database)
            if payload is None:
                continue
            installs = [value for number, wire, value in _protobuf_fields(payload)
                        if number == 1 and wire == 2]
            for message in installs[:1024]:
                fields = list(_protobuf_fields(message))
                code = next((value.decode('utf-8') for number, wire, value in fields
                             if number == 2 and wire == 2), '')
                mapped = titles.get(code.casefold())
                settings = next((value for number, wire, value in fields
                                 if number == 3 and wire == 2), b'')
                state = next((value for number, wire, value in fields
                              if number == 4 and wire == 2), b'')
                settings_fields = list(_protobuf_fields(settings)) if settings else []
                state_fields = list(_protobuf_fields(state)) if state else []
                windows_path = next((value.decode('utf-8') for number, wire, value in settings_fields
                                     if number == 1 and wire == 2), '')
                base_state = next((value for number, wire, value in state_fields
                                   if number == 1 and wire == 2), b'')
                base_fields = list(_protobuf_fields(base_state)) if base_state else []
                is_installed = next((value == 1 for number, wire, value in base_fields
                                     if number == 1 and wire == 0), False)
                if not mapped or not is_installed:
                    continue
                # Current product.db stores Windows absolute paths. Constrain
                # them to C: within this exact Lutris-registered prefix.
                match = re.fullmatch(r'(?i)c:[\\/](.+)', windows_path)
                if not match:
                    continue
                pieces = re.split(r'[\\/]+', match.group(1))
                if not pieces or any(part in {'', '.', '..'} for part in pieces):
                    continue
                install_dir = prefix / 'drive_c' / Path(*pieces)
                if not install_dir.is_dir():
                    continue
                product_id, title = mapped
                result.append({'store': 'battlenet', 'id': product_id, 'name': title,
                               'install_dir': str(install_dir), 'prefix': str(prefix),
                               'installed': True, 'product_code': code})
        except (OSError, ValueError, UnicodeError):
            continue
    return result


# ---------------------------------------------------------------------------
# Plain folders
# ---------------------------------------------------------------------------

def _has_game_payload(directory):
    try:
        for path in directory.rglob('*'):
            try:
                parts = path.relative_to(directory).parts
            except ValueError:
                continue
            if len(parts) > 2:
                continue
            if path.is_file() and path.suffix.lower() == '.exe':
                return True
            if (len(parts) >= 2 and parts[0].lower() == 'data'
                    and path.suffix.lower() == '.esm'):
                return True
    except OSError:
        return False
    return False


def folder_games(roots: list[str | Path]) -> list[dict]:
    """Detect portable games: an exe within depth 2 or a Data/*.esm folder."""
    games = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for directory in children:
            if not directory.is_dir() or not _has_game_payload(directory):
                continue
            games.append({
                'store': 'folder',
                'id': str(directory),
                'name': directory.name,
                'install_dir': str(directory),
                'prefix': '',
                'installed': True,
            })
    return games


# ---------------------------------------------------------------------------
# Aggregation and title matching
# ---------------------------------------------------------------------------

def waystone_games(install_root) -> list[dict]:
    """Waystone's own installs live at <install_root>/<install id>/<game folder>."""
    games = []
    root = Path(install_root) if install_root else None
    if not root or not root.is_dir():
        return games
    for job in sorted(p for p in root.iterdir() if p.is_dir()):
        for game in sorted(p for p in job.iterdir() if p.is_dir()):
            if _has_game_payload(game):
                games.append({"store": "waystone", "id": f"{job.name}/{game.name}", "name": game.name,
                              "install_dir": str(game), "prefix": "", "installed": True})
    return games


def find_installs(home, *, extra_roots=(), waystone_root=None, scan_mounts=False) -> list[dict]:
    """Combine every source, de-duplicating by resolved install directory."""
    installs = []
    installs.extend(steam_games(home, unregistered_steam_libraries() if scan_mounts else ()))
    installs.extend(waystone_games(waystone_root))
    installs.extend(heroic_games(home))
    installs.extend(lutris_games(home))
    installs.extend(folder_games(list(extra_roots)))
    seen, unique = set(), []
    for game in installs:
        key = _dedup_key(game.get('install_dir', ''))
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        unique.append(game)
    return unique


_EDITION_PHRASES = (
    'game of the year', 'goty', 'definitive', 'remastered', 'anniversary',
    'special', 'enhanced', 'complete', 'deluxe', 'edition',
)


def normalize_title(s: str) -> str:
    """Canonical, conservative title form used for exact install matching."""
    text = (s or '').lower()
    for symbol in ('™', '®', '©'):
        text = text.replace(symbol, '')
    text = text.replace('&', ' and ')
    text = re.sub(r'\([^)]*\)', ' ', text)
    text = re.sub(r'\[[^\]]*\]', ' ', text)
    for phrase in _EDITION_PHRASES:
        pattern = r'\b' + r'\s+'.join(re.escape(word) for word in phrase.split()) + r'\b'
        text = re.sub(pattern, ' ', text)
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def match_install(title: str, installs: list[dict]) -> list[dict]:
    """Exact normalized-title matches, installed first; no sequel fallback."""
    target = normalize_title(title)
    matches = [game for game in installs
               if normalize_title(game.get('name', '')) == target]
    return sorted(matches, key=lambda game: 0 if game.get('installed') else 1)
