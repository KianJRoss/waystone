"""Offscreen Qt Quick integration tests for SocialDashboard.qml."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QMetaObject, Qt, QCoreApplication, QUrl
from PySide6.QtCore import Property, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtTest import QTest

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from xodus_social_view import XodusSocialView, VOICE_PENDING_STATUS
from test_xodus_social_view import AsyncMockSocialTransport, wait_until


class SocialDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(["social-dashboard-tests"])

    def test_dashboard_ui_full_flow(self):
        transport = AsyncMockSocialTransport()
        view = XodusSocialView(transport=transport, account="gamer@xbox.com")
        self.addCleanup(view.shutdown)

        # 1. Verify initial view properties
        self.assertFalse(view.connected)
        self.assertEqual(view.voiceStatusText, VOICE_PENDING_STATUS)

        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("xodusSocialView", view)
        class FakeDiscord(QObject):
            def __init__(self):
                super().__init__()
                self.calls = 0
            @Property(bool)
            def hasClient(self): return True
            @Slot(result=bool)
            def openDiscord(self):
                self.calls += 1
                return True
        discord = FakeDiscord()
        engine.rootContext().setContextProperty("discordService", discord)
        engine.addImportPath(str(APP_ROOT))

        window = QQuickWindow()
        window.resize(1024, 768)
        component = QQmlComponent(engine)
        component.setData(b"""
import QtQuick
import QtQuick.Controls

Item {
    width: 1024; height: 768
    SocialDashboard {
        id: dash
    }
}
""", QUrl.fromLocalFile(str(APP_ROOT / "test_social_host.qml")))

        root_item = component.create()
        self.assertIsNotNone(root_item, f"Failed to instantiate dashboard host: {component.errors()}")
        root_item.setParentItem(window.contentItem())

        dialog = root_item.findChild(QObject, "socialDashboard")
        self.assertIsNotNone(dialog)
        dialog.open()
        QTest.qWait(50)
        self.assertTrue(dialog.property("visible"))

        # 2. Check initial UI text states
        status_text = dialog.findChild(QObject, "connectionStatusText")
        self.assertIsNotNone(status_text)
        self.assertEqual(status_text.property("text"), "Disconnected")
        open_discord = dialog.findChild(QObject, "openDiscordButton")
        self.assertIsNotNone(open_discord)
        self.assertTrue(open_discord.property("enabled"))
        QMetaObject.invokeMethod(open_discord, "clicked")
        self.assertEqual(discord.calls, 1)

        voice_text = dialog.findChild(QObject, "voiceStatusText")
        self.assertIsNotNone(voice_text)
        self.assertEqual(voice_text.property("text"), VOICE_PENDING_STATUS)

        party_text = dialog.findChild(QObject, "partyPhaseText")
        self.assertIsNotNone(party_text)
        self.assertIn("IDLE", party_text.property("text"))

        # 3. Click Connect button
        connect_btn = dialog.findChild(QObject, "connectButton")
        self.assertIsNotNone(connect_btn)
        QMetaObject.invokeMethod(connect_btn, "clicked")

        self.assertTrue(wait_until(lambda: view.connected, 2000))
        QTest.qWait(50)
        self.assertEqual(status_text.property("text"), "Connected to Xbox Live")
        self.assertEqual(dialog.findChild(QObject, "socialAccountName").property("text"), "gamer@xbox.com")
        self.assertEqual(connect_btn.property("text"), "Disconnect")

        # 4. Check friends list populated
        friends_list = dialog.findChild(QObject, "friendsListView")
        self.assertIsNotNone(friends_list)
        self.assertEqual(friends_list.property("count"), 2)

        # 5. Refresh party and verify leave party button interaction
        view.refreshParty()
        self.assertTrue(wait_until(lambda: view.partyState["phase"] == "connected", 2000))
        QTest.qWait(50)
        self.assertIn("CONNECTED", party_text.property("text"))
        self.assertEqual("party-abc-123", party_text.property("partyId"))

        leave_btn = dialog.findChild(QObject, "leavePartyButton")
        self.assertIsNotNone(leave_btn)
        self.assertTrue(leave_btn.property("enabled"))

        # Click Leave Party
        QMetaObject.invokeMethod(leave_btn, "clicked")
        self.assertTrue(wait_until(lambda: view.partyState["phase"] == "idle", 2000))
        QTest.qWait(50)
        self.assertIn("IDLE", party_text.property("text"))

        # 6. Click Disconnect button
        QMetaObject.invokeMethod(connect_btn, "clicked")
        self.assertTrue(wait_until(lambda: not view.connected, 2000))
        QTest.qWait(50)
        self.assertEqual(status_text.property("text"), "Disconnected")
        self.assertEqual(connect_btn.property("text"), "Connect Xbox")

        dialog.close()
        QTest.qWait(50)


if __name__ == "__main__":
    unittest.main()
