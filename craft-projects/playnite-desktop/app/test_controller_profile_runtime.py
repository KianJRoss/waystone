"""Offline fixtures only: no engine, host process, controller, or socket."""
import pytest

from controller_profile_runtime import ControllerProfileRuntime
from controller_profile_assignments import resolve


class Fixture:
    def __init__(self, accepted=True):
        self.calls = []
        self.document = {
            'version': 1, 'profiles': {'Default': {}, 'A': {}, 'B': {}},
            'active_profile': 'Default', 'default_profile': 'Default',
            'game_profiles': {'game-a': 'A', 'game-b': 'B'},
        }
        self.accepted = accepted
        self.runtime = ControllerProfileRuntime(
            lambda game: resolve(self.document, game), self.activate)

    def activate(self, profile, request_id):
        self.calls.append((profile, request_id))
        return self.accepted

    def finish(self, success=True, **kwargs):
        return self.runtime.complete(self.runtime.pending_request_id, success, **kwargs)

    @property
    def names(self):
        return [name for name, _ in self.calls]


def test_construction_does_not_activate_or_resolve():
    def forbidden(*args):
        raise AssertionError('no constructor effects')
    runtime = ControllerProfileRuntime(forbidden, forbidden)
    assert runtime.status == 'idle'
    assert runtime.active_profile is runtime.desired_profile is None


@pytest.mark.parametrize('state', ['launching', 'unknown', 'closed', 'failed'])
def test_no_activation_on_handoff_unconfirmed_or_failed_start(state):
    fixture = Fixture()
    fixture.runtime.observe('s1', 'game-a', state)
    assert not fixture.calls
    assert not fixture.runtime.active_sessions


def test_game_start_async_confirmation_then_actual_exit_restores_default():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    assert fixture.names == ['A']
    assert runtime.pending_profile == 'A'
    assert runtime.active_profile is None  # accepted is not confirmed
    fixture.finish()
    assert runtime.active_profile == 'A'
    runtime.observe('s1', 'game-a', 'closed')
    assert fixture.names == ['A', 'Default']
    assert runtime.active_profile == 'A'
    fixture.finish()
    assert runtime.active_profile == 'Default'


def test_unassigned_game_resolves_default():
    fixture = Fixture()
    fixture.runtime.observe('s1', 'unassigned-canonical-id', 'running')
    assert fixture.names == ['Default']


def test_duplicate_polls_do_not_repeat_or_promote_old_session():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    runtime.observe('s1', 'game-a', 'running')
    fixture.finish()
    runtime.observe('s2', 'game-b', 'running')
    fixture.finish()
    runtime.observe('s1', 'game-a', 'running')
    assert fixture.names == ['A', 'B']
    assert runtime.desired_profile == 'B'


@pytest.mark.parametrize('state', ['unknown', 'launching', 'failed'])
def test_uncertain_process_observation_is_not_an_exit(state):
    fixture = Fixture()
    fixture.runtime.observe('s1', 'game-a', 'running')
    fixture.finish()
    fixture.runtime.observe('s1', 'game-a', state)
    assert fixture.names == ['A']
    assert fixture.runtime.active_sessions == {'s1': 'game-a'}


def test_newest_game_exit_falls_back_to_other_running_game_then_default():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    fixture.finish()
    runtime.observe('s2', 'game-b', 'running')
    fixture.finish()
    runtime.observe('s2', 'game-b', 'closed')
    assert fixture.names == ['A', 'B', 'A']
    fixture.finish()
    runtime.observe('s1', 'game-a', 'closed')
    assert fixture.names == ['A', 'B', 'A', 'Default']


def test_older_game_exit_does_not_restore_default_or_switch_newer_game():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    fixture.finish()
    runtime.observe('s2', 'game-b', 'running')
    fixture.finish()
    runtime.observe('s1', 'game-a', 'closed')
    assert fixture.names == ['A', 'B']
    assert runtime.desired_profile == 'B'


def test_same_game_overlapping_sessions_keep_profile_until_last_exit():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    fixture.finish()
    runtime.observe('s2', 'game-a', 'running')
    runtime.observe('s1', 'game-a', 'closed')
    assert fixture.names == ['A']
    runtime.observe('s2', 'game-a', 'closed')
    assert fixture.names == ['A', 'Default']


def test_pending_changes_coalesce_without_parallel_requests():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    runtime.observe('s2', 'game-b', 'running')
    runtime.observe('s1', 'game-a', 'closed')
    assert fixture.names == ['A']
    fixture.finish()
    assert fixture.names == ['A', 'B']
    assert runtime.active_profile == 'A'
    fixture.finish()
    assert runtime.active_profile == 'B'


def test_exit_during_pending_start_restores_default_after_confirmation():
    fixture = Fixture()
    fixture.runtime.observe('s1', 'game-a', 'running')
    fixture.runtime.observe('s1', 'game-a', 'closed')
    fixture.finish()
    assert fixture.names == ['A', 'Default']


@pytest.mark.parametrize('terminal', ['closed', 'failed'])
def test_terminal_ids_do_not_revive_on_late_running_poll(terminal):
    fixture = Fixture()
    fixture.runtime.observe('s1', 'game-a', terminal)
    fixture.runtime.observe('s1', 'game-a', 'running')
    assert fixture.calls == []


def test_stale_or_duplicate_completion_cannot_confirm_another_request():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    first = runtime.pending_request_id
    assert runtime.complete(first + 99, True) is False
    assert runtime.complete(True, True) is False
    runtime.observe('s2', 'game-b', 'running')
    assert runtime.complete(first, True) is True
    assert runtime.complete(first, False, uncertain=True) is False
    assert runtime.pending_profile == 'B'
    assert runtime.uncertain is False


def test_false_callback_means_not_sent_and_only_explicit_reconcile_retries():
    fixture = Fixture(accepted=False)
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    assert runtime.status == 'rejected'
    runtime.observe('s1', 'game-a', 'running')
    runtime.observe('s2', 'game-b', 'running')
    assert fixture.names == ['A']
    fixture.accepted = True
    runtime.reconcile()
    assert fixture.names == ['A', 'B']


def test_async_rejection_does_not_retry_or_send_coalesced_mutation():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    runtime.observe('s2', 'game-b', 'running')
    fixture.finish(False)
    runtime.observe('s2', 'game-b', 'running')
    assert fixture.names == ['A']
    runtime.reconcile()
    assert fixture.names == ['A', 'B']


def test_uncertain_blocks_until_explicit_verified_readback_without_replay():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    fixture.finish(False, uncertain=True)
    runtime.observe('s1', 'game-a', 'closed')
    runtime.reconcile()
    assert fixture.names == ['A']
    assert runtime.uncertain is True
    assert runtime.active_profile is None
    runtime.reset_from_observation('A')
    assert fixture.names == ['A']
    runtime.reconcile()
    assert fixture.names == ['A', 'Default']


@pytest.mark.parametrize('accepted', [None, 1, 'queued'])
def test_ambiguous_callback_return_is_not_assumed_success(accepted):
    fixture = Fixture(accepted=accepted)
    fixture.runtime.observe('s1', 'game-a', 'running')
    assert fixture.runtime.uncertain is True
    fixture.runtime.reconcile()
    assert fixture.names == ['A']


def test_callback_exception_is_conservatively_uncertain_without_retry():
    calls = []
    def activate(name, request_id):
        calls.append(name)
        raise RuntimeError('unknown transport outcome')
    runtime = ControllerProfileRuntime(lambda game: 'A', activate)
    runtime.observe('s1', 'game-a', 'running')
    runtime.reconcile()
    assert calls == ['A']
    assert runtime.uncertain is True


def test_explicit_reconcile_applies_changed_assignment_and_default():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.reset_from_observation('Default')
    runtime.reconcile()
    assert fixture.calls == []
    runtime.observe('s1', 'game-a', 'running')
    fixture.finish()
    fixture.document['game_profiles']['game-a'] = 'B'
    runtime.observe('s1', 'game-a', 'running')
    assert fixture.names == ['A']
    runtime.reconcile()
    assert fixture.names == ['A', 'B']
    fixture.finish()
    fixture.document['default_profile'] = 'B'
    runtime.observe('s1', 'game-a', 'closed')
    assert fixture.names == ['A', 'B']
    fixture.document['default_profile'] = 'Default'
    runtime.reconcile()
    assert fixture.names == ['A', 'B', 'Default']


def test_deleted_assignment_falls_back_through_existing_resolver():
    fixture = Fixture()
    fixture.runtime.observe('s1', 'game-a', 'running')
    fixture.finish()
    del fixture.document['profiles']['A']
    fixture.runtime.reconcile()
    assert fixture.names == ['A', 'Default']


def test_no_profile_means_no_activation_not_stale_target():
    fixture = Fixture()
    fixture.document['profiles'] = {}
    fixture.runtime.observe('s1', 'game-a', 'running')
    assert fixture.calls == []
    assert fixture.runtime.desired_profile is None


def test_resolution_failure_during_pending_request_cannot_apply_stale_target():
    fixture = Fixture()
    runtime = fixture.runtime
    runtime.observe('s1', 'game-a', 'running')
    runtime.observe('s2', 'game-b', 'running')
    def broken(game):
        raise OSError('assignment storage unavailable')
    runtime._resolver = broken
    runtime.reconcile()
    fixture.finish()
    assert fixture.names == ['A']
    assert runtime.desired_profile is None


def test_readback_is_not_allowed_to_override_unsettled_request():
    fixture = Fixture()
    fixture.runtime.observe('s1', 'game-a', 'running')
    with pytest.raises(RuntimeError):
        fixture.runtime.reset_from_observation('B')
    assert fixture.runtime.pending_profile == 'A'


def test_session_identity_is_immutable_and_active_sessions_is_snapshot():
    fixture = Fixture()
    fixture.runtime.observe('s1', 'canonical-id', 'launching')
    with pytest.raises(ValueError):
        fixture.runtime.observe('s1', 'different-id', 'running')
    fixture.runtime.observe('s1', 'canonical-id', 'running')
    fixture.runtime.active_sessions.clear()
    assert fixture.runtime.active_sessions == {'s1': 'canonical-id'}


@pytest.mark.parametrize('args', [('', 'game-a', 'running'), ('s1', '', 'running'),
                                   ('s1', 'game-a', 'handed_off')])
def test_invalid_observation_rejected_without_effect(args):
    fixture = Fixture()
    with pytest.raises(ValueError):
        fixture.runtime.observe(*args)
    assert not fixture.calls


def test_synchronous_fixture_completion_is_supported():
    runtime = None
    def activate(name, request_id):
        runtime.complete(request_id, True)
        return True
    runtime = ControllerProfileRuntime(lambda game: 'A', activate)
    runtime.observe('s1', 'game-a', 'running')
    assert runtime.active_profile == 'A'
    assert runtime.pending_request_id is None
