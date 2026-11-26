import copy
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

# Test the selected candidate consistently; never delete/rebind sys.modules.
from native_services.xodus_install import XodusInstallError, verify_installation
from native_services.install_receipts import (
    InstallReceiptRegistry,
    ReceiptError,
    adapt_xodus_verified_receipt,
    project_canonical_installed_state,
)


PRODUCT = '9ABCDEF12345'
SELECTED = '9ABCDEF12345'
CONTENT = '11111111-2222-3333-4444-555555555555'


def make_xodus_files(root: Path, *, store_id=PRODUCT, exe_name='Game.exe', with_incomplete=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / '.xodus-streaming.msixvc').write_bytes(b'fixture container')
    exe = root / exe_name
    exe.write_bytes(b'MZ fixture')
    manifest = (
        f'<Game><StoreId>{store_id}</StoreId>'
        f'<ExecutableList><Executable Name="{exe_name}" /></ExecutableList></Game>'
    )
    (root / 'MicrosoftGame.Config').write_text(manifest, encoding='utf-8')
    if with_incomplete:
        (root / '.nexus-update-incomplete-v1').touch()
    return exe


def xodus_plan(root: Path):
    return SimpleNamespace(
        destination=root,
        product_id=PRODUCT,
        selected_product_id=SELECTED,
        content_id=CONTENT,
    )


class TestInstallReceipts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.host = 'fixture-host'
        self.clock_value = 1000
        self.reg = InstallReceiptRegistry(self.base / 'state' / 'install_receipts.json', host=self.host, clock=lambda: self.clock_value)

    def _gog_root(self, name='Rook'):
        root = self.base / 'games' / name
        root.mkdir(parents=True, exist_ok=True)
        exe = root / 'rook.exe'
        exe.write_bytes(b'game')
        return root, exe

    def _gog_receipt(self, root, exe, **extra):
        receipt = {
            'provider': 'gog',
            'product_id': '12345',
            'account_id': 'player',
            'host': self.host,
            'install_path': str(root),
            'executable': str(exe),
            'state': 'files_verified',
        }
        receipt.update(extra)
        return receipt

    def test_gog_verified_receipt_round_trip_and_detached_projection(self):
        root, exe = self._gog_root()
        stored = self.reg.register(self._gog_receipt(root, exe))
        stored['provider'] = 'mutated-local-copy'
        links = [{'game_id': 'canon-1', 'provider': 'gog', 'product_id': '12345'}]
        rows = self.reg.query('canon-1', links)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row['is_present'])
        self.assertTrue(row['files_verified'])
        self.assertEqual(row['playability'], 'unknown')
        self.assertEqual(row['history'][0]['state'], 'files_verified')
        self.assertEqual(row['provider'], 'gog')
        projection = project_canonical_installed_state(self.reg, 'canon-1', links)
        self.assertEqual(projection['game_id'], 'canon-1')
        self.assertEqual(len(projection['install_receipts']), 1)
        self.assertEqual(self.reg.query('canon-other', [{'game_id': 'canon-other', 'provider': 'gog', 'product_id': '999'}]), [])

    def test_actual_xodus_verified_receipt_round_trips_without_invented_required_fields(self):
        root = self.base / 'xbox' / PRODUCT
        make_xodus_files(root)
        plan = xodus_plan(root)
        xodus_receipt = verify_installation(plan)
        # Actual xodus receipt shape: no host/install_path/account/filesystem_device keys.
        self.assertEqual(
            set(xodus_receipt.keys()),
            {'provider', 'product_id', 'selected_product_id', 'content_id', 'destination', 'executable', 'state', 'playability'},
        )
        adapted = adapt_xodus_verified_receipt(xodus_receipt, host=self.host)
        # Adapter must not invent account id or extra required product fields.
        self.assertIsNone(adapted['account_id'])
        self.assertEqual(adapted['install_path'], str(root.resolve()))
        self.assertEqual(adapted['product_id'], PRODUCT)
        self.assertEqual(adapted['state'], 'files_verified')
        self.assertEqual(adapted.get('selected_product_id'), PRODUCT)
        self.assertEqual(adapted.get('content_id'), CONTENT)
        self.assertNotIn('token', json.dumps(adapted).lower())
        stored = self.reg.register(adapted)
        self.assertTrue(stored['is_present'] if 'is_present' in stored else True)
        links = [{'game_id': 'halo', 'provider': 'xbox', 'product_id': PRODUCT}]
        projected = self.reg.query('halo', links)
        self.assertEqual(len(projected), 1)
        self.assertTrue(projected[0]['is_present'])
        self.assertEqual(projected[0]['content_id'], CONTENT)
        self.assertEqual(projected[0]['executable'], xodus_receipt['executable'])
        # execute-style receipt file can be adapted the same way (public contract)
        receipt_path = root / '.waystone-install-receipt.json'
        receipt_path.write_text(json.dumps(xodus_receipt), encoding='utf-8')
        loaded = json.loads(receipt_path.read_text(encoding='utf-8'))
        self.reg2 = InstallReceiptRegistry(self.base / 'state' / 'xr2.json', host=self.host, clock=lambda: 1001)
        self.reg2.register_xodus_verified(loaded)
        self.assertTrue(self.reg2.query('halo', links)[0]['is_present'])

    def test_gog_or_amazon_completion_lacking_verified_metadata_remains_unverified(self):
        root, exe = self._gog_root('Amazonish')
        # No executable/path/verified state -> cannot be treated as installed.
        incomplete = {'provider': 'amazon', 'product_id': 'amzn1.game.123', 'host': self.host, 'state': 'completed'}
        with self.assertRaises(ReceiptError):
            self.reg.register(incomplete)
        # Download/job exit success without verified installation metadata must not register.
        missing_exe = {
            'provider': 'gog', 'product_id': '12345', 'host': self.host,
            'install_path': str(root), 'state': 'files_verified'
        }
        with self.assertRaises(ReceiptError):
            self.reg.register(missing_exe)
        not_verified = self._gog_receipt(root, exe, state='downloaded')
        with self.assertRaises(ReceiptError):
            self.reg.register(not_verified)
        self.assertEqual(self.reg.query('g', [{'game_id': 'g', 'provider': 'gog', 'product_id': '12345'}]), [])

    def test_query_requires_exact_provider_product_host_links(self):
        root, exe = self._gog_root()
        self.reg.register(self._gog_receipt(root, exe))
        # Same title-like intent is irrelevant; only exact canonical links match.
        self.assertEqual(self.reg.query('canon', [{'game_id': 'canon', 'provider': 'gog', 'product_id': '99999'}]), [])
        self.assertEqual(self.reg.query('canon', [{'game_id': 'other', 'provider': 'gog', 'product_id': '12345'}]), [])
        self.assertEqual(self.reg.query('canon', [{'game_id': 'canon', 'provider': 'amazon', 'product_id': '12345'}]), [])
        with self.assertRaises(ReceiptError):
            self.reg.query('', [{'game_id': '', 'provider': 'gog', 'product_id': '12345'}])

    def test_missing_exe_device_or_incomplete_marker_loses_present_without_deleting_history(self):
        root, exe = self._gog_root('Presence')
        self.reg.register(self._gog_receipt(root, exe))
        links = [{'game_id': 'g', 'provider': 'gog', 'product_id': '12345'}]
        self.assertTrue(self.reg.query('g', links)[0]['is_present'])

        (root / '.partial').touch()
        row = self.reg.query('g', links)[0]
        self.assertFalse(row['is_present'])
        self.assertEqual(len(row['history']), 1)
        (root / '.partial').unlink()

        self.assertTrue(self.reg.query('g', links)[0]['is_present'])
        exe.unlink()
        row = self.reg.query('g', links)[0]
        self.assertFalse(row['is_present'])
        self.assertEqual(len(row['history']), 1)

        # Recreate and re-register to get history growth, then vanish directory.
        exe.write_bytes(b'game')
        self.clock_value = 1001
        self.reg.register(self._gog_receipt(root, exe))
        for child in list(root.iterdir()):
            if child.is_file():
                child.unlink()
        root.rmdir()
        row = self.reg.query('g', links)[0]
        self.assertFalse(row['is_present'])
        self.assertEqual(len(row['history']), 2)

    def test_filesystem_device_mismatch_or_unavailable_loses_present(self):
        root, exe = self._gog_root('Device')
        fs_dev = root.stat().st_dev
        self.reg.register(self._gog_receipt(root, exe, filesystem_device=fs_dev))
        links = [{'game_id': 'g', 'provider': 'gog', 'product_id': '12345'}]
        self.assertTrue(self.reg.query('g', links)[0]['is_present'])

        # Tamper persisted device to simulate moved/unavailable mount identity.
        data = json.loads(self.reg.path.read_text(encoding='utf-8'))
        key = next(iter(data['records'].keys()))
        data['records'][key]['filesystem_device'] = fs_dev + 999999
        self.reg.path.write_text(json.dumps(data), encoding='utf-8')
        row = self.reg.query('g', links)[0]
        self.assertFalse(row['is_present'])
        self.assertEqual(len(row['history']), 1)

    def test_unknown_provider_or_mismatch_rejected(self):
        root, exe = self._gog_root('Bad')
        for change in (
            {'provider': 'steam'},
            {'product_id': 'not-a-gog-id'},
            {'host': 'other-host'},
            {'install_path': 'relative/path'},
            {'executable': str(root / '../escape.exe')},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ReceiptError):
                    self.reg.register(self._gog_receipt(root, exe, **change))

    def test_xodus_incomplete_or_wrong_store_rejected_by_source_contract(self):
        root = self.base / 'xbox-bad' / PRODUCT
        make_xodus_files(root, with_incomplete=True)
        plan = xodus_plan(root)
        with self.assertRaises(XodusInstallError):
            verify_installation(plan)

        root2 = self.base / 'xbox-wrong' / PRODUCT
        make_xodus_files(root2, store_id='9ZZZZZZZZZZZ')
        with self.assertRaises(XodusInstallError):
            verify_installation(xodus_plan(root2))

    def test_noncompleted_results_never_change_prior_registry(self):
        root, exe = self._gog_root('Stable')
        self.reg.register(self._gog_receipt(root, exe))
        old = self.reg.path.read_bytes()
        for state in ('cancelled', 'failed', 'downloaded', 'completed'):
            receipt = self._gog_receipt(root, exe, state=state)
            with self.subTest(state=state):
                with self.assertRaises(ReceiptError):
                    self.reg.register(receipt)
                self.assertEqual(self.reg.path.read_bytes(), old)

    def test_atomic_replace_failure_preserves_previous(self):
        root, exe = self._gog_root('Atomic')
        self.reg.register(self._gog_receipt(root, exe))
        old = self.reg.path.read_bytes()
        import native_services.install_receipts as module
        real_replace = module.os.replace

        def fail(src, dst):
            if Path(dst) == self.reg.path:
                raise OSError('fixture replace failure')
            return real_replace(src, dst)

        module.os.replace = fail
        try:
            with self.assertRaises(OSError):
                self.clock_value = 1002
                self.reg.register(self._gog_receipt(root, exe))
        finally:
            module.os.replace = real_replace
        self.assertEqual(self.reg.path.read_bytes(), old)

    def test_concurrent_updates_are_locked_and_not_lost(self):
        errors = []
        def worker(i):
            try:
                rroot, rexe = self._gog_root(f'Game{i}')
                reg = InstallReceiptRegistry(self.reg.path, host=self.host, clock=lambda: 2000 + i)
                reg.register(self._gog_receipt(rroot, rexe, product_id=str(10000 + i)))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])
        data = json.loads(self.reg.path.read_text(encoding='utf-8'))
        self.assertEqual(len(data['records']), 6)

    def test_corrupt_or_untrusted_persisted_records_do_not_crash_query(self):
        root, exe = self._gog_root('CorruptSafe')
        self.reg.register(self._gog_receipt(root, exe))
        # Inject malformed persisted record shape; query must stay safe.
        data = json.loads(self.reg.path.read_text(encoding='utf-8'))
        data['records']['broken'] = {'provider': 'gog', 'product_id': 12345, 'history': 'bad'}
        data['records']['alsobroken'] = 'nope'
        self.reg.path.write_text(json.dumps(data), encoding='utf-8')
        rows = self.reg.query('canon', [{'game_id': 'canon', 'provider': 'gog', 'product_id': '12345'}])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['is_present'])
        self.assertIsInstance(rows[0].get('history'), list)

        self.reg.path.write_text('{not json', encoding='utf-8')
        with self.assertRaises(ReceiptError):
            self.reg.query('canon', [{'game_id': 'canon', 'provider': 'gog', 'product_id': '12345'}])

    def test_no_credentials_or_prompts_persisted(self):
        root, exe = self._gog_root('Secrets')
        bad = self._gog_receipt(root, exe, token='SECRET', auth_prompt='enter password')
        with self.assertRaises(ReceiptError):
            self.reg.register(bad)
        self.assertFalse(self.reg.path.exists())

    def test_account_identity_isolated_but_same_host_provider_product_separated(self):
        root_a, exe_a = self._gog_root('AcctA')
        root_b, exe_b = self._gog_root('AcctB')
        self.reg.register(self._gog_receipt(root_a, exe_a, account_id='alice'))
        self.clock_value = 1001
        self.reg.register(self._gog_receipt(root_b, exe_b, account_id='bob'))
        links = [{'game_id': 'g', 'provider': 'gog', 'product_id': '12345'}]
        rows = self.reg.query('g', links)
        self.assertEqual(len(rows), 2)
        by_acct = {r.get('account_id'): r for r in rows}
        self.assertTrue(by_acct['alice']['is_present'])
        self.assertTrue(by_acct['bob']['is_present'])
        self.assertEqual(by_acct['alice']['install_path'], str(root_a))
        self.assertEqual(by_acct['bob']['install_path'], str(root_b))
        # repeated write to same identity grows history, does not merge accounts
        self.clock_value = 1002
        self.reg.register(self._gog_receipt(root_a, exe_a, account_id='alice'))
        rows2 = self.reg.query('g', links)
        self.assertEqual(len(rows2), 2)
        self.assertEqual(len([r for r in rows2 if r.get('account_id') == 'alice'][0]['history']), 2)
        self.assertEqual(len([r for r in rows2 if r.get('account_id') == 'bob'][0]['history']), 1)

    def test_persisted_host_identity_mixup_never_reports_present(self):
        root, exe = self._gog_root('HostMix')
        self.reg.register(self._gog_receipt(root, exe))
        links = [{'game_id': 'g', 'provider': 'gog', 'product_id': '12345'}]
        self.assertTrue(self.reg.query('g', links)[0]['is_present'])
        data = json.loads(self.reg.path.read_text(encoding='utf-8'))
        key = next(iter(data['records'].keys()))
        data['records'][key]['host'] = 'other-host'
        self.reg.path.write_text(json.dumps(data), encoding='utf-8')
        row = self.reg.query('g', links)[0]
        self.assertFalse(row['is_present'])
        self.assertEqual(len(row['history']), 1)

    def test_malformed_persisted_field_types_do_not_crash_query(self):
        root, exe = self._gog_root('Malformed')
        self.reg.register(self._gog_receipt(root, exe))
        links = [{'game_id': 'g', 'provider': 'gog', 'product_id': '12345'}]
        data = json.loads(self.reg.path.read_text(encoding='utf-8'))
        key = next(iter(data['records'].keys()))
        # hostile/wrong types for paths and metadata; query must not crash
        hostile = copy.deepcopy(data['records'][key])
        hostile['install_path'] = {'bad': True}
        hostile['executable'] = 123
        hostile['filesystem_device'] = 'not-a-device-id'
        hostile['provider'] = 'gog'
        hostile['product_id'] = '12345'
        data['records']['hostile-types'] = hostile
        data['records']['missing-paths'] = {'provider': 'gog', 'product_id': '12345', 'host': self.host}
        self.reg.path.write_text(json.dumps(data), encoding='utf-8')
        rows = self.reg.query('g', links)
        # valid record still projected correctly; malformed ones must not crash query
        self.assertTrue(any(r['is_present'] and r.get('install_path') == str(root) for r in rows))
        for r in rows:
            self.assertIn('is_present', r)
            self.assertIn('files_verified', r)

    def test_actual_xodus_verify_then_repeated_register_keeps_history(self):
        root = self.base / 'xbox-repeat' / PRODUCT
        make_xodus_files(root)
        plan = xodus_plan(root)
        receipt = verify_installation(plan)
        self.reg.register_xodus_verified(receipt)
        self.clock_value = 1001
        self.reg.register_xodus_verified(verify_installation(plan))
        links = [{'game_id': 'x', 'provider': 'xbox', 'product_id': PRODUCT}]
        rows = self.reg.query('x', links)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['is_present'])
        self.assertTrue(rows[0]['files_verified'])
        self.assertEqual(len(rows[0]['history']), 2)

if __name__ == '__main__':
    unittest.main()
