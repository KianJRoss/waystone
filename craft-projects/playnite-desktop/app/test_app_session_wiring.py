"""Real create_session wiring, not a second mocked reimplementation of the app."""
from unittest.mock import patch
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from app import create_session
from platform_social_view import PlatformSocialView
from xodus_social_view import XodusSocialView
from test_platform_social_view import app
from test_steam_session_persistence import Store, Qr, Native, OWNER, settle


def close(application, engine, root):
    application.aboutToQuit.emit()
    root.close()
    root.deleteLater()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


@pytest.mark.parametrize('headless,injected', [(True, False), (True, True), (False, False)])
def test_real_session_shared_store_and_restore_hooks(headless, injected):
    application = app()
    store = Store()
    with patch('native_services.provider_sessions.ProviderSessionStore', return_value=store) as factory, \
         patch.object(PlatformSocialView, 'restoreSession', autospec=True) as steam_restore, \
         patch.object(XodusSocialView, 'restoreSession', autospec=True) as xbox_restore:
        engine, model, root, warnings = create_session(
            application, headless=headless, provider_jobs_enabled=False,
            session_store=store if injected else None)
        try:
            expected = injected or not headless
            assert factory.call_count == int(not headless and not injected)
            assert steam_restore.call_count == xbox_restore.call_count == int(expected)
            assert engine.platform_social._session_store is (store if expected else None)
            assert engine.social_view._session_store is (store if expected else None)
            assert store.calls == []  # hooks spied, never real keyring or helper auth
        finally:
            close(application, engine, root)


def test_successful_restore_reaches_authenticated_account_and_keeps_session_on_shutdown():
    application = app()
    store, qr = Store(), Qr()
    store.rows['steam'] = {'kind': 'steam_refresh', 'account_id': OWNER,
                           'refresh_token': 'fixture-refresh'}
    view = PlatformSocialView(native=Native(), session_store=store, qr_session=qr)
    try:
        view.restoreSession()
        settle(view)
        assert qr.restores == 1 and qr.begins == 0
        assert view.steamAccount == OWNER
        assert view.steamConnected and view.achievementAccess
        assert store.rows['steam']['refresh_token'] == 'fixture-rotated-refresh'
    finally:
        view.shutdown()
    assert ('clear', 'steam') not in store.calls


def test_actual_qml_displays_keyring_failure_status():
    application = app()
    engine, model, root, warnings = create_session(application, headless=True,
                                                   provider_jobs_enabled=False)
    try:
        engine.social_view._on_session_status('Saved Xbox sign-in could not be removed.')
        engine.platform_social._session_status = 'Could not save Steam sign-in.'
        engine.platform_social.changed.emit()
        application.processEvents()
        xbox = root.findChild(QObject, 'xboxSessionStatus')
        steam = root.findChild(QObject, 'steamSessionStatus')
        assert xbox is not None and 'could not be removed' in xbox.property('text')
        assert steam is not None and steam.property('text') == 'Could not save Steam sign-in.'
        assert not [w for w in warnings if any(s in w for s in
                    ('ReferenceError', 'TypeError', 'Binding loop', 'Cannot assign'))]
    finally:
        close(application, engine, root)


def test_shutdown_joins_active_job_without_shorter_arbitrary_deadline():
    app()
    view = PlatformSocialView(native=Native())
    view._start('fixture', lambda: None)
    job = view._job
    with patch.object(job, 'wait', wraps=job.wait) as join:
        view.shutdown()
        join.assert_called_once_with()
        assert job.isFinished() and not job.isRunning()
