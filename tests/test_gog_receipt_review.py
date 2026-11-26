"""Independent producer-shaped checks; fixtures only, no gogdl subprocess/API."""
import importlib.util
import os
from pathlib import Path
import sys

from native_services import provider_receipt_gog as module
PRODUCT = '1424375862'


def fixture(tmp_path):
    root = tmp_path/'selected-game'
    (root/'bin').mkdir(parents=True)
    (root/'bin/game.exe').write_bytes(b'fixture exe')
    return root


def verify(metadata, root, **kwargs):
    return module.verify_gog_install(metadata, selected_product_id=PRODUCT,
                                     selected_destination=str(root), **kwargs)


def installed(root):
    # Heroic InstalledInfo, not the separate game-local goggame-ID.info.
    return {'appName': PRODUCT, 'install_path': str(root),
            'executable': str(root/'bin/game.exe'), 'is_dlc': False,
            'platform': 'windows', 'title': 'Original store name'}


def test_gogdl_resume_is_not_verified_even_with_present_executable(tmp_path):
    root = fixture(tmp_path)
    (root/'.gogdl-resume').write_text('fixture unfinished download')
    receipt, reason, detail = verify(installed(root), root)
    assert receipt is None and 'incomplete' in reason.lower()


def test_game_local_info_requires_no_heroic_installed_store_and_uses_direct_engine_paths(tmp_path):
    root = fixture(tmp_path)
    # Producer: Heroic types/gog.ts GOGGameDotInfoFile; engine launch.py lines78-103.
    metadata = {'version': 1, 'gameId': PRODUCT, 'rootGameId': PRODUCT,
                'standalone': True, 'name': 'Original store name', 'playTasks': [{
                    'type': 'FileTask', 'isPrimary': True, 'path': 'bin\\game.exe',
                    'workingDir': 'bin'}]}
    receipt, reason, detail = verify(metadata, root)
    assert receipt is not None, (reason, detail)
    assert Path(receipt['executable']) == root/'bin/game.exe'
    assert receipt['product_id'] == PRODUCT


def test_url_task_is_not_executable_proof_even_if_it_carries_a_path(tmp_path):
    root = fixture(tmp_path)
    metadata = installed(root)
    metadata.pop('executable')
    metadata['playTasks'] = [{'type': 'URLTask', 'isPrimary': True,
                             'path': 'bin/game.exe', 'link': 'https://example.test'}]
    assert verify(metadata, root)[0] is None


def test_task_working_directory_escape_is_refused_without_execution(tmp_path):
    root = fixture(tmp_path)
    metadata = installed(root)
    metadata.pop('executable')
    metadata['playTasks'] = [{'type': 'FileTask', 'isPrimary': True,
                             'path': 'bin/game.exe', 'workingDir': '../outside'}]
    assert verify(metadata, root)[0] is None


def test_user_rename_does_not_override_exact_product_and_file_identity(tmp_path):
    root = fixture(tmp_path)
    receipt, reason, detail = verify(installed(root), root, expected_title='My renamed game')
    assert receipt is not None, (reason, detail)
