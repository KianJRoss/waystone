from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
from native_services.installation_library import catalog_install_choices, merge_cached_choices, InstallPreferences, inspect_destination


def test_exact_store_ids_not_titles_or_internal_guids():
    catalog = {'games': [{'id': 'one', 'title': 'Same title', 'variants': [
        {'source': 'Steam', 'provider_game_id': '123', 'source_id': 'internal-guid'},
        {'source': 'GOG', 'provider_game_id': '123'},
        {'source': 'Steam', 'provider_game_id': 'internal-guid'},
        {'source': 'Steam', 'source_id': '999'},
        {'source': 'Unassigned', 'provider_game_id': '123'}]},
        {'id': 'two', 'title': 'Duplicate import', 'variants': [{'source': 'Steam', 'provider_game_id': '123'}]}]}
    rows = {r['key']: r for r in catalog_install_choices(catalog)}
    assert set(rows) == {'steam:123', 'gog:123'}
    assert rows['steam:123']['game_ids'] == ['one', 'two']
    assert rows['gog:123']['is_installed'] is None


def test_cached_metadata_preserves_exact_game_links_and_provider_isolation():
    original = catalog_install_choices({'games': [{'id': 'g', 'title': 'old', 'variants': [
        {'source': 'GOG', 'provider_game_id': '123'}]}]})
    items = [SimpleNamespace(provider='gog', product_id='123', title='Current cached title', is_installed=False)]
    result = merge_cached_choices(original, items)
    assert result[0]['game_ids'] == ['g'] and result[0]['title'] == 'Current cached title'
    assert result[0]['is_installed'] is False and original[0]['title'] == 'old'


def test_install_preferences_only_write_app_file_on_explicit_save(tmp_path):
    p = tmp_path/'state'/'install.json'; prefs = InstallPreferences(p)
    with patch('os.path.ismount', return_value=True), patch('os.access', return_value=True):
        assert prefs.load() == '/mnt/linux-data/Games/Waystone'
    assert not p.exists()
    prefs.save('/somewhere/Games'); assert prefs.load() == '/somewhere/Games'
    assert p.stat().st_mode & 0o777 == 0o600
    p.write_text('invalid'); assert Path(prefs.load()).is_absolute()


def test_bad_ids_do_not_become_install_commands():
    for product in ('0', '--delete', '../escape', 'steam://run/123', '4294967296', '', None):
        assert not catalog_install_choices({'games': [{'id': 'x', 'variants': [
            {'source': 'Steam', 'provider_game_id': product}]}]})


def test_storage_preflight_does_not_create_folders(tmp_path):
    target=tmp_path/'not-created'/'nested'
    with patch('shutil.disk_usage',return_value=SimpleNamespace(free=2**30)):
        result=inspect_destination(str(target))
        assert result['ready'] and result['free_bytes']==2**30
    assert not target.exists()
    existing=tmp_path/'file';existing.write_text('kept')
    assert not inspect_destination(str(existing))['ready']
    for bad in ('','relative/path','\x00'):
        assert not inspect_destination(bad)['ready']
    with patch('os.access',return_value=False):assert not inspect_destination(str(target))['ready']
    with patch('shutil.disk_usage',side_effect=OSError('fixture')):assert not inspect_destination(str(target))['ready']
    with patch('shutil.disk_usage',return_value=SimpleNamespace(free=0)):assert not inspect_destination(str(target))['ready']
