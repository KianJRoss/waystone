"""Offline native-refresh contract tests. Every collector runner is mocked."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QThread, QTimer
from PySide6.QtQml import QQmlEngine
import shiboken6

from native_refresh_view import NativeRefreshView, TIMEOUT_SECONDS, INVENTORY_LIMIT


OBSERVED = '2026-11-15T10:20:30+00:00'


def fixture(catalog):
    return {'schema_version': 1, 'inventory_kind': 'native-connections-readonly',
            'catalog_sha256': sha256(catalog.read_bytes()).hexdigest(),
            'execution_supported': False, 'observed_at': OBSERVED,
            'host': 'FixtureHost', 'connections': [], 'canonical_links': {},
            'unmatched_native_variant_keys': [], 'ambiguous_native_variant_keys': []}


class NativeRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.catalog = self.root / 'selected catalog.json'
        self.catalog.write_text(json.dumps({'schema_version': 2, 'games': []}))
        self.catalog_before = self.catalog.read_bytes()
        self.output = self.root / 'native-refresh'
        self.collector = self.root / '.native-connection-sandbox'
        self.collector.mkdir()
        self.calls = []
        self.views = []
        self.releases = []
        self.addCleanup(self.cleanup_workers)

    def cleanup_workers(self):
        # Even a failed assertion releases and joins every test worker before
        # fixtures disappear. No daemon threads or real child processes.
        for release in self.releases:
            release.set()
        for view in self.views:
            if shiboken6.isValid(view):
                view.close()
        self.app.processEvents()

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs, threading.get_ident()))
        path = Path(argv[argv.index('--output') + 1])
        path.write_text(json.dumps(fixture(self.catalog)))
        return SimpleNamespace(returncode=0)

    def view(self, runner=None):
        view = NativeRefreshView(self.catalog, self.output, self.collector,
                                 runner=self.runner if runner is None else runner)
        self.views.append(view)
        return view

    def pump_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.001)
        self.assertTrue(condition(), 'Qt completion did not arrive')

    def blocked_runner(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def run(argv, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test worker release was lost')
            return self.runner(argv, **kwargs)
        return run, entered, release

    def test_constructor_is_idle_and_qt_api_is_read_only(self):
        view = self.view()
        self.app.processEvents()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.output.exists())
        self.assertFalse(view.busy)
        self.assertEqual(view.error, '')
        self.assertEqual(view.observedAt, '')
        self.assertIn('Ready', view.status)
        meta = view.metaObject()
        for name in ('busy', 'status', 'error', 'observedAt'):
            prop = meta.property(meta.indexOfProperty(name))
            self.assertTrue(prop.hasNotifySignal())
            self.assertFalse(prop.isWritable())
        self.assertNotEqual(meta.indexOfSignal('refreshed(QString)'), -1)
        self.assertTrue(view.wait(0))

    def test_success_queued_to_qt_thread_unique_paths_and_source_preserved(self):
        view = self.view()
        paths, threads = [], []
        view.refreshed.connect(lambda path: (paths.append(path), threads.append(QThread.currentThread())))
        self.assertTrue(view.refresh())
        self.pump_until(lambda: len(paths) == 1)
        self.assertFalse(view.busy)
        self.assertEqual(view.error, '')
        self.assertEqual(view.observedAt, OBSERVED)
        first = Path(paths[0])
        before = first.read_bytes()
        self.assertEqual(first.name, 'native-connection-inventory.json')
        self.assertEqual(first.parent.parent, self.output)
        self.assertTrue(first.is_absolute())
        self.assertEqual(threads, [view.thread()])
        self.assertNotEqual(self.calls[0][2], threading.get_ident())
        # It is still a valid detached input for the existing full UI validator.
        from connection_view import load_inventory
        load_inventory(first, json.loads(self.catalog_before), sha256(self.catalog_before).hexdigest())
        self.assertTrue(view.refresh())
        self.assertTrue(view.wait(3000))
        self.assertEqual(len(paths), 2)
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual(first.read_bytes(), before)
        self.assertEqual(self.catalog.read_bytes(), self.catalog_before)

    def test_default_runner_pins_cwd_pythonpath_and_discards_output(self):
        # Patch subprocess.run, not the constructor's injection, to exercise its
        # actual default runner without ever executing the live collector.
        with patch.dict(os.environ, {'PYTHONPATH': '/untrusted:/other',
                                    'PYTHONHOME': '/bad', 'PYTHONINSPECT': '1',
                                    'LD_PRELOAD': '/bad.so', 'ACCESS_TOKEN': 'secret'}):
            with patch('native_refresh_view.subprocess.run', side_effect=self.runner) as run:
                view = NativeRefreshView(self.catalog, self.output, self.collector)
                self.views.append(view)
                view.refresh()
                self.assertTrue(view.wait(3000))
                self.assertEqual(run.call_count, 1)
        argv, kwargs, _ = self.calls[0]
        self.assertEqual(argv[:6], [sys.executable, '-B', '-m',
                                  'native_services.connection_inventory', '--catalog', str(self.catalog)])
        self.assertEqual(argv[6], '--output')
        self.assertEqual(len(argv), 8)
        self.assertEqual(kwargs['cwd'], str(self.collector))
        env = kwargs['env']
        self.assertEqual(env['PYTHONPATH'], str(self.collector))
        self.assertEqual(env['PYTHONNOUSERSITE'], '1')
        for key in ('PYTHONHOME', 'PYTHONINSPECT', 'LD_PRELOAD', 'ACCESS_TOKEN'):
            self.assertNotIn(key, env)
        self.assertIs(kwargs['shell'], False)
        self.assertIs(kwargs['check'], False)
        self.assertEqual(kwargs['timeout'], TIMEOUT_SECONDS)
        self.assertGreater(TIMEOUT_SECONDS, 0)
        self.assertLessEqual(TIMEOUT_SECONDS, 120)
        for channel in ('stdin', 'stdout', 'stderr'):
            self.assertEqual(kwargs[channel], subprocess.DEVNULL)

    def test_busy_ignores_click_and_event_loop_remains_responsive(self):
        run, entered, release = self.blocked_runner()
        view = self.view(run)
        view.refresh()
        self.assertTrue(entered.wait(1))
        self.assertTrue(view.busy)
        directories = list(self.output.iterdir())
        self.assertFalse(view.refresh())
        self.assertEqual(list(self.output.iterdir()), directories)
        self.assertFalse(view.wait(1))
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        self.pump_until(lambda: bool(ticks))
        self.assertTrue(view.busy)
        release.set()
        self.assertTrue(view.wait(3000))
        self.assertEqual(len(self.calls), 1)

    def test_failures_are_sanitized_and_preserve_last_good(self):
        view = self.view()
        paths = []
        view.refreshed.connect(paths.append)
        view.refresh()
        view.wait()
        last = Path(paths[0])
        before = last.read_bytes()
        secret = 'secret-credential-raw-source'

        def raised(*args, **kwargs):
            raise RuntimeError(secret)

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(secret, 60, output=secret, stderr=secret)

        for run in (lambda *a, **k: SimpleNamespace(returncode=2, stdout=secret, stderr=secret),
                    raised, timeout):
            with self.subTest(runner=run):
                view._runner = run
                view.refresh()
                view.wait()
                self.assertFalse(view.busy)
                self.assertTrue(view.error)
                self.assertNotIn(secret, view.status + view.error + view.observedAt)
                self.assertEqual(view.observedAt, OBSERVED)
                self.assertEqual(len(paths), 1)
                self.assertEqual(last.read_bytes(), before)
                self.assertEqual(self.catalog.read_bytes(), self.catalog_before)
        view._runner = self.runner
        view.refresh()
        self.assertEqual(view.error, '')
        view.wait()
        self.assertEqual(view.error, '')
        self.assertEqual(len(paths), 2)

    def test_invalid_metadata_and_hash_mismatch_do_not_emit(self):
        good = fixture(self.catalog)
        invalid = [None, [], {}, dict(good, schema_version=True),
                   dict(good, schema_version=2), dict(good, inventory_kind='other'),
                   dict(good, catalog_sha256='0' * 64),
                   dict(good, execution_supported=True), dict(good, observed_at='secret'),
                   dict(good, observed_at='2026-11-15T10:20:30'),
                   dict(good, observed_at=42)]
        raw_values = [json.dumps(data) for data in invalid]
        raw_values += ['not-json secret', '{"schema_version":1,"schema_version":1}',
                       json.dumps(good)[:-1] + ',"extra":NaN}']
        for raw in raw_values:
            with self.subTest(raw=raw[:80]):
                def run(argv, **kwargs):
                    Path(argv[-1]).write_text(raw)
                    return SimpleNamespace(returncode=0)
                view = self.view(run)
                emitted = []
                view.refreshed.connect(emitted.append)
                view.refresh()
                self.assertTrue(view.wait(3000))
                self.assertTrue(view.error)
                self.assertEqual(view.observedAt, '')
                self.assertEqual(emitted, [])
                self.assertNotIn('secret', view.error + view.status)

    def test_missing_oversized_symlink_and_fifo_outputs_fail_without_hanging(self):
        for kind in ('missing', 'oversized', 'symlink', 'fifo'):
            with self.subTest(kind=kind):
                def run(argv, **kwargs):
                    path = Path(argv[-1])
                    if kind == 'oversized':
                        with path.open('wb') as stream:
                            stream.truncate(INVENTORY_LIMIT + 1)
                    elif kind == 'symlink':
                        path.symlink_to(self.catalog)
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    return SimpleNamespace(returncode=0)
                view = self.view(run)
                view.refresh()
                self.assertTrue(view.wait(3000))
                self.assertTrue(view.error)
                self.assertEqual(view.observedAt, '')

    def test_catalog_changed_during_run_is_stale(self):
        def run(argv, **kwargs):
            data = fixture(self.catalog)
            Path(argv[-1]).write_text(json.dumps(data))
            self.catalog.write_text('{"schema_version":2,"games":[],"new":true}')
            return SimpleNamespace(returncode=0)
        view = self.view(run)
        emitted = []
        view.refreshed.connect(emitted.append)
        view.refresh()
        view.wait()
        self.assertIn('catalog changed', view.error)
        self.assertEqual(emitted, [])

    def test_change_after_worker_before_queued_completion_is_stale(self):
        for target in ('catalog', 'snapshot'):
            with self.subTest(target=target):
                view = self.view()
                emitted = []
                view.refreshed.connect(emitted.append)
                view.refresh()
                job = view._job
                job.thread.join(3)  # Intentionally do not process queued signals.
                self.assertFalse(job.thread.is_alive())
                path = self.catalog if target == 'catalog' else job.path
                path.write_bytes(path.read_bytes() + b' ')
                self.assertTrue(view.wait(3000))
                self.app.processEvents()
                self.assertEqual(emitted, [])
                self.assertTrue(view.error)

    def test_unsafe_catalog_fails_before_runner_or_output_creation(self):
        for kind in ('symlink', 'fifo', 'missing'):
            with self.subTest(kind=kind):
                self.catalog.unlink(missing_ok=True)
                if kind == 'symlink':
                    selected = self.root / 'other.json'
                    selected.write_bytes(self.catalog_before)
                    self.catalog.symlink_to(selected)
                elif kind == 'fifo':
                    os.mkfifo(self.catalog)
                view = self.view()
                view.refresh()
                self.assertTrue(view.wait(3000))
                self.assertTrue(view.error)
                self.assertEqual(self.calls, [])
                self.assertFalse(self.output.exists())

    def test_close_joins_with_no_late_signal_even_when_engine_is_destroyed(self):
        run, entered, release = self.blocked_runner()
        view = self.view(run)
        emitted = []
        view.refreshed.connect(emitted.append)
        engine = QQmlEngine()
        engine.rootContext().setContextProperty('nativeRefresh', view)
        view.refresh()
        self.assertTrue(entered.wait(1))
        job = view._job
        # Simulate teardown while work is in flight. A small independent releaser
        # lets close synchronously join without pumping the dying engine.
        releaser = threading.Timer(0.02, release.set)
        releaser.start()
        try:
            shiboken6.delete(engine)
            view.close()
        finally:
            release.set()
            releaser.join()
        self.assertFalse(job.thread.is_alive())
        self.app.processEvents()
        self.assertEqual(emitted, [])
        self.assertFalse(view.busy)
        self.assertEqual(view.observedAt, '')
        self.assertFalse(view.refresh())
        self.assertTrue(view.wait(0))
        view.close()

    def test_deleted_qobject_never_receives_worker_completion(self):
        run, entered, release = self.blocked_runner()
        view = self.view(run)
        view.refresh()
        self.assertTrue(entered.wait(1))
        job = view._job
        try:
            shiboken6.delete(view)
        finally:
            release.set()
            job.thread.join(3)
        self.assertFalse(job.thread.is_alive())
        self.app.processEvents()  # Qt discarded the deleted receiver safely.


if __name__ == '__main__':
    unittest.main()
