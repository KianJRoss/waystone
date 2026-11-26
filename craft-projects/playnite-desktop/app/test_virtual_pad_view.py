"""Fake native output and JSON-only W16 bridge tests; no hardware or games."""
import copy
import json
import threading

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from native_services.unified_controller_control import ControllerControlError, VIRTUAL_PAD_TYPES
from virtual_pad_view import VirtualPadView


@pytest.fixture(scope='module', autouse=True)
def application():
    return QGuiApplication.instance() or QGuiApplication([])


def settle(view):
    for _ in range(500):
        QTest.qWait(5)
        if not view.busy: return
    raise AssertionError('fake output job did not settle')


class OutputClient:
    def __init__(self):
        self.calls = []
        self.current = self.default = 'xbox360'
        self.game_state = 'unknown'
        self.fail = None
        self.bad_read = False
        self.block = None
        self.read_threads = []
        self.slots = []
        self.owned_devices = []

    def request(self, op, **fields):
        self.calls.append((op, copy.deepcopy(fields)))
        self.read_threads.append(threading.get_ident())
        if self.block is not None: self.block.wait(1)
        if op == 'output_get':
            if self.bad_read: return {}
            return dict(current_type=self.current, default_type=self.default,
                        supported_types=list(VIRTUAL_PAD_TYPES), game_state=self.game_state,
                        game_observation_scope='waystone_tracked_games',
                        game_observation_age_seconds=0, revision='a' * 64,
                        slots=copy.deepcopy(self.slots), owned_devices=copy.deepcopy(self.owned_devices))
        if op == 'output_game_state':
            self.game_state = ('unknown' if not fields.get('observation_known', True)
                               else 'active' if fields['active_game_ids'] else 'idle')
            return {}
        if op == 'output_set':
            if self.fail is not None:
                error, commit = self.fail
                if commit: self.current = fields['type']
                raise error
            if self.current != fields['type'] and self.game_state != 'idle' and not fields['force']:
                raise ControllerControlError('ERR_GAME_ACTIVE')
            self.current = fields['type']
            if fields['persist_default']: self.default = self.current
            return {}
        raise AssertionError(op)


@pytest.fixture
def fixture(tmp_path):
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'profiles': {'General': {'opaque': [1]}}, 'active_profile': 'General',
                                'game_profiles': {'canonical': 'General'}, 'opaque': {'keep': True}}))
    client = OutputClient()
    view = VirtualPadView(path, client)
    yield view, client, path
    view.shutdown()


def set_profile_type(path, profile, pad_type, **extra):
    doc = json.loads(path.read_text())
    doc.update(extra)
    doc['profiles'].setdefault(profile, {})['virtual_gamepad_driver'] = pad_type
    path.write_text(json.dumps(doc))


def test_pad_type_follows_the_games_profile_without_touching_native(fixture):
    view, client, path = fixture
    set_profile_type(path, 'General', 'dualsense')
    set_profile_type(path, 'Racing', 'xboxone', game_profiles={'canonical': 'General', 'racer': 'Racing'})
    view.reloadSettings()
    view.selectGame('canonical')
    assert view.profileForGame('canonical') == 'General' and view.resolveType('canonical') == 'dualsense'
    assert view.profileForGame('racer') == 'Racing' and view.resolveType('racer') == 'xboxone'
    # a game with no profile of its own uses the general (here: active) profile
    assert view.profileForGame('unassigned') == 'General' and view.resolvedType == 'dualsense'
    assert client.calls == [] and not view.available and view.launchEnvironment() == {}
    assert json.loads(path.read_text())['opaque'] == {'keep': True}

def test_launch_filters_withheld_when_engine_is_idle(fixture):
    view, client, _ = fixture
    client.slots = [{'slot': 1, 'type': 'xbox360', 'connected': True, 'state': 'connected'}]
    client.owned_devices = [{'stable_id': 'physical', 'vendor_id': 0x1234, 'product_id': 0x5678,
                             'connected': True, 'virtual': False}]
    client.game_state = 'idle'
    assert view.refresh()
    settle(view)
    assert view.available and view.currentType == 'xbox360'
    assert client.game_state == 'idle' and view.launchEnvironment() == {}


def test_profile_without_a_type_uses_the_default_and_old_per_game_map_is_ignored(fixture):
    view, client, path = fixture
    doc = json.loads(path.read_text())
    doc['virtual_gamepad_driver'] = 'xboxseries'
    doc['game_virtual_gamepad_drivers'] = {'canonical': 'ds4'}
    path.write_text(json.dumps(doc))
    view.reloadSettings()
    assert view.profileType('General') == '' and view.resolveType('canonical') == 'xboxseries'

def test_unreadable_settings_block_launch_and_are_never_written(fixture):
    view, client, path = fixture
    path.write_text('{broken')
    results = []
    assert not view.prepareLaunch('a', lambda *result: results.append(result))
    assert results and not results[0][0] and 'unreadable' in results[0][1]
    assert path.read_text() == '{broken' and client.calls == []

def test_switch_syncs_guard_then_applies_then_reads_back_off_gui_thread(fixture):
    view, client, _ = fixture
    assert view.setDefaultType('xboxseries')
    settle(view)
    assert client.calls == [('output_game_state', {'active_game_ids': [], 'observation_known': True}),
                            ('output_set', {'type': 'xboxseries', 'force': False, 'persist_default': True}),
                            ('output_get', {})]
    assert view.currentType == view.defaultType == 'xboxseries' and view.available
    assert not view.uncertain and not view.error
    assert all(ident != threading.get_ident() for ident in client.read_threads)


def test_active_overlap_rejects_without_force_and_never_persists_game_type(fixture):
    view, client, _ = fixture
    view.observe('session', 'canonical', 'launching')
    assert view.applyType('dualshock4')
    settle(view)
    assert view.currentType == 'xbox360' and client.default == 'xbox360'
    writes = [fields for op, fields in client.calls if op == 'output_set']
    assert writes == [{'type': 'dualshock4', 'force': False, 'persist_default': False}]
    assert client.calls[-1] == ('output_get', {})
    assert not view.uncertain


def test_unknown_invalidates_idle_and_failed_running_never_means_exit(fixture):
    view, client, _ = fixture
    view.observe('session', 'game', 'running')
    view.observe('session', 'game', 'unknown')
    assert view._observation() == ([], False)
    view.observe('session', 'game', 'failed')
    assert view._observation() == ([], False) and not view._restore_pending
    view.observe('session', 'game', 'closed')
    assert view._observation() == ([], True) and view._restore_pending


def test_uncertain_write_always_reads_back_no_timer_or_explicit_write_retry(fixture):
    view, client, _ = fixture
    client.fail = (ControllerControlError('ERR_TIMEOUT', uncertain=True), True)
    assert view.applyType('dualsense')
    settle(view)
    assert view.uncertain and view.currentType == 'dualsense'
    assert client.calls[-1] == ('output_get', {})
    before = list(client.calls)
    assert not view.applyType('xbox360') and client.calls == before
    view._heartbeat(); settle(view)
    assert view.uncertain and len([op for op, _ in client.calls if op == 'output_set']) == 1
    assert view.refresh(); settle(view)
    assert not view.uncertain and view.currentType == 'dualsense'


def test_malformed_post_write_readback_is_uncertain(fixture):
    view, client, _ = fixture
    client.bad_read = True
    assert view.setDefaultType('xboxone')
    settle(view)
    assert view.uncertain and not view.available and client.current == 'xboxone'


def test_restore_default_only_after_confirmed_last_exit_once(fixture):
    view, client, _ = fixture
    client.current = 'dualsense'
    view.observe('one', 'a', 'running'); view.observe('two', 'b', 'running')
    view.observe('two', 'b', 'closed')
    view._heartbeat(); settle(view)
    assert not [op for op, _ in client.calls if op == 'output_set']
    view.observe('one', 'a', 'unknown')
    view._heartbeat(); settle(view)
    assert not [op for op, _ in client.calls if op == 'output_set']
    view.observe('one', 'a', 'closed')
    view._heartbeat(); settle(view)
    assert client.current == 'xbox360'
    view._heartbeat(); settle(view)
    assert len([op for op, _ in client.calls if op == 'output_set']) == 1


def test_legacy_unavailable_launch_continues_unverified_but_explicit_override_fails(fixture):
    view, client, _path = fixture
    def unavailable(op, **fields):
        client.calls.append((op, fields))
        raise ControllerControlError('ERR_NOT_AVAILABLE')
    client.request = unavailable
    results = []
    assert view.prepareLaunch('game', lambda *result: results.append(result)); settle(view)
    assert results == [(True, '')] and 'unverified' in view.status
    set_profile_type(_path, 'General', 'xbox360')     # an explicit profile type needs a verified pad
    view.reloadSettings()
    results.clear()
    assert view.prepareLaunch('game', lambda *result: results.append(result)); settle(view)
    assert results and not results[0][0]


def test_legacy_launch_continues_when_the_engine_times_out(fixture):
    """Review 2026-11-25: a hung engine must not block Play for games that never asked for a pad type."""
    view, client, _ = fixture
    def slow(op, **fields):
        client.calls.append((op, fields))
        raise ControllerControlError('ERR_TIMEOUT', uncertain=False)
    client.request = slow
    results = []
    assert view.prepareLaunch('game', lambda *result: results.append(result)); settle(view)
    assert results == [(True, '')] and view.launchEnvironment() == {}


@pytest.mark.parametrize('default', ['dualsense', 'future'])
def test_nonlegacy_or_invalid_default_never_silently_allows_launch(fixture, default):
    view, client, path = fixture
    doc = json.loads(path.read_text()); doc['virtual_gamepad_driver'] = default; path.write_text(json.dumps(doc))
    def unavailable(*args, **kwargs): raise ControllerControlError('ERR_NOT_AVAILABLE')
    client.request = unavailable
    results = []
    accepted = view.prepareLaunch('game', lambda *result: results.append(result))
    if accepted: settle(view)
    assert results and not results[0][0]


def test_uncertain_switch_always_reads_back_and_blocks_replay_until_explicit_refresh(fixture):
    view, client, _ = fixture
    client.fail = (ControllerControlError('ERR_TIMEOUT', uncertain=True), True)
    assert view.setDefaultType('dualsense'); settle(view)
    assert view.currentType == 'dualsense' and view.uncertain
    assert [op for op, _ in client.calls] == ['output_game_state', 'output_set', 'output_get']
    before = len(client.calls)
    assert not view.setDefaultType('dualsense') and not view.prepareLaunch('game', lambda *args: None)
    assert len(client.calls) == before
    view._heartbeat(); settle(view)
    assert view.uncertain  # timer readback is not explicit reconciliation
    client.fail = None
    assert view.refresh(); settle(view)
    assert not view.uncertain and client.calls[-1] == ('output_get', {})


def test_failed_readback_does_not_expose_stale_prewrite_type(fixture):
    view, client, _ = fixture
    assert view.refresh(); settle(view)
    assert view.currentType == 'xbox360'
    client.fail = (ControllerControlError('ERR_TIMEOUT', uncertain=True), False)
    client.bad_read = True
    assert view.setDefaultType('xboxone'); settle(view)
    assert view.uncertain and not view.available and view.currentType == ''


def test_unknown_tracker_invalidates_idle_observation_and_never_restores(fixture):
    view, client, _ = fixture
    view.observe('session', 'immutable-game', 'running')
    view.observe('session', 'immutable-game', 'unknown')
    view._heartbeat(); settle(view)
    assert client.calls[0] == ('output_game_state', {'active_game_ids': [], 'observation_known': False})
    assert not [op for op, _ in client.calls if op == 'output_set']
    view.observe('session', 'immutable-game', 'failed')
    view._heartbeat(); settle(view)
    assert client.game_state == 'unknown' and not [op for op, _ in client.calls if op == 'output_set']


def test_unknown_profile_type_is_not_used(fixture):
    view, client, path = fixture
    set_profile_type(path, 'General', 'n64-pad')
    view.reloadSettings()
    assert view.profileType('General') == '' and view.resolveType('canonical') == 'xbox360'
    assert view.profileType('Missing') == '' and not client.calls

def test_known_current_mismatch_blocks_legacy_unavailable_fallback(fixture):
    view, client, _ = fixture
    client.current = 'dualsense'
    view.refresh(); settle(view)
    def unavailable(*args, **kwargs): raise ControllerControlError('ERR_NOT_AVAILABLE')
    client.request = unavailable
    results = []
    assert view.prepareLaunch('game', lambda *args: results.append(args)); settle(view)
    assert not results[0][0]


def test_play_waits_for_one_bounded_heartbeat_not_rejected_as_busy(fixture):
    view, client, _ = fixture
    gate = threading.Event(); client.block = gate
    assert view._start('heartbeat')
    results = []
    assert view.prepareLaunch('game', lambda *args: results.append(args))
    assert not results
    gate.set(); settle(view)
    assert results == [(True, '')]
    assert len([op for op, _ in client.calls if op == 'output_set']) == 1


# ---- passthrough: competitive games get the real controller (Kian 2026-11-25) ----

class FakeIndex:
    def __init__(self, titles): self.titles = titles
    def anticheats(self, title='', steam_app_id=''): return ['Easy Anti-Cheat'] if title in self.titles else []


def physical_pad(client, vendor=0x3537, product=0x100b):
    client.slots = [{'slot': 1, 'type': client.current, 'connected': True, 'state': 'connected'}]
    client.owned_devices = [{'stable_id': 'physical', 'vendor_id': vendor, 'product_id': product,
                             'connected': True, 'virtual': False}]


def launch(view, game, title=''):
    results = []
    assert view.prepareLaunch(game, lambda *r: results.append(r), title)
    settle(view)
    return results


def test_anticheat_game_defaults_to_the_real_controller_and_hides_only_the_virtual_pad(tmp_path):
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'profiles': {'General': {}}, 'active_profile': 'General'}))
    client = OutputClient(); physical_pad(client)
    view = VirtualPadView(path, client, anticheat=FakeIndex({'Rocket League®'}))
    try:
        assert view.resolveTypeFor('rl', 'Rocket League®') == 'passthrough'
        assert view.resolveTypeFor('other', 'Celeste') == 'xbox360'
        assert view.anticheatFor('Rocket League®') == 'Easy Anti-Cheat'
        assert launch(view, 'rl', 'Rocket League®') == [(True, '')]
        assert not any(op == 'output_set' for op, _ in client.calls)        # nothing switched for passthrough
        assert view.launchEnvironment() == {'SDL_GAMECONTROLLER_IGNORE_DEVICES': '0x045e/0x028e',
                                            'SDL_JOYSTICK_BLACKLIST_DEVICES': '0x045e/0x028e'}
        assert view.lastFilterReason == 'passthrough: virtual pad hidden'
    finally:
        view.shutdown()


def test_profile_choice_beats_the_anticheat_default_both_ways(tmp_path):
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'profiles': {'General': {}, 'Pad': {'virtual_gamepad_driver': 'dualsense'},
                                             'Real': {'virtual_gamepad_driver': 'passthrough'}},
                                'active_profile': 'General', 'game_profiles': {'rl': 'Pad', 'celeste': 'Real'}}))
    view = VirtualPadView(path, OutputClient(), anticheat=FakeIndex({'Rocket League'}))
    try:
        assert view.resolveTypeFor('rl', 'Rocket League') == 'dualsense'        # asked for a virtual pad
        assert view.resolveTypeFor('celeste', 'Celeste') == 'passthrough'       # asked for the real controller
        assert view.profileType('Real') == 'passthrough'
        assert any(c['value'] == 'passthrough' for c in view.choices)
    finally:
        view.shutdown()


def test_passthrough_moves_the_virtual_pad_off_a_real_pads_id(tmp_path):
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'profiles': {'General': {'virtual_gamepad_driver': 'passthrough'}},
                                'active_profile': 'General'}))
    client = OutputClient(); physical_pad(client, 0x045e, 0x028e)           # a genuine Xbox 360 pad
    view = VirtualPadView(path, client, anticheat=FakeIndex(set()))
    try:
        assert view.refresh(); settle(view)
        assert launch(view, 'game') == [(True, '')]
        sets = [f['type'] for op, f in client.calls if op == 'output_set']
        assert sets == ['xboxone'] and client.default == 'xbox360'          # switched for this launch only
        env = view.launchEnvironment()
        assert env['SDL_GAMECONTROLLER_IGNORE_DEVICES'] == '0x045e/0x02ea'  # the real 045e:028e stays visible
    finally:
        view.shutdown()


def test_passthrough_never_blocks_play_when_the_engine_is_down(tmp_path):
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'profiles': {'General': {}}, 'active_profile': 'General'}))
    client = OutputClient()
    def unavailable(op, **fields):
        raise ControllerControlError('ERR_NOT_AVAILABLE')
    client.request = unavailable
    view = VirtualPadView(path, client, anticheat=FakeIndex({'Rocket League'}))
    try:
        assert launch(view, 'rl', 'Rocket League') == [(True, '')]
        assert view.launchEnvironment() == {}
        assert view.lastFilterReason in ('engine not available', 'no engine readback')
    finally:
        view.shutdown()


def test_virtual_pad_launch_hides_the_real_pad_when_type_is_unchanged(tmp_path):
    """Kian's Xbox 360 run: profile Xbox 360, engine already Xbox 360 -> the real pad must still be hidden."""
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'profiles': {'General': {'virtual_gamepad_driver': 'xbox360'}},
                                'active_profile': 'General'}))
    client = OutputClient(); physical_pad(client)
    view = VirtualPadView(path, client, anticheat=FakeIndex(set()))
    try:
        assert launch(view, 'game', 'Some Game') == [(True, '')]
        env = view.launchEnvironment()
        assert env.get('SDL_GAMECONTROLLER_IGNORE_DEVICES') == '0x3537/0x100b', env
    finally:
        view.shutdown()
