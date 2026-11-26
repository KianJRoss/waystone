import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QObject,Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from voice_audio_routes_view import VoiceAudioRoutesView,owned_voice_pids

class Voice(QObject):
    changed=Signal()
    connected=False
class Router:
    def __init__(self): self.calls=[]
    def get_devices(self):
        self.calls.append('inventory')
        return {'sources':[{'name':'mic','description':'Desk microphone'}], 'sinks':[{'name':'out','description':'Headphones'}]}
    def route_selected(self,i,o,p):
        self.calls.append((i,o,p)); return {'errors':[]}
def wait(view):
    for _ in range(200):
        QTest.qWait(5)
        if not view.busy:return
    raise AssertionError('worker did not finish')
def test_constructor_silent_explicit_device_choice_and_new_voice_stream_route():
    app=QGuiApplication.instance() or QGuiApplication([])
    voice=Voice();router=Router();view=VoiceAudioRoutesView(voice,router=router,available=True,pids=lambda:{33})
    try:
        assert router.calls==[]
        view.refreshDevices();wait(view)
        assert view.inputs==[{'id':'mic','label':'Desk microphone'}]
        view.selectInput('mic');wait(view)
        assert router.calls[-1]==('mic','',set()) and not voice.connected
        voice.connected=True;voice.changed.emit();wait(view)
        assert router.calls[-1]==('mic','',{33})
        view.selectOutput('out');wait(view)
        assert view.outputId=='out'
        count=len(router.calls);view.selectInput('not-known');assert len(router.calls)==count
        voice.connected=False;voice.changed.emit();assert not view._timer.isActive()
    finally:view.shutdown()
def test_pid_discovery_excludes_games_and_unrelated_processes(tmp_path):
    def proc(pid,children,exe):
        root=tmp_path/str(pid);(root/'task'/str(pid)).mkdir(parents=True)
        (root/'task'/str(pid)/'children').write_text(children)
        (root/'exe').symlink_to(exe)
    chromium=tmp_path/'QtWebEngineProcess';chromium.touch()
    game=tmp_path/'game';game.touch()
    proc(10,'11 12',game);proc(11,'13',chromium);proc(12,'',game);proc(13,'',chromium);proc(14,'',chromium)
    assert owned_voice_pids(tmp_path,10)=={10,11,13}

def test_failed_inventory_is_not_presented_as_healthy_empty_devices():
    app=QGuiApplication.instance() or QGuiApplication([])
    class Broken(Router):
        def get_devices(self):return {'sources':[],'sinks':[],'error':'ERR_PACTL_COMMAND_FAILED'}
    view=VoiceAudioRoutesView(Voice(),router=Broken(),available=True)
    try:
        view.refreshDevices();wait(view)
        assert 'unavailable' in view.status and view.inputs==[]
    finally:view.shutdown()
