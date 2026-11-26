"""Owned-child lifecycle tests; disposable fake executable, no engine/devices."""
import time
from pathlib import Path
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from controller_engine_host import ControllerEngineHost

_app = None

def app():
    global _app
    _app = QGuiApplication.instance() or QGuiApplication([])
    return _app

def wait(predicate):
    end = time.monotonic() + 4
    while time.monotonic() < end:
        app().processEvents()
        if predicate():
            return
        QTest.qWait(10)
    assert predicate()

def executable(tmp_path, source):
    p = tmp_path / 'fake-engine'
    p.write_text('#!/usr/bin/python3\n' + source)
    p.chmod(0o700)
    return p

def test_construction_and_missing_binary_are_inert(tmp_path):
    app()
    h = ControllerEngineHost(tmp_path/'absent', tmp_path/'data')
    assert not h.running
    h.start()
    assert not h.running and 'not packaged' in h.status
    assert not (tmp_path/'data').exists()

def test_missing_production_settings_never_launches(tmp_path):
    app()
    p = executable(tmp_path, 'raise AssertionError("must not execute")\n')
    h = ControllerEngineHost(p, tmp_path/'data')
    h.start()
    assert not h.running and 'migrated' in h.status
    assert not (tmp_path/'data').exists()

def test_fixture_rejects_legacy_root(tmp_path):
    app()
    p = executable(tmp_path, 'raise AssertionError("must not execute")\n')
    h = ControllerEngineHost(p, tmp_path/'data', tmp_path/'legacy', fixture=True)
    h.start()
    assert not h.running and 'Fixture mode' in h.status

def test_ready_single_child_and_clean_shutdown(tmp_path, monkeypatch):
    app()
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    p = executable(tmp_path, 'import sys\nprint("WAYSTONE_ENGINE_READY=1",flush=True)\nfor line in sys.stdin:\n if line.strip()=="shutdown": break\n')
    h = ControllerEngineHost(p, tmp_path/'data', fixture=True)
    events = []
    h.ready.connect(lambda: events.append('ready'))
    try:
        h.start()
        wait(lambda: h.available)
        pid = h._process.processId()
        h.start()
        assert h._process.processId() == pid and events == ['ready']
        h.shutdown()
        wait(lambda: not h.running)
        assert not h.available and 'exit 0' in h.status
    finally:
        h.shutdown()

def test_early_failure_has_no_ready_or_retry(tmp_path):
    app()
    p = executable(tmp_path, 'raise SystemExit(3)\n')
    h = ControllerEngineHost(p, tmp_path/'data', fixture=True)
    events = []
    h.ready.connect(lambda: events.append('ready'))
    h.start()
    wait(lambda: 'exit 3' in h.status)
    assert not h.available and not h.running and events == []
