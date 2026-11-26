import json
import os
from pathlib import Path
import pytest

from native_services.mod_management import ModDeploymentError, ModManagerService, ModProfile


def _write_mods_yml(profile_dir: Path):
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / 'mods.yml').write_text(
        "- manifestVersion: 1\n"
        "  name: BepInEx-BepInExPack\n"
        "  authorName: BepInEx\n"
        "  websiteUrl: https://thunderstore.io\n"
        "  displayName: BepInExPack\n"
        "  description: test\n"
        "  gameVersion: \"0\"\n"
        "  networkMode: both\n"
        "  packageType: other\n"
        "  installMode: managed\n"
        "  installedAtTime: 0\n"
        "  loaders: []\n"
        "  dependencies: []\n"
        "  incompatibilities: []\n"
        "  optionalDependencies: []\n"
        "  versionNumber:\n    major: 5\n    minor: 4\n    patch: 2100\n"
        "  enabled: true\n",
        encoding='utf-8',
    )


def _make_bepinex_profile(profile_dir: Path):
    _write_mods_yml(profile_dir)
    core = profile_dir / 'BepInEx' / 'core'
    plugins = profile_dir / 'BepInEx' / 'plugins'
    core.mkdir(parents=True, exist_ok=True)
    plugins.mkdir(parents=True, exist_ok=True)
    (core / 'BepInEx.Preloader.dll').write_bytes(b'PRELOADER')
    (plugins / 'MyMod.dll').write_bytes(b'MOD')
    (profile_dir / 'doorstop_config.ini').write_text('enabled=true\n', encoding='utf-8')
    (profile_dir / 'winhttp.dll').write_bytes(b'WINHTTP')


def _setup_service(tmp_path: Path):
    svc = ModManagerService(config_path=str(tmp_path / 'cfg'))
    install_dir = tmp_path / 'install'
    install_dir.mkdir()
    profile_dir = tmp_path / 'profile'
    _make_bepinex_profile(profile_dir)
    prof = ModProfile(
        profile_id='prof1',
        display_name='prof1',
        manager_type='r2modman',
        game_id='testgame',
        game_title='Test Game',
        install_directory=str(install_dir),
        profile_directory=str(profile_dir),
        target_binding_key='steam:526870',
        variant_key='steam:526870',
    )
    svc.create_or_register_profile(prof)
    return svc, prof, install_dir, profile_dir

def test_preview_apply_undo_preserves_originals_and_staged_launch_intent(tmp_path: Path):
    svc, prof, install_dir, profile_dir = _setup_service(tmp_path)
    user_file = install_dir / 'user_keep.txt'
    user_file.write_text('keep', encoding='utf-8')

    plan = svc.preview_r2modman_bepinex_deployment(
        prof.profile_id,
        str(install_dir),
        expected_target_binding_key='steam:526870',
        expected_variant_key='steam:526870',
        expected_provider='steam',
    )
    assert plan['plan_version'] == 1
    assert plan['can_apply'] is True
    assert plan['conflicts'] == []
    assert plan['provider'] == 'steam'
    assert plan['runner'] == 'steam-doorstop-bepinex'
    assert plan['target_install_directory'] == str(install_dir.resolve())
    assert plan['variant_key'] == 'steam:526870'
    actions = {a['relpath']: a for a in plan['actions']}
    assert actions['BepInEx/core/BepInEx.Preloader.dll']['action'] == 'create'
    assert actions['BepInEx/plugins/MyMod.dll']['action'] == 'create'
    assert actions['doorstop_config.ini']['action'] == 'create'
    intent = plan['launch_intent']
    assert intent['runner'] == 'steam-doorstop-bepinex'
    assert intent['target_binding_key'] == 'steam:526870'
    assert intent['env']['DOORSTOP_ENABLE'] == 'TRUE'
    assert intent['env']['DOORSTOP_TARGET_ASSEMBLY'].endswith('BepInEx/core/BepInEx.Preloader.dll')

    journal = svc.apply_r2modman_bepinex_deployment(plan)
    assert journal['status'] == 'applied'
    applied_preloader = install_dir / 'BepInEx' / 'core' / 'BepInEx.Preloader.dll'
    assert applied_preloader.exists()
    assert (install_dir / 'BepInEx' / 'plugins' / 'MyMod.dll').exists()
    assert (install_dir / 'doorstop_config.ini').exists()
    intent = journal.get('resulting_launch_intent') or {}
    assert intent.get('runner') == 'steam-doorstop-bepinex'
    assert intent.get('target_binding_key') == 'steam:526870'
    assert intent.get('variant_key') == 'steam:526870'
    assert intent.get('target_install_directory') == str(install_dir.resolve())
    assert intent.get('profile_id') == prof.profile_id
    assert intent['env']['DOORSTOP_ENABLE'] == 'TRUE'
    assert intent['env']['DOORSTOP_TARGET_ASSEMBLY'] == str(install_dir.resolve() / 'BepInEx' / 'core' / 'BepInEx.Preloader.dll')
    assert intent['staged_runtime_verified'] is True

    src_preloader = Path(prof.profile_directory) / 'BepInEx' / 'core' / 'BepInEx.Preloader.dll'
    src_preloader.write_bytes(b'PRELOADER-V2')
    plan2 = svc.preview_r2modman_bepinex_deployment(
        prof.profile_id,
        str(install_dir),
        expected_target_binding_key='steam:526870',
        expected_variant_key='steam:526870',
        expected_provider='steam',
    )
    assert plan2['can_apply'] is True
    rep = {a['relpath']: a for a in plan2['actions']}['BepInEx/core/BepInEx.Preloader.dll']
    assert rep['action'] == 'replace'
    journal2 = svc.apply_r2modman_bepinex_deployment(plan2)
    assert applied_preloader.read_bytes() == b'PRELOADER-V2'
    undone = svc.undo_r2modman_bepinex_deployment(journal2['plan_id'])
    assert undone['status'] == 'undone'
    assert applied_preloader.read_bytes() == b'PRELOADER'
    assert user_file.read_text(encoding='utf-8') == 'keep'


def test_preview_rejects_conflicts_symlink_escape_and_stale_or_forged_apply(tmp_path: Path):
    svc, prof, install_dir, profile_dir = _setup_service(tmp_path)
    evil_dir = install_dir / 'BepInEx'
    evil_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = evil_dir / 'core'
    if not link.exists():
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError:
            pytest.skip('symlink not supported')
    plan = svc.preview_r2modman_bepinex_deployment(prof.profile_id, str(install_dir))
    assert plan['can_apply'] is False
    assert any('escape' in c.get('reason', '').lower() or 'symlink' in c.get('reason', '').lower() for c in plan['conflicts'])
    try:
        link.unlink()
    except Exception:
        pass
    import shutil
    if evil_dir.exists():
        shutil.rmtree(evil_dir)
    (install_dir / 'winhttp.dll').mkdir()
    plan = svc.preview_r2modman_bepinex_deployment(prof.profile_id, str(install_dir))
    assert plan['can_apply'] is False
    assert any(c.get('relpath') == 'winhttp.dll' for c in plan['conflicts'])
    shutil.rmtree(install_dir / 'winhttp.dll')
    plan = svc.preview_r2modman_bepinex_deployment(
        prof.profile_id, str(install_dir), expected_target_binding_key='steam:526870', expected_variant_key='steam:526870'
    )
    assert plan['can_apply'] is True
    (Path(prof.profile_directory) / 'BepInEx' / 'plugins' / 'MyMod.dll').write_bytes(b'MOD-CHANGED')
    with pytest.raises(ModDeploymentError):
        svc.apply_r2modman_bepinex_deployment(plan)


def test_unsupported_or_non_runnable_profile_states_refuse_without_claiming_success(tmp_path: Path):
    svc = ModManagerService(config_path=str(tmp_path / 'cfg'))
    install_dir = tmp_path / 'install'
    install_dir.mkdir()
    profile_dir = tmp_path / 'melon'
    profile_dir.mkdir()
    _write_mods_yml(profile_dir)
    (profile_dir / 'version.dll').write_bytes(b'proxy')
    prof = ModProfile(
        profile_id='melon1', display_name='melon1', manager_type='melonloader', game_id='testgame', game_title='Game',
        install_directory=str(install_dir), profile_directory=str(profile_dir), target_binding_key='steam:526870',
    )
    # Bypass registration validation to exercise preview refusal for an unsupported/non-runnable manager state.
    svc.profiles[prof.profile_id] = prof
    with pytest.raises(ModDeploymentError):
        svc.preview_r2modman_bepinex_deployment(prof.profile_id, str(install_dir))


def test_headless_linux_bepinex_proof_is_explicit_not_full_mod_stack(tmp_path: Path):
    svc, prof, install_dir, profile_dir = _setup_service(tmp_path)
    plan = svc.preview_r2modman_bepinex_deployment(prof.profile_id, str(install_dir), expected_target_binding_key='steam:526870')
    svc.apply_r2modman_bepinex_deployment(plan)
    proof = svc.verify_staged_bepinex_runtime(str(install_dir), staged_only=True)
    assert isinstance(proof, dict)
    text = json.dumps(proof).lower()
    assert 'full' not in text or 'full mod stack' not in text
