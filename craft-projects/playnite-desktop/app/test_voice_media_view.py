"""Native media host consent/session tests without Chromium or Xbox calls."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtGui import QGuiApplication
from voice_media_view import VoiceMediaView

class Social(QObject):
    connectionChanged=Signal()
    connected=True

def host():
    app=QGuiApplication.instance() or QGuiApplication([])
    social=Social();view=VoiceMediaView(social,available=True)
    view._test_app=app;view._test_social=social
    return view

def event(view, **data):
    view.receive(json.dumps({'source':'xodus-voice','nonce':view.nonce,**data}))

def test_constructor_silent_and_explicit_activation():
    view=host();commands=[];view.command.connect(commands.append)
    assert not view.active and not view.ready and commands==[]
    view.startParty('party-1');assert view.active and commands==[]
    event(view,event='ready');assert len(commands)==1
    assert json.loads(commands[0])['command']=={'type':'start','partyId':'party-1'}
    event(view,event='ready');assert len(commands)==1
    view.shutdown()

def test_microphone_requires_current_explicit_unmute_and_connection():
    view=host();view.activate()
    assert not view.allowMicrophone(QUrl('file:///'))
    event(view,event='ready');event(view,event='state',state={'connected':True,'muted':True})
    assert not view.allowMicrophone(QUrl('file:///'))
    view.setMuted(False)
    assert view.allowMicrophone(QUrl('file:///'))
    assert not view.allowMicrophone(QUrl('https://attacker.invalid'))
    view.setMuted(True);assert not view.allowMicrophone(QUrl('file:///'))
    view.setMuted(False);view.shutdown();assert not view.allowMicrophone(QUrl('file:///'))

def test_wrong_session_dropped_and_disconnect_destroys_media():
    view=host();view.activate();old=view.nonce
    view.receive(json.dumps({'source':'xodus-voice','nonce':'wrong','event':'ready'}))
    assert not view.ready
    view._test_social.connected=False;view._test_social.connectionChanged.emit()
    assert not view.active and not view.nonce
    view._test_social.connected=True;view.activate()
    assert view.nonce!=old
    view.receive(json.dumps({'source':'xodus-voice','nonce':old,'event':'ready'}));assert not view.ready
    view.shutdown()

def test_only_known_native_calls_and_current_reply_relayed():
    view=host();view.activate();requests=[];replies=[]
    view.nativeRequest.connect(requests.append);view.reply.connect(replies.append)
    event(view,type='nativeCall',method='arbitrary_method',callId='a',args=[])
    assert not requests
    event(view,type='nativeCall',method='startXboxParty',callId='b',args=['offer'])
    assert len(requests)==1
    view.deliverReply(json.dumps({'nonce':'old','callId':'b','result':True}));assert not replies
    view.deliverReply(json.dumps({'source':'xodus-voice-parent','nonce':view.nonce,'callId':'b','result':True}))
    assert len(replies)==1
    view.shutdown()

def test_web_page_cannot_authorize_its_own_microphone():
    view=host();view.activate();replies=[];view.reply.connect(replies.append)
    event(view,event='ready');event(view,event='state',state={'connected':True,'muted':True})
    event(view,type='nativeCall',method='allowXboxMicrophone',callId='mic',args=[])
    assert json.loads(replies[-1])['error']=='ERR_MIC_PERMISSION_DENIED'
    view.setMuted(False)
    event(view,type='nativeCall',method='allowXboxMicrophone',callId='mic2',args=[])
    assert json.loads(replies[-1])['result'] is True
    view.shutdown()

def test_leave_closes_after_matching_completion_and_rejects_old_reply():
    view=host();view.startParty('party-fixture');commands=[];view.command.connect(commands.append)
    event(view,event='ready');view.leave()
    assert view.active
    cid=json.loads(commands[-1])['commandId']
    event(view,event='commandComplete',commandId=cid,error=None)
    assert not view.active and not view.ready and view.muted

def test_ready_without_connection_does_not_enable_unmute():
    view=host();view.activate();commands=[];view.command.connect(commands.append)
    event(view,event='ready');view.setMuted(False)
    assert not commands and not view.allowMicrophone(QUrl('file:///'))
    view.shutdown()

def test_social_worker_native_call_roundtrip_and_sanitized_failure():
    from xodus_social_view import XodusSocialView
    from test_xodus_social_view import AsyncMockSocialTransport, wait_until
    app=QGuiApplication.instance() or QGuiApplication([])
    social= XodusSocialView(transport=AsyncMockSocialTransport(),account='gamer@xbox.com')
    view=VoiceMediaView(social,available=True)
    class Signaling:
        def __init__(self):
            from types import SimpleNamespace
            self.calls=[];self.state=SimpleNamespace(party_id='party-fixture',phase='connecting',members=())
        async def start_xbox_party(self,offer,party_id=None):
            self.calls.append((offer,party_id));return {'id':'party-fixture','answer':{}}
        async def xbox_voice_allowed(self,xuid):
            raise RuntimeError('Bearer secret SDP exception')
        async def leave_xbox_party(self): pass
    try:
        social.connectService();assert wait_until(lambda:social.connected,2000)
        fake=Signaling();social._voice_signaling=fake
        view.activate();replies=[];view.reply.connect(replies.append)
        event(view,type='nativeCall',method='startXboxParty',callId='start1',args=['offer','party-fixture'])
        assert wait_until(lambda:len(replies)==1,2000)
        assert fake.calls==[('offer','party-fixture')]
        assert json.loads(replies[0])['result']['id']=='party-fixture'
        event(view,type='nativeCall',method='xboxVoiceAllowed',callId='policy1',args=['123'])
        assert wait_until(lambda:len(replies)==2,2000)
        assert json.loads(replies[1])['error']=='ERR_VOICE_SIGNALING_FAILED'
        assert 'secret' not in replies[1]
    finally:
        view.shutdown();social.shutdown()

def test_failed_leave_keeps_retry_available_with_microphone_disabled():
    view=host();view.activate();commands=[];view.command.connect(commands.append)
    event(view,event='ready');event(view,event='state',state={'connected':True,'muted':True})
    view.setMuted(False);view.leave()
    cid=json.loads(commands[-1])['commandId']
    event(view,event='commandComplete',commandId=cid,error='ERR_VOICE_SIGNALING_FAILED')
    assert view.active and view.error=='ERR_LEAVE_UNCONFIRMED_RETRY'
    assert not view.allowMicrophone(QUrl('file:///'))
    view.leave();assert json.loads(commands[-1])['commandId']!=cid
    view.shutdown()

def test_helper_eof_stops_media_without_waiting_for_another_user_click():
    from xodus_social_view import XodusSocialView
    from test_xodus_social_view import AsyncMockSocialTransport,wait_until
    app=QGuiApplication.instance() or QGuiApplication([])
    transport=AsyncMockSocialTransport();social=XodusSocialView(transport=transport,account='gamer@xbox.com')
    view=VoiceMediaView(social,available=True)
    try:
        social.connectService();assert wait_until(lambda:social.connected,2000)
        view.activate();event(view,event='ready')
        social._worker.loop.call_soon_threadsafe(transport._read_queue.put_nowait, None)
        assert wait_until(lambda:not view.active and not social.connected,2000)
        assert view.muted and not view.allowMicrophone(QUrl('file:///'))
    finally: view.shutdown();social.shutdown()

def test_media_failure_revokes_consent_and_cleans_up_only_owned_voice():
    view=host();view.activate();requests=[];view.nativeRequest.connect(requests.append)
    event(view,event='ready');event(view,event='state',state={'connected':True,'muted':True})
    view.setMuted(False);view.mediaFailed()
    assert not view.active and view.muted and view.error=='ERR_VOICE_MEDIA_FAILED'
    assert not view.allowMicrophone(QUrl('file:///'))
    assert len(requests)==1 and json.loads(requests[0])['method']=='leaveXboxParty'
    view.mediaFailed();assert len(requests)==1

def test_malformed_bridge_types_do_not_escape_qt_slots():
    view=host();view.activate()
    event(view,type='nativeCall',method=[],callId='bad',args=[])
    view.deliverReply(json.dumps({'nonce':view.nonce,'callId':[],'result':True}))
    assert not view.ready and not view.connected
    view.shutdown()

def test_audio_enumeration_without_login_or_capture_and_reuses_host_for_party():
    view=host();view._test_social.connected=False
    commands=[];requests=[]
    view.command.connect(commands.append);view.nativeRequest.connect(requests.append)
    view.refreshDevices()
    assert view.active and not view.sessionActive and not commands
    event(view,event='ready')
    assert json.loads(commands[-1])['command']=={'type':'enumerateDevices'}
    assert not requests and not view.allowMicrophone(QUrl('file:///'))
    event(view,event='devices',inputs=[{'id':'mic','label':'Desk microphone'}],outputs=[{'id':'speaker','label':'Headphones'}],selectedInputId=None,selectedOutputId=None)
    assert view.audioInputs==[{'id':'mic','label':'Desk microphone'}]
    assert view.audioOutputs==[{'id':'speaker','label':'Headphones'}]
    nonce=view.nonce;view._test_social.connected=True;view.startParty('party-test')
    assert view.nonce==nonce and view.sessionActive
    assert json.loads(commands[-1])['command']=={'type':'start','partyId':'party-test'}
    view.shutdown()

def test_audio_selection_ack_is_authoritative_and_bad_frames_are_atomic():
    view=host();view.activate();event(view,event='ready')
    commands=[];view.command.connect(commands.append)
    view.selectDevices('mic','speaker')
    assert view.inputId==view.outputId==''
    event(view,event='devicesSelected',inputId='mic',outputId='speaker')
    assert (view.inputId,view.outputId)==('mic','speaker')
    event(view,event='devices',inputs=[{'id':'other','label':'Other'}],outputs=[{'id':'bad','label':'bad\nlabel'}],selectedInputId='other',selectedOutputId='other')
    assert view.audioInputs==[] and view.inputId=='mic'
    event(view,event='commandComplete',commandId=json.loads(commands[-1])['commandId'],error='ERR_DEVICE_NOT_FOUND')
    assert 'disconnected' in view.audioStatus and not view.error
    view.shutdown()

def test_settings_leave_never_requests_party_leave():
    view=host();view.refreshDevices();event(view,event='ready')
    commands=[];requests=[];view.command.connect(commands.append);view.nativeRequest.connect(requests.append)
    view.leave()
    assert not view.active and not commands and not requests

def test_speaking_state_validated_and_cleared_on_disconnect():
    view=host();view.activate();event(view,event='ready')
    event(view,event='state',state={'connected':True,'muted':True,'speakingMembers':['2','1'],'speakingAvailable':True})
    assert view.speakingMembers==['2','1'] and view.speakingAvailable is True
    # invalid entries/types are ignored (keep previous valid state)
    event(view,event='state',state={'connected':True,'muted':True,'speakingMembers':['bad',2],'speakingAvailable':'yes'})
    assert view.speakingMembers==['2','1'] and view.speakingAvailable is True
    # disconnect clears speaking state
    event(view,event='state',state={'connected':False,'muted':True,'speakingMembers':['9'],'speakingAvailable':True})
    assert view.speakingMembers==[] and view.speakingAvailable is False
    view.shutdown()
    assert view.speakingMembers==[] and view.speakingAvailable is False
