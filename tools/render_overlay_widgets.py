"""Offscreen-only widget evidence; synthetic party/friends, no native services."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT/'craft-projects/playnite-desktop/app'
sys.path.insert(0, str(APP))
from PySide6.QtCore import QObject, QMetaObject, QRectF
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor
from PySide6.QtTest import QTest
from test_desktop_overlay import FakeSocialView, FakeVoiceView, create_engine_with_context, load_desktop_overlay

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
parser.add_argument('--width', type=int, default=1200)
parser.add_argument('--height', type=int, default=900)
parser.add_argument('--arrange', action='store_true')
parser.add_argument('--presence-fixture', action='store_true', help='explicit synthetic online/offline/unknown rows')
parser.add_argument('--backdrop', action='store_true', help='synthetic grayscale grid to inspect alpha, not a desktop capture')
args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
app = QGuiApplication(['waystone-overlay-evidence'])
with tempfile.TemporaryDirectory(prefix='overlay-evidence-') as isolated:
    os.environ['XDG_CONFIG_HOME'] = isolated
    friends = [
        {'xuid':'10001','gamertag':'PlayerOne','online':True,'presence':'Playing a game'},
        {'xuid':'10002','gamertag':'PlayerTwo','online':False},
        {'xuid':'10003','gamertag':'PlayerThree'},
    ] if args.presence_fixture else None
    social = FakeSocialView(friends=friends, parent=app); voice = FakeVoiceView(parent=app)
    engine = create_engine_with_context(social, voice)
    manager = load_desktop_overlay(engine)
    manager.setProperty('persistenceEnabled', False)
    manager.setProperty('boundsOverride', QRectF(0, 0, args.width, args.height))
    try:
        QMetaObject.invokeMethod(manager, 'open'); QTest.qWait(150)
        if args.arrange:
            manager.beginArrange(1, 'move'); QTest.qWait(30)
        canvas = QImage(args.width, args.height, QImage.Format_ARGB32)
        canvas.fill(QColor('#000000')); painter = QPainter(canvas)
        if args.backdrop:
            # Test scene only; never read a desktop surface or download an image.
            for y in range(0, args.height, 64):
                for x in range(0, args.width, 64):
                    painter.fillRect(x, y, 64, 64, QColor('#1f1f1f' if (x//64+y//64)%2 else '#3b3b3b'))
            painter.setPen(QColor('#f2f2f2'))
            painter.drawText(24, args.height-24, 'SYNTHETIC BACKDROP / FIXTURE PRESENCE — NOT A GAME OR DESKTOP CAPTURE')
        for name in ('overlayPartyWindow','overlayFriendsWindow','overlayToolsWindow','overlayToolbarWindow'):
            window = manager.findChild(QObject, name)
            image = window.grabWindow()
            assert not image.isNull(), name
            assert image.save(str(args.output/(name+'.png')))
            painter.drawImage(window.x(), window.y(), image)
        painter.end(); assert canvas.save(str(args.output/'overview.png'))
        assert not social.calls and not voice.calls
        if engine._warnings: raise AssertionError(engine._warnings)
    finally:
        QMetaObject.invokeMethod(manager, 'closeOverlay')
        for name in ('overlayPartyWindow','overlayFriendsWindow','overlayToolsWindow','overlayToolbarWindow'):
            manager.findChild(QObject, name).setProperty('shown', False)
        manager.deleteLater(); engine.deleteLater(); QTest.qWait(50)
print(args.output)
