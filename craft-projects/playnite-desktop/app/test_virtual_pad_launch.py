"""Correlated prelaunch continuations and fake-only actual spawn ordering."""
import json
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from launch_view import LaunchView
from native_services.launch_profiles import LaunchProfile
from test_virtual_pad_view import OutputClient, settle
from virtual_pad_view import VirtualPadView


@pytest.fixture(scope='module', autouse=True)
def application():
    return QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture
def fixture(tmp_path):
    spawned = []
    exe = tmp_path / 'fixture-steam'; exe.write_text('never executed'); exe.chmod(0o700)
    view = LaunchView(tmp_path / 'launch-profiles', spawn_fn=lambda *a, **k: (spawned.append((a, k)) or SimpleNamespace(pid=42)))
    view._profile_store.create_profile(LaunchProfile('test', 'Fixture', 'native_game', 'steam', str(exe), ('{game_id}',)))
    view._profile_store.default_associations['steam'] = 'test'; view._profile_store.save()
    view.setTarget('variant', {'provider': 'steam', 'product_id': '111', 'canonical_game_id': 'canonical-A'})
    yield view, spawned
    view._tracking_timer.stop()


def test_pending_guard_never_spawns_until_correlated_success_and_blocks_duplicate(fixture):
    view, spawned = fixture
    callbacks = []
    view.setPrelaunchGuard(lambda game, callback: (callbacks.append((game, callback)) or True))
    assert view.play()['status'] == 'preparing_controller'
    assert callbacks[0][0] == 'canonical-A' and not spawned
    assert view.play()['status'] == 'preparing_controller' and len(callbacks) == 1
    callbacks[0][1](True, '')
    assert len(spawned) == 1 and view.launchStatus == 'handed_off'
    assert 'SDL_GAMECONTROLLER_IGNORE_DEVICES' not in spawned[0][1]['env']
    callbacks[0][1](True, '')
    assert len(spawned) == 1  # duplicate completion cannot spawn again


def test_selection_change_cancels_late_success(fixture):
    view, spawned = fixture
    callbacks = []
    view.setPrelaunchGuard(lambda game, callback: (callbacks.append(callback) or True))
    view.play()
    view.setTarget('other', {'provider': 'steam', 'product_id': '222', 'canonical_game_id': 'canonical-B'})
    callbacks[0](True, '')
    assert not spawned and view.selectedTargetId == '222' and view.launchStatus == 'idle'


def test_guard_rejection_failure_and_exception_never_spawn(fixture):
    view, spawned = fixture
    callbacks = []
    view.setPrelaunchGuard(lambda game, callback: (callbacks.append(callback) or True))
    view.play(); callbacks[0](False, 'Output switch rejected: tracked game active')
    assert not spawned and 'rejected' in view._last_error
    view.setPrelaunchGuard(lambda game, callback: False)
    view.play(); assert not spawned and view.launchStatus == 'failed'
    def broken(*args): raise RuntimeError('private exception')
    view.setPrelaunchGuard(broken)
    view.play(); assert not spawned and 'private' not in view._last_error


def test_real_fake_bridge_applies_before_spawn_and_does_not_persist_override(fixture, tmp_path):
    view, spawned = fixture
    path = tmp_path / 'pad-profiles.json'
    path.write_text(json.dumps({'virtual_gamepad_driver': 'xbox360', 'active_profile': 'General',
                                'profiles': {'General': {}, 'Pads': {'virtual_gamepad_driver': 'dualsense'}},
                                'game_profiles': {'canonical-A': 'Pads'}}))
    client = OutputClient()
    client.slots = [{'slot': 1, 'type': 'dualsense', 'connected': True, 'state': 'connected'}]
    client.owned_devices = [{'stable_id': 'gamesir-one', 'vendor_id': 0x3537, 'product_id': 0x0575,
                             'connected': True, 'virtual': False},
                            {'stable_id': 'virtual-pad', 'vendor_id': 0x054c, 'product_id': 0x0ce6,
                             'connected': True, 'virtual': True}]
    pad = VirtualPadView(path, client)
    try:
        view.setPrelaunchGuard(pad.prepareLaunch)
        view.setLaunchEnvironmentProvider(pad.launchEnvironment)
        assert view.play()['status'] == 'preparing_controller' and not spawned
        settle(pad)
        assert client.current == 'dualsense' and client.default == 'xbox360' and len(spawned) == 1
        assert [op for op, _ in client.calls] == ['output_game_state', 'output_set', 'output_game_state', 'output_get']
        assert client.calls[0][1]['active_game_ids'] == []
        assert client.calls[2][1]['active_game_ids'] == ['canonical-A']
        assert client.calls[1][1] == {'type': 'dualsense', 'force': False, 'persist_default': False}
        launch_env = spawned[0][1]['env']
        assert launch_env['SDL_GAMECONTROLLER_IGNORE_DEVICES'] == '0x3537/0x0575'
        assert launch_env['SDL_JOYSTICK_BLACKLIST_DEVICES'] == '0x3537/0x0575'
        assert launch_env['PROTON_DISABLE_HIDRAW'] == '1'
    finally:
        pad.shutdown()


def test_overlapping_different_pad_is_rejected_not_forced(fixture, tmp_path):
    view, spawned = fixture
    path = tmp_path / 'pad-profiles.json'
    path.write_text(json.dumps({'active_profile': 'General', 'profiles': {
        'General': {}, 'Pads': {'virtual_gamepad_driver': 'dualsense'}}, 'game_profiles': {'canonical-A': 'Pads'}}))
    client = OutputClient(); pad = VirtualPadView(path, client)
    try:
        pad.observe('existing', 'another-game', 'running')
        view.setPrelaunchGuard(pad.prepareLaunch)
        view.play(); settle(pad)
        assert not spawned and client.current == 'xbox360' and client.calls[1][1]['force'] is False
        assert client.calls[0][1]['active_game_ids'] == ['another-game']
    finally:
        pad.shutdown()
