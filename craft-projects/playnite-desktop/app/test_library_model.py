import ast
import json
import tempfile
import unittest
from pathlib import Path
from PySide6.QtCore import QModelIndex, Qt
from library_model import LibraryModel, load_fixture
from snapshot_bridge import load_export, load_exports, key

HERE = Path(__file__).parent

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.games = load_fixture(HERE / 'fixture-library.json')
        self.model = LibraryModel(self.games)

    def test_all_fixture_games_visible(self):
        self.assertEqual(self.model.rowCount(), 8)

    def test_distinct_identical_titles_not_collapsed(self):
        self.model.setFilters('Demo Adventure', 'All sources', 'All platforms', 'All states')
        self.assertEqual(self.model.rowCount(), 2)
        self.assertNotEqual(self.model.visible[0]['id'], self.model.visible[1]['id'])

    def test_case_insensitive_search(self):
        self.model.setFilters('rAcInG', 'All sources', 'All platforms', 'All states')
        self.assertEqual(self.model.visible[0]['id'], 'demo-racing')

    def test_source_filter_keeps_full_variant_evidence(self):
        self.model.setFilters('', 'Steam', 'All platforms', 'All states')
        self.assertEqual(self.model.rowCount(), 1)
        self.assertIn('Heroic', self.model.details('demo-adventure'))

    def test_ribbon_keys_cover_native_store_ids_then_exact_title_fallback(self):
        games = json.loads((HERE / 'fixture-library.json').read_text())['games']
        games[0]['variants'][0]['provider_game_id'] = '239030'
        games[0]['variants'][1]['source'] = 'GOG'
        games[0]['variants'][1]['provider_game_id'] = '1207658691'
        games[0]['variants'].append({'source': 'Epic Games Store', 'source_id': 'epic-copy',
                                     'provider_game_id': 'App-1', 'platform': 'Windows',
                                     'status': 'unknown', 'installation_id': 'fixture-epic'})
        model = LibraryModel(games)
        keys = model._ribbon_keys(model.games[0])
        self.assertEqual(keys[:3], [('steam', '239030'), ('gog', '1207658691'), ('epic', 'App-1')])
        provider, payload = keys[-1]
        self.assertEqual(provider, 'steamgriddb')
        self.assertEqual(json.loads(payload), {'steam_app_id': '239030', 'title': 'Demo Adventure'})

    def test_manual_ribbon_override_wins_over_fetched_cache(self):
        class Overrides:
            def artworkUrl(self, _game_id, role):
                return 'image://manual/hero' if role == 'hero' else ''
        class Cache:
            def lookup(self, _provider, _product_id):
                return {'hero': '/tmp/fetched-hero.png', 'logo': '/tmp/fetched-logo.png'}
        model = LibraryModel(self.games)
        model._artwork_view = Overrides()
        model._ribbon_cache = Cache()
        urls = model._urls(model.games[0])
        self.assertEqual(urls['heroUrl'], 'image://manual/hero')
        self.assertTrue(urls['logoUrl'].endswith('fetched-logo.png'))

    def test_request_skips_fetch_when_manual_hero_and_logo_exist(self):
        class Overrides:
            def artworkUrl(self, _game_id, role):
                return 'image://manual/' + role if role in ('hero', 'logo', 'cover') else ''
        class Cache:
            def lookup(self, _provider, _product_id):
                return {}
        class Fetcher:
            def __init__(self): self.requests = []
            def request(self, provider, product_id): self.requests.append((provider, product_id))
        model = LibraryModel(self.games)
        model._artwork_view = Overrides()
        model._ribbon_cache = Cache()
        fetcher = Fetcher()
        model.configureRibbons(Cache(), fetcher)
        model.requestRibbon(self.games[0]['id'])
        self.assertEqual(fetcher.requests, [])

    def test_unknown_is_not_installed(self):
        self.model.setFilters('', 'Lichii', 'All platforms', 'installed')
        self.assertEqual(self.model.rowCount(), 0)
        self.model.setFilters('', 'Lichii', 'All platforms', 'unknown')
        self.assertEqual(self.model.rowCount(), 1)

    def test_filters_must_match_same_variant(self):
        self.model.setFilters('', 'Steam', 'Windows', 'All states')
        self.assertEqual(self.model.rowCount(), 0)

    def test_platform_filter(self):
        self.model.setFilters('', 'All sources', 'GameCube', 'All states')
        self.assertEqual(self.model.visible[0]['id'], 'demo-racing')

    def test_metadata_collections_are_explicit_and_filter_by_variant(self):
        games = json.loads((HERE / 'fixture-library.json').read_text())['games']
        games[0]['variants'][0]['metadata'] = {'genres': ['Adventure', 'Co-op']}
        games[0]['variants'][1]['metadata'] = {'genres': ['Action']}
        games[1]['variants'][0]['metadata'] = {'genres': ['Adventure']}
        model = LibraryModel(games)
        self.assertEqual(model.collectionValues('genres'), ['Action', 'Adventure', 'Co-op'])
        self.assertEqual(model.facetCount('genres', 'Adventure'), 2)
        model.setFilters('', 'Heroic', 'Windows', 'unknown')
        model.setCollectionFilter('genres', 'Adventure')
        self.assertEqual(model.visible, [])  # the other copy has Action, not the chosen genre
        model.setFilters('', 'Steam', 'Linux', 'installed')
        model.setCollectionFilter('genres', 'Adventure')
        self.assertEqual([game['id'] for game in model.visible], ['demo-adventure'])
        model.setCollectionFilter('', '')
        self.assertEqual(model.rowCount(), 1)

    def test_collection_facet_rejects_untrusted_field_and_does_not_infer(self):
        self.assertEqual(self.model.collectionValues('genres'), [])
        with self.assertRaises(ValueError):
            self.model.setCollectionFilter('title', 'Demo Adventure')

    def test_multi_facets_or_within_field_and_and_on_same_variant(self):
        games = json.loads((HERE / 'fixture-library.json').read_text())['games']
        games[0]['variants'][0]['metadata'] = {
            'genres': ['Adventure', 'Action'], 'tags': ['Controller Friendly'], 'categories': ['Backlog']}
        games[0]['variants'][1]['metadata'] = {
            'genres': ['Adventure'], 'tags': ['Solo'], 'categories': ['Completed']}
        games[1]['variants'][0]['metadata'] = {
            'genres': ['Adventure'], 'tags': ['Controller Friendly'], 'categories': ['Completed']}
        model = LibraryModel(games)
        model.setCollectionFilters({'genres': ['Action', 'Adventure'], 'tags': ['Controller Friendly'],
                                    'categories': ['Backlog']})
        # The first Steam variant satisfies all filters; no combination of a
        # different record or different account/source copy may fill a gap.
        self.assertEqual([game['id'] for game in model.visible], ['demo-adventure'])
        model.setCollectionFilters({'tags': ['Controller Friendly', 'Solo']})
        self.assertEqual({game['id'] for game in model.visible}, {'demo-adventure', 'demo-adventure-remaster'})
        model.setCollectionFilters({'tags': ['Controller Friendly'], 'categories': ['Backlog']})
        self.assertEqual([game['id'] for game in model.visible], ['demo-adventure'])
        model.setCollectionFilters({})
        self.assertEqual(model.rowCount(), 8)

    def test_multi_facet_validation_fails_closed(self):
        for filters in ({'unknown': ['x']}, {'tags': 'not-a-list'}, {'tags': [None]}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.model.setCollectionFilters(filters)

    def test_favorites_use_explicit_boolean_metadata_only(self):
        games = json.loads((HERE / 'fixture-library.json').read_text())['games']
        games[0]['variants'][0]['metadata'] = {'favorite': True}
        games[1]['variants'][0]['metadata'] = {'favorite': False}
        model = LibraryModel(games)
        self.assertEqual(model.collectionValues('favorite'), ['Favorite', 'Not favorite'])
        model.setCollectionFilter('favorite', 'Favorite')
        self.assertEqual([game['id'] for game in model.visible], ['demo-adventure'])
        model.setCollectionFilter('favorite', 'Not favorite')
        self.assertEqual([game['id'] for game in model.visible], ['demo-adventure-remaster'])

    def test_completion_visibility_release_decade_playtime_and_play_count_facets(self):
        games = json.loads((HERE / 'fixture-library.json').read_text())['games']
        games[0]['variants'][0]['metadata'] = {
            'completion': 'Played', 'hidden': True, 'release_date': '1996-04-01',
            'playtime_seconds': 7200, 'play_count': 3}
        games[1]['variants'][0]['metadata'] = {
            'completion': 'Not Played', 'hidden': False, 'release_date': '2013',
            'playtime_seconds': 0, 'play_count': 0}
        model = LibraryModel(games)
        self.assertEqual(model.collectionValues('completion'), ['Not Played', 'Played'])
        self.assertEqual(model.collectionValues('hidden'), ['Hidden', 'Not hidden'])
        self.assertEqual(model.collectionValues('release_decade'), ['1990s', '2010s'])
        self.assertEqual(model.collectionValues('playtime_bucket'), ['0 minutes', '1–10 hours'])
        self.assertEqual(model.collectionValues('play_count'), ['0', '3'])
        model.setCollectionFilters({'release_decade': ['1990s'], 'playtime_bucket': ['1–10 hours']})
        self.assertEqual([game['id'] for game in model.visible], ['demo-adventure'])
        self.assertEqual(model.facetCount('hidden', 'Hidden'), 1)

    def test_approved_identity_link_groups_display_but_preserves_source_records(self):
        original = json.loads(json.dumps(self.games))
        self.model.setIdentityLinks({'demo-adventure-remaster': 'demo-adventure'})
        self.assertEqual(self.model.totalCount, 7)
        self.assertEqual(self.model.sourceRecordCount, 8)
        self.assertEqual(self.model.rowOf('demo-adventure-remaster'), self.model.rowOf('demo-adventure'))
        group = next(g for g in self.model.games if g['id'] == 'demo-adventure')
        self.assertEqual(set(group['linked_record_ids']), {'demo-adventure', 'demo-adventure-remaster'})
        self.assertEqual(len(group['variants']), 3)
        self.assertIn('demo-adventure-remaster', self.model.details('demo-adventure'))
        self.assertEqual(self.games, original)
        self.model.setIdentityLinks({})
        self.assertEqual(self.model.totalCount, 8)

    def test_identity_link_overlay_rejects_unknown_and_chained_ids(self):
        for mapping in ({'missing': 'demo-adventure'},
                        {'demo-adventure-remaster': 'demo-adventure', 'demo-adventure': 'demo-racing'}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                self.model.setIdentityLinks(mapping)
        self.assertEqual(self.model.totalCount, 8)

    def test_link_representative_identity_independent_of_import_order(self):
        self.model.setIdentityLinks({'demo-adventure': 'demo-adventure-remaster'})
        group = next(g for g in self.model.games if g['id'] == 'demo-adventure-remaster')
        representative = next(g for g in self.games if g['id'] == 'demo-adventure-remaster')
        self.assertEqual(group['title'], representative['title'])
        self.assertEqual(len(group['variants']), 3)
        self.assertEqual(self.model.rowOf('demo-adventure'), self.model.rowOf('demo-adventure-remaster'))
        self.assertEqual(self.model.overview('demo-adventure')['id'], 'demo-adventure-remaster')

    def test_grouping_retains_member_artwork_without_modifying_imports(self):
        self.games[0]['artwork'] = {'cover': {'status': 'local_present', 'relative_reference': 'member/cover.jpg'}}
        before = json.dumps(self.games, sort_keys=True)
        self.model.setIdentityLinks({'demo-adventure': 'demo-adventure-remaster'})
        group = next(g for g in self.model.games if g['id'] == 'demo-adventure-remaster')
        self.assertTrue(any(art.get('cover', {}).get('relative_reference') ==
                            'member/cover.jpg' for art in group['linked_member_artwork']))
        self.assertEqual(json.dumps(self.games, sort_keys=True), before)

    def test_linked_member_local_overrides_remain_visible_and_editable(self):
        with tempfile.TemporaryDirectory() as directory:
            self.model.configure_organizer(Path(directory) / 'organizer.sqlite3')
            self.model.previewOrganizer('demo-adventure-remaster', '["user-member-tag"]', True)
            self.model.applyOrganizer('demo-adventure-remaster')
            self.model.setIdentityLinks({'demo-adventure-remaster': 'demo-adventure'})
            view = self.model.overview('demo-adventure')
            self.assertIn('user-member-tag', view['tags'])
            self.assertIn('Yes', view['favorite'])
            self.assertEqual(self.model.overview('demo-adventure-remaster'), view)
            self.assertEqual(len(view['editTargets']), 2)
            self.assertIn('user-member-tag', self.model.organizerEditValues('demo-adventure-remaster')['tags'])
            self.assertNotIn('user-member-tag', self.model.organizerEditValues('demo-adventure')['tags'])
            self.model.previewOrganizer('demo-adventure-remaster', '["revised-member-tag"]', False)
            self.model.applyOrganizer('demo-adventure-remaster')
            self.assertIn('revised-member-tag', self.model.overview('demo-adventure')['tags'])
            self.model.setCollectionFilter('tags', 'revised-member-tag')
            self.assertEqual(self.model.rowCount(), 1)

    def test_zero_result(self):
        self.model.setFilters('missinggame', 'All sources', 'All platforms', 'All states')
        self.assertEqual(self.model.rowCount(), 0)

    def test_clear_filters(self):
        self.model.setFilters('missinggame', 'All sources', 'All platforms', 'All states')
        self.model.setFilters('', 'All sources', 'All platforms', 'All states')
        self.assertEqual(self.model.rowCount(), 8)

    def test_invalid_index(self):
        self.assertIsNone(self.model.data(QModelIndex()))
        self.assertIsNone(self.model.data(self.model.index(100, 0)))

    def test_parent_index_has_no_children(self):
        self.assertEqual(self.model.rowCount(self.model.index(0, 0)), 0)

    def test_qt_roles(self):
        roles = {v.decode(): k for k, v in self.model.roleNames().items()}
        first = self.model.index(0, 0)
        self.assertEqual(self.model.data(first, roles['installState']), 'mixed')
        self.assertEqual(self.model.data(first, roles['variantCount']), 2)
        self.assertEqual(self.model.data(first, Qt.DisplayRole), 'Demo Adventure')

    def test_argv_spaces_preserved_as_data(self):
        self.assertIn('"fixture instance"', self.model.details('demo-builder'))
        self.assertIn('text only', self.model.details('demo-builder'))

    def test_unknown_selection(self):
        self.assertIn('Select a game', self.model.details('not-here'))

    def test_details_title_lookup_is_read_only(self):
        self.assertEqual(self.model.titleFor('demo-racing'), 'Demo Racing')
        self.assertEqual(self.model.titleFor('unknown-work'), '')

    def test_launcher_refresh_qml_keeps_actions_inert(self):
        qml = (HERE / 'Main.qml').read_text()
        for forbidden in ['openUrlExternally', 'Qt.openUrlExternally', 'Process {', 'onPlayClicked', 'onInstallClicked']:
            self.assertNotIn(forbidden, qml)
        self.assertIn('No artwork imported', (HERE / 'MissingCover.qml').read_text())
        self.assertIn('collectionFieldNames.filter(field => libraryModel.collectionValues(field).length > 0)', qml)
        self.assertIn('tags: "Tags"', qml)
        self.assertIn('Read-only', qml)

    def check_invalid(self, mutate):
        data = json.loads((HERE / 'fixture-library.json').read_text())
        mutate(data)
        with tempfile.TemporaryDirectory(dir=HERE) as root:
            p = Path(root) / 'bad.json'; p.write_text(json.dumps(data))
            with self.assertRaises(ValueError): load_fixture(p)

    def test_duplicate_work_id_rejected(self):
        self.check_invalid(lambda d: d['games'][1].update(id=d['games'][0]['id']))

    def test_duplicate_source_identity_rejected(self):
        self.check_invalid(lambda d: d['games'][1].update(variants=d['games'][0]['variants']))

    def test_same_record_distinct_installations_preserved(self):
        data = json.loads((HERE / 'fixture-library.json').read_text())
        second = dict(data['games'][0]['variants'][0])
        second['installation_id'] = 'other-drive'
        data['games'][0]['variants'].append(second)
        with tempfile.TemporaryDirectory(dir=HERE) as root:
            p = Path(root) / 'two-installs.json'; p.write_text(json.dumps(data))
            self.assertEqual(len(load_fixture(p)[0]['variants']), 3)

    def test_invalid_installation_id_rejected(self):
        self.check_invalid(lambda d: d['games'][0]['variants'][0].update(installation_id=''))

    def test_invalid_status_rejected(self):
        self.check_invalid(lambda d: d['games'][0]['variants'][0].update(status='probably'))

    def test_shell_recipe_not_supported(self):
        self.check_invalid(lambda d: d['games'][0]['variants'][0].update(recipe='sh -c do-stuff'))

    def test_unknown_schema_rejected(self):
        self.check_invalid(lambda d: d.update(schema_version=2))

    def test_no_launch_or_network_implementation(self):
        for name in ['app.py', 'library_model.py']:
            tree = ast.parse((HERE / name).read_text())
            imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            self.assertFalse(any(x and any(p in x for p in ['subprocess', 'urllib', 'socket', 'webbrowser', 'QtNetwork']) for x in imports))
        qml = (HERE / 'Main.qml').read_text()
        self.assertNotIn('openUrlExternally', qml)
        self.assertNotIn('Process', qml)

class SnapshotBridgeTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((HERE / 'fixtures/demo-snapshot.json').resolve().read_text())

    def write_snapshot(self, root, name, data):
        path = Path(root) / name
        path.write_text(json.dumps(data))
        return path

    def test_single_export_api_stays_compatible(self):
        games, context = load_export(HERE / 'fixtures/demo-snapshot.json', with_context=True)
        self.assertEqual(len(games), 2)
        self.assertEqual(context['provider'], 'steam')
        self.assertTrue(context['fixture_mode'])

    def test_multiple_explicit_exports_merge_by_stable_identity_only(self):
        second = json.loads(json.dumps(self.data))
        second['source']['host'] = 'another-fixture-host'
        second['observed_at'] = '2026-11-15T01:00:00+00:00'
        for row in second['records']:
            record_key = key(['steam', second['source']['instance'], row['external_id']])
            row['record_key'] = record_key
            row['variant_key'] = key([record_key, second['source']['host'], row['installation_id']])
        with tempfile.TemporaryDirectory() as root:
            first_path = self.write_snapshot(root, 'one.json', self.data)
            second_path = self.write_snapshot(root, 'two.json', second)
            games, contexts = load_exports([first_path, second_path], with_context=True)
        self.assertEqual(len(games), 2)
        self.assertEqual(len(contexts), 2)
        repeated_record = max(games, key=lambda game: len(game['variants']))
        self.assertEqual(len(repeated_record['variants']), 4)
        self.assertEqual({v['source_context']['host'] for v in repeated_record['variants']}, {'fixture-host', 'another-fixture-host'})

    def test_duplicate_export_variant_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.write_snapshot(root, 'one.json', self.data)
            with self.assertRaisesRegex(ValueError, 'Duplicate variant'):
                load_exports([path, path])

    def test_same_title_with_different_source_ids_is_not_merged(self):
        data = json.loads(json.dumps(self.data))
        extra = json.loads(json.dumps(data['records'][0]))
        extra['external_id'] = '789'
        extra['title'] = next(row['title'] for row in data['records'] if row['external_id'] == '456')
        extra['record_key'] = key(['steam', data['source']['instance'], '789'])
        extra['variant_key'] = key([extra['record_key'], data['source']['host'], extra['installation_id']])
        data['records'].append(extra)
        with tempfile.TemporaryDirectory() as root:
            path = self.write_snapshot(root, 'same-title-different-id.json', data)
            games = load_exports([path])
        self.assertEqual(len(games), 3)
        self.assertEqual(sum(game['title'] == 'Synthetic Registered Entry' for game in games), 2)

    def test_export_count_limit(self):
        with self.assertRaisesRegex(ValueError, 'between 1 and'):
            load_exports([])
        with self.assertRaisesRegex(ValueError, 'between 1 and'):
            load_exports([HERE / 'fixtures/demo-snapshot.json'] * 17)

if __name__ == '__main__': unittest.main(verbosity=2)
