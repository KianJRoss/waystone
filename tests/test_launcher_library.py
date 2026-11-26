import json
import sqlite3
from pathlib import Path

from native_services.launcher_library import collect_launcher_library, default_library_path, sync_library
from native_services.library_repository import LibraryRepository
from native_services.native_catalog import load_native_catalog


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def _varint(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def _pb_bytes(field, value):
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _bnet_installed_product(code, path):
    settings = _pb_bytes(1, path.encode())
    state = _pb_bytes(1, _varint(8) + _varint(1))  # BaseProductState.installed = true
    product = (_pb_bytes(2, code.encode()) + _pb_bytes(3, settings)
               + _pb_bytes(4, state))
    return _pb_bytes(1, product)


def fake_home(tmp_path):
    home = tmp_path / 'home'; home.mkdir()
    data = home / '.local/share/Steam/steamapps'
    library = home / 'steam-library'
    (home / '.local/share/Steam/steamapps').mkdir(parents=True)
    (library / 'steamapps/common/Installed Steam Game').mkdir(parents=True)
    (home / '.local/share/Steam/steamapps/libraryfolders.vdf').write_text(
        '"libraryfolders" { "0" { "path" "' + str(home / '.local/share/Steam') + '" } '
        '"1" { "path" "' + str(library) + '" } }', encoding='utf-8')
    (library / 'steamapps/appmanifest_10.acf').write_text(
        '"AppState" { "appid" "10" "name" "Installed Steam Game" "installdir" "Installed Steam Game" }')
    (home / '.local/share/Steam/userdata/765/config').mkdir(parents=True)
    (home / '.local/share/Steam/userdata/765/config/localconfig.vdf').write_text(
        '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "20" { "LastPlayed" "1" } } } } } }')

    heroic = home / '.config/heroic'
    (heroic / 'legendaryConfig/legendary').mkdir(parents=True)
    epic_install = home / 'Games/Epic Installed'; epic_install.mkdir(parents=True)
    _json(heroic / 'legendaryConfig/legendary/installed.json', {
        'epic-key': {'title': 'Epic Installed Game', 'install_path': str(epic_install)}})
    _json(heroic / 'epic/store_cache/legendary_library.json', {
        'library': [{'app_name': 'epic-key', 'title': 'Epic Installed Game'},
                    {'app_name': 'epic-owned', 'title': 'Epic Owned Only'}]})
    gog_install = home / 'Games/GOG'; gog_install.mkdir(parents=True)
    _json(heroic / 'gog_store/installed.json', {'installed': [{'appName': '12345', 'install_path': str(gog_install)}]})
    _json(heroic / 'gog/store_cache/gog_library.json', {
        'games': [{'id': '12345', 'title': 'GOG Installed'}, {'id': '67890', 'title': 'GOG Owned Only'}]})
    amazon_install = home / 'Games/Amazon'; amazon_install.mkdir(parents=True)
    _json(heroic / 'nile_config/nile/installed.json', [{'id': 'amazon-id', 'path': str(amazon_install)}])
    _json(heroic / 'amazon/store_cache/nile_library.json', {'library': [
        {'id': 'amazon-id', 'title': 'Amazon Installed'}, {'id': 'amazon-owned', 'title': 'Amazon Owned'}]})
    (heroic / 'legendaryConfig/legendary/auth.json').write_text('SECRET_DO_NOT_READ')

    lutris_path = home / '.local/share/lutris/pga.db'; lutris_path.parent.mkdir(parents=True)
    lutris_install = home / 'Games/Lutris'; lutris_install.mkdir(parents=True)
    db = sqlite3.connect(lutris_path)
    db.execute('CREATE TABLE games(name TEXT,slug TEXT,directory TEXT,runner TEXT,installed INTEGER)')
    db.execute('INSERT INTO games VALUES(?,?,?,?,?)', ('Lutris Game', 'lutris-game', str(lutris_install), 'wine', 1))
    db.commit(); db.close()

    data_home = home / '.local/share/waystone'
    own_install = data_home / 'games/job/WaystoneNative'; own_install.mkdir(parents=True)
    (own_install / 'game.exe').write_text('fake')
    local_path = data_home / 'local_games.json'
    _json(local_path, {'schema': 1, 'games': [{'id': 'local:modlist', 'title': 'My Modlist', 'platform': 'PC'}]})
    config_home = home / '.config/waystone'
    rom_root = home / 'roms/switch'; rom_root.mkdir(parents=True)
    (rom_root / 'Sample.nsp').write_bytes(b'fake-rom')
    _json(config_home / 'rom_config.json', {'kind': 'rom-inventory-config-v1', 'roots': [str(rom_root)]})
    return home, data_home, config_home


def test_collects_local_launcher_and_rom_sources_without_network_or_secrets(tmp_path):
    home, data_dir, config_dir = fake_home(tmp_path)
    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir)
    by_key = {r['source_key']: r for r in records}
    assert len(records) == 11
    assert by_key['steam:10']['status'] == 'installed'
    assert 'steam:20' not in by_key  # stale localconfig app IDs have no title/ownership proof
    assert by_key['epic:epic-key']['status'] == 'installed'
    assert by_key['epic:epic-owned']['status'] == 'registered'
    assert by_key['gog:12345']['status'] == 'installed'
    assert by_key['gog:67890']['status'] == 'registered'
    assert by_key['amazon:amazon-id']['status'] == 'installed'
    assert by_key['lutris:lutris-game']['status'] == 'installed'
    assert any(r['provider'] == 'Emulation' and r['title'] == 'Sample' for r in records)
    assert any(r['provider'] == 'Local' and r['title'] == 'My Modlist' for r in records)
    assert states['Epic']['ownership_complete'] is True
    assert all('SECRET' not in json.dumps(r) for r in records)


def test_lutris_imported_third_party_games_use_exact_service_rows_and_native_ids(tmp_path):
    home, data_dir, config_dir = fake_home(tmp_path)
    db_path = home / '.local/share/lutris/pga.db'
    db = sqlite3.connect(db_path)
    db.execute('ALTER TABLE games ADD COLUMN id INTEGER')
    db.execute('ALTER TABLE games ADD COLUMN service TEXT')
    db.execute('ALTER TABLE games ADD COLUMN service_id TEXT')
    db.execute('ALTER TABLE games ADD COLUMN configpath TEXT')
    db.execute('ALTER TABLE games ADD COLUMN installer_slug TEXT')
    db.execute("INSERT INTO games(name,slug,directory,runner,installed,id,service,service_id,configpath,installer_slug) VALUES(?,?,?,?,?,?,?,?,?,?)",
               ('EA Imported Title', 'ea-title-ea_app', str(home / 'Games/Lutris'), 'wine', 1,
                101, 'ea_app', 'EA-CONTENT-01', 'ea-title-ea_app', 'ea-title-ea_app'))
    db.execute("INSERT INTO games(name,slug,directory,runner,installed,id,service,service_id,configpath,installer_slug) VALUES(?,?,?,?,?,?,?,?,?,?)",
               ('Ubisoft Imported Title', 'ubi-title-uplay', str(home / 'Games/Lutris'), 'wine', 1,
                102, 'uplay', 'ubi-product-02', 'ubi-title-uplay', 'ubi-title-uplay'))
    db.execute("INSERT INTO games(name,slug,directory,runner,installed,id,service,service_id,configpath,installer_slug) VALUES(?,?,?,?,?,?,?,?,?,?)",
               ('Battle.net Imported Title', 'bn-title-battlenet', str(home / 'Games/Lutris'), 'wine', 1,
                103, 'battlenet', 'fenris', 'bn-title-battlenet', 'bn-title-battlenet'))
    db.execute("INSERT INTO games(name,slug,directory,runner,installed,id,service,service_id,configpath,installer_slug) VALUES(?,?,?,?,?,?,?,?,?,?)",
               ('Untrusted service row', 'bad', str(home / 'Games/Lutris'), 'wine', 1,
                104, 'unknown-service', 'secret-ish', 'bad', 'bad'))
    db.commit(); db.close()

    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir)
    by_key = {row['source_key']: row for row in records}
    assert by_key['ea app:101']['title'] == 'EA Imported Title'
    assert by_key['ea app:101']['metadata']['store_id'] == 'EA-CONTENT-01'
    assert by_key['ea app:101']['install_path'] == ''  # Lutris directory is launcher/prefix, not game path.
    assert by_key['ubisoft connect:102']['metadata']['lutris_game_id'] == '102'
    assert by_key['battle.net:103']['metadata']['store_id'] == 'fenris'
    assert 'lutris:bad' in by_key  # Unknown services remain in the generic Lutris bucket.
    assert 'secret-ish' not in json.dumps(records)
    assert states['EA app']['count'] == 1
    assert states['Ubisoft Connect']['count'] == 1
    assert states['Battle.net']['count'] == 1


def test_heroic_sideloads_import_as_exact_manual_provider_rows(tmp_path):
    home, data_dir, config_dir = fake_home(tmp_path)
    folder = home / 'Games/manual-game'
    folder.mkdir(parents=True)
    executable = folder / 'game.exe'
    executable.write_bytes(b'fixture')
    _json(home / '.config/heroic/sideload_apps/library.json', {'games': [
        {'runner': 'sideload', 'app_name': 'manual-abc', 'title': 'User Added Game',
         'folder_name': str(folder), 'is_installed': True,
         'install': {'executable': 'game.exe'}},
        {'runner': 'sideload', 'app_name': 'not-present', 'title': 'Stale Game',
         'folder_name': str(home / 'missing'), 'is_installed': True,
         'install': {'executable': 'missing.exe'}},
    ]})
    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir)
    by_key = {row['source_key']: row for row in records}
    record = by_key['heroic-sideload:manual-abc']
    assert record['provider'] == 'heroic-sideload'
    assert record['status'] == 'installed'
    assert record['install_path'] == str(folder)
    assert record['metadata']['game_executable'] == str(executable)
    assert by_key['heroic-sideload:not-present']['status'] == 'not-installed'
    assert states['heroic-sideload']['count'] == 2


def test_lutris_registered_battlenet_prefix_adds_exact_local_productdb_games(tmp_path):
    home, data_dir, config_dir = fake_home(tmp_path)
    prefix = home / 'Games' / 'battlenet'
    product = prefix / 'drive_c/ProgramData/Battle.net/Agent/product.db'
    product.parent.mkdir(parents=True)
    game_dir = prefix / 'drive_c/Program Files (x86)/Overwatch'
    game_dir.mkdir(parents=True)
    product.write_bytes(_bnet_installed_product('pro', r'C:\Program Files (x86)\Overwatch'))
    db_path = home / '.local/share/lutris/pga.db'
    db = sqlite3.connect(db_path)
    db.execute('ALTER TABLE games ADD COLUMN configpath TEXT')
    db.execute('UPDATE games SET name=?,slug=?,directory=?,runner=?,configpath=?',
               ('Battle.net', 'kian-battlenet', str(prefix), 'wine', 'kian-battlenet'))
    db.commit(); db.close()
    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir)
    by_key = {row['source_key']: row for row in records}
    assert by_key['battle.net:Pro']['title'] == 'Overwatch 2'
    assert by_key['battle.net:Pro']['status'] == 'installed'
    assert by_key['battle.net:Pro']['install_path'] == str(game_dir)
    assert by_key['battle.net:Pro']['metadata']['product_code'] == 'pro'
    assert states['Battle.net']['count'] == 1

    # The exact provider ID augments an existing Playnite game variant instead
    # of creating a duplicate card; title similarity is not involved.
    db_path = data_dir / 'library.sqlite3'
    LibraryRepository.create_empty(db_path)
    repo = LibraryRepository(db_path, writable=True)
    repo.sync_launcher_records(records, states, '2026-11-25T00:00:00+00:00')
    repo.db.execute('INSERT INTO games VALUES(?,?,?,?,?,?,?)', (
        'playnite-guid', 'native:overwatch', 'Overwatch 2', 'Battle.net', 'Pro', 'plugin',
        json.dumps({'SourceId_resolved_names': ['Battle.net'], 'PlatformIds_resolved_names': ['PC'],
                    'IsInstalled': False})))
    repo.db.commit(); repo.close()
    catalog = load_native_catalog(db_path)
    game = next(g for g in catalog['games'] if g['id'] == 'native:overwatch')
    assert len(game['variants']) == 1
    assert game['variants'][0]['status'] == 'installed'
    assert game['variants'][0]['observation']['installation_verified'] is True
    assert game['variants'][0]['metadata']['provenance']['local_installation']['installation_path'] == str(game_dir)


def test_explicit_steam_api_disable_never_reads_keyring(tmp_path, monkeypatch):
    home, data_dir, config_dir = fake_home(tmp_path)
    class ForbiddenStore:
        def load(self, _provider):
            raise AssertionError('keyring must not be queried when Steam API is disabled')
    monkeypatch.setenv('WAYSTONE_DISABLE_STEAM_API', '1')
    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir,
                                               steam_session_store=ForbiddenStore())
    assert states['Steam']['ownership_complete'] is False
    assert 'steam:10' in {row['source_key'] for row in records}


def test_sync_add_update_and_source_absence_are_non_destructive(tmp_path):
    home, data_dir, config_dir = fake_home(tmp_path)
    db_path = data_dir / 'library.sqlite3'
    result = sync_library(db_path, home=home, data_dir=data_dir, config_dir=config_dir,
                          observed_at='2026-11-25T00:00:00+00:00')
    assert result['records'] == 11
    assert db_path.stat().st_mode & 0o777 == 0o600
    catalog = load_native_catalog(db_path)
    assert len(catalog['games']) == 11
    assert any(g['title'] == 'Installed Steam Game' and g['variants'][0]['status'] == 'installed'
               for g in catalog['games'])

    # A still-present appmanifest with a missing install directory updates state,
    # while an absent/malformed ownership feed does not destroy prior claims.
    install = home / 'steam-library/steamapps/common/Installed Steam Game'
    install.rmdir()
    sync_library(db_path, home=home, data_dir=data_dir, config_dir=config_dir,
                 observed_at='2026-11-25T00:01:00+00:00')
    catalog = load_native_catalog(db_path)
    steam = next(g for g in catalog['games'] if g['id'] == 'launcher:steam:10')
    assert steam['variants'][0]['status'] == 'not-installed'
    assert steam['variants'][0]['metadata']['provenance']['installation_path'] is None


def test_complete_ownership_feed_marks_missing_claim_not_owned_and_keeps_user_edits(tmp_path):
    home, data_dir, config_dir = fake_home(tmp_path)
    db_path = data_dir / 'library.sqlite3'
    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir)
    LibraryRepository.create_empty(db_path)
    repository = LibraryRepository(db_path, writable=True)
    repository.sync_launcher_records(records, states, '2026-11-25T00:00:00+00:00')
    repository.set_native_metadata('launcher:epic:epic-owned', {'notes': 'User note'})
    repository.close()

    cache = home / '.config/heroic/epic/store_cache/legendary_library.json'
    _json(cache, {'library': [{'app_name': 'epic-key', 'title': 'Epic Installed Game'}]})
    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir)
    repository = LibraryRepository(db_path, writable=True)
    repository.sync_launcher_records(records, states, '2026-11-25T00:01:00+00:00')
    repository.close()
    catalog = load_native_catalog(db_path)
    game = next(g for g in catalog['games'] if g['id'] == 'launcher:epic:epic-owned')
    assert game['variants'][0]['status'] == 'not-owned'
    assert game['variants'][0]['metadata']['notes'] == 'User note'


def test_installed_cache_alone_is_not_authority_to_revoke_cached_ownership(tmp_path):
    home, data_dir, config_dir = fake_home(tmp_path)
    db_path = data_dir / 'library.sqlite3'
    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir)
    LibraryRepository.create_empty(db_path)
    repository = LibraryRepository(db_path, writable=True)
    repository.sync_launcher_records(records, states, '2026-11-25T00:00:00+00:00')
    repository.close()

    # Remove the Epic entitlement feed but leave installed.json. That cache is
    # sufficient only for installation facts, not ownership-absence deltas.
    (home / '.config/heroic/epic/store_cache/legendary_library.json').unlink()
    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir)
    assert states['Epic']['installed_complete'] is True
    assert states['Epic']['ownership_complete'] is False
    repository = LibraryRepository(db_path, writable=True)
    repository.sync_launcher_records(records, states, '2026-11-25T00:01:00+00:00')
    repository.close()
    catalog = load_native_catalog(db_path)
    game = next(g for g in catalog['games'] if g['id'] == 'launcher:epic:epic-owned')
    assert game['variants'][0]['status'] == 'registered'


def test_failed_sync_rolls_back_schema_and_rows_atomically(tmp_path):
    path = tmp_path / 'library.sqlite3'
    LibraryRepository.create_empty(path)
    repo = LibraryRepository(path, writable=True)
    row = {'provider': 'Steam', 'product_id': '1', 'title': 'Game', 'platform': 'PC', 'status': 'installed'}
    try:
        try:
            repo.sync_launcher_records([row], {'Steam': None}, '2026-11-25T00:00:00+00:00')
        except ValueError:
            pass
        else:
            raise AssertionError('invalid source state should abort sync')
        assert repo.db.execute('SELECT count(*) FROM launcher_records').fetchone()[0] == 0
        assert repo.db.execute('SELECT count(*) FROM launcher_sync').fetchone()[0] == 0
    finally:
        repo.close()


def test_optional_steam_account_adds_owned_uninstalled_games_and_never_revokes_on_failure(tmp_path):
    home, data_dir, config_dir = fake_home(tmp_path)
    db_path = data_dir / 'library.sqlite3'

    class SessionStore:
        def load(self, provider):
            assert provider == 'steam'
            return {'kind': 'steam_web', 'account_id': '76561198000000001', 'api_key': 'fixture-secret'}

    class Adapter:
        def __init__(self, account, key):
            assert account == '76561198000000001' and key == 'fixture-secret'
        def fetch_owned_games(self):
            return {'complete': True, 'games': [
                {'product_id': '10', 'title': 'Installed Steam Game', 'playtime_seconds': 3600},
                {'product_id': '30', 'title': 'Steam Owned Only', 'playtime_seconds': 120}]}

    records, states = collect_launcher_library(home, data_dir=data_dir, config_dir=config_dir,
                                                steam_session_store=SessionStore(), steam_web_factory=Adapter)
    by_key = {row['source_key']: row for row in records}
    assert states['Steam']['ownership_complete'] is True
    assert by_key['steam:10']['status'] == 'installed'
    assert by_key['steam:10']['metadata']['playtime_seconds'] == 3600
    assert by_key['steam:30']['status'] == 'registered'

    sync_library(db_path, home=home, data_dir=data_dir, config_dir=config_dir,
                 steam_session_store=SessionStore(), steam_web_factory=Adapter)

    class FailedAdapter(Adapter):
        def fetch_owned_games(self): raise RuntimeError('request URL contains fixture-secret')

    sync_library(db_path, home=home, data_dir=data_dir, config_dir=config_dir,
                 steam_session_store=SessionStore(), steam_web_factory=FailedAdapter)
    catalog = load_native_catalog(db_path)
    owned = next(g for g in catalog['games'] if g['id'] == 'launcher:steam:30')
    assert owned['variants'][0]['status'] == 'registered'
    assert 'fixture-secret' not in json.dumps(catalog)

    # Only a complete successful response may mark an absent non-installed
    # Steam entitlement as not-owned.
    class ChangedAdapter(Adapter):
        def fetch_owned_games(self):
            return {'complete': True, 'games': [{'product_id': '10', 'title': 'Installed Steam Game',
                                                  'playtime_seconds': 3600}]}
    sync_library(db_path, home=home, data_dir=data_dir, config_dir=config_dir,
                 steam_session_store=SessionStore(), steam_web_factory=ChangedAdapter)
    catalog = load_native_catalog(db_path)
    owned = next(g for g in catalog['games'] if g['id'] == 'launcher:steam:30')
    assert owned['variants'][0]['status'] == 'not-owned'


def test_xdg_data_and_config_paths_are_respected(tmp_path):
    home = tmp_path / 'home'; home.mkdir()
    data = tmp_path / 'data'; config = tmp_path / 'config'
    env = {'XDG_DATA_HOME': str(data), 'XDG_CONFIG_HOME': str(config)}
    assert default_library_path(home, env) == data / 'waystone/library.sqlite3'
