"""Explicit in-app gateway connection and selected-record proposal jobs."""
from copy import deepcopy
from PySide6.QtCore import QObject, Property, Signal, Slot
from organizer_workspace_view import _Work
from native_services.organizer_agent_gateway import OrganizerAgentGateway

class OrganizerAgentView(QObject):
    changed = Signal()
    proposalReady = Signal()
    def __init__(self, workspace=None, gateway=None, parent=None):
        super().__init__(parent)
        self.gateway = gateway or (OrganizerAgentGateway(workspace) if workspace else None)
        self._job = None
        self._closed = False
        self._profiles = []
        self._status = 'Not connected. Connect reads the registered agent directory; it does not start a model.'
        self._events = []
        self._cursor = 0
        self._op = ''
    @Property(bool, notify=changed)
    def available(self): return self.gateway is not None
    @Property(bool, notify=changed)
    def busy(self): return self._job is not None
    @Property(bool, notify=changed)
    def connected(self): return bool(self.gateway and self.gateway.connected)
    @Property(bool, notify=changed)
    def unsettled(self): return bool(self.gateway and self.gateway.pending and not self.gateway.pending.get('settled'))
    @Property(str, notify=changed)
    def status(self): return self._status
    @Property('QVariantList', notify=changed)
    def profiles(self): return deepcopy(self._profiles)
    @Property('QVariantList', notify=changed)
    def events(self): return deepcopy(self._events)
    @Property(str, notify=changed)
    def attemptId(self): return self.gateway.pending['attempt_id'] if self.gateway and self.gateway.pending else ''
    def _start(self, op, fn):
        if not self.available or self.busy or self._closed: return
        self._op = op
        self._status = {'connect':'Connecting to existing UA/Herald gateway (no inference)…',
                        'run':'Task submitted once. Backend owns the run; do not replay on disconnect.',
                        'inspect':'Inspecting the existing attempt (GET only)…',
                        'events':'Reading existing run events…'}[op]
        self._job = _Work(fn, self)
        self._job.finished.connect(self._finished)
        self._job.start()
        self.changed.emit()
    @Slot()
    def connectGateway(self): self._start('connect', self.gateway.connect if self.gateway else lambda:None)
    @Slot(str, str, str, int, 'QVariantList')
    def runTask(self, agent, task, game_id, revision, fields):
        if self.unsettled: return
        self._start('run', lambda:self.gateway.start(agent, task, game_id, revision, list(fields)))
    @Slot()
    def inspectRun(self): self._start('inspect', self.gateway.inspect if self.gateway else lambda:None)
    @Slot()
    def readEvents(self): self._start('events', lambda:self.gateway.observations(self._cursor))
    @Slot()
    def _finished(self):
        job = self._job
        if job is None: return
        result = job.result or {'ok':False}
        self._job = None
        job.deleteLater()
        if self._closed: return
        if not result['ok']:
            self._status = ('Outcome unsettled; no retry sent. Use Check existing run (GET only).'
                if self.unsettled else 'Request rejected or gateway unavailable. No automatic retry or model started by Connect.')
        elif self._op == 'connect':
            self._profiles = result['value']
            self._status = 'Gateway connected · ' + str(len(self._profiles)) + ' registered profiles. Choose one and explicitly Run.'
        elif self._op == 'events':
            batch = result['value']
            self._events = (self._events + batch.get('events', []))[-100:]
            self._cursor = batch.get('cursor', self._cursor)
            self._status = 'Read existing events; no task replayed.'
        else:
            value = result['value']
            self._status = {'proposal_ready':'Agent proposal ready below. Review changes before applying.',
                            'failed':'Backend attempt failed. No metadata applied.',
                            'invalid_proposal':'Agent answered, but its reply was not an applicable proposal.',
                            'running_or_interrupted':'Existing attempt is running or interrupted; no second run submitted.'}.get(value['status'], 'Run inspected.')
            self.proposalReady.emit()
        self.changed.emit()
    def shutdown(self):
        self._closed = True
        # SDK auth/read calls and the one bounded POST settle without termination.
        if self._job:
            self._job.wait()
