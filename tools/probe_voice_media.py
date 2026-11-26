"""Offline real Chromium->QtWebChannel probe. No Xbox helper/party/microphone."""
import json, os, sys
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_QUICK_BACKEND']='software'
from PySide6.QtWebEngineQuick import QtWebEngineQuick
QtWebEngineQuick.initialize()
from PySide6.QtCore import QObject, Signal, QTimer, QUrl, QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'craft-projects/playnite-desktop/app'
sys.path.insert(0,str(APP))
from voice_media_view import VoiceMediaView
class Social(QObject):
    connectionChanged=Signal()
    connected=True
app=QGuiApplication([])
social=Social();voice=VoiceMediaView(social,available=True)
engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('xboxVoiceMedia',voice)
engine.addImportPath(str(APP));warnings=[];engine.warnings.connect(lambda rows:warnings.extend(r.toString() for r in rows))
voice.activate()  # no initial start command; strictly media IPC only
engine.loadData(b'import QtQuick\nWindow { width:64; height:64; visible:true; VoiceMediaPane { anchors.fill:parent } }',QUrl.fromLocalFile(str(APP/'offline_voice_probe.qml')))
result={'ready':False,'connected':False,'muted':True};sent=False
requests=[];voice.nativeRequest.connect(requests.append)
def finish():
    result.update(ready=voice.ready,connected=voice.connected,muted=voice.muted,warnings=warnings,roundtrip_error=voice.error,native_requests=len(requests))
    voice.shutdown();engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    print(json.dumps(result));app.quit()
def poll():
    global sent
    if voice.ready and not sent:
        sent=True
        # Exercise Python signal -> real Chromium -> validated command -> Qt slot.
        # Invalid command must NOT create a peer, party or microphone stream.
        voice._send({'type':'setMuted'})
    if voice.error: finish()
timer=QTimer();timer.setInterval(50);timer.timeout.connect(poll);timer.start()
QTimer.singleShot(10000,finish)
app.exec()
sys.exit(0 if result['ready'] and not result['connected'] and result['muted'] and not warnings and result['roundtrip_error']=='ERR_INVALID_MUTED_TYPE' and not requests else 1)
