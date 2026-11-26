import json

from native_services.windows_catalog import (
    steam_rows, epic_rows, gog_rows, ubisoft_rows, ea_rows, battlenet_rows, xbox_package_rows,
    collect_windows_game_installs, _read_xml, _appx_packages,
)


def _v(n):
    out = bytearray()
    while n > 0x7f:
        out.append((n & 0x7f) | 0x80); n >>= 7
    out.append(n)
    return bytes(out)


def _field(number, wire, value):
    tag = _v((number << 3) | wire)
    if wire == 0:
        return tag + _v(value)
    return tag + _v(len(value)) + value


def _bnet_record(code, path, installed=True):
    settings = _field(1, 2, str(path).encode())
    base_state = _field(1, 0, int(installed))
    state = _field(1, 2, base_state)
    record = _field(2, 2, code.encode()) + _field(3, 2, settings) + _field(4, 2, state)
    return _field(1, 2, record)


def test_steam_exact_library_manifest_and_installed_bit(tmp_path):
    root = tmp_path / 'Steam'
    main = root / 'steamapps'
    main.mkdir(parents=True)
    second = tmp_path / 'Games' / 'SteamLibrary'
    (second / 'steamapps' / 'common' / 'Portal 2').mkdir(parents=True)
    (main / 'libraryfolders.vdf').write_text(
        '"libraryfolders" { "0" { "path" "' + str(root).replace('\\', '\\\\') +
        '" } "1" { "path" "' + str(second).replace('\\', '\\\\') + '" } }')
    (second / 'steamapps' / 'appmanifest_620.acf').write_text(
        '"AppState" { "appid" "620" "name" "Portal 2" "installdir" "Portal 2" "StateFlags" "4" }')
    (second / 'steamapps' / 'appmanifest_621.acf').write_text(
        '"AppState" { "appid" "621" "name" "Not installed" "installdir" "Missing" "StateFlags" "2" }')
    (second / 'steamapps' / 'appmanifest_622.acf').write_text(
        '"AppState" { "appid" "622" "name" "Steam Runtime" "type" "Tool" '
        '"installdir" "Portal 2" "StateFlags" "4" }')
    rows = steam_rows([root])
    assert [(r['product_id'], r['title'], r['status']) for r in rows] == [('620', 'Portal 2', 'installed')]


def test_steam_requires_installed_state_flag_even_when_payload_folder_exists(tmp_path):
    root = tmp_path / 'Steam'; steamapps = root / 'steamapps'
    (steamapps / 'common' / 'LooksInstalled').mkdir(parents=True)
    steamapps.mkdir(exist_ok=True)
    (steamapps / 'appmanifest_1.acf').write_text(
        '"AppState" { "appid" "1" "name" "Unknown state" "installdir" "LooksInstalled" }')
    assert steam_rows([root]) == []


def test_epic_manifest_rejects_incomplete_dlc_and_nonexistent_paths(tmp_path):
    root = tmp_path / 'epic'; root.mkdir()
    install = tmp_path / 'EpicGames' / 'Game'; install.mkdir(parents=True)
    docs = [
        {'AppName': 'GameA', 'DisplayName': 'Game A', 'InstallLocation': str(install),
         'CatalogNamespace': 'ns', 'CatalogItemId': 'item'},
        {'AppName': 'DlcA', 'DisplayName': 'DLC', 'InstallLocation': str(install), 'MainGameAppName': 'GameA'},
        {'AppName': 'Bad', 'DisplayName': 'Bad', 'InstallLocation': str(install / 'missing'), 'bIsIncompleteInstall': False},
        {'AppName': 'UE_ASSET', 'DisplayName': 'Unreal data', 'InstallLocation': str(install), 'CatalogNamespace': 'ue'},
    ]
    for i, data in enumerate(docs):
        (root / f'{i}.item').write_text(json.dumps(data))
    rows = epic_rows([root])
    assert [(r['product_id'], r['title']) for r in rows] == [('GameA', 'Game A')]
    assert rows[0]['metadata']['catalog_item_id'] == 'item'


def test_gog_registry_identity_requires_installed_folder_and_excludes_dlc(tmp_path):
    root = tmp_path / 'gog'; root.mkdir()
    (root / 'goggame-123.info').write_text(json.dumps({'gameId': 123, 'rootGameId': 123}))
    (root / 'goggame-456.info').write_text(json.dumps({'gameId': 456, 'rootGameId': 123}))
    rows = gog_rows([
        {'key': '123_is1', 'publisher': 'GOG.com', 'display_name': 'GOG Game', 'install_location': str(root)},
        {'key': '456_is1', 'publisher': 'GOG.com', 'display_name': 'DLC', 'install_location': str(root)},
        {'key': '789_is1', 'publisher': 'Other', 'display_name': 'Not GOG', 'install_location': str(root)},
        {'key': 'GOGPACK_123_is1', 'publisher': 'GOG.com', 'display_name': 'Bundle', 'install_location': str(root)},
    ])
    assert [(r['product_id'], r['title']) for r in rows] == [('123', 'GOG Game')]


def test_ubisoft_id_is_exact_registry_key_and_bad_paths_skipped(tmp_path):
    root = tmp_path / 'FarCry'; root.mkdir()
    rows = ubisoft_rows([{'key': '401', 'install_dir': str(root)},
                         {'key': 'bad/id', 'install_dir': str(root)},
                         {'key': '402', 'install_dir': str(root / 'missing')}])
    assert len(rows) == 1
    assert rows[0]['product_id'] == '401' and rows[0]['title'] == 'FarCry'


def test_ea_requires_publisher_exact_directory_and_local_installer_manifest(tmp_path):
    root = tmp_path / 'EA'; installer = root / '__Installer'; installer.mkdir(parents=True)
    (installer / 'installerdata.xml').write_text('<installer><contentID>OFB-EAST:123</contentID></installer>')
    rows = ea_rows([
        {'key': 'OFB-EAST:123', 'publisher': 'Electronic Arts', 'display_name': 'EA Game', 'install_location': str(root)},
        {'key': 'wrong', 'publisher': 'Other', 'display_name': 'Wrong', 'install_location': str(root)},
        {'key': 'missing-manifest', 'publisher': 'EA', 'display_name': 'Unknown', 'install_location': str(tmp_path)},
    ])
    assert len(rows) == 1
    assert rows[0]['product_id'] == 'OFB-EAST:123'
    assert rows[0]['metadata']['content_ids'] == ['OFB-EAST:123']


def test_ea_uses_install_folder_as_title_when_registry_has_no_display_name(tmp_path):
    root = tmp_path / 'Registered EA Game'; installer = root / '__Installer'; installer.mkdir(parents=True)
    (installer / 'installerdata.xml').write_text('<installer/>')
    rows = ea_rows([{'key': 'ea-key', 'provider_root': 'ea-games', 'install_location': str(root)}])
    assert rows[0]['title'] == root.name


def test_battlenet_product_db_only_maps_known_installed_products(tmp_path):
    game = tmp_path / 'Overwatch'; game.mkdir()
    db = tmp_path / 'product.db'
    db.write_bytes(_bnet_record('pro', game) + _bnet_record('agent', game) + _bnet_record('wow', game, False))
    rows = battlenet_rows(db)
    assert [(r['product_id'], r['title']) for r in rows] == [('pro', 'Overwatch 2')]


def test_battlenet_maps_fenris_as_diablo_iv(tmp_path):
    game = tmp_path / 'Diablo IV'; game.mkdir()
    db = tmp_path / 'product.db'; db.write_bytes(_bnet_record('fenris', game))
    rows = battlenet_rows(db)
    assert [(r['product_id'], r['title']) for r in rows] == [('fenris', 'Diablo IV')]


def test_xbox_imports_only_msix_with_microsoft_game_marker_and_valid_aumid(tmp_path):
    game = tmp_path / 'XboxGame'; game.mkdir()
    (game / 'MicrosoftGame.config').write_text('<Game/>')
    (game / 'AppxManifest.xml').write_text(
        '<Package><Properties><DisplayName>Xbox Test Game</DisplayName></Properties>'
        '<Applications><Application Id="Game" /></Applications></Package>')
    ordinary = tmp_path / 'OrdinaryApp'; ordinary.mkdir()
    rows = xbox_package_rows([
        {'name': 'XboxTest', 'package_family_name': 'XboxTest_1234567890123', 'install_location': str(game)},
        {'name': 'OrdinaryApp', 'package_family_name': 'OrdinaryApp_1234567890123', 'install_location': str(ordinary)},
    ])
    assert len(rows) == 1
    assert rows[0]['product_id'] == 'XboxTest_1234567890123!Game'
    assert rows[0]['title'] == 'Xbox Test Game'


def test_xml_entity_declarations_are_rejected_for_ea(tmp_path):
    game = tmp_path / 'EA'; installer = game / '__Installer'; installer.mkdir(parents=True)
    (installer / 'installerdata.xml').write_text('<!DOCTYPE x [<!ENTITY y "z">]><x>&y;</x>')
    assert ea_rows([{'key': 'id', 'publisher': 'EA', 'display_name': 'EA Game',
                     'install_location': str(game)}]) == []


def test_xml_utf16_dtd_is_rejected_and_bounded_reader_accepts_utf8(tmp_path):
    xml = tmp_path / 'manifest.xml'
    xml.write_bytes('<!DOCTYPE x [<!ENTITY y "z">]><x>&y;</x>'.encode('utf-16'))
    assert _read_xml(xml) is None
    xml.write_text('<Package><Application Id="ok"/></Package>')
    assert _read_xml(xml).tag == 'Package'


def test_appx_query_uses_static_read_only_powershell_and_bounded_json(monkeypatch):
    import subprocess
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, b'{"Name":"Game","PackageFamilyName":"Game_1234567890123","InstallLocation":"C:\\\\Games"}', b'')
    monkeypatch.setattr('native_services.windows_catalog.subprocess.run', run)
    assert _appx_packages()[0]['name'] == 'Game'
    args, kwargs = calls[0]
    assert args[:4] == ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive']
    assert '-Command' in args and 'Get-AppxPackage' in args[-1]
    assert kwargs['timeout'] == 20 and kwargs['check'] is True


def test_windows_collector_wires_all_seven_local_provider_adapters(tmp_path):
    steam = tmp_path / 'Steam'; steamapps = steam / 'steamapps'; steamapps.mkdir(parents=True)
    (steamapps / 'appmanifest_440.acf').write_text(
        '"AppState" { "appid" "440" "name" "Team Fortress 2" "installdir" "TF2" "StateFlags" "4" }')
    (steamapps / 'common' / 'TF2').mkdir(parents=True)
    data = tmp_path / 'ProgramData'; epic_root = data / 'Epic/EpicGamesLauncher/Data/Manifests'
    epic_root.mkdir(parents=True)
    epic_dir = tmp_path / 'EpicGame'; epic_dir.mkdir()
    (epic_root / 'game.item').write_text(json.dumps({'AppName': 'epic-id', 'DisplayName': 'Epic Game',
        'InstallLocation': str(epic_dir), 'CatalogNamespace': 'ns', 'CatalogItemId': 'item'}))
    gog_dir = tmp_path / 'GogGame'; gog_dir.mkdir()
    (gog_dir / 'goggame-123.info').write_text('{"gameId":123,"rootGameId":123}')
    ubi_dir = tmp_path / 'UbiGame'; ubi_dir.mkdir()
    ea_dir = tmp_path / 'EaGame'; (ea_dir / '__Installer').mkdir(parents=True)
    (ea_dir / '__Installer/installerdata.xml').write_text('<installer><contentID>ea-id</contentID></installer>')
    battlenet_dir = tmp_path / 'BnetGame'; battlenet_dir.mkdir()
    bnet_db = data / 'Battle.net/Agent/product.db'; bnet_db.parent.mkdir(parents=True)
    bnet_db.write_bytes(_bnet_record('pro', battlenet_dir))
    xbox_dir = tmp_path / 'XboxGame'; xbox_dir.mkdir()
    (xbox_dir / 'MicrosoftGame.config').write_text('<Game/>')
    (xbox_dir / 'AppxManifest.xml').write_text(
        '<Package><Properties><DisplayName>Xbox Game</DisplayName></Properties>'
        '<Applications><Application Id="Game" /></Applications></Package>')
    records, states = collect_windows_game_installs(
        environ={'PROGRAMDATA': str(data)},
        registry={'steam_roots': [str(steam)],
                  'uninstall': [{'key': '123_is1', 'publisher': 'GOG.com', 'display_name': 'GOG Game',
                                 'install_location': str(gog_dir)}],
                  'ubisoft': [{'key': '401', 'install_dir': str(ubi_dir)}],
                  'ea': [{'key': 'ea-id', 'provider_root': 'ea-games', 'publisher': 'Electronic Arts',
                          'display_name': 'EA Game', 'install_location': str(ea_dir)}]},
        appx_packages=[{'name': 'XboxGame', 'package_family_name': 'XboxGame_1234567890123',
                        'install_location': str(xbox_dir)}])
    assert {row['provider'] for row in records} == {
        'Steam', 'Epic', 'GOG', 'EA app', 'Ubisoft Connect', 'Battle.net', 'Xbox'}
    assert all(row['status'] == 'installed' and row['metadata']['ownership'] == 'not-asserted'
               for row in records)
    assert set(states) == {'Steam', 'Epic', 'GOG', 'EA app', 'Ubisoft Connect', 'Battle.net', 'Xbox'}
    assert all(state['ownership_complete'] is False for state in states.values())


def test_launcher_library_dispatches_windows_snapshot_to_native_catalog(tmp_path, monkeypatch):
    import native_services.launcher_library as launcher_library

    expected = ([{'source_key': 'steam:400', 'provider': 'Steam', 'title': 'Portal',
                  'status': 'installed'}], {'Steam': {'count': 1, 'ownership_complete': False}})
    monkeypatch.setattr('native_services.windows_catalog.collect_windows_launcher_library',
                        lambda **kwargs: expected)
    monkeypatch.setattr(launcher_library, 'is_windows', lambda: True)
    records, states = launcher_library.collect_launcher_library(home=tmp_path)
    assert records == expected[0] and states == expected[1]
