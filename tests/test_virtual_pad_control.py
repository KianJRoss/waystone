"""Protocol-only W16 validation; no sockets or devices."""
import pytest
from native_services.unified_controller_control import (
    ControllerControlError, _validate_operation_args, validate_output_state,
    VIRTUAL_PAD_TYPES, MUTATION_OPERATIONS,
)


def state(**changes):
    data = dict(current_type='xbox360', default_type='xbox360', supported_types=list(VIRTUAL_PAD_TYPES),
                game_state='idle', game_observation_scope='waystone_tracked_games',
                game_observation_age_seconds=0.5, revision='a' * 64)
    data.update(changes)
    return data


@pytest.mark.parametrize('pad_type', VIRTUAL_PAD_TYPES)
def test_typed_output_set_canonical_values(pad_type):
    _validate_operation_args('output_set', {'type': pad_type, 'force': False, 'persist_default': True})
    assert 'output_set' in MUTATION_OPERATIONS


@pytest.mark.parametrize('fields', [
    {}, {'type': 'switchpro'}, {'type': 'XboxOne'}, {'type': 'xbox360', 'force': 1},
    {'type': 'xbox360', 'persist_default': None}, {'type': 'xbox360', 'extra': False},
    {'type': 'xbox360', 'request_id': 'x'},
])
def test_bad_switch_rejected_before_any_io(fields):
    with pytest.raises(ControllerControlError): _validate_operation_args('output_set', fields)


@pytest.mark.parametrize('fields', [
    {'active_game_ids': []}, {'active_game_ids': ['canonical:1', '["steam","7"]']},
    {'active_game_ids': [], 'observation_known': False},
])
def test_typed_observation(fields):
    _validate_operation_args('output_game_state', fields)


@pytest.mark.parametrize('fields', [
    {'active_game_ids': None}, {'active_game_ids': ['same', 'same']},
    {'active_game_ids': ['']}, {'active_game_ids': ['a\nb']},
    {'active_game_ids': [], 'observation_known': 0},
    {'active_game_ids': ['active'], 'observation_known': False},
    {'active_game_ids': [], 'state': 'idle'},
])
def test_unknown_observation_contract_rejects_ambiguous_data(fields):
    with pytest.raises(ControllerControlError): _validate_operation_args('output_game_state', fields)


def test_readback_retains_unknown_and_null_device():
    data = state(current_type=None, extension={'keep': True})
    assert validate_output_state(data) is data


@pytest.mark.parametrize('changes', [
    {'current_type': 'switchpro'}, {'default_type': 'future'}, {'supported_types': []},
    {'supported_types': ['xbox360', 'xbox360']}, {'game_state': 'gaming'},
    {'game_observation_scope': 'all_system_games'}, {'game_observation_age_seconds': float('nan')},
    {'game_observation_age_seconds': True}, {'revision': 'untrusted'},
])
def test_invalid_readback_fails_closed(changes):
    with pytest.raises(ControllerControlError): validate_output_state(state(**changes))
