"""Lazy media-only Xbox voice host. No WebEngine/process/network/mic on construction."""
from __future__ import annotations
import json
from pathlib import Path
import re
import secrets
from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl, QSettings

MAX_FRAME = 262144
METHODS = {'startXboxParty', 'acceptXboxPartyInvite', 'confirmXboxParty',
           'leaveXboxParty', 'xboxVoiceAllowed'}
SAFE_CODE = re.compile(r'ERR_[A-Z0-9_]{1,56}\Z')


class VoiceMediaView(QObject):
    changed = Signal()
    command = Signal(str)
    reply = Signal(str)
    nativeRequest = Signal(str)

    def __init__(self, social, available=False, parent=None, settings=None):
        super().__init__(parent)
        self._social = social
        self._available = available
        self._active = self._ready = self._connected = False
        self._muted = True
        self._nonce = self._error = ''
        self._initial = None
        self._session_requested = False
        self._audio_inputs = []; self._audio_outputs = []
        self._input_id = self._output_id = ''
        self._audio_status = 'Refresh devices to choose your microphone and speakers.'
        self._enumerate_pending = False
        self._audio_commands = set()
        self._muted_members = []
        self._speaking_members = []
        self._speaking_available = False
        self._settings = settings or QSettings('Waystone', 'Waystone')
        self._peer_volumes = self._read_peer_volumes()
        self._seq = 0
        self._closing_id = None
        self._pending = set()
        self._mic_authorized = False
        self._mic_timer = QTimer(self)
        self._mic_timer.setSingleShot(True)
        self._mic_timer.timeout.connect(self._revoke_mic)
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self.mediaFailed)
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self._leave_timeout)
        social.connectionChanged.connect(self._connection_changed)
        if hasattr(social,'voiceConnectionLost'):
            social.voiceConnectionLost.connect(self.mediaFailed)
        if hasattr(social, 'voiceReply'):
            social.voiceReply.connect(self.deliverReply)
            self.nativeRequest.connect(social.requestVoiceCall)

    @Property(bool, constant=True)
    def available(self): return self._available
    @Property(bool, notify=changed)
    def active(self): return self._active
    @Property(bool, notify=changed)
    def sessionActive(self): return self._session_requested
    @Property('QVariantList', notify=changed)
    def audioInputs(self): return [dict(r) for r in self._audio_inputs]
    @Property('QVariantList', notify=changed)
    def audioOutputs(self): return [dict(r) for r in self._audio_outputs]
    @Property(str, notify=changed)
    def inputId(self): return self._input_id
    @Property(str, notify=changed)
    def outputId(self): return self._output_id
    @Property(str, notify=changed)
    def audioStatus(self): return self._audio_status
    @Property('QStringList', notify=changed)
    def mutedMembers(self): return list(self._muted_members)
    @Property('QStringList', notify=changed)
    def speakingMembers(self): return list(self._speaking_members)
    @Property(bool, notify=changed)
    def speakingAvailable(self): return self._speaking_available
    @Property('QVariantMap', notify=changed)
    def peerVolumes(self): return dict(self._peer_volumes)
    @Property(bool, notify=changed)
    def ready(self): return self._ready
    @Property(bool, notify=changed)
    def connected(self): return self._connected
    @Property(bool, notify=changed)
    def muted(self): return self._muted
    @Property(str, notify=changed)
    def nonce(self): return self._nonce
    @Property(str, notify=changed)
    def error(self): return self._error
    @Property(str, notify=changed)
    def status(self):
        if self._error:
            return {
                'ERR_LEAVE_UNCONFIRMED_RETRY': 'Microphone off · Xbox has not confirmed Leave; retry Leave',
                'ERR_MIC_PERMISSION_DENIED': 'Microphone permission denied · choose Unmute to retry',
                'ERR_VOICE_MEDIA_FAILED': 'Voice media stopped · reconnect to retry',
            }.get(self._error, 'Voice connection failed · Leave and reconnect')
        if self._connected: return 'Connected · microphone muted' if self._muted else 'Connected · microphone live'
        if self._active: return 'Connecting voice…' if self._session_requested else 'Audio settings ready · no party joined'
        return 'Voice ready · microphone off' if self._available else 'Voice media unavailable in this session'
    @Property(QUrl, constant=True)
    def pageUrl(self):
        return QUrl.fromLocalFile(str(Path(__file__).resolve().parents[3] / 'adapters/xodus_voice/qt_voice.html'))

    def activate(self, initial=None):
        """initial=None is the offline media/IPC probe: never allocates a party."""
        if self._active or not self._available or (initial is not None and not self._social.connected): return False
        self._nonce = secrets.token_urlsafe(24)
        self._active = True
        self._ready = self._connected = False
        self._muted = True
        self._error = ''
        self._speaking_members = []
        self._speaking_available = False
        self._initial = initial
        self._session_requested = initial is not None
        self._closing_id = None
        self._load_timer.start(10000)
        self.changed.emit()
        return True

    @Slot(str)
    def startParty(self, party_id=''):
        if party_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', party_id): return
        if not self._social.connected or self._session_requested: return
        command = {'type':'start', **({'partyId':party_id} if party_id else {})}
        if self._active:
            self._session_requested = True
            if self._ready: self._send(command)
            else: self._initial = command
            self.changed.emit()
        else:
            self.activate(command)

    def _send(self, command):
        self._seq += 1
        cid = f'qt_{self._seq}'
        self.command.emit(json.dumps({'target':'xodus-voice','nonce':self._nonce,
            'type':'command','commandId':cid,'command':command}))
        return cid

    @Slot()
    def refreshDevices(self):
        if not self._available: return
        self._enumerate_pending = True
        if not self._active: self.activate()
        if self._ready:
            self._enumerate_pending = False
            self._audio_commands.add(self._send({'type':'enumerateDevices'}))
        self._audio_status = 'Reading voice audio devices…'
        self.changed.emit()

    @Slot(str, str)
    def selectDevices(self, input_id, output_id):
        if not self._ready or any(len(i)>512 or any(ord(c)<32 for c in i) for i in (input_id,output_id)): return
        if self._connected and not self._muted and input_id != self._input_id:
            self._mic_authorized = True; self._mic_timer.start(10000)
        self._audio_commands.add(self._send({'type':'selectDevices','inputId':input_id,'outputId':output_id}))
        self._audio_status = 'Applying voice audio selection…';self.changed.emit()

    @Slot(str,bool)
    def mutePeer(self,xuid,muted):
        if self._connected and re.fullmatch(r'[1-9][0-9]{0,19}',xuid) and int(xuid)<=18446744073709551615:
            self._send({'type':'mute','xuid':xuid,'muted':muted})

    def _read_peer_volumes(self):
        raw = self._settings.value('overlay/peerVolumes', {})
        if not isinstance(raw, dict): return {}
        result = {}
        for peer_id, value in raw.items():
            if (isinstance(peer_id, str) and re.fullmatch(r'[1-9][0-9]{0,19}', peer_id)
                    and int(peer_id) <= 18446744073709551615):
                try: result[peer_id] = max(0, min(200, int(value)))
                except (TypeError, ValueError): pass
        return result

    @Slot(str, int)
    def setPeerVolume(self, peer_id, percent):
        if not isinstance(peer_id, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', peer_id) or int(peer_id) > 18446744073709551615:
            return
        value = max(0, min(200, int(percent)))
        self._peer_volumes[peer_id] = value
        self._settings.setValue('overlay/peerVolumes', dict(self._peer_volumes))
        self._settings.sync()
        if self._connected: self._send({'type':'setPeerVolume','xuid':peer_id,'percent':value})
        self.changed.emit()

    @Slot(bool)
    def setMuted(self, muted):
        if not self._ready or not self._connected or self._closing_id: return
        self._mic_authorized = not muted
        if muted: self._mic_timer.stop()
        else: self._mic_timer.start(10000)
        self._send({'type':'setMuted','muted':muted})

    @Slot()
    def resumeAudio(self):
        if self._ready: self._send({'type':'resume'})

    @Slot()
    def leave(self):
        if not self._active: return
        self._revoke_mic()
        if not self._session_requested and not self._connected:
            self.shutdown(); return
        if not self._ready:
            self.shutdown()
            return
        self._closing_id = self._send({'type':'leave'})
        self._close_timer.start(2000)

    @Slot()
    def _leave_timeout(self):
        self._closing_id = None
        self._error = 'ERR_LEAVE_UNCONFIRMED_RETRY'
        self._revoke_mic()
        self._speaking_members = []
        self._speaking_available = False
        self.changed.emit()

    @Slot()
    def mediaFailed(self):
        if not self._active: return
        if self._ready and (self._session_requested or self._connected):
            # Only our owned session; no retry/replay of Create or Join.
            self.nativeRequest.emit(json.dumps({'source':'xodus-voice','nonce':self._nonce,
                'type':'nativeCall','callId':'fault_cleanup','method':'leaveXboxParty','args':[]}))
        self._error = 'ERR_VOICE_MEDIA_FAILED'
        self.shutdown()

    @Slot()
    def shutdown(self):
        self._mic_timer.stop()
        self._load_timer.stop()
        self._close_timer.stop()
        self._active = self._ready = self._connected = self._mic_authorized = False
        self._muted = True
        self._pending.clear()
        self._audio_commands.clear()
        self._initial = None
        self._closing_id = None
        self._nonce = ''
        self._session_requested = self._enumerate_pending = False
        self._audio_inputs = []; self._audio_outputs = []; self._muted_members = []
        self._speaking_members = []
        self._speaking_available = False
        self._input_id = self._output_id = ''
        self.changed.emit()

    @Slot()
    def _connection_changed(self):
        if not self._social.connected: self.shutdown()

    @Slot()
    def _revoke_mic(self):
        self._mic_authorized = False

    @Slot(QUrl, result=bool)
    def allowMicrophone(self, origin):
        # Browser permission is granted only after THIS session's explicit Unmute.
        return bool(self._active and self._connected and self._mic_authorized
                    and origin.scheme() == 'file' and origin.host() == '')

    @Slot(str)
    def receive(self, raw):
        if not self._active or len(raw.encode('utf-8')) > MAX_FRAME: return
        try: frame = json.loads(raw)
        except (ValueError, TypeError): return
        if not isinstance(frame, dict) or frame.get('nonce') != self._nonce or frame.get('source') != 'xodus-voice': return
        if frame.get('type') == 'nativeCall':
            method, cid, args = frame.get('method'), frame.get('callId'), frame.get('args')
            if not isinstance(cid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',cid) or cid in self._pending: return
            if not isinstance(method,str) or not isinstance(args,list) or len(args)>2: return
            if method == 'allowXboxMicrophone':
                reply = {'source':'xodus-voice-parent','nonce':self._nonce,'callId':cid}
                if self._mic_authorized and self._connected and not args: reply['result'] = True
                else: reply['error'] = 'ERR_MIC_PERMISSION_DENIED'
                self.reply.emit(json.dumps(reply))
            elif method in METHODS and len(self._pending)<16 and not self._closing_id:
                self._pending.add(cid)
                self.nativeRequest.emit(raw)
            elif method == 'leaveXboxParty' and len(self._pending)<16:
                self._pending.add(cid)
                self.nativeRequest.emit(raw)
            return
        event = frame.get('event')
        if event == 'ready' and not self._ready:
            self._load_timer.stop()
            self._ready = True
            initial, self._initial = self._initial, None
            self.changed.emit()
            if initial is not None: self._send(initial)
            for peer_id, percent in self._peer_volumes.items():
                self._send({'type':'setPeerVolume','xuid':peer_id,'percent':percent})
            if self._enumerate_pending:
                self._enumerate_pending = False
                self._audio_commands.add(self._send({'type':'enumerateDevices'}))
        elif event in ('devices', 'devicesSelected'):
            def device_id(value):
                return isinstance(value, str) and len(value) <= 512 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            input_id = frame.get('selectedInputId' if event == 'devices' else 'inputId')
            output_id = frame.get('selectedOutputId' if event == 'devices' else 'outputId')
            input_id = '' if input_id is None else input_id
            output_id = '' if output_id is None else output_id
            if not device_id(input_id) or not device_id(output_id): return
            if event == 'devices':
                rows = []
                for key in ('inputs', 'outputs'):
                    devices = frame.get(key)
                    if not isinstance(devices, list) or len(devices) > 128: return
                    safe = []; seen = set()
                    for row in devices:
                        if not isinstance(row, dict) or not device_id(row.get('id')) or not row['id']: return
                        label = row.get('label')
                        if not isinstance(label, str) or len(label) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in label): return
                        if row['id'] not in seen:
                            safe.append({'id': row['id'], 'label': label or 'Unnamed audio device'})
                            seen.add(row['id'])
                    rows.append(safe)
                self._audio_inputs, self._audio_outputs = rows
            self._input_id, self._output_id = input_id, output_id
            self._audio_status = ('Selected device is disconnected. Choose another device.'
                if frame.get('missingSelectedInput') or frame.get('missingSelectedOutput') else
                'Audio devices ready. Microphone remains off until you choose Unmute.')
            self.changed.emit()
        elif event == 'state':
            state = frame.get('state')
            if not isinstance(state,dict) or type(state.get('connected')) is not bool or type(state.get('muted')) is not bool: return
            self._connected, self._muted = state['connected'], state['muted']
            members=state.get('mutedMembers',[])
            if isinstance(members,list) and len(members)<=128 and all(isinstance(m,str) and re.fullmatch(r'[1-9][0-9]{0,19}',m) for m in members): self._muted_members=list(members)
            speaking = state.get('speakingMembers', [])
            if isinstance(speaking, list) and len(speaking) <= 128 and all(isinstance(m, str) and re.fullmatch(r'[1-9][0-9]{0,19}', m) for m in speaking): self._speaking_members = list(speaking)
            speaking_available = state.get('speakingAvailable')
            if type(speaking_available) is bool: self._speaking_available = speaking_available
            if not self._speaking_available:
                self._speaking_members = []
            if not self._connected:
                self._speaking_members = []
                self._speaking_available = False
            error = state.get('error')
            self._error = error if isinstance(error,str) and SAFE_CODE.fullmatch(error) else ''
            if not self._connected: self._revoke_mic()
            self.changed.emit()
        elif event == 'commandComplete':
            if frame.get('commandId') in self._audio_commands:
                self._audio_commands.discard(frame['commandId'])
                if frame.get('error'):
                    self._audio_status = {
                        'ERR_SINK_ID_UNSUPPORTED': 'This browser cannot select speakers. Use system audio routing.',
                        'ERR_DEVICE_NOT_FOUND': 'Selected audio device is disconnected.',
                        'ERR_AUDIO_OUTPUT_FAILED': 'Speaker routing failed. Previous selection retained.',
                    }.get(frame['error'] if isinstance(frame['error'], str) else '', 'Audio device request failed. Refresh devices and try again.')
                    self._revoke_mic()
                    self.changed.emit()
                return
            if frame.get('commandId') == self._closing_id:
                if frame.get('error'):
                    self._close_timer.stop()
                    self._closing_id = None
                    self._error = 'ERR_LEAVE_UNCONFIRMED_RETRY'
                    self.changed.emit()
                else:
                    self.shutdown()
            elif frame.get('error'):
                error = frame['error']
                self._error = error if isinstance(error,str) and SAFE_CODE.fullmatch(error) else 'ERR_VOICE_INTERNAL'
                self._revoke_mic()
                self.changed.emit()

    @Slot(str)
    def deliverReply(self, raw):
        if not self._active or len(raw.encode('utf-8')) > MAX_FRAME: return
        try: frame=json.loads(raw)
        except (ValueError,TypeError): return
        if not isinstance(frame,dict) or frame.get('nonce')!=self._nonce or not isinstance(frame.get('callId'),str) or frame.get('callId') not in self._pending: return
        self._pending.remove(frame['callId'])
        self.reply.emit(raw)
