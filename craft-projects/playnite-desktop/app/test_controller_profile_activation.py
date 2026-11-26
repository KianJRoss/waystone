"""Dedicated-client activation fixtures: no sockets, engine, devices or games."""
import os
import threading

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import QObject
from PySide6.QtTest import QTest

from controller_profile_activation import ControllerProfileActivation


@pytest.fixture(scope='module', autouse=True)
def application():
    return QGuiApplication.instance() or QGuiApplication([])


class Client:
    def __init__(self):
        self.calls = []
        self.active = 'Default'
        self.revision = 'a' * 64
        self.reads = 0

    def request(self, operation, **fields):
        self.calls.append((operation, fields))
        if operation == 'profiles':
            self.reads += 1
            return {'names': ['Default', 'A', 'B'], 'active_profile': self.active,
                    'revision': self.revision}
        if operation == 'profile_select':
            assert fields['expected_revision'] == self.revision
            self.active = fields['name']
            self.revision = 'b' * 64
            return {'active_profile': self.active, 'revision': self.revision}
        raise AssertionError('unrelated operation: ' + operation)


class NativeError(Exception):
    def __init__(self, *, uncertain, code='ERR_REVISION_CONFLICT'):
        super().__init__('private transport detail must not appear in status')
        self.uncertain = uncertain
        self.code = code


def settle(adapter):
    for _ in range(400):
        QTest.qWait(5)
        if not adapter.busy:
            return
    raise AssertionError('activation job did not settle')


def capture(adapter):
    finished, observed = [], []
    adapter.finished.connect(lambda request_id, success, uncertain:
                             finished.append((request_id, success, uncertain)))
    adapter.observed.connect(observed.append)
    return finished, observed


def test_constructor_no_client_creation_io_or_automatic_activation(monkeypatch):
    import native_services.unified_controller_control as protocol
    def forbidden():
        raise AssertionError('construction must remain lazy')
    monkeypatch.setattr(protocol, 'ControllerControlClient', forbidden)
    adapter = ControllerProfileActivation()
    try:
        assert not adapter.busy and not adapter.available
        assert adapter.activeProfile == '' and not adapter.uncertain
        QTest.qWait(10)
        assert adapter._client is None
    finally:
        adapter.shutdown()


def test_selection_exact_order_cas_latest_inventory_and_verified_completion():
    client = Client()
    client.revision = 'C' * 64  # Revision comes from the fresh native read.
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    try:
        assert adapter.request('A', 'request-17') is True
        assert adapter.busy and finished == []
        settle(adapter)
        assert client.calls == [
            ('profiles', {}),
            ('profile_select', {'name': 'A', 'expected_revision': 'C' * 64}),
            ('profiles', {}),
        ]
        assert finished == [('request-17', True, False)]
        assert observed == []
        assert adapter.available and adapter.activeProfile == 'A'
        assert not adapter.uncertain
    finally:
        adapter.shutdown()


def test_explicit_refresh_only_reads_and_never_emits_request_completion():
    client = Client()
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    try:
        assert adapter.refresh() is True
        settle(adapter)
        assert client.calls == [('profiles', {})]
        assert observed == ['Default'] and finished == []
        assert adapter.available and adapter.activeProfile == 'Default'
        QTest.qWait(10)
        assert client.calls == [('profiles', {})]
    finally:
        adapter.shutdown()


def test_already_active_shortcut_is_verified_read_only_success():
    client = Client()
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    try:
        assert adapter.request('Default', 'same-active')
        settle(adapter)
        assert client.calls == [('profiles', {})]
        assert finished == [('same-active', True, False)] and observed == []
    finally:
        adapter.shutdown()


def test_unknown_profile_rejected_after_read_without_mutation():
    client = Client()
    adapter = ControllerProfileActivation(client)
    finished, _ = capture(adapter)
    try:
        assert adapter.request('Missing', 'missing')
        settle(adapter)
        assert client.calls == [('profiles', {})]
        assert finished == [('missing', False, False)]
        assert not adapter.uncertain and 'ERR_PROFILE_NOT_FOUND' in adapter.status
    finally:
        adapter.shutdown()


@pytest.mark.parametrize('uncertain', [False, True])
def test_read_failure_before_selection_is_known_not_sent_even_native_uncertain(uncertain):
    class ReadFailure(Client):
        def request(self, operation, **fields):
            self.calls.append((operation, fields))
            raise NativeError(uncertain=uncertain, code='ERR_NOT_AVAILABLE')
    client = ReadFailure()
    adapter = ControllerProfileActivation(client)
    finished, _ = capture(adapter)
    try:
        assert adapter.request('A', 'pre-read')
        settle(adapter)
        assert client.calls == [('profiles', {})]
        assert finished == [('pre-read', False, False)]
        assert not adapter.uncertain
        assert 'private transport' not in adapter.status
    finally:
        adapter.shutdown()


@pytest.mark.parametrize('payload', [None, {}, {'names': ['A'], 'active_profile': 'A', 'revision': 'bad'},
    {'names': ['A', 'A'], 'active_profile': 'A', 'revision': 'a' * 64},
    {'names': ['A'], 'active_profile': 'Missing', 'revision': 'a' * 64}])
def test_invalid_pre_read_inventory_never_mutates(payload):
    class InvalidInventory(Client):
        def request(self, operation, **fields):
            self.calls.append((operation, fields))
            return payload
    client = InvalidInventory()
    adapter = ControllerProfileActivation(client)
    finished, _ = capture(adapter)
    try:
        assert adapter.request('A', 'invalid-read')
        settle(adapter)
        assert client.calls == [('profiles', {})]
        assert finished == [('invalid-read', False, False)]
    finally:
        adapter.shutdown()


@pytest.mark.parametrize('uncertain', [False, True])
def test_native_mutation_exception_preserves_uncertainty_and_never_retries(uncertain):
    class SelectFailure(Client):
        def request(self, operation, **fields):
            if operation == 'profile_select':
                self.calls.append((operation, fields))
                raise NativeError(uncertain=uncertain)
            return super().request(operation, **fields)
    client = SelectFailure()
    adapter = ControllerProfileActivation(client)
    finished, _ = capture(adapter)
    try:
        assert adapter.request('A', 'failed-select')
        settle(adapter)
        assert [op for op, _ in client.calls] == ['profiles', 'profile_select']
        assert finished == [('failed-select', False, uncertain)]
        assert adapter.uncertain is uncertain
        QTest.qWait(10)
        assert len(client.calls) == 2
        assert 'private transport' not in adapter.status
    finally:
        adapter.shutdown()


def test_unclassified_exception_during_mutation_is_conservatively_uncertain():
    class SelectFailure(Client):
        def request(self, operation, **fields):
            if operation == 'profile_select':
                self.calls.append((operation, fields))
                raise RuntimeError('may have committed')
            return super().request(operation, **fields)
    client = SelectFailure()
    adapter = ControllerProfileActivation(client)
    finished, _ = capture(adapter)
    try:
        adapter.request('A', 'generic-failure')
        settle(adapter)
        assert finished == [('generic-failure', False, True)]
        assert adapter.uncertain
    finally:
        adapter.shutdown()


@pytest.mark.parametrize('mode', ['exception', 'mismatch', 'malformed'])
def test_post_select_readback_failure_is_always_uncertain(mode):
    class BadReadback(Client):
        def request(self, operation, **fields):
            if operation == 'profiles' and self.reads:
                self.calls.append((operation, fields))
                if mode == 'exception':
                    raise NativeError(uncertain=False, code='ERR_NOT_AVAILABLE')
                if mode == 'mismatch':
                    return {'names': ['Default', 'A'], 'active_profile': 'Default', 'revision': 'b' * 64}
                return {}
            return super().request(operation, **fields)
    client = BadReadback()
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    try:
        adapter.request('A', 'write-readback')
        settle(adapter)
        assert client.active == 'A'  # A committed effect is not a verified outcome.
        assert finished == [('write-readback', False, True)] and observed == []
        assert adapter.uncertain and adapter.activeProfile == ''
        assert [op for op, _ in client.calls] == ['profiles', 'profile_select', 'profiles']
        assert adapter.request('B', 'blocked') is False
        assert len(client.calls) == 3
    finally:
        adapter.shutdown()


def test_successful_explicit_refresh_reconciles_uncertain_without_mutation_replay():
    class LoseReply(Client):
        def request(self, operation, **fields):
            result = super().request(operation, **fields)
            if operation == 'profile_select':
                raise NativeError(uncertain=True, code='ERR_REPLY_LOST')
            return result
    client = LoseReply()
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    try:
        adapter.request('A', 'lost')
        settle(adapter)
        assert adapter.uncertain and finished == [('lost', False, True)]
        assert adapter.request('B', 'blocked') is False
        assert adapter.refresh()
        settle(adapter)
        assert observed == ['A'] and finished == [('lost', False, True)]
        assert not adapter.uncertain and adapter.activeProfile == 'A'
        assert [op for op, _ in client.calls] == ['profiles', 'profile_select', 'profiles']
    finally:
        adapter.shutdown()


def test_failed_refresh_neither_clears_uncertainty_nor_emits_completion_or_observation():
    class Failing(Client):
        failed = False
        def request(self, operation, **fields):
            if self.failed:
                self.calls.append((operation, fields))
                raise NativeError(uncertain=False)
            if operation == 'profile_select':
                self.failed = True
                self.calls.append((operation, fields))
                raise NativeError(uncertain=True)
            return super().request(operation, **fields)
    client = Failing()
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    try:
        adapter.request('A', 'lost')
        settle(adapter)
        assert adapter.refresh()
        settle(adapter)
        assert adapter.uncertain and observed == []
        assert finished == [('lost', False, True)]
        assert adapter.request('A', 'still-blocked') is False
    finally:
        adapter.shutdown()


class BlockingClient(Client):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def request(self, operation, **fields):
        if operation == 'profiles' and self.reads == 0:
            self.entered.set()
            if not self.release.wait(2):
                raise RuntimeError('fixture release timeout')
        return super().request(operation, **fields)


def test_busy_rejects_requests_and_refresh_without_hidden_queue():
    client = BlockingClient()
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    try:
        assert adapter.request('A', 'first')
        assert client.entered.wait(1)
        assert adapter.request('B', 'must-not-queue') is False
        assert adapter.refresh() is False
        client.release.set()
        settle(adapter)
        assert finished == [('first', True, False)] and observed == []
        QTest.qWait(10)
        assert [op for op, _ in client.calls] == ['profiles', 'profile_select', 'profiles']
    finally:
        client.release.set()
        adapter.shutdown()


def test_refresh_busy_suppression_does_not_queue_runtime_mutation():
    client = BlockingClient()
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    try:
        assert adapter.refresh()
        assert client.entered.wait(1)
        assert adapter.request('A', 'not-queued') is False
        client.release.set()
        settle(adapter)
        assert finished == [] and observed == ['Default']
        assert client.calls == [('profiles', {})]
    finally:
        client.release.set()
        adapter.shutdown()


def test_request_ids_remain_correlated_and_refresh_has_no_finished_signal():
    client = Client()
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    try:
        adapter.request('A', 'runtime:19')
        settle(adapter)
        adapter.refresh()
        settle(adapter)
        adapter.request('B', 'runtime:20')
        settle(adapter)
        assert finished == [('runtime:19', True, False), ('runtime:20', True, False)]
        assert observed == ['A']
    finally:
        adapter.shutdown()


def test_dedicated_adapter_does_not_change_ui_editor_or_dirty_draft():
    from controller_view import ControllerView
    client = Client()
    editor = ControllerView(client)
    editor._editing = 'Default'
    editor._profile = {'opaque': {'unsaved': True}}
    studio = QObject()
    studio.setProperty('dirty', True)
    before = (editor.editingProfile, editor.profileJson, studio.property('dirty'))
    adapter = ControllerProfileActivation(client)
    try:
        adapter.request('A', 'no-editor-change')
        settle(adapter)
        assert client.active == 'A'
        assert (editor.editingProfile, editor.profileJson, studio.property('dirty')) == before
        assert all(op != 'profile_get' for op, _ in client.calls)
    finally:
        adapter.shutdown()
        editor.shutdown()


@pytest.mark.parametrize('name,request_id', [('', 'id'), (' ', 'id'), ('A\n', 'id'),
    ('A', ''), ('A', 'bad\n'), ('A', 42), (None, 'id')])
def test_invalid_request_rejected_synchronously_without_client_calls(name, request_id):
    client = Client()
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    try:
        assert adapter.request(name, request_id) is False
        assert client.calls == [] and finished == [] and observed == []
    finally:
        adapter.shutdown()


def test_shutdown_closes_admission_and_never_synthesizes_completion():
    client = Client()
    adapter = ControllerProfileActivation(client)
    finished, observed = capture(adapter)
    adapter.shutdown()
    assert adapter.request('A', 'closed') is False
    assert adapter.refresh() is False
    assert client.calls == [] and finished == [] and observed == []


def test_shutdown_timeout_retains_running_thread_without_terminate_or_retry():
    client = BlockingClient()
    adapter = ControllerProfileActivation(client, shutdown_timeout_ms=1)
    finished, observed = capture(adapter)
    try:
        assert adapter.request('A', 'in-flight')
        assert client.entered.wait(1)
        with pytest.raises(RuntimeError, match='still settling'):
            adapter.shutdown()
        assert adapter.busy
        assert adapter.request('B', 'closed') is False
        client.release.set()
        settle(adapter)
        assert finished == [] and observed == []
        assert len([op for op, _ in client.calls if op == 'profile_select']) == 1
    finally:
        client.release.set()
        adapter.shutdown()
