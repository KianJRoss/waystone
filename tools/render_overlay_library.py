"""Capture offscreen evidence screenshots for Waystone Overlay Quick Library (Xbox-guide).

Produces:
1. now-playing.png: Now playing card with Resume and Quit actions for the active game.
2. recent-pinned-all.png: Recent games row, Pinned games row, All installed list.
3. search.png: Search input and filtered results.
4. switch-confirmation.png: "Switch to X? Y will be closed." safe switch confirmation dialog.
5. game-card.png: Focused game detail card with primary Play action.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'craft-projects/playnite-desktop/app'))
sys.path.insert(0, str(ROOT / 'tests'))

from PySide6.QtCore import QRectF, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QQuickItem
from PySide6.QtTest import QTest

from test_desktop_overlay import FakeSocialView, FakeVoiceView, create_engine_with_context, load_desktop_overlay
from overlay_library import OverlayLibraryBridge
from test_overlay_library import FakeLibraryModel, FakeLaunchView, make_sample_games


def render_all(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication(['waystone-overlay-library-evidence'])

    with tempfile.TemporaryDirectory(prefix='waystone-overlay-library-') as isolated:
        os.environ['XDG_CONFIG_HOME'] = isolated

        social = FakeSocialView(parent=app)
        voice = FakeVoiceView(parent=app)
        engine = create_engine_with_context(social, voice)

        games = make_sample_games()
        model = FakeLibraryModel(games, parent=app)
        launch = FakeLaunchView(running_id="game-elden-ring", running_title="Elden Ring", parent=app, closes_on_stop=False)
        bridge = OverlayLibraryBridge(library_model=model, launch_view=launch, parent=app)
        engine.rootContext().setContextProperty("overlayLibrary", bridge)

        manager = load_desktop_overlay(engine)
        metadata = {'fixture_only': True, 'captures': []}

        try:
            surface = manager.findChild(QQuickWindow, 'overlaySurfaceWindow')
            assert surface is not None

            library_window = manager.findChild(QQuickItem, 'overlayLibraryWindow')
            library_panel = manager.findChild(QQuickItem, 'overlayLibraryPanel')

            def capture(label):
                QTest.qWait(120)
                image = surface.grabWindow()
                assert not image.isNull()
                filepath = output_dir / f"{label}.png"
                image.save(str(filepath))
                metadata['captures'].append({
                    'file': f"{label}.png",
                    'size': [image.width(), image.height()],
                    'editing': manager.property('editing'),
                })
                print(f"Captured {filepath}")

            manager.setProperty('boundsOverride', QRectF(0, 0, WIDTH, HEIGHT))
            manager.open()
            library_window.setProperty('shown', True)
            manager.selectWidget(4)

            # 1. Now Playing screenshot (Elden Ring active)
            capture('now-playing')

            # 2. Game Card detail
            bridge.selectGame("game-balatro")
            capture('game-card')

            # 3. Switch confirmation dialog
            bridge.playGame("game-balatro")  # triggers switch prompt because Elden Ring is running
            capture('switch-confirmation')
            bridge.confirmSwitch()  # the fake game does not close: shows the waiting state
            capture('switch-closing')
            bridge.cancelSwitch()
            bridge.clearSelectedGame()
            launch.stop_calls = 0

            # 4. Search filtered view
            search_field = library_panel.findChild(QObject, "overlayLibrarySearchField")
            if search_field:
                search_field.setProperty("text", "")
            library_panel.navigate("details")  # Y: on-screen letters
            for action in ("right", "right", "right", "right", "right", "right", "right", "right", "right", "right",
                           "right", "right", "accept"):  # M
                library_panel.navigate(action)
            capture('search')
            library_panel.navigate("back")

            # 5. Full Overview: Recent, Pinned, All Installed (with no game running)
            if search_field:
                search_field.setProperty("text", "")
            launch.closes_on_stop = True
            launch.stopActiveGame()
            launch.pollGameState()  # Elden Ring closed
            QTest.qWait(50)
            capture('recent-pinned-all')

            metadata['warnings'] = engine._warnings
            (output_dir / 'capture-metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
            print("Capture metadata written successfully.")
        finally:
            manager.closeOverlay()
            for item in surface.contentItem().childItems():
                if item.metaObject().indexOfProperty('shown') >= 0:
                    item.setProperty('shown', False)
            manager.deleteLater()
            engine.deleteLater()
            QTest.qWait(30)


WIDTH, HEIGHT = 1200, 900

if __name__ == '__main__':
    target_dir = ROOT / 'research/w5-v2/library'
    if len(sys.argv) > 1:
        target_dir = Path(sys.argv[1])
    if len(sys.argv) > 2:
        WIDTH, HEIGHT = (int(v) for v in sys.argv[2].split('x'))
    render_all(target_dir)
