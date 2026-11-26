"""Bounded, offline import regressions; all writes stay in temporary directories."""
import copy
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import snapshot_bridge as bridge

HERE = Path(__file__).parent.resolve()


class RecordingReader(io.BytesIO):
    """Small synthetic reads, including short reads, without large allocations."""
    def __init__(self, raw, fragment=None):
        super().__init__(raw)
        self.fragment = fragment
        self.requests = []
        self.delivered = []
        self.consumed = 0

    def read(self, size=-1):
        self.requests.append(size)
        if self.fragment is not None:
            size = min(size, self.fragment)
        block = super().read(size)
        self.delivered.append(len(block))
        self.consumed += len(block)
        return block


class SnapshotHardeningTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((HERE / 'fixtures/demo-snapshot.json').read_text())
        self.temp = tempfile.TemporaryDirectory(prefix='snapshot-hardening-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, data, name='export.json'):
        path = self.root / name
        path.write_bytes(json.dumps(data, ensure_ascii=False).encode('utf-8'))
        return path

    def changed_host(self):
        data = copy.deepcopy(self.data)
        data['source']['host'] = 'hardening-second-host'
        for row in data['records']:
            row['variant_key'] = bridge.key([row['record_key'], data['source']['host'], row['installation_id']])
        return data

    def rejected(self, data):
        # Both direct projection and file import must expose validation errors,
        # never AttributeError/TypeError from malformed JSON containers.
        with self.assertRaises(ValueError):
            bridge.snapshot_games(data)
        path = self.write(data)
        with self.assertRaisesRegex(ValueError, 'export.json:'):
            bridge.load_export(path)

    def test_top_level_json_shapes(self):
        for value in (None, [], [1], 'text', 1, True):
            with self.subTest(value=value):
                self.rejected(value)

    def test_source_and_records_container_shapes(self):
        for field in ('source', 'records'):
            for value in (None, 'text', 1, True, {}, []):
                if field == 'records' and value == []:
                    continue
                with self.subTest(field=field, value=value):
                    data = copy.deepcopy(self.data)
                    data[field] = value
                    self.rejected(data)

    def test_record_shapes_and_index(self):
        for value in (None, [], 'text', 1, True):
            with self.subTest(value=value):
                data = copy.deepcopy(self.data)
                data['records'].append(value)
                with self.assertRaisesRegex(ValueError, r'records\[3\]'):
                    bridge.load_export(self.write(data))

    def test_unhashable_enums_are_validation_errors(self):
        for field in ('availability', 'installation_state', 'kind', 'payload_presence'):
            for value in ([], {}, None, True, 1):
                with self.subTest(field=field, value=value):
                    data = copy.deepcopy(self.data)
                    target = data if field == 'availability' else data['records'][0]
                    target[field] = value
                    self.rejected(data)

    def test_bad_identity_title_and_source_values(self):
        for field in ('external_id', 'installation_id', 'record_key', 'variant_key', 'title'):
            for value in ([], {}, None, True, 1, ''):
                with self.subTest(field=field, value=value):
                    data = copy.deepcopy(self.data)
                    data['records'][0][field] = value
                    self.rejected(data)
        for field in bridge.SOURCE_FIELDS:
            data = copy.deepcopy(self.data)
            data['source'][field] = []
            self.rejected(data)

    def test_evidence_container_and_field_shapes(self):
        for value in (None, [], 'text', 1, True, {}):
            data = copy.deepcopy(self.data)
            data['records'][0]['evidence'] = value
            self.rejected(data)
        for field in bridge.EVIDENCE_FIELDS:
            for value in (None, {}, True):
                with self.subTest(field=field, value=value):
                    data = copy.deepcopy(self.data)
                    data['records'][0]['evidence'][field] = value
                    self.rejected(data)
        data = copy.deepcopy(self.data)
        data['records'][0]['evidence']['field_locators'] = [{}]
        self.rejected(data)

    def test_schema_and_complete_require_real_types(self):
        for field, values in (('schema_version', (True, 1.0, [], {})), ('complete', (None, 0, 1, 'false', [], {}))):
            for value in values:
                with self.subTest(field=field, value=value):
                    data = copy.deepcopy(self.data)
                    data[field] = value
                    self.rejected(data)

    def test_observed_at_requires_real_date_time_and_timezone(self):
        values = (None, [], {}, 1, '', 'not-a-date', '2026-11-15',
                  '2026-11-15T01:00:00', '2026-02-29T01:00:00Z',
                  '2026-13-15T01:00:00Z', '2026-11-15T24:00:00Z',
                  '2026-11-15T01:60:00Z', '2026-11-15T01:00:60Z',
                  '2026-11-15T01:00:00+24:00', '2026-11-15T01:00:00+00:60',
                  '2026-11-15T01:00:00Z trailing', '2026-11-15T01:00:00+0000')
        for value in values:
            with self.subTest(value=value):
                data = copy.deepcopy(self.data)
                data['observed_at'] = value
                self.rejected(data)
        data = copy.deepcopy(self.data)
        del data['observed_at']
        self.rejected(data)

    def test_valid_aware_dates_are_preserved_verbatim(self):
        for value in ('2026-11-15T01:00:00Z', '2024-02-29T23:59:59.123456+00:00',
                      '2026-11-15T01:00:00.1-05:30', '2026-11-15T01:00:00+05:45'):
            with self.subTest(value=value):
                self.data['observed_at'] = value
                games, context = bridge.load_export(self.write(self.data), with_context=True)
                self.assertEqual(context['observed_at'], value)
                for game in games:
                    for variant in game['variants']:
                        self.assertEqual(variant['observation']['at'], value)

    def test_fixture_mode_is_genuine_boolean(self):
        for value in ('false', 'true', 0, 1, None, [], {}):
            with self.subTest(value=value):
                data = copy.deepcopy(self.data)
                data['fixture_mode'] = value
                self.rejected(data)
        for value in (False, True):
            self.data['fixture_mode'] = value
            _, context = bridge.load_export(self.write(self.data), with_context=True)
            self.assertIs(context['fixture_mode'], value)

    def test_missing_fixture_mode_defaults_false_without_mutating_input(self):
        self.data.pop('fixture_mode', None)
        before = copy.deepcopy(self.data)
        bridge.snapshot_games(self.data)
        _, context = bridge.load_export(self.write(self.data), with_context=True)
        self.assertIs(context['fixture_mode'], False)
        self.assertEqual(self.data, before)

    def test_empty_records_are_valid_observations(self):
        self.data['records'] = []
        games, context = bridge.load_export(self.write(self.data), with_context=True)
        self.assertEqual(games, [])
        self.assertEqual(context['observed_at'], self.data['observed_at'])

    def test_valid_import_preserves_input_bytes_stat_identity_and_provenance(self):
        path = self.write(self.data)
        before = path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode
        original = copy.deepcopy(self.data)
        self.assertEqual(bridge.snapshot_games(self.data), bridge.load_export(path))
        self.assertEqual(self.data, original)
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode))
        variants = {v['installation_id']: v for g in bridge.load_export(path) for v in g['variants']}
        for row in self.data['records']:
            variant = variants[row['variant_key']]
            self.assertEqual(variant['source_id'], row['record_key'])
            self.assertEqual(variant['evidence'], row['evidence'])
            self.assertEqual(variant['status'], row['installation_state'])
            self.assertEqual(variant['source_context'], self.data['source'])
            self.assertEqual(variant['provider_installation_id'], row['installation_id'])
            self.assertEqual(variant['recipe'], [])
            self.assertNotEqual(variant['status'], 'installed')

    def test_stable_merge_keeps_hosts_and_same_title_distinct_ids(self):
        for row in self.data['records']:
            row['title'] = 'Same title'
        paths = [self.write(self.data), self.write(self.changed_host(), 'two.json')]
        games, contexts = bridge.load_exports(paths, with_context=True)
        self.assertEqual(len(games), 2)
        self.assertEqual(sum(len(g['variants']) for g in games), 6)
        self.assertEqual(len(contexts), 2)
        self.assertEqual({v['source_context']['host'] for g in games for v in g['variants']},
                         {self.data['source']['host'], 'hardening-second-host'})
        with self.assertRaisesRegex(ValueError, 'Duplicate variant'):
            bridge.load_exports([paths[0], paths[0]])

    def test_late_invalid_export_fails_atomically(self):
        good = self.write(self.data)
        with self.assertRaises(ValueError):
            bridge.load_exports([good, self.write([], 'bad.json')], with_context=True)
        self.assertEqual(bridge.load_export(good), bridge.snapshot_games(self.data))

    def test_selection_count_caps_before_open(self):
        with patch.object(bridge.os, 'open') as opener:
            for paths in ([], [self.root / 'absent.json'] * 17):
                with self.assertRaises(ValueError):
                    bridge.load_exports(paths)
            opener.assert_not_called()

    def test_exact_byte_caps_and_unicode_are_accepted(self):
        self.data['records'][0]['title'] = 'Unicode \u2603 \U0001f3ae'
        path = self.write(self.data)
        size = len(path.read_bytes())
        with patch.object(bridge, 'MAX_SNAPSHOT_BYTES', size), patch.object(bridge, 'MAX_TOTAL_SNAPSHOT_BYTES', size):
            self.assertEqual(bridge.load_export(path), bridge.snapshot_games(self.data))

    def test_descriptor_size_preflight_rejects_before_read(self):
        path = self.write(self.data)
        with patch.object(bridge, 'MAX_SNAPSHOT_BYTES', path.stat().st_size - 1), patch.object(bridge.os, 'fdopen') as fdopen:
            with self.assertRaisesRegex(ValueError, 'size cap'):
                bridge.load_export(path)
            fdopen.assert_not_called()

    def test_small_file_growth_after_fstat_hits_actual_per_file_cap(self):
        path = self.write(self.data)
        cap = path.stat().st_size + 8
        real_fstat = os.fstat

        def grow_after_fstat(fd):
            info = real_fstat(fd)
            with path.open('ab') as writer:
                writer.write(b' ' * 32)
            return info

        with patch.object(bridge, 'MAX_SNAPSHOT_BYTES', cap), patch.object(bridge.os, 'fstat', side_effect=grow_after_fstat):
            with self.assertRaisesRegex(ValueError, 'grew beyond size cap'):
                bridge.load_export(path)

    def test_growth_after_open_hits_actual_combined_cap(self):
        paths = [self.write(self.data), self.write(self.changed_host(), 'two.json')]
        cap = sum(p.stat().st_size for p in paths) + 16
        real_reader = bridge._read_export

        def growing_reader(path):
            with Path(path).open('ab') as writer:
                writer.write(b' ' * 32)
            return real_reader(path)

        with patch.object(bridge, 'MAX_TOTAL_SNAPSHOT_BYTES', cap), patch.object(bridge, '_read_export', side_effect=growing_reader):
            with self.assertRaisesRegex(ValueError, 'Combined snapshot size cap'):
                bridge.load_exports(paths)

    def test_remaining_total_budget_bounds_reads_to_one_overflow_byte(self):
        first = json.dumps(self.data).encode()
        second = json.dumps(self.changed_host()).encode() + b' ' * 32
        readers = [RecordingReader(first, fragment=7), RecordingReader(second, fragment=11)]
        cap = len(first) + len(second) - 20
        with patch.object(bridge, 'MAX_TOTAL_SNAPSHOT_BYTES', cap), patch.object(bridge, '_read_export', side_effect=readers):
            with self.assertRaisesRegex(ValueError, 'Combined snapshot size cap'):
                bridge.load_exports(['first.json', 'second.json'])
        self.assertEqual(readers[0].consumed, len(first))
        self.assertEqual(sum(r.consumed for r in readers), cap + 1)
        self.assertTrue(all(r.closed for r in readers))
        self.assertTrue(all(0 < n <= min(64 * 1024, cap + 1) for r in readers for n in r.requests))
        total = 0
        for reader in readers:
            file_bytes = 0
            for requested, delivered in zip(reader.requests, reader.delivered):
                self.assertLessEqual(requested, min(64 * 1024, cap - total + 1,
                                                    bridge.MAX_SNAPSHOT_BYTES - file_bytes + 1))
                total += delivered
                file_bytes += delivered

    def test_short_reads_continue_until_eof(self):
        raw = json.dumps(self.data).encode()
        reader = RecordingReader(raw, fragment=7)
        with patch.object(bridge, '_read_export', return_value=reader):
            self.assertEqual(bridge.load_export('mock.json'), bridge.snapshot_games(self.data))
        self.assertEqual(reader.consumed, len(raw))
        self.assertTrue(reader.closed)

    def test_regular_symlink_uses_opened_target_and_directory_is_rejected(self):
        path = self.write(self.data)
        link = self.root / 'explicit-link.json'
        link.symlink_to(path)
        self.assertEqual(bridge.load_export(link), bridge.load_export(path))
        with self.assertRaisesRegex(ValueError, 'regular file'):
            bridge.load_export(self.root)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO required')
    def test_fifo_symlink_and_open_race_refuse_without_hanging(self):
        # A child timeout bounds even a regressed blocking open. No pipe writer
        # is ever needed. Also swap a regular path to a FIFO at os.open itself.
        script = '''
import os, sys
from pathlib import Path
from unittest.mock import patch
import snapshot_bridge as bridge
root = Path(sys.argv[1])
fifo = root / 'fifo.json'
os.mkfifo(fifo)
link = root / 'fifo-link.json'
link.symlink_to(fifo)
for path in (fifo, link):
    try:
        bridge.load_export(path)
    except ValueError as exc:
        assert 'regular file' in str(exc), exc
    else:
        raise AssertionError('FIFO accepted')
target = root / 'race.json'
target.write_text('{}')
real_open = os.open
def swap(path, flags, *args, **kwargs):
    assert flags & os.O_NONBLOCK
    assert flags & os.O_ACCMODE == os.O_RDONLY
    target.unlink()
    os.mkfifo(target)
    return real_open(path, flags, *args, **kwargs)
with patch.object(bridge.os, 'open', side_effect=swap):
    try:
        bridge.load_export(target)
    except ValueError as exc:
        assert 'regular file' in str(exc), exc
    else:
        raise AssertionError('Raced FIFO accepted')
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, str(self.root)],
                                cwd=HERE, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_descriptors_closed_on_success_and_validation_errors(self):
        good = self.write(self.data)
        bad = self.write([], 'bad.json')
        real_open = os.open
        opened = []

        def track(path, flags):
            fd = real_open(path, flags)
            opened.append(fd)
            return fd

        with patch.object(bridge.os, 'open', side_effect=track):
            bridge.load_export(good)
            for path in (bad, self.root):
                with self.assertRaises(ValueError):
                    bridge.load_export(path)
            with patch.object(bridge, 'MAX_SNAPSHOT_BYTES', 1):
                with self.assertRaises(ValueError):
                    bridge.load_export(good)
        self.assertEqual(len(opened), 4)
        for fd in opened:
            with self.assertRaises(OSError) as caught:
                os.fstat(fd)
            self.assertEqual(caught.exception.errno, errno.EBADF)

    def test_malformed_json_and_utf8_are_validation_errors(self):
        for raw in (b'', b'{', b'\xff'):
            path = self.root / 'bad.json'
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'bad.json:'):
                bridge.load_export(path)

    def test_cli_malformed_envelope_has_no_traceback(self):
        path = self.write([])
        result = subprocess.run([sys.executable, '-B', str(HERE / 'app.py'), '--smoke-test', '--snapshot', str(path)],
                                cwd=HERE, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                                                  QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Snapshot must be a JSON object', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
