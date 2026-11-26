"""Fake-only shared native-write admission and verified-readback regressions."""
import copy
import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QObject, Property, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from controller_commands import ControllerProfileCommands
from controller_profile_integration import ControllerProfileIntegration
from controller_profiles_view import ControllerProfilesView
from controller_view import ControllerView
from native_services.controller_command_protocol import PROFILE_OPERATIONS


@pytest.fixture(scope='module', autouse=True)
def application():
    return QGuiApplication.instance() or QGuiApplication([])


def settle(obj):
    for _ in range(400):
        QTest.qWait(5)
        if not obj.busy:
            return
    raise AssertionError('fake native job did not settle')


class EditorClient:
    def __init__(self, mode='normal'):
        self.calls = []
        self.mode = mode
        self.active = 'General'
        self.revision = 'a' * 64
        self.documents = {'General': {'routes': {}, 'opaque': {'keep': True}},
                          'Racing': {'routes': {}, 'note': 'before'}}
        self.mutated = False

    def request(self, op, **fields):
        self.calls.append((op, copy.deepcopy(fields)))
        if op == 'status': return {'running': True, 'devices': []}
        if op == 'profiles':
            if self.mutated and self.mode == 'malformed': return {}
            return {'names': list(self.documents), 'active_profile': self.active, 'revision': self.revision}
        if op == 'profile_get':
            return {'name': fields['name'], 'profile': copy.deepcopy(self.documents[fields['name']]),
                    'revision': self.revision}
        if op in ('profile_select', 'profile_update'):
            assert fields['expected_revision'] == self.revision
            self.mutated = True
            if self.mode not in ('mismatch', 'malformed'):
                if op == 'profile_select': self.active = fields['name']
                else: self.documents[fields['name']].update(copy.deepcopy(fields['profile']))
            self.revision = 'b' * 64
            return {'name': fields['name'], 'active_profile': self.active, 'revision': self.revision}
        raise AssertionError(op)


def editor(client):
    result = ControllerView(client)
    result._available = True
    result._names = ['General', 'Racing']
    result._active = 'General'
    result._editing = 'Racing'
    result._revision = 'a' * 64
    result._profile = copy.deepcopy(client.documents['Racing'])
    return result


@pytest.mark.parametrize('gate', [lambda: False, lambda: None, lambda: 1])
def test_editor_admission_requires_true_and_preserves_loaded_draft(gate):
    client = EditorClient()
    view = editor(client)
    completions = []
    view.operationFinished.connect(completions.append)
    before = view.profileJson
    try:
        view.setMutationAdmission(gate)
        view.saveProfile('{"note":"unsaved draft"}')
        view.useProfile()
        assert client.calls == [] and not view.busy
        assert completions == [False, False]
        assert view.profileJson == before and view.editingProfile == 'Racing'
    finally:
        view.shutdown()


def test_editor_admission_exception_fails_closed_but_refresh_is_read_only_allowed():
    def broken(): raise RuntimeError('admission unavailable')
    client = EditorClient()
    view = editor(client)
    completions = []
    view.operationFinished.connect(completions.append)
    try:
        view.setMutationAdmission(broken)
        view.useProfile()
        view.saveProfile('{"note":"unsaved"}')
        assert client.calls == [] and completions == [False, False]
        view.reloadProfile('Racing')
        settle(view)
        assert [op for op, _ in client.calls] == ['status', 'profiles', 'profile_get']
        assert view.editingProfile == 'Racing'
    finally:
        view.shutdown()


@pytest.mark.parametrize('operation', ['select', 'update'])
def test_first_successful_reply_with_mismatched_readback_is_uncertain_not_success(operation):
    client = EditorClient(mode='mismatch')
    view = editor(client)
    results = []
    view.operationFinished.connect(results.append)
    try:
        view.setMutationAdmission(lambda: True)
        if operation == 'select': view.useProfile()
        else: view.saveProfile('{"routes":{},"note":"changed"}')
        settle(view)
        assert results == [False] and view.uncertain
        assert 'unconfirmed' in view.status.lower() or 'uncertain' in view.status.lower()
        before = list(client.calls)
        view.useProfile(); view.saveProfile('{"note":"must not retry"}')
        assert client.calls == before
        assert len([op for op, _ in client.calls if op in ('profile_select', 'profile_update')]) == 1
    finally:
        view.shutdown()


def test_malformed_post_write_readback_is_also_uncertain():
    client = EditorClient(mode='malformed')
    view = editor(client)
    results = []
    view.operationFinished.connect(results.append)
    try:
        view.useProfile()
        settle(view)
        assert results == [False] and view.uncertain
        assert len([op for op, _ in client.calls if op == 'profile_select']) == 1
    finally:
        view.shutdown()


def test_native_merge_preserved_unknown_fields_does_not_falsely_fail_readback():
    client = EditorClient()
    view = editor(client)
    view._editing = 'General'
    view._profile = copy.deepcopy(client.documents['General'])
    results = []
    view.operationFinished.connect(results.append)
    try:
        view.setMutationAdmission(lambda: True)
        view.saveProfile('{"routes":{},"note":"added field"}')
        settle(view)
        assert results == [True] and not view.uncertain
        assert client.documents['General']['opaque'] == {'keep': True}
    finally:
        view.shutdown()


class CommandClient:
    def __init__(self): self.calls = []

    def request(self, op, **fields):
        self.calls.append((op, copy.deepcopy(fields)))
        if op == 'profile_command_capabilities':
            return {'schema_version': 1, 'operations': sorted(PROFILE_OPERATIONS)}
        if op == 'profiles':
            return {'names': ['General'], 'active_profile': 'General', 'revision': 'a' * 64}
        if op == 'profile_export':
            return {'name': fields['name'], 'revision': 'a' * 64,
                    'document': {'version': 1, 'profiles': {'General': {}}, 'active_profile': 'General'}}
        return {'name': fields['name'], 'revision': 'b' * 64,
                'active_profile': 'General', 'committed': True}


@pytest.mark.parametrize('method,args', [
    ('createProfile', ('New',)), ('copyProfile', ('General', 'Copy')),
    ('renameProfile', ('General', 'Renamed')), ('deleteProfile', ('General',)),
    ('importProfile', ('Imported', '{"version":1,"profiles":{"General":{}},"active_profile":"General"}')),
    ('setField', ('General', 'source_merge_policy', 'first_active')),
])
def test_all_profile_command_mutations_share_admission_gate(method, args):
    client = CommandClient()
    commands = ControllerProfileCommands(client)
    try:
        commands._caps = sorted(PROFILE_OPERATIONS)
        commands._revision = 'a' * 64
        commands.setMutationAdmission(lambda: False)
        getattr(commands, method)(*args)
        assert client.calls == [] and not commands.busy
    finally:
        commands.shutdown()


def test_profile_command_reads_and_export_remain_available_when_writes_blocked():
    client = CommandClient()
    commands = ControllerProfileCommands(client)
    try:
        commands.setMutationAdmission(lambda: False)
        commands.refreshProfiles(); settle(commands)
        commands.exportProfile('General'); settle(commands)
        assert [op for op, _ in client.calls] == ['profile_command_capabilities', 'profiles', 'profile_export']
        assert commands.exportDocument
        commands.createProfile('Blocked')
        assert len(client.calls) == 3
    finally:
        commands.shutdown()


def test_profile_command_admission_exception_cannot_send_mutation():
    def broken(): raise RuntimeError('shared state missing')
    client = CommandClient()
    commands = ControllerProfileCommands(client)
    try:
        commands._caps = sorted(PROFILE_OPERATIONS)
        commands._revision = 'a' * 64
        commands.setMutationAdmission(broken)
        commands.createProfile('Blocked')
        assert client.calls == [] and not commands.busy
    finally:
        commands.shutdown()


class Writer(QObject):
    changed = Signal()

    def __init__(self, *, busy=False, uncertain=False):
        super().__init__()
        self._busy, self._uncertain = busy, uncertain

    @Property(bool, notify=changed)
    def busy(self): return self._busy

    @Property(bool, notify=changed)
    def uncertain(self): return self._uncertain

    def state(self, *, busy=False, uncertain=False):
        self._busy, self._uncertain = busy, uncertain
        self.changed.emit()


class Activation(QObject):
    changed = Signal()
    finished = Signal(str, bool, bool)
    observed = Signal(str)

    def __init__(self):
        super().__init__()
        self.calls = []
        self.pending = None
        self.uncertain = False
        self.status = 'Fixture only'

    @property
    def busy(self): return self.pending is not None

    def request(self, name, token):
        if self.busy or self.uncertain: return False
        self.calls.append(name)
        self.pending = (name, token)
        self.changed.emit()
        return True

    def finish(self, *, uncertain=False):
        name, token = self.pending
        self.pending = None
        self.uncertain = uncertain
        self.changed.emit()
        self.finished.emit(token, not uncertain, uncertain)


def integration(tmp_path, *writers):
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'version': 1, 'active_profile': 'General',
        'default_profile': 'General', 'game_profiles': {'A': 'Racing', 'B': 'Couch'},
        'profiles': {'General': {}, 'Racing': {}, 'Couch': {}}}))
    bridge = ControllerProfilesView(path)
    activation = Activation()
    wire = ControllerProfileIntegration(bridge, activation, native_writers=writers)
    return bridge, activation, wire


def test_shared_admission_excludes_only_own_busy_not_own_uncertainty(tmp_path):
    first, second = Writer(), Writer()
    bridge, activation, wire = integration(tmp_path, first, second)
    assert activation.calls == [] and wire.mutationAllowed(first)
    first.state(busy=True)
    assert bridge.engineMutationBlocked
    assert wire.mutationAllowed(first) and not wire.mutationAllowed(second)
    assert not wire.mutationAllowed()
    first.state(uncertain=True)
    assert not wire.mutationAllowed(first) and not wire.mutationAllowed(second)
    first.state(); second.state(busy=True)
    assert not wire.mutationAllowed(first) and wire.mutationAllowed(second)


def test_runtime_pending_and_uncertainty_block_all_editor_admission(tmp_path):
    writer = Writer()
    bridge, activation, wire = integration(tmp_path, writer)
    wire.observe('one', 'A', 'running')
    assert activation.calls == ['Racing']
    assert bridge.engineMutationBlocked and not wire.mutationAllowed(writer)
    activation.finish(uncertain=True)
    assert bridge.engineMutationBlocked and not wire.mutationAllowed(writer)
    before = bridge._path.read_bytes()
    assert not bridge.assignGame('A', 'Couch') and bridge._path.read_bytes() == before
    writer.changed.emit()
    assert activation.calls == ['Racing']


def test_known_busy_writer_defers_latest_desired_profile_exactly_once(tmp_path):
    writer = Writer(busy=True)
    bridge, activation, wire = integration(tmp_path, writer)
    wire.observe('one', 'A', 'running')
    wire.observe('two', 'B', 'running')
    assert activation.calls == [] and bridge.engineMutationBlocked
    writer.state()
    QTest.qWait(15)
    assert activation.calls == ['Couch']
    writer.changed.emit(); writer.changed.emit()
    QTest.qWait(15)
    assert activation.calls == ['Couch']
    activation.finish()
    writer.changed.emit()
    assert activation.calls == ['Couch']


def test_exit_while_writer_busy_defers_default_not_stale_game(tmp_path):
    writer = Writer(busy=True)
    _, activation, wire = integration(tmp_path, writer)
    wire.observe('one', 'A', 'running')
    wire.observe('one', 'A', 'closed')
    writer.state()
    QTest.qWait(15)
    assert activation.calls == ['General']


@pytest.mark.parametrize('initially_busy', [False, True])
def test_uncertain_writer_never_causes_automatic_retry_when_uncertainty_clears(tmp_path, initially_busy):
    writer = Writer(busy=initially_busy, uncertain=not initially_busy)
    _, activation, wire = integration(tmp_path, writer)
    wire.observe('one', 'A', 'running')
    if initially_busy:
        writer.state(uncertain=True)  # A formerly known-busy write lost its reply.
    writer.state()
    QTest.qWait(15)
    assert activation.calls == []
    wire.reconcile()
    assert activation.calls == ['Racing']


@pytest.mark.parametrize('invalid_default', [None, 'Missing', 7, []])
def test_invalid_explicit_default_is_pinned_before_runtime_selection(tmp_path, invalid_default):
    path = tmp_path / 'profiles.json'
    source = {'version': 1, 'active_profile': 'General', 'default_profile': invalid_default,
              'game_profiles': {'A': 'Racing'}, 'profiles': {'General': {}, 'Racing': {}},
              'opaque': {'keep': [1, 2]}}
    path.write_text(json.dumps(source))
    before = path.read_bytes()
    bridge = ControllerProfilesView(path)
    assert bridge.defaultProfile == 'General' and path.read_bytes() == before
    assert bridge.ensureRuntimeMetadata()
    saved = json.loads(path.read_text())
    assert saved['default_profile'] == 'General' and saved['opaque'] == source['opaque']
    saved['active_profile'] = 'Racing'
    path.write_text(json.dumps(saved))  # Fixture of the existing native selector's persisted change.
    assert bridge.refresh() and bridge.defaultProfile == 'General'
