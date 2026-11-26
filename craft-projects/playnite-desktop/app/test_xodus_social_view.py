"""Qt integration tests for XodusSocialView."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QSignalSpy, QTest

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from xodus_social_view import XodusSocialView, VOICE_PENDING_STATUS
from native_services.xodus_social import SocialDisconnectedError


class AsyncMockSocialTransport:
    """Mock transport feeding exact representative frames for testing XodusSocialView."""

    def __init__(self) -> None:
        self.is_open = False
        self.written_lines: list[str] = []
        self._read_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def open(self) -> None:
        self.is_open = True

    async def write_line(self, data: str) -> None:
        if not self.is_open:
            raise SocialDisconnectedError("Mock closed")
        self.written_lines.append(data)
        # Automatically handle expected requests
        try:
            req = json.loads(data.strip())
            req_id = req["id"]
            args = req.get("args", [])
            inp = json.loads(req["input"]) if req.get("input") else {}

            if args == ["friends"]:
                await self.push_response({
                    "id": req_id,
                    "ok": True,
                    "result": {
                        "schema": 1,
                        "kind": "friends",
                        "items": [
                            {
                                "xuid": "2533274900000001",
                                "gamertag": "Chief",
                                "online": True,
                                "presence": "Halo",
                                "currentGame": "Halo Infinite",
                                "favorite": True,
                                "inParty": False,
                                "avatarUrl": "https://pic.png",
                            },
                            {
                                "xuid": "2533274900000002",
                                "gamertag": "Cortana",
                                "online": False,
                                "presence": "Offline",
                                "favorite": False,
                                "inParty": False,
                                "avatarUrl": None,
                            },
                        ],
                    },
                })
            elif args == ["party"]:
                op=inp.get('operation')
                if op=='active': data={'ids':['party-abc-123']}
                elif op=='get': data={'id':inp.get('partyId','party-abc-123'),
                    'members':[{'xuid':'2533274900000001','role':'leader'}], 'transportResponse':None}
                elif op=='voice-policy': data={'isAllowed':True}
                else: data={'success':True}
                await self.push_response({'id':req_id,'ok':True,'result':{
                    'schema':1,'kind':'party','account':inp.get('account'),
                    'operation':op,'result':data}})

            elif args == ["send-message"]:
                await self.push_response({
                    "id": req_id,
                    "ok": True,
                    "result": {"items": [{"id": "msg-sent-1"}]},
                })
        except Exception:
            pass

    async def read_line(self) -> str | None:
        if not self.is_open:
            return None
        return await self._read_queue.get()

    async def close(self) -> None:
        self.is_open = False
        await self._read_queue.put(None)

    async def push_response(self, frame: dict) -> None:
        await self._read_queue.put(json.dumps(frame) + "\n")


def wait_until(predicate, timeout_ms: int = 2000) -> bool:
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        QTest.qWait(25)
        elapsed += 25
    return predicate()


class XodusSocialViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_initial_state_no_helper_started(self):
        transport = AsyncMockSocialTransport()
        view = XodusSocialView(transport=transport, account="test@xbox.com")
        self.assertFalse(view.connected)
        self.assertFalse(view.connecting)
        self.assertEqual(view.account, "test@xbox.com")
        self.assertEqual(view.friends, [])
        self.assertEqual(view.activeParties, [])
        self.assertEqual(view.partyState["phase"], "idle")
        self.assertEqual(view.voiceStatusText, VOICE_PENDING_STATUS)
        self.assertFalse(transport.is_open)
        view.close()

    def test_connect_service_populates_friends_and_parties(self):
        transport = AsyncMockSocialTransport()
        view = XodusSocialView(transport=transport, account="test@xbox.com")
        self.addCleanup(view.close)

        view.connectService()
        self.assertTrue(wait_until(lambda: view.connected, 2000))
        self.assertFalse(view.connecting)

        # Verify friends loaded
        self.assertEqual(len(view.friends), 2)
        f1 = view.friends[0]
        self.assertEqual(f1["gamertag"], "Chief")
        self.assertTrue(f1["online"])
        self.assertTrue(f1["favorite"])
        self.assertEqual(f1["currentGame"], "Halo Infinite")

        # Verify parties loaded
        self.assertEqual(view.activeParties, ["party-abc-123"])

    def test_party_operations(self):
        transport = AsyncMockSocialTransport()
        view = XodusSocialView(transport=transport, account="test@xbox.com")
        self.addCleanup(view.close)

        view.connectService()
        self.assertTrue(wait_until(lambda: view.connected, 2000))

        # Refresh party to populate members
        view.refreshParty()
        self.assertTrue(wait_until(lambda: view.partyState["phase"] == "connected", 2000))
        self.assertEqual(view.partyState["partyId"], "party-abc-123")
        self.assertEqual(len(view.partyState["members"]), 1)

        # Invite to party
        spy_op = QSignalSpy(view.operationCompleted)
        view.inviteToParty("party-abc-123", "2533274900000002")
        self.assertTrue(wait_until(lambda: spy_op.count() > 0, 2000))

        # Leave party
        view.leaveParty("party-abc-123")
        self.assertTrue(wait_until(lambda: view.partyState["phase"] == "idle", 2000))
        self.assertEqual(view.partyState["partyId"], "")

    def test_send_message_flow(self):
        transport = AsyncMockSocialTransport()
        view = XodusSocialView(transport=transport, account="test@xbox.com")
        self.addCleanup(view.close)

        view.connectService()
        self.assertTrue(wait_until(lambda: view.connected, 2000))

        spy_op = QSignalSpy(view.operationCompleted)
        view.sendMessage("2533274900000001", "Hello Chief")
        self.assertTrue(wait_until(lambda: spy_op.count() > 0, 2000))

    def test_missing_binary_fails_fast_without_worker(self):
        # When binary_path is None and no transport is provided
        view = XodusSocialView()
        self.addCleanup(view.close)
        spy_err = QSignalSpy(view.errorChanged)

        view.connectService()
        self.assertFalse(view.connected)
        self.assertFalse(view.connecting)
        self.assertIsNone(view._worker)
        self.assertEqual(view.error, "Xodus binary path is not configured.")

        # When binary_path does not exist
        view2 = XodusSocialView(binary_path="/nonexistent/xodus-cli")
        self.addCleanup(view2.close)
        view2.connectService()
        self.assertFalse(view2.connected)
        self.assertIsNone(view2._worker)
        self.assertIn("not found", view2.error)

    def test_close_while_in_flight(self):
        transport = AsyncMockSocialTransport()
        view = XodusSocialView(transport=transport, account="test@xbox.com")
        self.addCleanup(view.close)

        view.connectService()
        self.assertTrue(wait_until(lambda: view.connected, 2000))

        # Schedule an in-flight operation
        view.refreshFriends()
        self.assertTrue(view.refreshing)

        # Abruptly close view while operation is in-flight
        view.close()
        self.assertFalse(view.connected)
        self.assertFalse(view.refreshing)
        self.assertIsNone(view._worker)

    def test_error_sanitization(self):
        # Verify that error messages containing tokens are scrubbed
        sanitized = XodusSocialView._sanitize_error(ValueError("Failed with authKey=eyJhbGciOi..."))
        self.assertNotIn("authKey", sanitized)
        self.assertNotIn("eyJhbGciOi", sanitized)
        self.assertEqual(sanitized, "Xbox social communication error occurred.")

        sanitized_bearer = XodusSocialView._sanitize_error(RuntimeError("Bearer secret123 expired"))
        self.assertNotIn("Bearer", sanitized_bearer)
        self.assertNotIn("secret123", sanitized_bearer)

        # Unrecognized error message defaults to generic safe message
        sanitized_unknown = XodusSocialView._sanitize_error(RuntimeError("Arbitrary private trace or internal path /var/log/secret"))
        self.assertEqual(sanitized_unknown, "Xbox social service operation failed.")
        self.assertNotIn("/var/log/secret", sanitized_unknown)

        # Safe known prefix passes through
        safe = XodusSocialView._sanitize_error(ValueError("Xbox did not authorize this action."))
        self.assertEqual(safe, "Xbox did not authorize this action.")


if __name__ == "__main__":
    unittest.main()
