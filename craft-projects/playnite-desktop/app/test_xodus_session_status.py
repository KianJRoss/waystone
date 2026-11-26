"""Focused Xodus session status tests with injected stores (no real keyring)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

APP_ROOT = SERVICE_ROOT / "craft-projects" / "playnite-desktop" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from xodus_social_view import XodusSocialView
from native_services.xodus_social import SocialDisconnectedError


class AsyncMockSocialTransport:
    def __init__(self, account="saved@xbox.test") -> None:
        self.account = account
        self.is_open = False
        self.written_lines: list[str] = []
        self._read_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def open(self) -> None:
        self.is_open = True

    async def write_line(self, data: str) -> None:
        if not self.is_open:
            raise SocialDisconnectedError("Mock closed")
        self.written_lines.append(data)
        request = json.loads(data)
        args = request.get("args")
        payload = json.loads(request.get("input") or "{}")
        if args == ["friends"]:
            result = {
                "schema": 1,
                "kind": "friends",
                "account": self.account,
                "items": [],
            }
        elif args == ["party"] and payload.get("operation") == "active":
            result = {
                "schema": 1,
                "kind": "party",
                "account": self.account,
                "operation": "active",
                "result": {"ids": []},
            }
        else:
            raise AssertionError("Unexpected helper command")
        await self._read_queue.put(
            json.dumps({"id": request["id"], "ok": True, "result": result})
        )

    async def read_line(self) -> str | None:
        return await self._read_queue.get()

    async def close(self) -> None:
        self.is_open = False
        await self._read_queue.put(None)


class InjectedStore:
    def __init__(self, data=None, fail_save=False, fail_load=False, fail_clear=False):
        self.data = {} if data is None else dict(data)
        self.fail_save = fail_save
        self.fail_load = fail_load
        self.fail_clear = fail_clear

    def load(self, service: str):
        if self.fail_load:
            raise RuntimeError("simulated keyring locked")
        return self.data.get(service)

    def save(self, service: str, payload):
        if self.fail_save:
            raise RuntimeError("simulated keyring unavailable")
        self.data[service] = payload

    def clear(self, service: str):
        if self.fail_clear:
            raise RuntimeError("simulated keyring unavailable")
        self.data.pop(service, None)


def wait_until(pred, timeout_ms=3000):
    deadline = timeout_ms
    while deadline > 0:
        QTest.qWait(20)
        deadline -= 20
        if pred():
            return True
    return False


class XodusSessionStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def make_view(self, **kwargs):
        self.addCleanup(kwargs.get("transport", None) and (lambda: None) or (lambda: None))
        view = XodusSocialView(**kwargs)
        self.addCleanup(lambda: (view.shutdown() if view else None))
        return view

    def test_failing_keyring_save_exposes_safe_status(self):
        store = InjectedStore(fail_save=True)
        transport = AsyncMockSocialTransport(account="alice@xbox.test")
        view = self.make_view(transport=transport, account="alice@xbox.test", session_store=store)
        view.connectService()
        self.assertTrue(wait_until(lambda: view.connected))
        self.assertTrue(wait_until(lambda: view.sessionStatus == "Xbox sign-in could not be saved."))
        self.assertNotIn("keyring", view.sessionStatus.lower())
        self.assertNotIn("locked", view.sessionStatus.lower())
        self.assertNotIn("unavailable", view.sessionStatus.lower())

    def test_failing_keyring_clear_not_reported_as_forgotten(self):
        store = InjectedStore(
            data={"xbox": {"kind": "xbox_helper", "account": "saved@xbox.test"}},
            fail_clear=True,
        )
        view = self.make_view(transport=AsyncMockSocialTransport(), session_store=store)
        view.disconnectService()
        self.assertTrue(wait_until(lambda: view.sessionStatus == "Saved Xbox sign-in could not be removed."))
        self.assertNotIn("forgotten", view.sessionStatus.lower())
        self.assertNotIn("removed", view.sessionStatus.lower().replace("could not be removed", ""))
        self.assertNotIn("keyring", view.sessionStatus.lower())

    def test_failing_keyring_load_exposes_safe_restore_status(self):
        store = InjectedStore(fail_load=True)
        view = self.make_view(transport=AsyncMockSocialTransport(), session_store=store)
        view.restoreSession()
        self.assertTrue(wait_until(lambda: view.sessionStatus == "Saved Xbox sign-in could not be restored."))
        self.assertFalse(view.connected)
        self.assertNotIn("keyring", view.sessionStatus.lower())
        self.assertNotIn("locked", view.sessionStatus.lower())

    def test_successful_restore_connects_and_does_not_report_failure(self):
        store = InjectedStore({"xbox": {"kind": "xbox_helper", "account": "saved@xbox.test"}})
        transport = AsyncMockSocialTransport(account="saved@xbox.test")
        view = self.make_view(transport=transport, session_store=store)
        view.restoreSession()
        self.assertTrue(wait_until(lambda: view.connected))
        self.assertTrue(wait_until(lambda: len(view.friends) == 0))
        self.assertNotIn("could not", view.sessionStatus.lower())
        self.assertNotIn("failed", view.sessionStatus.lower())


if __name__ == "__main__":
    unittest.main()
