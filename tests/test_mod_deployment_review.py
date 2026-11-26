"""Independent destructive-edge fixtures. Never points at a real installation."""
import importlib.util
import os
from pathlib import Path
import sys
import pytest

# Review harnesses point this at a staged artifact; by default test this checkout.
artifact = Path(os.environ.get('WAYSTONE_MOD_ARTIFACT') or Path(__file__).resolve().parents[1])
spec = importlib.util.spec_from_file_location('waystone_mod_review', artifact/'native_services/mod_management.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def fixture(tmp_path):
    source, target = tmp_path/'profile', tmp_path/'game'
    (source/'BepInEx/core').mkdir(parents=True)
    target.mkdir()
    (source/'BepInEx/core/BepInEx.Preloader.dll').write_bytes(b'fixture-preloader')
    (source/'winhttp.dll').write_bytes(b'fixture-doorstop')
    (source/'mods.yml').write_text('[]')
    service = module.ModManagerService(tmp_path/'state/mods.json')
    service.profiles['fixture'] = module.ModProfile(
        profile_id='fixture', display_name='Fixture', manager_type='r2modman',
        game_id='canonical-fixture', game_title='Fixture',
        install_directory=str(target), profile_directory=str(source),
        target_binding_key='632360', variant_key='steam:632360')
    return service, source, target


def preview(service, target):
    return service.preview_r2modman_bepinex_deployment(
        'fixture', str(target), expected_provider='steam',
        expected_target_binding_key='632360', expected_variant_key='steam:632360')


def test_unmanaged_conflicting_loader_is_not_overwritten(tmp_path):
    service, source, target = fixture(tmp_path)
    original = target/'winhttp.dll'
    original.write_bytes(b'user-owned-unmanaged-loader')
    plan = preview(service, target)
    assert not plan['can_apply'] and plan['conflicts']
    with pytest.raises(module.ModDeploymentError):
        service.apply_r2modman_bepinex_deployment(plan)
    assert original.read_bytes() == b'user-owned-unmanaged-loader'


@pytest.mark.parametrize('replace_with_directory', [False, True])
def test_undo_refuses_to_delete_changed_user_content(tmp_path, replace_with_directory):
    service, source, target = fixture(tmp_path)
    applied = service.apply_r2modman_bepinex_deployment(preview(service, target))
    path = target/'winhttp.dll'
    if replace_with_directory:
        path.unlink()  # fixture-owned file only
        path.mkdir()
        (path/'new-user-file').write_bytes(b'keep me')
    else:
        path.write_bytes(b'user-edited-after-deploy')
    untouched = target/'BepInEx/core/BepInEx.Preloader.dll'
    before = untouched.read_bytes()
    with pytest.raises(module.ModDeploymentError):
        service.undo_r2modman_bepinex_deployment(applied['operation_id'])
    assert untouched.read_bytes() == before  # preflight all, no partial undo
    assert (path/'new-user-file').read_bytes() == b'keep me' if replace_with_directory else path.read_bytes() == b'user-edited-after-deploy'


def test_post_replace_verification_error_does_not_leave_unjournaled_payload(tmp_path, monkeypatch):
    service, source, target = fixture(tmp_path)
    plan = preview(service, target)
    original_hash = service._file_sha256
    failed = [False]
    def fail_after_replace(path):
        path = Path(path)
        if path.is_relative_to(target) and path.is_file() and not failed[0]:
            failed[0] = True
            raise OSError('fixture post-replace failure')
        return original_hash(path)
    monkeypatch.setattr(service, '_file_sha256', fail_after_replace)
    with pytest.raises((OSError, module.ModDeploymentError)):
        service.apply_r2modman_bepinex_deployment(plan)
    assert failed[0]
    assert not list(target.rglob('*.dll'))


def test_caller_cannot_substitute_unreviewed_source_path(tmp_path):
    service, source, target = fixture(tmp_path)
    plan = preview(service, target)
    other = tmp_path/'unselected.dll'
    other.write_bytes(b'not-the-selected-profile')
    plan['actions'][0]['src'] = str(other)
    with pytest.raises(module.ModDeploymentError):
        service.apply_r2modman_bepinex_deployment(plan)
    assert not list(target.rglob('*.dll'))


def test_recovery_of_intent_never_deletes_later_user_edits(tmp_path):
    import json
    service, source, target = fixture(tmp_path)
    applied = service.apply_r2modman_bepinex_deployment(preview(service, target))
    journal_path = service._journal_path(applied['operation_id'])
    journal = json.loads(journal_path.read_text())
    # Reproduce the durable state after replace but before recording post-hash.
    record = next(r for r in journal['applied'] if r['relpath'] == 'winhttp.dll')
    record['status'] = 'intent'
    record.pop('dest_sha256_after', None)
    journal['status'] = 'applying'
    journal_path.write_text(json.dumps(journal))
    (target/'winhttp.dll').write_bytes(b'new user edit after crash')
    with pytest.raises(module.ModDeploymentError):
        service.undo_r2modman_bepinex_deployment(applied['operation_id'])
    assert (target/'winhttp.dll').read_bytes() == b'new user edit after crash'


def test_missing_preloader_cannot_be_applied_as_supported_runtime(tmp_path):
    service, source, target = fixture(tmp_path)
    (source/'BepInEx/core/BepInEx.Preloader.dll').unlink()
    plan = preview(service, target)
    assert not plan['can_apply'] and plan['conflicts']
    with pytest.raises(module.ModDeploymentError):
        service.apply_r2modman_bepinex_deployment(plan)
    assert not list(target.rglob('*.dll'))


def test_selected_profile_cannot_deploy_a_symlink_from_outside_it(tmp_path):
    service, source, target = fixture(tmp_path)
    outside = tmp_path/'unselected-private-file'
    outside.write_bytes(b'private fixture bytes must not be copied')
    plugin = source/'BepInEx/plugins/escaped.dll'
    plugin.parent.mkdir()
    plugin.symlink_to(outside)
    try:
        plan = preview(service, target)
    except module.ModManagementError:
        return
    assert not plan['can_apply'] and plan['conflicts']
    with pytest.raises(module.ModManagementError):
        service.apply_r2modman_bepinex_deployment(plan)
    assert not list(target.rglob('*.dll'))
