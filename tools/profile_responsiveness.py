"""Offline W1 GUI-thread probe: explicit read-only library, isolated state, no IPC/processes.

Run on a development machine: uv run --offline --with pyside6 --with mcp python
  tools/profile_responsiveness.py --data-root ~/Work/waystone-data-ro --output research/w1/baseline.json
Scroll is a model-role traversal proxy; no media bytes are supplied or decoded.
"""
import argparse
import cProfile
import hashlib
import json
import os
from pathlib import Path
import pstats
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'craft-projects/playnite-desktop/app'
sys.path[:0] = [str(APP), str(ROOT)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', action='store_true', help='Enable cProfile (adds substantial overhead)')
    args = parser.parse_args()
    data = args.data_root.resolve(strict=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = {'kind': 'W1-offscreen-probe-v1', 'profile_enabled': args.profile, 'python': sys.version, 'measurements': {},
               'limits': ['No media bytes: scroll is role traversal, not raster/compositor FPS',
                          'Controller engine is a stopped fixture; construction only',
                          'No native connection inventory supplied', 'Warm filesystem cache not controlled'],
               'external_attempts': []}
    results['input_sha256'] = {}
    for relative in ('library/native-library-v3.sqlite3', 'organizer-state-v1.sqlite3',
                     'catalog-links-v1.sqlite3', 'library-reconciliation-v1.json'):
        with (data/relative).open('rb') as stream:
            results['input_sha256'][relative] = hashlib.file_digest(stream, 'sha256').hexdigest()
    with tempfile.TemporaryDirectory(prefix='w1-', dir=args.output.parent) as temporary:
        sandbox = Path(temporary).resolve()
        os.environ.update(HOME=str(sandbox), XDG_DATA_HOME=str(sandbox/'data'),
                          XDG_CONFIG_HOME=str(sandbox/'config'), XDG_CACHE_HOME=str(sandbox/'cache'),
                          QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                          QT_QUICK_CONTROLS_STYLE='Basic')
        import PySide6
        results['pyside_version'] = PySide6.__version__
        from PySide6.QtCore import QObject, Signal, Property, Slot, Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtTest import QTest
        from app import create_session
        class StoppedHost(QObject):
            ready = Signal()
            changed = Signal()
            @Property(bool, notify=changed)
            def available(self): return False
            @Property(str, notify=changed)
            def status(self): return 'Offline profiling fixture'
            @Slot()
            def start(self): pass
            @Slot()
            def shutdown(self): pass
        class EmptyReceipts:
            def snapshot(self): return []
        def denied(*unused, **kw):
            results['external_attempts'].append('blocked')
            raise OSError('W1 probe forbids external IPC/network/processes')
        app = QGuiApplication(['w1-offline-probe'])
        def measure(name, operation, repeats=1):
            values = []; profiler = cProfile.Profile()
            for _ in range(repeats):
                start = time.perf_counter()
                if args.profile: profiler.enable()
                value = operation()
                if args.profile: profiler.disable()
                values.append((time.perf_counter()-start)*1000)
            hot = sorted(pstats.Stats(profiler).stats.items(), key=lambda row: row[1][3], reverse=True)[:12] if args.profile else []
            results['measurements'][name] = {'ms': values, 'median_ms': statistics.median(values),
                'hot_cumulative': [{'file': k[0].replace(str(ROOT), '.'), 'line': k[1], 'function': k[2],
                                    'calls': v[1], 'cumulative_ms': v[3]*1000} for k,v in hot]}
            print(name, round(statistics.median(values), 2), flush=True)
            return value
        organizer = sandbox/'organizer.sqlite3'
        shutil.copyfile(data/'organizer-state-v1.sqlite3', organizer)
        links = sandbox/'links.sqlite3'
        shutil.copyfile(data/'catalog-links-v1.sqlite3', links)
        with patch.object(socket.socket, 'connect', denied), patch.object(subprocess, 'Popen', denied):
            engine, model, root, warnings = measure('startup', lambda: create_session(app, headless=True,
                library_db=data/'library/native-library-v3.sqlite3', organizer_state=organizer,
                link_state=links, reconciliation=data/'library-reconciliation-v1.json',
                native_controller_ui=True, studio_host=StoppedHost(), receipt_registry=EmptyReceipts(),
                provider_jobs_enabled=False))
            results['games'] = len(model.games)
            results['source_games'] = len(model._source_games)
            results['source_variants'] = sum(len(g['variants']) for g in model._source_games)
            results['display_variants'] = sum(len(g['variants']) for g in model.games)
            resets = []
            model.modelReset.connect(lambda: resets.append(True))
            measure('receipt_tick_unchanged', engine.refresh_receipts, 5)
            results['receipt_tick_model_resets'] = len(resets)
            for query in ('', 'mario', 'skyrim', 'nonexistent-w1-title'):
                measure('filter_'+(query or 'all'), lambda q=query: model.setFilters(q, 'All sources', 'All platforms', 'All states'), 3)
            model.setFilters('', 'All sources', 'All platforms', 'All states')
            roles = list(model.roleNames())
            measure('scroll_200_rows_all_roles', lambda: [model.data(model.index(i), r) for i in range(min(200, model.rowCount())) for r in roles], 3)
            selected = model.games[len(model.games)//2]['id']
            measure('details', lambda: model.details(selected), 5)
            root.setFlag(Qt.WindowDoesNotAcceptFocus, True)
            root.setVisible(True); QTest.qWait(80)
            measure('controllers_open_slot', root.openControllers)
            measure('controllers_first_80ms_event_drain', lambda: QTest.qWait(80))
            root.showLibraryView(); QTest.qWait(80)
            measure('controllers_reopen_slot', root.openControllers)
            QTest.qWait(80); root.showLibraryView(); QTest.qWait(80)
            measure('controllers_third_open_slot', root.openControllers)
            results['warnings'] = warnings[:30]
            root.close()
            app.aboutToQuit.emit()
            engine.deleteLater(); QTest.qWait(50)
    args.output.write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps({'output': str(args.output), 'games': results['games'],
                      'median_ms': {k: round(v['median_ms'], 2) for k,v in results['measurements'].items()},
                      'unchanged_receipt_resets': results['receipt_tick_model_resets']}))

if __name__ == '__main__': main()
