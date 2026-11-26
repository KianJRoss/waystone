from pathlib import Path

from native_services.launch_profiles import LaunchProfile, LaunchProfileStore, _default_executable


def _executable(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o755)
    return path


def test_default_command_prefers_path_and_only_uses_existing_local_bin(tmp_path):
    path_bin = _executable(tmp_path / 'path' / 'steam')
    local_bin = _executable(tmp_path / 'home' / '.local/bin/steam')
    assert _default_executable('steam', home=tmp_path / 'home', which=lambda _name: str(path_bin)) == str(path_bin)
    assert _default_executable('steam', home=tmp_path / 'home', which=lambda _name: None) == str(local_bin)
    assert _default_executable('missing', home=tmp_path / 'home', which=lambda _name: None) == 'missing'


def test_seeded_default_resolves_current_path_for_existing_profile_without_overwriting_user_edit(tmp_path, monkeypatch):
    path_bin = _executable(tmp_path / 'bin' / 'steam')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    store = LaunchProfileStore(tmp_path / 'profiles')
    store.seed_reviewed_recipes()
    # Simulate an older saved default whose former ~/.local/bin path is stale.
    old = store.profiles['native-steam']
    from dataclasses import replace
    store.profiles[old.profile_id] = replace(old, executable=str(Path.home() / '.local/bin/steam'))
    monkeypatch.setattr('native_services.launch_profiles.shutil.which', lambda name: str(path_bin) if name == 'steam' else None)
    intent = store.build_launch_intent('42', 'steam', target_type='native_game',
                                       current_host=store.profiles['native-steam'].target_host)
    assert intent.verified_contract is True
    assert intent.executable == str(path_bin)

    custom = replace(store.profiles['native-steam'], executable=str(tmp_path / 'custom/steam'))
    store.update_profile(custom)
    intent = store.build_launch_intent('43', 'steam', target_type='native_game', current_host=custom.target_host)
    assert intent.executable == str(tmp_path / 'custom/steam')


def test_lutris_service_defaults_target_exact_native_game_row_id(tmp_path, monkeypatch):
    lutris = _executable(tmp_path / 'bin' / 'lutris')
    monkeypatch.setattr('native_services.launch_profiles.shutil.which', lambda name: str(lutris) if name == 'lutris' else None)
    store = LaunchProfileStore(tmp_path / 'profiles')
    store.seed_reviewed_recipes()
    for provider, expected_profile in (
        ('ea app', 'native-lutris-ea'),
        ('ubisoft connect', 'native-lutris-ubisoft'),
        ('battle.net', 'native-lutris-battlenet-game'),
    ):
        intent = store.build_launch_intent('123', provider, target_type='native_game',
                                           current_host=store.profiles[expected_profile].target_host)
        assert intent.verified_contract
        assert intent.profile_id == expected_profile
        assert intent.executable == str(lutris)
        assert intent.argv == (str(lutris), 'lutris:rungameid/123')


def test_heroic_sideload_profile_uses_explicit_runner_and_app_identity(tmp_path, monkeypatch):
    heroic = _executable(tmp_path / 'bin' / 'heroic')
    monkeypatch.setattr('native_services.launch_profiles.shutil.which', lambda name: str(heroic) if name == 'heroic' else None)
    store = LaunchProfileStore(tmp_path / 'profiles')
    store.seed_reviewed_recipes()
    profile = store.profiles['native-heroic-sideload']
    assert profile.executable == str(heroic)
    intent = store.build_launch_intent('sid-abc', 'heroic-sideload', target_type='native_game',
                                       current_host=profile.target_host)
    assert intent.verified_contract
    assert intent.argv == (str(heroic), 'heroic://launch?appName=sid-abc&runner=sideload')


def test_lutris_battlenet_profile_rejects_product_codes_not_native_game_row_ids(tmp_path):
    store = LaunchProfileStore(tmp_path / 'profiles')
    store.seed_reviewed_recipes()
    intent = store.build_launch_intent('Pro', 'battle.net', target_type='native_game',
                                       current_host=store.profiles['native-lutris-battlenet-game'].target_host)
    assert not intent.verified_contract
    assert 'numeric Lutris game row ID' in intent.error
    assert not intent.argv
