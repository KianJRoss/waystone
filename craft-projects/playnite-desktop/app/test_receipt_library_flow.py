"""Real session/model/download completion; synthetic transfers, no live clients."""
from pathlib import Path
from unittest.mock import patch
import json
import sys
from app import create_session
from test_game_install_controller import close_session
from test_xodus_install_view import app, setup, wait_until
from test_xodus_install import PRODUCT
from native_services.install_receipts import InstallReceiptRegistry


GAMES = [{'id': 'work', 'title': 'Same title', 'variants': [{
    'source': 'Xbox', 'source_id': 'source-guid', 'provider_game_id': PRODUCT,
    'platform': 'Windows', 'status': 'not-installed', 'recipe': [],
    'installation_id': 'fixture-import'}]},
    {'id': 'different-product', 'title': 'Same title', 'variants': [{
    'source': 'Xbox', 'source_id': 'other-guid', 'provider_game_id': '9DIFFERENT12',
    'platform': 'Windows', 'status': 'not-installed', 'recipe': [],
    'installation_id': 'fixture-other'}]}]


def session(application, registry):
    with patch('library_model.load_fixture', return_value=GAMES):
        return create_session(application, headless=True, receipt_registry=registry)


def test_actual_install_completion_updates_existing_model_and_restart(tmp_path):
    application = app()
    registry = InstallReceiptRegistry(tmp_path/'state/receipts.json')
    assert registry.snapshot() == [] and not registry.path.parent.exists()
    fixture_view, calls = setup(tmp_path)
    engine, model, root, warnings = session(application, registry)
    try:
        view = engine.provider_jobs
        root.setProperty('status', 'installed')
        model.setFilters('', 'All sources', 'All platforms', 'installed')
        assert model.rowCount() == 0
        view.configureXodus(sys.executable, installer=fixture_view._xodus)
        view._root = str(tmp_path)
        assert engine.game_installs.install('work')
        assert wait_until(lambda: not view.busy and model.rowCount() == 1, 3000)
        assert len(calls) == 2 and root.property('activePage') == 'library'
        assert model.visible[0]['id'] == 'work'  # Same-title other product stays out.
        detail = model._effective_variants(model.visible[0])[0]
        assert detail['status'] == 'installed'
        assert 'Verified provider files' in detail['installation_basis']
        receipt = view.xboxReceipt
        assert receipt['state'] == 'files_verified'
        assert registry.snapshot()[0]['is_present']
        view._on_result({'generation': view._generation, 'progress': {
            'phase': 'completed', 'receipt': receipt}})
        assert len(registry.snapshot()[0]['history']) == 1  # no duplicate callback replay
        assert not engine.connection_view.receipt_for_variant('work', {'source': 'Xbox', 'provider_game_id': []})
    finally:
        close_session(application, engine, root)
        fixture_view.shutdown()

    fresh = InstallReceiptRegistry(registry.path)
    engine, model, root, warnings = session(application, fresh)
    try:
        root.setProperty('status', 'installed')
        model.setFilters('', 'All sources', 'All platforms', 'installed')
        assert model.rowCount() == 1 and engine.provider_jobs._worker is None
        Path(receipt['executable']).unlink()  # Test-owned synthetic payload only.
        engine.refresh_receipts()
        assert model.rowCount() == 0
        assert fresh.snapshot() and not fresh.snapshot()[0]['is_present']
        assert not [w for w in warnings if any(v in w for v in ('TypeError', 'ReferenceError', 'Binding loop'))]
    finally:
        close_session(application, engine, root)


def test_completed_receipt_must_match_the_dispatched_product(tmp_path):
    application = app()
    registry = InstallReceiptRegistry(tmp_path/'state/receipts.json')
    engine, model, root, warnings = session(application, registry)
    try:
        view = engine.provider_jobs
        view._target = ('xbox', PRODUCT)
        view._on_result({'generation': view._generation, 'progress': {
            'phase': 'completed', 'receipt': {'provider': 'xbox',
            'product_id': '9DIFFERENT12', 'state': 'files_verified'}}})
        assert not registry.path.exists()
        assert not engine.connection_view.receipt_for_variant('work', GAMES[0]['variants'][0])
    finally:
        close_session(application, engine, root)


def test_corrupt_persisted_receipt_identity_is_not_presence_or_a_query_crash(tmp_path):
    registry = InstallReceiptRegistry(tmp_path/'receipts.json')
    registry.path.write_text(json.dumps({'schema': 1, 'records': {
        'malformed': {'provider': [], 'product_id': {}, 'state': 'files_verified'}}}))
    assert registry.snapshot() == []
    assert registry.query('work', [{'game_id': 'work', 'provider': 'xbox', 'product_id': PRODUCT}]) == []


def test_non_xbox_exit_zero_is_not_invented_installation_proof(tmp_path):
    application = app()
    registry = InstallReceiptRegistry(tmp_path/'state/receipts.json')
    engine, model, root, warnings = session(application, registry)
    try:
        for provider in ('gog', 'amazon', 'epic'):
            view = engine.provider_jobs
            view._target = (provider, '123')
            view._on_result({'generation': view._generation, 'progress': {
                'phase': 'completed', 'exit_code': 0}})
        assert not registry.path.exists()
        assert engine.connection_view._receipt_present == set()
    finally:
        close_session(application, engine, root)


def test_receipt_save_failure_is_visible_without_private_exception_text(tmp_path):
    application = app()
    registry = InstallReceiptRegistry(tmp_path/'state/receipts.json')
    engine, model, root, warnings = session(application, registry)
    try:
        view = engine.provider_jobs
        view._target = ('xbox', PRODUCT)
        with patch.object(registry, 'register_xodus_verified', side_effect=OSError('fixture-private-error')):
            view._on_result({'generation': view._generation, 'progress': {
                'phase': 'completed', 'message': 'Files downloaded.',
                'receipt': {'provider': 'xbox', 'product_id': PRODUCT, 'state': 'files_verified'}}})
        assert 'could not be saved' in view.state['message']
        assert 'fixture-private-error' not in str(view.state)
        assert not registry.path.exists()
    finally:
        close_session(application, engine, root)
