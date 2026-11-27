"""Read-only, bounded live input/output frames. Never changes capture or profiles."""
import copy
from PySide6.QtCore import QObject, Property, Signal, Slot, QThread, QTimer

_FIELDS = {'buttons':(0,65535),'left_x':(-32768,32767),'left_y':(-32768,32767),
           'right_x':(-32768,32767),'right_y':(-32768,32767),
           'left_trigger':(0,255),'right_trigger':(0,255)}

def validated_frame(status):
    if not isinstance(status,dict) or status.get('running') is not True:
        raise ValueError('Engine is not running')
    frame=status.get('live')
    if not isinstance(frame,dict) or frame.get('schema_version') != 1 or isinstance(frame.get('schema_version'),bool):
        raise ValueError('Live telemetry is not supported')
    clean={'schema_version':1}
    for side in ('input','output'):
        value=frame.get(side)
        if not isinstance(value,dict):raise ValueError('Missing frame')
        clean[side]={}
        for key,(low,high) in _FIELDS.items():
            n=value.get(key)
            if type(n) is not int or not low<=n<=high:raise ValueError('Invalid frame value')
            clean[side][key]=n
    source=frame.get('selected_source_id')
    if source is not None and (not isinstance(source,str) or len(source)>512):raise ValueError('Invalid source')
    clean['selected_source_id']=source
    if 'extended_buttons' in frame:
        names=frame['extended_buttons']
        if not isinstance(names,list) or len(names)>64 or any(not isinstance(n,str) or not 0<len(n)<=64 or any(ord(c)<32 for c in n) for n in names):
            raise ValueError('Invalid extended input')
        clean['extended_buttons']=list(dict.fromkeys(names))
    for key in ('mod_active','controller_mouse_mode'):
        if key in frame:
            if type(frame[key]) is not bool:raise ValueError('Invalid mode flag')
            clean[key]=frame[key]
    if 'orientation' in frame and isinstance(frame['orientation'], dict):
        clean['orientation'] = frame['orientation']
    if 'dsu' in frame and isinstance(frame['dsu'], dict):
        clean['dsu'] = frame['dsu']
    if 'motion' in frame and isinstance(frame['motion'], dict):
        clean['motion'] = frame['motion']
    return clean

class _Fetch(QThread):
    def __init__(self,client,parent):super().__init__(parent);self.client=client;self.value=None
    def run(self):
        try:self.value=validated_frame(self.client.request('status'))
        except Exception:self.value=None

class ControllerLiveView(QObject):
    changed=Signal()
    def __init__(self,client=None,parent=None):
        super().__init__(parent)
        if client is None:
            from native_services.unified_controller_control import ControllerControlClient
            client=ControllerControlClient(timeout=.2)
        self._client=client;self._job=None;self._active=False;self._closed=False;self._frame={};self._owners=set()
        self._timer=QTimer(self);self._timer.setInterval(33);self._timer.timeout.connect(self._poll)

    @Property(bool,notify=changed)
    def available(self):return self._active and bool(self._frame)
    @Property('QVariantMap',notify=changed)
    def frame(self):return copy.deepcopy(self._frame)
    @Property(str,notify=changed)
    def status(self):
        if not self._active:return 'Live display paused.'
        return 'Live input/output connected.' if self.available else 'Live input unavailable; no movement is being simulated.'

    @Slot()
    def start(self):
        self.acquire('legacy')
    @Slot(str)
    def acquire(self,owner):
        if self._closed or owner not in {'legacy','studio','navigation','guide','sticks','ptt'}:return
        self._owners.add(owner)
        self._apply_rate()
        if self._active:return
        self._active=True;self._timer.start();self._poll();self.changed.emit()
    def _apply_rate(self):
        # Only the background Guide/PTT watch: a light poll (~12/s); any UI owner gets full rate (~30/s).
        self._timer.setInterval(80 if self._owners <= {'guide', 'ptt'} else 33)
    @Slot(str)
    def release(self,owner):
        self._owners.discard(owner)
        if not self._owners:self.stop()
        else:self._apply_rate()
    @Slot()
    def stop(self):
        self._owners.clear()
        self._active=False;self._timer.stop();self._frame={};self.changed.emit()
    def _poll(self):
        if not self._active or self._closed or self._job is not None:return
        self._job=_Fetch(self._client,self);self._job.finished.connect(self._finish);self._job.start()
    def _finish(self):
        job=self._job
        if job is None:return
        self._job=None
        if self._active and not self._closed:
            self._frame=job.value or {};self.changed.emit()
        job.deleteLater()
    @Slot()
    def shutdown(self):
        self._closed=True;self.stop()
        if self._job is not None:
            self._job.wait();self._job.deleteLater();self._job=None
