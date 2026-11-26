"""Offline Qt Quick regression tests for Connections -> Steam & Accounts UX."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app import create_session


class ConnectionsSteamAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(["connections-steam-access-tests"])

    def _create_real_session(self):
        engine, model, root, warnings = create_session(
            self.app,
            headless=True,
            provider_jobs_enabled=False,
        )

        def _cleanup():
            self.app.aboutToQuit.emit()
            root.close()
            root.deleteLater()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()

        self.addCleanup(_cleanup)
        return engine, model, root, warnings

    def test_connections_overview_manage_accounts_routes_to_platforms(self):
        _engine, _model, root, _warnings = self._create_real_session()
        rail_btn = root.findChild(QObject, "settingsLinuxConnections")
        self.assertIsNotNone(rail_btn)
        QMetaObject.invokeMethod(rail_btn, "clicked")
        QTest.qWait(50)

        overview = root.findChild(QObject, "menuOverviewDashboard")
        self.assertIsNotNone(overview)
        self.assertTrue(overview.property("visible"))
        self.assertEqual(overview.property("section"), "connections")

        primary_btn = overview.findChild(QObject, "overviewPrimaryAction")
        count_text = overview.findChild(QObject, "overviewGroupCount")
        manage_btn = overview.findChild(QObject, "overviewManageAccounts")
        self.assertIsNotNone(primary_btn)
        self.assertIsNotNone(count_text)
        self.assertIsNotNone(manage_btn)
        self.assertEqual(primary_btn.property("text"), "Refresh installed inventory")
        self.assertTrue(primary_btn.property("visible"))
        self.assertTrue(count_text.property("visible"))
        self.assertEqual(manage_btn.property("text"), "Manage accounts")
        self.assertTrue(manage_btn.property("visible"))

        QMetaObject.invokeMethod(manage_btn, "clicked")
        QTest.qWait(50)

        self._assert_settings_accounts_open(root)

    def _assert_settings_accounts_open(self, root):
        # Store and platform sign-ins have one home: Settings > Accounts.
        settings = root.findChild(QObject, "settingsDashboard")
        self.assertIsNotNone(settings)
        self.assertTrue(settings.property("visible"))
        self.assertEqual(settings.property("selectedCategory"), 0)
        self.assertIsNotNone(settings.findChild(QObject, "steamQrSignIn"))
        return settings

    def test_social_panel_account_connections_routes_to_settings_accounts(self):
        _engine, _model, root, _warnings = self._create_real_session()
        rail_social = root.findChild(QObject, "railSocial")
        self.assertIsNotNone(rail_social)
        QMetaObject.invokeMethod(rail_social, "clicked")
        QTest.qWait(50)

        social_dash = root.findChild(QObject, "socialDashboard")
        social_panel = root.findChild(QObject, "socialPanel")
        self.assertTrue(social_dash.property("visible"))
        self.assertEqual(social_panel.property("selectedTab"), 0)

        acct_btn = social_panel.findChild(QObject, "accountConnectionsButton")
        self.assertIsNotNone(acct_btn)
        self.assertEqual(acct_btn.property("text"), "Account connections")
        self.assertTrue(acct_btn.property("visible"))

        QMetaObject.invokeMethod(acct_btn, "clicked")
        QTest.qWait(50)

        self._assert_settings_accounts_open(root)

    def test_platform_connections_places_steam_before_store_accounts(self):
        _engine, _model, root, _warnings = self._create_real_session()
        QMetaObject.invokeMethod(root, "openFriendsPlatforms")
        QTest.qWait(80)

        settings = self._assert_settings_accounts_open(root)

        gaming_hdr = settings.findChild(QObject, "gamingPlatformsHeader")
        steam_qr = settings.findChild(QObject, "steamQrSignIn")
        steam_native = settings.findChild(QObject, "connectNativeSteam")
        steam_api = settings.findChild(QObject, "connectSteamApi")
        store_panel = settings.findChild(QObject, "storeAccountsPanel")

        for obj in (gaming_hdr, steam_qr, steam_native, steam_api, store_panel):
            self.assertIsNotNone(obj)

        self.assertLess(gaming_hdr.property("y"), store_panel.property("y"))
        self.assertLess(steam_qr.property("y"), store_panel.property("y"))


if __name__ == "__main__":
    unittest.main()
