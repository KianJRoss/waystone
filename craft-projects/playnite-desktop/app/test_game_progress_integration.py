"""Parent wiring: actual Main/Details selection reaches exact-ID cached progress view."""
import time
from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session


def test_main_details_exact_selection_refresh_and_clear():
    app = QGuiApplication.instance() or QGuiApplication([])
    class Repository:
        def __init__(self): self.calls = []
        def get_game(self, key):
            self.calls.append(key)
            return dict(game_key=key, achievements=[], completion_times=[], notices=[key], live_sync=False)
    repo = Repository()
    engine, model, root, warnings = create_session(app, headless=True, game_progress_repository=repo)
    view = engine.game_progress
    def settle(key):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            QTest.qWait(10)
            if view.gameKey == key and not view.busy and (not key or view.notices == [key]): return
        raise AssertionError((view.gameKey, view.status, view.notices))
    try:
        root.setVisible(True)
        panel = root.findChild(QObject, 'gameProgressPanel')
        assert panel is not None
        ids = [g['id'] for g in model._source_games]
        assert len(ids) >= 2 and ids[0] != ids[1]
        for key in ids[:2]:
            root.setProperty('selectedId', key)
            settle(key)
            assert panel.property('gameKey') == key
            assert repo.calls[-1] == key
        root.setProperty('selectedId', '')
        QTest.qWait(50)
        # Hidden panels are deliberately lazy: no new read, no visible stale row.
        assert not panel.property('visible')
        root.setProperty('selectedId', ids[0]); settle(ids[0])
        assert set(repo.calls) <= set(ids[:2])
        assert not warnings, warnings
    finally:
        view.shutdown()
        engine.controller_profile_commands.shutdown(); engine.controller_device_commands.shutdown()
        engine.controller_commands.shutdown(); engine.controller_live.shutdown()
        engine.unified_controller.shutdown(); engine.voice_media.shutdown(); engine.social_view.shutdown()
        root.setProperty('quitRequested', True); root.close(); engine.deleteLater(); QTest.qWait(30)
