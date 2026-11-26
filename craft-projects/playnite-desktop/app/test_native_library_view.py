"""Offline native-library projections; synthetic inventories, no live discovery."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from connection_view import ConnectionView
from native_library_view import ALL_PROVIDERS, MAX_PROJECTION, NativeLibraryView


def key(text):
    return hashlib.sha256(text.encode()).hexdigest()


def native(number, *, provider='steam', product=None, state='installed',
           title='Same title', installation='disk-a', account='account-a',
           record_key=None):
    product = str(number + 1) if product is None else product
    record_key = record_key or key(f'{provider}/{product}/{account}')
    variant_key = key(f'{record_key}/{installation}/{number}')
    return dict(provider=provider, product_id=product, record_key=record_key,
                variant_key=variant_key, title=title, state=state, platform=None,
                source_context=dict(host='offline-host', instance='fixture',
                                    installation_id=installation, account_id=account),
                evidence=['synthetic declaration and directory'],
                current_claims=dict(installation=state, client_only=state == 'client-only',
                                    gameplay_tested=False, verification_scope='metadata plus directory presence'),
                launch_intent=dict(provider=provider, variant_key=variant_key,
                                   dry_run=True, execution_supported=False))


def inventory(records, matched=()):
    """Make a small supported Steam catalog, including explicit collision cases."""
    catalog = {'games': [{'id': 'canonical-' + product, 'variants': [
        {'source': 'Steam', 'provider_game_id': product}]} for product in matched]}
    data = dict(schema_version=1, inventory_kind='native-connections-readonly',
                execution_supported=False, host='offline-host', observed_at='offline-fixture',
                catalog_sha256=key('catalog'), connections=copy.deepcopy(records),
                canonical_links={}, unmatched_native_variant_keys=[], ambiguous_native_variant_keys=[])
    owners = {}
    for row in records:
        owners.setdefault((row['provider'], row['product_id']), set()).add(row['record_key'])
    for row in data['connections']:
        provider, product = row['provider'], row['product_id']
        aliases = [{'source': 'Steam' if provider == 'steam' else 'Epic', 'provider_game_id': product}]
        if provider not in ('steam', 'heroic-epic'):
            aliases = []
        join = ('ambiguous' if aliases and len(owners[(provider, product)]) > 1
                else 'matched' if provider == 'steam' and product in matched else 'unmatched')
        canonical = ['canonical-' + product] if join == 'matched' else []
        row.update(join_state=join, canonical_game_ids=canonical, join_evidence=aliases)
        for game_id in canonical:
            data['canonical_links'].setdefault(game_id, []).append(row['variant_key'])
        if join != 'matched':
            data[join + '_native_variant_keys'].append(row['variant_key'])
    return data, catalog


class NativeLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthetic-connections.json'

    def connection_view(self, records, matched=()):
        data, catalog = inventory(records, matched)
        self.path.write_text(json.dumps(data, ensure_ascii=False))
        # Exercise the real validation boundary, never bypass load_inventory.
        return ConnectionView(self.path, catalog, key('catalog'))

    def test_live_count_semantics_are_synthetic_and_unmatched_installed_remain(self):
        records = [native(i) for i in range(55)]
        records.append(native(55, provider='lutris-battlenet', state='client-only'))
        connection = self.connection_view(records, {str(i + 1) for i in range(47)})
        view = NativeLibraryView(connection)
        self.assertEqual(view.count, 55)
        self.assertEqual(len(view.rows), 50)
        self.assertIn('55 installed / 56 total', view.notice)
        self.assertIn('1 client-only', view.notice)
        self.assertIn('8 unmatched installed (retained)', view.notice)
        self.assertEqual(view.providers, [ALL_PROVIDERS, 'lutris-battlenet', 'steam'])
        all_rows = list(view.rows)
        view.page(1)
        all_rows += view.rows
        self.assertEqual(sum(r['joinState'] == 'unmatched' for r in all_rows), 8)
        self.assertEqual({r['variantKey'] for r in all_rows}, {r['variant_key'] for r in records[:55]})
        view.setFilters(ALL_PROVIDERS, '', False)
        self.assertEqual(view.count, 56)
        view.setFilters('lutris-battlenet', '', False)
        self.assertEqual(view.rows[0]['state'], 'client-only')
        view.select(records[-1]['variant_key'])
        self.assertIn('no game inventory', json.loads(view.detail)['observation'])

    def test_same_product_installations_and_accounts_are_not_collapsed(self):
        records = [native(0, product='42', installation='disk-a'),
                   native(1, product='42', installation='disk-b'),
                   native(2, product='42', account='account-b')]
        view = NativeLibraryView(self.connection_view(records, {'42'}))
        self.assertEqual(view.count, 3)
        self.assertEqual(len({r['variantKey'] for r in view.rows}), 3)
        self.assertEqual(len({r['recordKey'] for r in view.rows}), 2)
        self.assertTrue(all(r['joinState'] == 'ambiguous' and r['state'] == 'installed' for r in view.rows))
        for record in records:
            view.select(record['variant_key'])
            detail = json.loads(view.detail)
            self.assertEqual(detail['source_context']['installation_id'], record['source_context']['installation_id'])
            self.assertEqual(detail['source_context']['account_id'], record['source_context']['account_id'])
            self.assertEqual(detail['canonical_game_ids'], [])

    def test_pagination_boundaries_and_filter_reset(self):
        records = [native(i) for i in range(101)]
        view = NativeLibraryView(self.connection_view(records))
        self.assertEqual(view.pageLabel, '1–50 of 101 native records')
        self.assertFalse(view.canPrevious)
        view.page(-1)
        self.assertEqual(len(view.rows), 50)
        seen = [r['variantKey'] for r in view.rows]
        view.page(1)
        self.assertEqual(view.pageLabel, '51–100 of 101 native records')
        seen.extend(r['variantKey'] for r in view.rows)
        view.page(1)
        seen.extend(r['variantKey'] for r in view.rows)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 101)
        self.assertEqual(view.pageLabel, '101–101 of 101 native records')
        self.assertFalse(view.canNext)
        view.page(1)
        self.assertEqual(len(view.rows), 1)
        view.page(-1)
        self.assertEqual(view.pageLabel, '51–100 of 101 native records')
        view.select(records[80]['variant_key'])
        view.setFilters('steam', '101', True)
        self.assertEqual(view.count, 1)
        self.assertEqual(view.detail, '')
        self.assertFalse(view.canPrevious)
        view.setFilters('unknown-provider', '', True)
        self.assertEqual(view.count, 0)
        self.assertEqual(view.pageLabel, '0–0 of 0 native records')
        self.assertFalse(view.canNext)

    def test_unverified_is_unknown_not_uninstalled_and_exact_links_are_retained(self):
        records = [native(0), native(1, state='unverified'),
                   native(2, provider='heroic-epic', product='1')]
        view = NativeLibraryView(self.connection_view(records, {'1'}))
        self.assertEqual(view.count, 2)
        first = next(row for row in view.rows if row['variantKey'] == records[0]['variant_key'])
        self.assertEqual(first['canonicalGameIds'], ['canonical-1'])
        self.assertEqual(next(row for row in view.rows if row['variantKey'] == records[2]['variant_key'])['canonicalGameIds'], [])
        view.select(records[0]['variant_key'])
        self.assertEqual(json.loads(view.detail)['canonical_game_ids'], ['canonical-1'])
        view.setFilters(ALL_PROVIDERS, '', False)
        self.assertEqual(view.count, 3)
        self.assertEqual(next(row for row in view.rows if row['variantKey'] == records[1]['variant_key'])['state'], 'unverified')
        view.select(records[1]['variant_key'])
        self.assertIn('installation unknown', view.detail)
        self.assertNotIn('uninstalled', view.detail)
        view.setFilters('Heroic / Epic', 'SAME TITLE', True)
        self.assertEqual(view.count, 1)
        self.assertEqual(view.rows[0]['providerKey'], 'heroic-epic')

    def test_refresh_replaces_snapshot_preserves_filters_and_resets_selection(self):
        initial = self.connection_view([native(i) for i in range(52)])
        view = NativeLibraryView(initial)
        view.setFilters('steam', 'same', False)
        view.page(1)
        view.select(initial._records[0]['variant_key'])
        replacement = self.connection_view([native(100, state='unverified'),
                                            native(101, provider='heroic-epic'),
                                            native(102, title='Not matching')])
        view.refreshFrom(replacement)
        self.assertEqual(view.count, 1)
        self.assertEqual(view.rows[0]['state'], 'unverified')
        self.assertFalse(view.canPrevious)
        self.assertEqual(view.detail, '')
        view.select(initial._records[0]['variant_key'])
        self.assertEqual(view.detail, '')
        view.refreshFrom(ConnectionView())
        self.assertEqual(view.rows, [])
        self.assertEqual(view.providers, [ALL_PROVIDERS])
        self.assertIn('not selected', view.notice)

    def test_wrong_shapes_fail_closed_without_partial_records_or_stale_selection(self):
        valid = self.connection_view([native(0), native(1)])
        view = NativeLibraryView(valid)
        for wrong in (None, {}, [], object(), {'_records': valid._records, '_data': valid._data}):
            view.refreshFrom(wrong)
            self.assertEqual(view.rows, [])
            self.assertEqual(view.count, 0)
            self.assertIn('unavailable', view.notice)
        mutations = [lambda v: setattr(v, '_records', {}),
                     lambda v: v._data.update(connections=[]),
                     lambda v: v._records.append(None),
                     lambda v: v._records.append(copy.deepcopy(v._records[0])),
                     lambda v: v._records[1].update(state='uninstalled'),
                     lambda v: v._records[1].update(provider=[]),
                     lambda v: v._records[1].update(source_context=[]),
                     lambda v: v._records[1].update(canonical_game_ids='fake-id'),
                     lambda v: v._data.update(canonical_links=[]),
                     lambda v: v._data.update(unmatched_native_variant_keys=[])]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                candidate = self.connection_view([native(0), native(1)])
                view.refreshFrom(candidate)
                view.select(candidate._records[0]['variant_key'])
                mutate(candidate)
                view.refreshFrom(candidate)
                self.assertEqual(view.count, 0)
                self.assertEqual(view.rows, [])
                self.assertEqual(view.detail, '')
                self.assertIn('unavailable', view.notice)

    def test_plain_bounded_unicode_detached_projection_and_no_execution(self):
        records = [native(i, title='<img src="https://invalid.test">' + '😀' * 4000) for i in range(51)]
        for record in records:
            record['source_context'].update(recipe=['DO-NOT-EXPOSE'], argv=['DO-NOT-EXPOSE'],
                                            account_name='😀' * 10000,
                                            metadata={'secret': 'DO-NOT-EXPOSE'})
            record['evidence'] = ['DO-NOT-EXPOSE']
            record['launch_intent']['argv'] = ['DO-NOT-EXPOSE']
        connection = self.connection_view(records, {'1'})
        before = copy.deepcopy(connection._data)
        file_before = self.path.read_bytes()
        with patch('subprocess.Popen', side_effect=AssertionError('No processes')), \
             patch('os.system', side_effect=AssertionError('No shell')), \
             patch('socket.socket', side_effect=AssertionError('No network')), \
             patch('builtins.open', side_effect=AssertionError('Projection reads no files')):
            view = NativeLibraryView(connection)
            self.assertEqual(len(view.rows), 50)
            self.assertLessEqual(len(json.dumps(view.rows).encode()), MAX_PROJECTION)
            self.assertIn('<img', view.rows[0]['title'])
            returned = view.rows
            returned[0]['canonicalGameIds'].append('caller-edit')
            returned[0]['title'] = 'caller-edit'
            self.assertNotIn('caller-edit', json.dumps(view.rows))
            view.select(records[0]['variant_key'])
            self.assertLessEqual(len(view.detail.encode()), MAX_PROJECTION)
            self.assertNotIn('DO-NOT-EXPOSE', view.detail)
            self.assertNotIn('recipe', view.detail)
            self.assertNotIn('argv', view.detail)
            self.assertEqual(json.loads(view.detail)['variant_key'], records[0]['variant_key'])
            view.page(1)
            self.assertEqual(len(view.rows), 1)
            view.select('unknown-key')
            self.assertEqual(view.detail, '')
            view.refreshFrom(connection)
        self.assertEqual(connection._data, before)
        self.assertEqual(self.path.read_bytes(), file_before)
        connection._records[0]['title'] = 'source-edit'
        connection._records[0]['canonical_game_ids'].append('source-edit')
        connection._records[0]['source_context']['account_id'] = 'source-edit'
        self.assertNotIn('source-edit', json.dumps(view.rows))
        view.select(records[0]['variant_key'])
        self.assertNotIn('source-edit', view.detail)

    def test_qt_contract_and_notify(self):
        from PySide6.QtTest import QSignalSpy
        view = NativeLibraryView(self.connection_view([native(0)]))
        meta = view.metaObject()
        for name in ('rows', 'providers', 'detail', 'pageLabel', 'canNext', 'canPrevious', 'notice', 'count'):
            self.assertTrue(meta.property(meta.indexOfProperty(name)).hasNotifySignal())
        for signature in ('setFilters(QString,QString,bool)', 'select(QString)', 'page(int)', 'refreshFrom(QObject*)'):
            self.assertNotEqual(meta.indexOfMethod(signature), -1)
        spy = QSignalSpy(view.changed)
        view.setFilters(ALL_PROVIDERS, '', True)
        view.select(view.rows[0]['variantKey'])
        view.page(1)
        view.refreshFrom(ConnectionView())
        self.assertEqual(spy.count(), 4)

    def test_large_canonical_ids_are_exact_and_byte_bounded_pages_lose_no_records(self):
        records = [native(i) for i in range(3)]
        data, catalog = inventory(records, {'1', '2', '3'})
        # Valid catalog identities need not be short UUIDs. Preserve them even
        # when the byte budget, rather than the 50-record limit, ends a page.
        games = {game['id']: game for game in catalog['games']}
        for row in data['connections']:
            game = games[row['canonical_game_ids'][0]]
            old = game['id']
            game['id'] = old + '😀' * 4000
            row['canonical_game_ids'] = [game['id']]
            data['canonical_links'][game['id']] = data['canonical_links'].pop(old)
            row['source_context']['account_name'] = '😀' * 10000
        self.path.write_text(json.dumps(data, ensure_ascii=False))
        view = NativeLibraryView(ConnectionView(self.path, catalog, key('catalog')))
        seen = []
        while True:
            self.assertGreater(len(view.rows), 0)
            self.assertLessEqual(len(json.dumps(view.rows).encode()), MAX_PROJECTION)
            for row in view.rows:
                seen.append(row['variantKey'])
                view.select(row['variantKey'])
                self.assertEqual(json.loads(view.detail)['canonical_game_ids'], row['canonicalGameIds'])
                self.assertLessEqual(len(view.detail.encode()), MAX_PROJECTION)
            if not view.canNext:
                break
            view.page(1)
        self.assertEqual(seen, sorted(r['variant_key'] for r in records))
        view.page(-1)
        view.page(-1)
        self.assertFalse(view.canPrevious)
        self.assertEqual(view.rows[0]['variantKey'], seen[0])

    def test_title_casefold_then_identity_sort_is_stable_across_pages_and_refresh(self):
        records = [native(i, title=('Zulu', 'alpha', 'ALPHA', 'Straße', 'STRASSE')[i % 5])
                   for i in range(53)]
        expected = [r['variant_key'] for r in sorted(records, key=lambda r: (r['title'].casefold(), r['variant_key']))]
        connection = self.connection_view(records)
        before = copy.deepcopy(connection._records)
        view = NativeLibraryView(connection)
        first_page = [r['variantKey'] for r in view.rows]
        view.page(1)
        self.assertEqual(first_page + [r['variantKey'] for r in view.rows], expected)
        view.page(-1)
        self.assertEqual([r['variantKey'] for r in view.rows], expected[:50])
        self.assertEqual(connection._records, before)
        view.refreshFrom(self.connection_view(list(reversed(records))))
        self.assertEqual([r['variantKey'] for r in view.rows], expected[:50])
        view.setFilters('steam', 'alpha', True)
        self.assertEqual([r['variantKey'] for r in view.rows],
                         [r['variant_key'] for r in sorted(records, key=lambda r: (r['title'].casefold(), r['variant_key']))
                          if 'alpha' in r['title'].casefold()])


if __name__ == '__main__':
    unittest.main()

def test_records_for_game_exact_installed_detached_and_not_filtered():
    from connection_view import ConnectionView
    from native_library_view import NativeLibraryView
    import tempfile
    data,catalog=inventory([native(0,product='1'),native(1,product='2')],matched=('1','2'))
    # Reuse the already validated fixture loader, not a trusted raw dictionary.
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'inventory.json';path.write_text(json.dumps(data))
        connection=ConnectionView(path,catalog,key('catalog'))
        view=NativeLibraryView(connection)
        rows=view.recordsForGame('canonical-1')
        assert len(rows)==1 and rows[0]['product_id']=='1'
        rows[0]['source_context']['host']='changed'
        view.setFilters('All native providers','no match',True)
        assert view.recordsForGame('canonical-1')[0]['source_context']['host']=='offline-host'
        assert view.recordsForGame('missing')==[]
