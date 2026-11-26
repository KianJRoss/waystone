"""Tests for bounded Xbox session persistence/restore in XodusSocialView."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
import time

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

from xodus_social_view import XodusSocialView
from native_services.xodus_social import SocialDisconnectedError


class AsyncMockSocialTransport:
    def __init__(self, account="persisted@xbox.test") -> None:
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
            result = {"schema": 1, "kind": "friends", "account": self.account,
                      "items": [{"xuid": "2533274900000001", "gamertag": "Friend",
                                 "online": True, "presence": "Home",
                                 "favorite": False, "inParty": False}]}
        elif args == ["party"] and payload.get("operation") == "active":
            result = {"schema": 1, "kind": "party", "account": self.account,
                      "operation": "active", "result": {"ids": []}}
        else:
            raise AssertionError("Unexpected helper command in read-only persistence test")
        await self._read_queue.put(json.dumps({"id": request["id"], "ok": True, "result": result}))

    async def read_line(self) -> str | None:
        if not self.is_open:
            return None
        return await self._read_queue.get()

    async def close(self) -> None:
        self.is_open = False
        await self._read_queue.put(None)


class MemorySessionStore:
    def __init__(self, initial=None):
        self.data = dict(initial or {})
        self.calls = []

    def load(self, key):
        self.calls.append(("load", key))
        return self.data.get(key)

    def save(self, key, value):
        self.calls.append(("save", key, value))
        self.data[key] = value

    def clear(self, key):
        self.calls.append(("clear", key))
        self.data.pop(key, None)


class FailingStore:
    def __init__(self, fail_load=False, fail_save=False, fail_clear=False):
        self.fail_load = fail_load
        self.fail_save = fail_save
        self.fail_clear = fail_clear
        self.calls = []

    def load(self, key):
        self.calls.append(("load", key))
        if self.fail_load:
            raise RuntimeError("load boom")
        return None

    def save(self, key, value):
        self.calls.append(("save", key, value))
        if self.fail_save:
            raise RuntimeError("save boom")

    def clear(self, key):
        self.calls.append(("clear", key))
        if self.fail_clear:
            raise RuntimeError("clear boom")


class NoIOStore:
    def load(self, key):
        raise AssertionError("unexpected startup load")

    def save(self, key, value):
        raise AssertionError("unexpected startup save")

    def clear(self, key):
        raise AssertionError("unexpected startup clear")


def wait_until(predicate, timeout_ms=3000):
    # Some distro PySide builds retain the GIL throughout repeated qWait
    # calls. Deliver queued Qt signals AND give Python worker threads a slice.
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return predicate()


def methods(transport):
    out = []
    for line in transport.written_lines:
        request = json.loads(line)
        args = request["args"]
        payload = json.loads(request.get("input") or "{}")
        out.append(args[0] + (":" + payload["operation"] if args == ["party"] else ""))
    return out


class XboxSessionPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def make_view(self, **kwargs):
        view = XodusSocialView(**kwargs)
        # Register before any assertion or async work, retaining the QObject
        # until its worker and transport have been joined even on test failure.
        self.addCleanup(view.shutdown)
        return view

    def test_failed_assertion_still_joins_worker_and_transport(self):
        captured = {}

        class ExpectedFailure(unittest.TestCase):
            def runTest(inner):
                transport = AsyncMockSocialTransport()
                view = XodusSocialView(transport=transport)
                inner.addCleanup(view.shutdown)
                captured.update(view=view, transport=transport)
                view.connectService()
                inner.assertTrue(wait_until(lambda: view.connected))
                captured["connected_before_failure"] = True
                inner.fail("intentional fixture failure to verify unconditional cleanup")

        result = unittest.TestResult()
        ExpectedFailure().run(result)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.errors, [])
        self.assertTrue(captured.get("connected_before_failure"))
        self.assertIsNone(captured["view"]._worker)
        self.assertFalse(captured["transport"].is_open)

    def test_no_startup_io_and_session_store_inert(self):
        view = self.make_view(transport=AsyncMockSocialTransport(), session_store=NoIOStore())
        QTest.qWait(50)
        self.assertFalse(view.connected)
        self.assertFalse(view.connecting)
        view.shutdown()

        view2 = self.make_view(transport=AsyncMockSocialTransport(), session_store=None)
        QTest.qWait(50)
        self.assertFalse(view2.connected)
        view2.shutdown()

    def test_explicit_connect_saves_selector_after_success(self):
        store = MemorySessionStore()
        transport = AsyncMockSocialTransport(account="alice@xbox.test")
        view = self.make_view(transport=transport, account="alice@xbox.test", session_store=store)
        view.connectService()
        self.assertTrue(wait_until(lambda: view.connected))
        self.assertTrue(wait_until(lambda: ("save", "xbox", {"kind": "xbox_helper", "account": "alice@xbox.test"}) in store.calls))
        self.assertEqual(store.data.get("xbox"), {"kind": "xbox_helper", "account": "alice@xbox.test"})
        self.assertNotIn("party:create", methods(transport))
        self.assertNotIn("party:join", methods(transport))
        view.shutdown()

    def test_restore_reconnects_and_reads_only_no_auto_party_or_voice(self):
        store = MemorySessionStore({"xbox": {"kind": "xbox_helper", "account": "saved@xbox.test"}})
        transport = AsyncMockSocialTransport(account="saved@xbox.test")
        view = self.make_view(transport=transport, session_store=store)
        view.restoreSession()
        self.assertTrue(wait_until(lambda: view.connected))
        self.assertTrue(wait_until(lambda: len(view.friends) == 1))
        ms = methods(transport)
        self.assertTrue(transport.is_open)
        self.assertEqual(ms, ["friends", "party:active"])
        self.assertEqual(view._service.account, "saved@xbox.test")
        self.assertNotIn("party:create", ms)
        self.assertNotIn("party:join", ms)
        self.assertNotIn("send-message", ms)
        self.assertNotIn("party:invite", ms)
        saves = [c for c in store.calls if c[0] == "save"]
        self.assertEqual(saves, [])
        view.shutdown()

    def test_explicit_disconnect_clears_selector_even_if_disconnected(self):
        store = MemorySessionStore({"xbox": {"kind": "xbox_helper", "account": "saved@xbox.test"}})
        view = self.make_view(transport=AsyncMockSocialTransport(), session_store=store)
        view.disconnectService()
        self.assertTrue(wait_until(lambda: ("clear", "xbox") in store.calls))
        self.assertNotIn("xbox", store.data)
        view.shutdown()

    def test_storage_failure_does_not_crash(self):
        # failing save on connect
        store = FailingStore(fail_save=True)
        view = self.make_view(transport=AsyncMockSocialTransport(account="a@x"), account="a@x", session_store=store)
        view.connectService()
        self.assertTrue(wait_until(lambda: view.connected))
        self.assertTrue(wait_until(lambda: any(c[0] == "save" for c in store.calls)))
        view.shutdown()

        # failing load on restore
        store2 = FailingStore(fail_load=True)
        view2 = self.make_view(transport=AsyncMockSocialTransport(), session_store=store2)
        view2.restoreSession()
        QTest.qWait(100)
        self.assertFalse(view2.connected)
        view2.shutdown()

        # failing clear on disconnect
        store3 = FailingStore(fail_clear=True)
        view3 = self.make_view(transport=AsyncMockSocialTransport(), session_store=store3)
        view3.disconnectService()
        self.assertTrue(wait_until(lambda: any(c[0] == "clear" for c in store3.calls)))
        view3.shutdown()


if __name__ == "__main__":
    unittest.main()
