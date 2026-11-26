"""Native sidebar route must replace Library, not create a window or deploy."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from unittest.mock import patch
from PySide6.QtCore import QObject, QMetaObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session
from test_game_install_controller import close_session
from test_platform_social_view import app
from native_services.mod_management import ModManagerService


def test_sidebar_mods_is_lazy_same_workspace_and_back_returns_to_library(tmp_path):
    application = app()
    config = tmp_path/'mods/config.json'
    service = ModManagerService(config)
    with patch.object(service, 'load', wraps=service.load) as loads, \
         patch.object(service, 'apply_r2modman_bepinex_deployment', side_effect=AssertionError('No implicit apply')):
        engine, model, root, warnings = create_session(
            application, headless=True, mod_config=config, mod_service=service)
        try:
            assert loads.call_count == 0
            loader = root.findChild(QObject, 'modWorkspaceLoader')
            assert loader is not None and loader.property('item') is None
            windows_before = len(QGuiApplication.allWindows())
            rail = root.findChild(QObject, 'railMods')
            assert rail is not None
            QMetaObject.invokeMethod(rail, 'clicked', Qt.DirectConnection)
            application.processEvents()
            assert root.property('activePage') == 'mods'
            assert loader.property('item') is not None
            assert loads.call_count == 1
            assert len(QGuiApplication.allWindows()) == windows_before
            assert not config.exists()
            choice = root.findChild(QObject, 'modLibraryGameChoice')
            assert choice is not None and choice.property('count') > 0
            choice.setProperty('currentIndex', 0)
            choice.activated.emit(0)
            assert root.findChild(QObject, 'importGameIdField').property('text') == model.games[0]['id']
            root.resize(1280, 800)
            root.show()
            QTest.qWait(80)
            image = root.grabWindow()
            if not image.isNull():
                fixture=tmp_path/'mods-workspace-fixture.png'
                assert image.save(str(fixture))
                assert fixture.stat().st_size > 0
            QMetaObject.invokeMethod(root, 'goBack', Qt.DirectConnection)
            application.processEvents()
            assert root.property('activePage') == 'library'
            assert not [w for w in warnings if any(v in w for v in (
                'TypeError', 'ReferenceError', 'Cannot assign', 'Binding loop'))]
        finally:
            close_session(application, engine, root)
