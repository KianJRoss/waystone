"""Tests for read-only, stdlib-only local game install discovery."""
import json
import sqlite3

import pytest

from native_services.game_installs import (
    battlenet_installed_games,
    find_installs,
    folder_games,
    heroic_games,
    lutris_games,
    match_install,
    normalize_title,
    parse_vdf,
    steam_games,
    steam_libraries,
)


STEAM_LOCATIONS = (
    ('.local', 'share', 'Steam', 'steamapps', 'libraryfolders.vdf'),
    ('.steam', 'steam', 'steamapps', 'libraryfolders.vdf'),
    ('.var', 'app', 'com.valvesoftware.Steam', '.local', 'share', 'Steam',
     'steamapps', 'libraryfolders.vdf'),
)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


def _write_steam_libraries(home, paths, relative=STEAM_LOCATIONS[0]):
    target = home.joinpath(*relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ['"libraryfolders"', '{']
    for index, path in enumerate(paths):
        lines.extend([
            f'  "{index}"',
            '  {',
            f'    "path"  "{path}"',
            '    "label"  ""',
            '  }',
        ])
    lines.append('}')
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return target


def _write_manifest(library, appid, name, installdir):
    manifest = library / 'steamapps' / f'appmanifest_{appid}.acf'
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        '"AppState"\n{\n'
        f'  "appid"    "{appid}"\n'
        f'  "name"     "{name}"\n'
        f'  "installdir"  "{installdir}"\n'
        '}\n',
        encoding='utf-8',
    )
    return manifest


# --- VDF parsing -----------------------------------------------------------

def test_parse_vdf_comments_escapes_nesting_and_duplicate_keys():
    text = r'''
    // leading comment
    "Root"
    {
      "name"  "A \"quoted\" title"   // trailing comment
      "path"  "C:\\Games\\Doom"
      "dup"   "first"
      "dup"   "second"
      "nested"
      {
        "leaf"  "value"
        "deeper" { "x" "y" }
      }
    }
    '''
    data = parse_vdf(text)
    root = data['Root']
    assert root['name'] == 'A "quoted" title'
    assert root['path'] == 'C:\\Games\\Doom'
    assert root['dup'] == 'second'
    assert root['nested']['leaf'] == 'value'
    assert root['nested']['deeper']['x'] == 'y'


def test_parse_vdf_empty_and_top_level_pairs():
    assert parse_vdf('') == {}
    assert parse_vdf('// only a comment\n') == {}
    assert parse_vdf('"a" "1"\n"b" "2"\n') == {'a': '1', 'b': '2'}


# --- Steam libraries -------------------------------------------------------

@pytest.mark.parametrize('relative', STEAM_LOCATIONS)
def test_steam_libraries_supported_locations(tmp_path, relative):
    library = tmp_path / 'library'
    library.mkdir()
    _write_steam_libraries(tmp_path, [str(library)], relative=relative)
    assert steam_libraries(tmp_path) == [library]


def test_steam_libraries_uses_first_existing_file_dedups_and_keeps_order(tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    # Same directory via a trailing slash must not appear twice.
    _write_steam_libraries(tmp_path, [str(first), str(first) + '/', str(second)])
    # A lower-priority libraryfolders.vdf must be ignored entirely.
    _write_steam_libraries(tmp_path, [str(tmp_path / 'ignored')],
                           relative=STEAM_LOCATIONS[1])
    assert steam_libraries(tmp_path) == [first, second]


def test_steam_libraries_missing_file_or_missing_paths(tmp_path):
    assert steam_libraries(tmp_path) == []
    _write_steam_libraries(tmp_path, [str(tmp_path / 'does-not-exist')])
    assert steam_libraries(tmp_path) == []


# --- Steam games -----------------------------------------------------------

def test_steam_games_two_libraries_with_prefixes(tmp_path):
    lib_a = tmp_path / 'libA'
    lib_b = tmp_path / 'libB'
    _write_steam_libraries(tmp_path, [str(lib_a), str(lib_b)])
    for library in (lib_a, lib_b):
        (library / 'steamapps' / 'common').mkdir(parents=True)

    _write_manifest(lib_a, '100', 'Alpha Game', 'AlphaGame')
    (lib_a / 'steamapps' / 'common' / 'AlphaGame').mkdir()
    prefix = lib_a / 'steamapps' / 'compatdata' / '100' / 'pfx'
    prefix.mkdir(parents=True)

    _write_manifest(lib_b, '200', 'Beta Game', 'BetaGame')
    # BetaGame payload and prefix are deliberately absent.

    games = steam_games(tmp_path)
    assert len(games) == 2
    by_id = {game['id']: game for game in games}
    alpha = by_id['100']
    assert alpha['store'] == 'steam'
    assert alpha['name'] == 'Alpha Game'
    assert alpha['install_dir'] == str(lib_a / 'steamapps' / 'common' / 'AlphaGame')
    assert alpha['prefix'] == str(prefix)
    assert alpha['installed'] is True

    beta = by_id['200']
    assert beta['install_dir'] == str(lib_b / 'steamapps' / 'common' / 'BetaGame')
    assert beta['prefix'] == ''
    assert beta['installed'] is False


def test_steam_games_skips_malformed_manifests(tmp_path):
    assert steam_games(tmp_path) == []
    library = tmp_path / 'library'
    _write_steam_libraries(tmp_path, [str(library)])
    (library / 'steamapps').mkdir(parents=True)
    (library / 'steamapps' / 'appmanifest_1.acf').write_text(
        '"AppState"\n{\n  "appid" "1"\n', encoding='utf-8')
    (library / 'steamapps' / 'appmanifest_2.acf').write_text(
        '"AppState"\n{ "appid" "2" }\n', encoding='utf-8')
    assert steam_games(tmp_path) == []


# --- Heroic ----------------------------------------------------------------

def test_heroic_epic_title_path_and_wine_prefix(tmp_path):
    heroic = tmp_path / '.config' / 'heroic'
    install = tmp_path / 'epic-game'
    install.mkdir()
    _write_json(heroic / 'legendaryConfig' / 'legendary' / 'installed.json', {
        'EpicApp': {
            'app_name': 'EpicApp',
            'title': 'Epic Game',
            'install_path': str(install),
            'platform': 'Windows',
        },
    })
    _write_json(heroic / 'GamesConfig' / 'EpicApp.json', {
        'EpicApp': {'winePrefix': str(tmp_path / 'epic-prefix')},
    })
    games = heroic_games(tmp_path)
    assert games == [{
        'store': 'epic',
        'id': 'EpicApp',
        'name': 'Epic Game',
        'install_dir': str(install),
        'prefix': str(tmp_path / 'epic-prefix'),
        'installed': True,
    }]


def test_heroic_gog_titles_from_library_and_fallback(tmp_path):
    heroic = tmp_path / '.config' / 'heroic'
    known = tmp_path / 'gog-known'
    known.mkdir()
    _write_json(heroic / 'gog_store' / 'installed.json', {'installed': [
        {'appName': 'gog-known', 'install_path': str(known), 'platform': 'windows'},
        {'appName': 'gog-unknown', 'install_path': str(tmp_path / 'missing'),
         'platform': 'windows'},
    ]})
    _write_json(heroic / 'gog_store' / 'library.json', {
        'games': [{'app_name': 'gog-known', 'title': 'Known GOG Title'}],
    })
    games = heroic_games(tmp_path)
    assert len(games) == 2
    assert games[0]['store'] == 'gog'
    assert games[0]['id'] == 'gog-known'
    assert games[0]['name'] == 'Known GOG Title'
    assert games[0]['installed'] is True
    assert games[0]['prefix'] == ''
    assert games[1]['name'] == 'gog-unknown'
    assert games[1]['installed'] is False


def test_heroic_amazon_title_from_store_cache(tmp_path):
    heroic = tmp_path / '.config' / 'heroic'
    install = tmp_path / 'amazon-game'
    install.mkdir()
    _write_json(heroic / 'nile_config' / 'nile' / 'installed.json', [
        {'id': 'amazon-id', 'path': str(install)},
    ])
    _write_json(heroic / 'store_cache' / 'nile_library.json', {
        'library': [{'app_name': 'amazon-id', 'title': 'Amazon Game'}],
    })
    _write_json(heroic / 'GamesConfig' / 'amazon-id.json', {
        'amazon-id': {'winePrefix': str(tmp_path / 'amazon-prefix')},
    })
    assert heroic_games(tmp_path) == [{
        'store': 'amazon',
        'id': 'amazon-id',
        'name': 'Amazon Game',
        'install_dir': str(install),
        'prefix': str(tmp_path / 'amazon-prefix'),
        'installed': True,
    }]


def test_heroic_missing_or_invalid_files_are_skipped(tmp_path):
    assert heroic_games(tmp_path) == []
    heroic = tmp_path / '.config' / 'heroic'
    (heroic / 'gog_store' / 'installed.json').parent.mkdir(parents=True)
    (heroic / 'gog_store' / 'installed.json').write_text('{ broken', encoding='utf-8')
    assert heroic_games(tmp_path) == []


def test_heroic_sideload_reads_installed_rows_from_native_and_flatpak_roots(tmp_path):
    install = tmp_path / 'Games' / 'Sideloaded'
    install.mkdir(parents=True)
    exe = install / 'game.exe'
    exe.write_bytes(b'fixture')
    native = tmp_path / '.config/heroic/sideload_apps/library.json'
    flatpak = tmp_path / '.var/app/com.heroicgameslauncher.hgl/config/heroic/sideload_apps/library.json'
    _write_json(native, {'games': [
        {'runner': 'sideload', 'app_name': 'sid-1', 'title': 'Manual Game',
         'folder_name': str(install), 'is_installed': True,
         'install': {'executable': 'game.exe'}},
        {'runner': 'sideload', 'app_name': 'sid-2', 'title': 'Not Installed',
         'folder_name': str(tmp_path / 'missing'), 'is_installed': True,
         'install': {'executable': 'game.exe'}},
        {'runner': 'gog', 'app_name': 'not-sideload', 'title': 'Do not import'}]})
    _write_json(flatpak, {'games': [
        {'runner': 'sideload', 'app_name': 'flat-1', 'title': 'Flatpak Manual',
         'folder_name': str(install), 'is_installed': False,
         'install': {'executable': 'game.exe'}}]})

    rows = [row for row in heroic_games(tmp_path) if row['store'] == 'sideload']
    assert rows == [
        {'store': 'sideload', 'id': 'sid-1', 'name': 'Manual Game',
         'install_dir': str(install), 'prefix': '', 'executable': str(exe),
         'installed': True, 'runner': 'sideload'},
        {'store': 'sideload', 'id': 'sid-2', 'name': 'Not Installed',
         'install_dir': '', 'prefix': '', 'executable': '',
         'installed': False, 'runner': 'sideload'},
        {'store': 'sideload', 'id': 'flat-1', 'name': 'Flatpak Manual',
         'install_dir': str(install), 'prefix': '', 'executable': str(exe),
         'installed': False, 'runner': 'sideload'},
    ]


def test_heroic_sideload_rejects_symlinked_library_file(tmp_path):
    real = tmp_path / 'outside.json'
    _write_json(real, {'games': [{'runner': 'sideload', 'app_name': 'id', 'title': 'T',
                                  'folder_name': str(tmp_path), 'is_installed': True}]})
    index = tmp_path / '.config/heroic/sideload_apps/library.json'
    index.parent.mkdir(parents=True)
    index.symlink_to(real)
    assert heroic_games(tmp_path) == []
# --- Lutris ----------------------------------------------------------------

def test_lutris_games_filters_to_installed_rows(tmp_path):
    database = tmp_path / '.local' / 'share' / 'lutris' / 'pga.db'
    database.parent.mkdir(parents=True)
    game_dir = tmp_path / 'lutris-game'
    game_dir.mkdir()
    connection = sqlite3.connect(database)
    connection.execute(
        'CREATE TABLE games (name TEXT, slug TEXT, directory TEXT, '
        'runner TEXT, installed INTEGER)'
    )
    connection.execute('INSERT INTO games VALUES (?, ?, ?, ?, ?)',
                       ('Lutris Game', 'lutris-game', str(game_dir), 'wine', 1))
    connection.execute('INSERT INTO games VALUES (?, ?, ?, ?, ?)',
                       ('Uninstalled Game', 'uninstalled', '', 'wine', 0))
    connection.commit()
    connection.close()

    games = lutris_games(tmp_path)
    assert games == [{
        'store': 'lutris',
        'id': 'lutris-game',
        'name': 'Lutris Game',
        'install_dir': str(game_dir),
        'prefix': '',
        'installed': True,
        'runner': 'wine',
    }]


def test_lutris_games_missing_or_invalid_database(tmp_path):
    assert lutris_games(tmp_path) == []
    database = tmp_path / '.local' / 'share' / 'lutris' / 'pga.db'
    database.parent.mkdir(parents=True)
    database.write_bytes(b'not a sqlite database')
    assert lutris_games(tmp_path) == []


def _pb_varint(value):
    out = bytearray()
    while value > 0x7f:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _pb_int(field, value):
    return _pb_varint(field << 3) + _pb_varint(value)


def _pb_bytes(field, value):
    return _pb_varint((field << 3) | 2) + _pb_varint(len(value)) + value


def _bnet_product(code, windows_path, installed=True):
    settings = _pb_bytes(1, windows_path.encode())
    base_state = _pb_int(1, int(installed))
    cached = _pb_bytes(1, base_state)
    product = _pb_bytes(2, code.encode()) + _pb_bytes(3, settings) + _pb_bytes(4, cached)
    return _pb_bytes(1, product)


def test_battlenet_product_db_reads_only_installed_known_products_and_prefix_paths(tmp_path):
    prefix = tmp_path / 'Games' / 'battlenet'
    product_db = prefix / 'drive_c/ProgramData/Battle.net/Agent/product.db'
    product_db.parent.mkdir(parents=True)
    wc3 = prefix / 'drive_c/Program Files (x86)/Warcraft III'
    ow = prefix / 'drive_c/Program Files (x86)/Overwatch'
    wc3.mkdir(parents=True)
    ow.mkdir(parents=True)
    product_db.write_bytes(
        _bnet_product('w3', r'C:\Program Files (x86)\Warcraft III')
        + _bnet_product('pro', r'C:\Program Files (x86)\Overwatch')
        + _bnet_product('w3', r'C:\Program Files\Not Installed', installed=False)
        + _bnet_product('unknown', r'C:\Unknown')
        + _bnet_product('w3', r'C:\..\outside'))
    assert battlenet_installed_games(tmp_path, [prefix]) == [
        {'store': 'battlenet', 'id': 'W3', 'name': 'Warcraft III',
         'install_dir': str(wc3), 'prefix': str(prefix), 'installed': True, 'product_code': 'w3'},
        {'store': 'battlenet', 'id': 'Pro', 'name': 'Overwatch 2',
         'install_dir': str(ow), 'prefix': str(prefix), 'installed': True, 'product_code': 'pro'},
    ]


def test_battlenet_product_db_rejects_malformed_and_unregistered_prefixes(tmp_path):
    prefix = tmp_path / 'prefix'
    product_db = prefix / 'drive_c/ProgramData/Battle.net/Agent/product.db'
    product_db.parent.mkdir(parents=True)
    product_db.write_bytes(b'\x0a\x80')
    assert battlenet_installed_games(tmp_path, [prefix]) == []
    product_db.unlink()
    assert battlenet_installed_games(tmp_path, [prefix]) == []


def test_battlenet_product_db_rejects_symlinked_database(tmp_path):
    prefix = tmp_path / 'prefix'
    product_db = prefix / 'drive_c/ProgramData/Battle.net/Agent/product.db'
    product_db.parent.mkdir(parents=True)
    outside = tmp_path / 'external.db'
    outside.write_bytes(_bnet_product('pro', r'C:\Program Files (x86)\Overwatch'))
    product_db.symlink_to(outside)
    assert battlenet_installed_games(tmp_path, [prefix]) == []


# --- Plain folders ---------------------------------------------------------

def test_folder_games_detects_exe_only_within_depth_two(tmp_path):
    root = tmp_path / 'games'
    root.mkdir()

    shallow = root / 'Shallow'
    shallow.mkdir()
    (shallow / 'game.exe').write_bytes(b'')

    nested = root / 'Nested'
    (nested / 'bin').mkdir(parents=True)
    (nested / 'bin' / 'launcher.exe').write_bytes(b'')

    too_deep = root / 'TooDeep'
    (too_deep / 'a' / 'b').mkdir(parents=True)
    (too_deep / 'a' / 'b' / 'deep.exe').write_bytes(b'')

    (root / 'Empty').mkdir()
    (root / 'loose.exe').write_bytes(b'')

    games = folder_games([root])
    assert [game['name'] for game in games] == ['Nested', 'Shallow']
    assert games[0] == {
        'store': 'folder',
        'id': str(nested),
        'name': 'Nested',
        'install_dir': str(nested),
        'prefix': '',
        'installed': True,
    }


def test_folder_games_detects_data_folder_with_esm(tmp_path):
    root = tmp_path / 'games'
    root.mkdir()
    elder = root / 'Elder'
    (elder / 'Data').mkdir(parents=True)
    (elder / 'Data' / 'Skyrim.esm').write_bytes(b'')
    no_esm = root / 'NoEsm'
    (no_esm / 'Data').mkdir(parents=True)
    (no_esm / 'Data' / 'readme.txt').write_text('nothing', encoding='utf-8')

    games = folder_games([root])
    assert [game['name'] for game in games] == ['Elder']


def test_folder_games_ignores_missing_roots(tmp_path):
    assert folder_games([tmp_path / 'missing', tmp_path / 'a-file']) == []


# --- Aggregation and matching ----------------------------------------------

def test_find_installs_dedups_by_resolved_install_dir(tmp_path):
    library = tmp_path / 'SteamLib'
    _write_steam_libraries(tmp_path, [str(library)])
    common = library / 'steamapps' / 'common'
    shared = common / 'Shared'
    shared.mkdir(parents=True)
    (shared / 'game.exe').write_bytes(b'')
    _write_manifest(library, '10', 'Shared Game', 'Shared')

    installs = find_installs(tmp_path, extra_roots=[common])
    assert len(installs) == 1
    assert installs[0]['store'] == 'steam'
    assert installs[0]['install_dir'] == str(shared)


def test_find_installs_combines_all_sources_and_is_read_only(tmp_path):
    # Steam
    library = tmp_path / 'lib'
    _write_steam_libraries(tmp_path, [str(library)])
    common = library / 'steamapps' / 'common'
    steam_dir = common / 'SteamGame'
    steam_dir.mkdir(parents=True)
    _write_manifest(library, '11', 'Steam Game', 'SteamGame')

    # Heroic GOG
    gog_dir = tmp_path / 'GogGame'
    gog_dir.mkdir()
    _write_json(tmp_path / '.config' / 'heroic' / 'gog_store' / 'installed.json',
                {'installed': [{'appName': 'gog', 'install_path': str(gog_dir)}]})

    # Lutris
    database = tmp_path / '.local' / 'share' / 'lutris' / 'pga.db'
    database.parent.mkdir(parents=True)
    lutris_dir = tmp_path / 'LutrisGame'
    lutris_dir.mkdir()
    connection = sqlite3.connect(database)
    connection.execute(
        'CREATE TABLE games (name TEXT, slug TEXT, directory TEXT, '
        'runner TEXT, installed INTEGER)'
    )
    connection.execute('INSERT INTO games VALUES (?, ?, ?, ?, ?)',
                       ('Lutris Game', 'lutris', str(lutris_dir), 'wine', 1))
    connection.commit()
    connection.close()

    # Folder (a distinct extra root)
    folder_root = tmp_path / 'Portable'
    portable = folder_root / 'PortableGame'
    (portable / 'bin').mkdir(parents=True)
    (portable / 'bin' / 'game.exe').write_bytes(b'')

    before = {str(path) for path in tmp_path.rglob('*')}
    installs = find_installs(tmp_path, extra_roots=[folder_root])
    after = {str(path) for path in tmp_path.rglob('*')}

    assert after == before
    assert {game['store'] for game in installs} == {
        'steam', 'gog', 'lutris', 'folder'}
    assert len(installs) == 4


def test_normalize_title_drops_editions_and_sequel_words_are_not_removed():
    assert (normalize_title('The Elder Scrolls V: Skyrim Special Edition')
            == normalize_title('The Elder Scrolls V: Skyrim')
            == 'the elder scrolls v skyrim')
    assert (normalize_title('Fallout 4')
            == normalize_title('Fallout 4: Game of the Year Edition')
            == 'fallout 4')
    assert normalize_title('Fallout 3') == 'fallout 3'
    assert normalize_title('Subnautica: Below Zero') == 'subnautica below zero'


def test_normalize_title_symbols_ampersand_brackets_and_punctuation():
    assert normalize_title('DOOM® (2016) [PC]') == 'doom'
    assert (normalize_title("Tom Clancy's Ghost Recon & Future Soldier™")
            == 'tom clancy s ghost recon and future soldier')
    assert normalize_title('Copyright © 2020 — Deluxe Edition') == 'copyright 2020'


def test_match_install_prefers_installed_entries():
    installs = [
        {'name': 'Alpha', 'installed': False, 'store': 'steam'},
        {'name': 'Alpha™ (2020)', 'installed': True, 'store': 'folder'},
        {'name': 'Other', 'installed': True, 'store': 'gog'},
    ]
    assert match_install('alpha', installs) == [installs[1], installs[0]]
    assert match_install('Missing', installs) == []


def test_match_install_never_matches_sequels():
    fallout_3 = {'name': 'Fallout 3', 'installed': True}
    below_zero = {'name': 'Subnautica: Below Zero', 'installed': True}
    installs = [fallout_3, below_zero]
    assert match_install('Fallout 4', installs) == []
    assert match_install('Subnautica', installs) == []
    assert match_install('Fallout 3', installs) == [fallout_3]


def test_unregistered_library_and_waystone_installs(tmp_path):
    from native_services import game_installs as g
    lib = tmp_path / "mnt" / "ssd" / "SteamLibrary" / "steamapps"
    (lib / "common" / "Skyrim Special Edition").mkdir(parents=True)
    (lib / "appmanifest_489830.acf").write_text('"AppState" { "appid" "489830" "name" "The Elder Scrolls V: Skyrim Special Edition" "installdir" "Skyrim Special Edition" }')
    assert g.unregistered_steam_libraries([str(tmp_path / "mnt")]) == [lib.parent]
    games = g.steam_games(tmp_path / "home", g.unregistered_steam_libraries([str(tmp_path / "mnt")]))
    assert [x["name"] for x in games] == ["The Elder Scrolls V: Skyrim Special Edition"]
    root = tmp_path / "Waystone" / "abc123" / "Fallout Tactics"; root.mkdir(parents=True)
    (root / "BOS.exe").write_text("x")
    w = g.waystone_games(tmp_path / "Waystone")
    assert w[0]["store"] == "waystone" and w[0]["name"] == "Fallout Tactics"
