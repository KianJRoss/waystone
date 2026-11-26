"""Capture the actual single QQuickWindow offscreen, synthetic services only."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_QUICK_BACKEND']='software'
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'craft-projects/playnite-desktop/app'))
from PySide6.QtCore import QRectF, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from test_desktop_overlay import FakeSocialView,FakeVoiceView,create_engine_with_context,load_desktop_overlay

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('output',type=Path)
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
app=QGuiApplication(['waystone-overlay-v2-evidence'])
with tempfile.TemporaryDirectory(prefix='waystone-overlay-v2-') as isolated:
    os.environ['XDG_CONFIG_HOME']=isolated
    social=FakeSocialView(friends=[
        {'xuid':'10001','gamertag':'PlayerOne','online':True,'presence':'Playing','currentGame':'Example Game','joinablePartyId':'party-join-fixture'},
        {'xuid':'10002','gamertag':'PlayerTwo','online':False},
        {'xuid':'10003','gamertag':'PlayerThree'}],parent=app)
    voice=FakeVoiceView(parent=app)
    engine=create_engine_with_context(social,voice);manager=load_desktop_overlay(engine)
    metadata={'fixture_only':True,'voice_calls':[],'social_calls':[],'captures':[]}
    try:
        surface=manager.findChild(QQuickWindow,'overlaySurfaceWindow')
        assert manager.findChildren(QQuickWindow)==[surface]
        def capture(label):
            QTest.qWait(100)
            image=surface.grabWindow()
            assert not image.isNull() and image.save(str(args.output/(label+'.png')))
            metadata['captures'].append({'file':label+'.png','size':[image.width(),image.height()],
                'native_windows':len(manager.findChildren(QQuickWindow)),'editing':manager.property('editing')})
        manager.setProperty('boundsOverride',QRectF(0,0,1200,900));manager.open()
        capture('edit-1200x900')
        manager.beginArrange(1,'move');capture('arrange-1200x900')
        manager.finishArrange()
        for name in ('overlayPartyWindow','overlayFriendsWindow'):
            manager.findChild(QObject,name).setProperty('pinned',True)
        manager.closeOverlay();capture('pinned-1200x900')
        manager.setProperty('boundsOverride',QRectF(0,0,800,600));manager.open();capture('reflow-800x600')
        manager.resetLayout();capture('edit-800x600')
        assert not social.calls and not voice.calls and not engine._warnings
        metadata['warnings']=engine._warnings
        (args.output/'capture-metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    finally:
        manager.closeOverlay()
        for item in surface.contentItem().childItems():
            if item.metaObject().indexOfProperty('shown')>=0:item.setProperty('shown',False)
        manager.deleteLater();engine.deleteLater();QTest.qWait(30)
print(args.output)
