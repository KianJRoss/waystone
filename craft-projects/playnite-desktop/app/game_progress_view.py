"""Inert async cached progress view. Selection generations prevent stale game results."""
from copy import deepcopy
import json
from PySide6.QtCore import QObject, Property, Signal, Slot, QThread

class _ReadProgress(QThread):
    def __init__(self,repo,key,generation,parent):
        super().__init__(parent);self.repo=repo;self.key=key;self.generation=generation;self.data=None;self.error=False
    def run(self):
        try:
            data=self.repo.get_game(self.key)
            if not isinstance(data,dict) or data.get('game_key')!=self.key:raise ValueError()
            if any(type(data.get(k)) is not list for k in ('achievements','completion_times','notices')):raise ValueError()
            if any(not isinstance(x,dict) for k in ('achievements','completion_times') for x in data[k]):raise ValueError()
            if any(type(x) is not str for x in data['notices']):raise ValueError()
            if len(json.dumps(data,allow_nan=False).encode())>262144:raise ValueError()
            self.data=deepcopy(data)
        except Exception:self.error=True

class GameProgressView(QObject):
    changed=Signal()
    def __init__(self,repository=None,parent=None):
        super().__init__(parent);self._repo=repository;self._job=None;self._closed=False
        self._key='';self._generation=0;self._data={};self._status='Select a game to inspect cached progress.'
    @Property(str,notify=changed)
    def gameKey(self):return self._key
    @Property(bool,notify=changed)
    def busy(self):return self._job is not None
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property('QVariantList',notify=changed)
    def achievementSources(self):return deepcopy(self._data.get('achievements',[]))
    @Property('QVariantList',notify=changed)
    def completionTimes(self):return deepcopy(self._data.get('completion_times',[]))
    @Property('QStringList',notify=changed)
    def notices(self):return list(self._data.get('notices',[]))
    @Slot(str)
    def selectGame(self,key):
        if self._closed:return
        self._key=key;self._generation+=1;self._data={}
        if not key or len(key)>512 or any(not c.isprintable() for c in key):
            self._status='Select an exact canonical game ID.';self.changed.emit();return
        self._status='Reading cached progress…' if self._repo is not None else 'No cached progress repository configured.'
        if not self.busy and self._repo is not None:self._launch()
        self.changed.emit()
    @Slot()
    def refresh(self):self.selectGame(self._key)
    def _launch(self):
        if not self._key or self._closed:return
        self._job=_ReadProgress(self._repo,self._key,self._generation,self)
        self._job.finished.connect(self._finished);self._job.start()
    @Slot()
    def _finished(self):
        job=self._job
        if job is None:return
        self._job=None;job.deleteLater()
        if self._closed:return
        if job.generation!=self._generation:
            if self._key and self._repo is not None and len(self._key)<=512 and all(c.isprintable() for c in self._key):self._launch()
        elif job.error:
            self._data={};self._status='Cached progress unavailable. No live provider fallback was attempted.'
        else:
            self._data=job.data;self._status='Cached/imported observations only · no live account sync.'
        self.changed.emit()
    def shutdown(self):
        self._closed=True
        if self._job:
            self._job.wait(10000)
            if self._job.isRunning():raise RuntimeError('Progress read is still settling')
