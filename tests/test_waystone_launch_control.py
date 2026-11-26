from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest

import pytest

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalSocket
from mcp.server.mcpserver.exceptions import ToolError

from native_services.waystone_control import (
    AppJournal,
    WaystoneControlServer,
    WaystoneSocketClient,
)
from tools.waystone_mcp import create_mcp_app
from native_services.organizer_control import OrganizerControl
from native_services.organizer_store import OrganizerStore


APP = QGuiApplication.instance() or QGuiApplication(["waystone-control-tests"])  # GUI app: later QML tests reuse it


class FakeLaunchView:
    def __init__(self):
        self.activeGame = {}
        self.launchDetail = ""
        self.playLabel = "Play"
        self.lastError = ""
        self.stop_calls = 0

    def stopActiveGame(self):
        self.stop_calls += 1
        return True


class FakeLibraryModel:
    games = [
        {"id": "g-1", "title": "Alpha"},
        {"id": "g-2", "title": "Beta"},
        {"id": "g-3", "title": "Not installed"},
    ]

    def overview(self, game_id):
        return {"id": game_id, "title": next(g["title"] for g in self.games if g["id"] == game_id),
                "platforms": "Linux"}


class FakeQmlRoot:
    def __init__(self):
        self.played = []

    def playGameById(self, game_id):
        self.played.append(game_id)
        return game_id == "g-1"

    def launchOptionsFor(self, overview, game_id):
        # A real QML root hands JS arrays back to Python as QJSValue (JsValue below), never as a list.
        if game_id == "g-3":
            return JsValue([])
        provider = "Steam" if game_id == "g-1" else "GOG"
        return JsValue([{"provider": provider, "targetId": game_id}])


class FakeAssistantQmlRoot(FakeQmlRoot):
    def __init__(self):
        super().__init__()
        self.animating = True
        self.phase = "scrolling"
        self.target = "g-1"
        self.cancel_calls = 0

    def requestAssistantPlayById(self, game_id):
        self.target = game_id
        self.animating = True
        self.phase = "opening"
        return {"status": "started", "animating": True}

    def property(self, name):
        return {"assistantAnimating": self.animating, "assistantAnimationPhase": self.phase,
                "assistantAnimationTarget": self.target}.get(name)

    def cancelAssistantAnimationAndLaunch(self):
        self.cancel_calls += 1
        self.animating = False
        self.phase = "idle"


@pytest.mark.linux_only
class WaystoneControlSocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.launch = FakeLaunchView()
        self.model = FakeLibraryModel()
        self.qml = FakeQmlRoot()
        self.server = WaystoneControlServer(
            launch_view=self.launch,
            library_model=self.model,
            qml_root=self.qml,
            runtime_dir=self.root,
            journal=AppJournal(self.root / "state" / "waystone" / "app-journal.jsonl"),
        )
        self.server.start()

    def tearDown(self):
        self.server.close()
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.tmp.cleanup()

    def request(self, obj):
        client = QLocalSocket()
        client.connectToServer(str(self.server.socket_path))
        self.assertTrue(client.waitForConnected(500))
        client.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
        client.flush()
        deadline = time.monotonic() + 1.0
        while client.bytesAvailable() == 0 and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.001)
        self.assertGreater(client.bytesAvailable(), 0, "no response from local control socket")
        line = bytes(client.readLine()).strip()
        client.disconnectFromServer()
        if client.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            client.waitForDisconnected(250)
        client.deleteLater()
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        return json.loads(line)

    def test_socket_permissions_and_request_journal(self):
        self.assertEqual(stat.S_IMODE(self.server.socket_path.stat().st_mode), 0o600)
        response = self.request({"request_id": "status-1", "action": "status"})
        self.assertEqual(response["request_id"], "status-1")
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"], {
            "activeGame": {}, "launchDetail": "", "playLabel": "Play",
            "assistantAnimation": {"active": False, "phase": "idle", "gameId": ""},
        })
        journal = self.root / "state" / "waystone" / "app-journal.jsonl"
        self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(journal.parent.stat().st_mode), 0o700)
        rows = [json.loads(line) for line in journal.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["request_id"], rows[0]["action"], rows[0]["outcome"]),
                         ("status-1", "status", "ok"))

    def test_play_calls_the_qml_library_path_and_busy_refuses_second_game(self):
        response = self.request({"request_id": "play-1", "action": "play", "game_id": "g-1"})
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["status"], "started")
        self.assertEqual(self.qml.played, ["g-1"])
        self.launch.activeGame = {"gameId": "g-1", "state": "running", "title": "Alpha"}
        response = self.request({"request_id": "play-2", "action": "play", "game_id": "g-2"})
        self.assertEqual(response["result"]["status"], "busy")
        self.assertEqual(response["result"]["activeGame"]["gameId"], "g-1")
        self.assertEqual(self.qml.played, ["g-1"])

    def test_play_rejects_unknown_game_id_without_calling_qml(self):
        response = self.request({"request_id": "play-unknown", "action": "play", "game_id": "not-in-library"})
        self.assertEqual(response["result"]["status"], "rejected")
        self.assertEqual(self.qml.played, [])

    def test_play_rejects_known_but_uninstalled_game_through_qml(self):
        response = self.request({"request_id": "play-uninstalled", "action": "play", "game_id": "g-3"})
        self.assertEqual(response["result"]["status"], "rejected")
        self.assertEqual(response["result"]["detail"], "No installed launch option or launch profile is available.")
        self.assertEqual(self.qml.played, ["g-3"])

    def test_stop_uses_launchview_stop_and_search_only_lists_installed_options(self):
        self.launch.activeGame = {"gameId": "g-2", "state": "running", "title": "Beta"}
        response = self.request({"request_id": "stop-1", "action": "stop"})
        self.assertEqual(response["result"]["status"], "stop_requested")
        self.assertEqual(self.launch.stop_calls, 1)
        response = self.request({"request_id": "search-1", "action": "search", "query": "steam", "limit": 5})
        self.assertEqual(response["result"], [{"id": "g-1", "title": "Alpha", "provider": "Steam", "platform": "Linux"}])
        response = self.request({"request_id": "search-2", "action": "search", "query": "", "limit": 5})
        self.assertEqual([g["id"] for g in response["result"]], ["g-1", "g-2"])

    def test_arbitrary_fields_invalid_ids_and_non_owner_are_rejected(self):
        response = self.request({"request_id": "bad-1", "action": "play", "game_id": "g-1", "argv": ["rm"]})
        self.assertFalse(response["ok"])
        self.assertEqual(self.qml.played, [])
        response = self.request({"request_id": "bad-2", "action": "search", "query": "x", "limit": 0})
        self.assertFalse(response["ok"])

        other_root = self.root / "other"
        other_root.mkdir(mode=0o700)
        other = WaystoneControlServer(
            launch_view=self.launch, library_model=self.model, qml_root=self.qml,
            runtime_dir=other_root, peer_uid=lambda _fd: os.getuid() + 1)
        other.start()
        try:
            client = QLocalSocket()
            client.connectToServer(str(other.socket_path))
            self.assertTrue(client.waitForConnected(500))
            client.write(b'{"request_id":"nope","action":"status"}\n')
            client.flush()
            deadline = time.monotonic() + 1.0
            while client.state() != QLocalSocket.LocalSocketState.UnconnectedState and time.monotonic() < deadline:
                APP.processEvents()
                time.sleep(0.001)
            self.assertEqual(client.state(), QLocalSocket.LocalSocketState.UnconnectedState)
            client.deleteLater()
            APP.processEvents()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        finally:
            other.close()
            APP.processEvents()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_second_live_server_does_not_unlink_existing_socket(self):
        second = WaystoneControlServer(launch_view=self.launch, library_model=self.model,
                                       qml_root=self.qml, runtime_dir=self.root)
        with self.assertRaisesRegex(RuntimeError, "already listening"):
            second.start()
        self.assertTrue(self.server.socket_path.exists())
        self.assertTrue(self.request({"request_id": "still-live", "action": "status"})["ok"])

    def test_assistant_play_reports_animation_and_status_exposes_phase(self):
        from unittest.mock import patch
        self.server._root = FakeAssistantQmlRoot()
        with patch.object(WaystoneControlServer, "_focus_waystone_window"):
            response = self.request({"request_id": "assistant-play", "action": "play", "game_id": "g-1"})
        self.assertEqual(response["result"]["status"], "started")
        self.assertTrue(response["result"]["animating"])
        self.assertEqual(response["result"]["assistantAnimation"], {
            "active": True, "phase": "opening", "gameId": "g-1",
        })
        status = self.request({"request_id": "assistant-status", "action": "status"})
        self.assertEqual(status["result"]["assistantAnimation"]["phase"], "opening")

    def test_user_key_or_mouse_input_cancels_animation_through_event_filter(self):
        self.server._root = FakeAssistantQmlRoot()
        self.assertTrue(self.server.eventFilter(None, QEvent(QEvent.Type.KeyPress)))
        self.assertEqual(self.server._root.cancel_calls, 1)
        self.assertFalse(self.server._root.animating)
        self.server._root.animating = True
        self.assertTrue(self.server.eventFilter(None, QEvent(QEvent.Type.MouseButtonPress)))
        self.assertEqual(self.server._root.cancel_calls, 2)
        self.assertFalse(self.server._root.animating)

    def test_input_filter_is_installed_only_while_an_animation_plays(self):
        """It is a Python call for every Qt event, so it must not stay installed (performance with ~4500 games)."""
        from unittest.mock import patch
        self.assertFalse(getattr(self.server, "_watching_input", False))
        self.server._root = FakeAssistantQmlRoot()
        with patch.object(WaystoneControlServer, "_focus_waystone_window"):
            self.request({"request_id": "assistant-play-2", "action": "play", "game_id": "g-1"})
        self.assertTrue(self.server._watching_input)
        self.server._root.animating = False                  # animation finished on its own
        self.assertFalse(self.server.eventFilter(None, QEvent(QEvent.Type.MouseMove)))
        self.assertFalse(self.server._watching_input)

    def test_external_answering_socket_is_never_removed(self):
        self.server.close()
        path = self.root / "waystone" / "control.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        second = WaystoneControlServer(launch_view=self.launch, library_model=self.model,
                                       qml_root=self.qml, runtime_dir=self.root)
        try:
            with self.assertRaisesRegex(RuntimeError, "already listening"):
                second.start()
            self.assertTrue(path.exists())
            conn, _ = listener.accept()
            conn.close()
        finally:
            listener.close()
            path.unlink(missing_ok=True)

    def test_stale_socket_is_removed_only_after_connection_refusal(self):
        self.server.close()
        path = self.root / "waystone" / "control.sock"
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()  # Leaves a socket inode but no process answers.
        replacement = WaystoneControlServer(launch_view=self.launch, library_model=self.model,
                                            qml_root=self.qml, runtime_dir=self.root)
        replacement.start()
        try:
            self.assertTrue(replacement._server.isListening())
            self.assertTrue(path.exists())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        finally:
            replacement.close()


class FakeSocketPeer:
    def __init__(self, path: Path, responses: dict[str, dict]):
        self.path, self.responses = path, responses
        self.requests = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen()
        self.server.settimeout(0.1)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stop_event.is_set():
            try:
                conn, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.stop_event.is_set():
                    return
                continue
            with conn:
                data = bytearray()
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data.extend(chunk)
                request = json.loads(bytes(data).split(b"\n", 1)[0])
                self.requests.append(request)
                result = self.responses.get(request["action"], {})
                response = {"request_id": request["request_id"], "ok": True, "result": result}
                conn.sendall(json.dumps(response).encode() + b"\n")

    def close(self):
        self.stop_event.set()
        self.server.close()
        self.thread.join(timeout=1)


@pytest.mark.linux_only
class WaystoneMcpLiveToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_tools_use_fake_unix_socket_and_no_command_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fake.sock"
            peer = FakeSocketPeer(path, {
                "search": [{"id": "g-1", "title": "Alpha", "provider": "Steam", "platform": "Linux"}],
                "play": {"status": "started"},
                "status": {"activeGame": {"gameId": "g-1"}, "launchDetail": "Playing", "playLabel": "Playing"},
                "stop": {"status": "stop_requested"},
            })
            try:
                # OrganizerControl is unused by the four live tools, but is required by the common MCP factory.
                store = OrganizerStore.create(Path(tmp) / "organizer.sqlite", ["g-1"])
                app = create_mcp_app(OrganizerControl(organizer_store=store), WaystoneSocketClient(path=path))
                found = await app.call_tool("waystone_find_installed_games", {"query": "Alpha", "limit": 4})
                self.assertFalse(found.is_error)
                played = await app.call_tool("waystone_play_game", {"game_id": "g-1"})
                running = await app.call_tool("waystone_running_game", {})
                stopped = await app.call_tool("waystone_stop_game", {})
                self.assertFalse(played.is_error or running.is_error or stopped.is_error)
                self.assertEqual([r["action"] for r in peer.requests], ["search", "play", "status", "stop"])
                self.assertEqual(peer.requests[1]["game_id"], "g-1")
                self.assertNotIn("argv", peer.requests[1])
            finally:
                peer.close()

    async def test_no_running_app_is_an_actionable_tool_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_mcp_app(OrganizerControl(organizer_store=OrganizerStore.create(Path(tmp) / "o.sqlite", ["g"])),
                                 WaystoneSocketClient(path=Path(tmp) / "absent.sock", timeout=0.05))
            with self.assertRaisesRegex(ToolError, "Waystone is not running"):
                await app.call_tool("waystone_running_game", {})


if __name__ == "__main__":
    unittest.main()


class JsValue:
    """What a real QML root returns from Python for JS arrays (PySide6 QJSValue)."""
    def __init__(self, value):
        self._value = value
    def toVariant(self):
        return self._value
    def __iter__(self):
        raise TypeError("'QJSValue' object is not iterable")


def test_js_values_from_real_qml_are_converted():
    from native_services.waystone_control import _js_value
    assert _js_value(JsValue([{"provider": "steam"}])) == [{"provider": "steam"}]
    assert _js_value(True) is True
