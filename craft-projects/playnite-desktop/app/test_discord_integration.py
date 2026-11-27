"""W20a Discord behavior tests. All IPC endpoints and clients are temporary fakes."""
from __future__ import annotations

import json
import os
import socket
import struct
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from native_services.discord_integration import (
    DiscordClientInfo,
    DiscordIpcClient,
    OP_FRAME,
    OP_HANDSHAKE,
    choose_discord_client,
    detect_discord_clients,
    pack_ipc_frame,
    send_vesktop_ptt,
)


def _read_frame(conn):
    header = b""
    while len(header) < 8:
        part = conn.recv(8 - len(header))
        if not part:
            raise EOFError
        header += part
    opcode, size = struct.unpack("<II", header)
    body = b""
    while len(body) < size:
        part = conn.recv(size - len(body))
        if not part:
            raise EOFError
        body += part
    return opcode, json.loads(body.decode("utf-8"))


class FakeDiscordIpc:
    def __init__(self, path: Path):
        self.path = path
        self.frames = []
        self.error = None
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        self.listener.listen(3)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            while True:
                conn, _ = self.listener.accept()
                with conn:
                    op, handshake = _read_frame(conn)
                    self.frames.append((op, handshake))
                    conn.sendall(pack_ipc_frame(OP_FRAME, {"cmd": "DISPATCH", "evt": "READY", "data": {}}))
                    while True:
                        try:
                            op, payload = _read_frame(conn)
                        except EOFError:
                            break
                        self.frames.append((op, payload))
                        conn.sendall(pack_ipc_frame(OP_FRAME, {"cmd": payload.get("cmd"), "nonce": payload.get("nonce")}))
                if len(self.frames) >= 3:
                    break
        except Exception as exc:  # surfaced in test thread rather than silently accepted
            self.error = exc
        finally:
            self.listener.close()

    def close(self):
        self.listener.close()
        self.thread.join(timeout=1)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def test_rich_presence_handshake_set_and_clear_use_fake_ipc(tmp_path):
    path = tmp_path / "discord-ipc-0"
    fake = FakeDiscordIpc(path)
    client = DiscordIpcClient(app_id="123456789", runtime_dir=tmp_path, timeout=0.5)
    try:
        assert client.set_activity("Fixture Game", start_timestamp=1234, pid=42)
        assert client.clear_activity(pid=42)
        client.close()
        fake.thread.join(timeout=1)
        assert fake.error is None
        assert fake.frames[0] == (OP_HANDSHAKE, {"v": 1, "client_id": "123456789"})
        set_payload = fake.frames[1][1]
        assert fake.frames[1][0] == OP_FRAME
        assert set_payload["cmd"] == "SET_ACTIVITY"
        assert set_payload["args"]["activity"]["details"] == "Playing Fixture Game"
        assert set_payload["args"]["activity"]["timestamps"]["start"] == 1234
        clear_payload = fake.frames[2][1]
        assert clear_payload["cmd"] == "SET_ACTIVITY"
        assert clear_payload["args"]["activity"] is None
    finally:
        client.close()
        fake.close()


def test_client_detection_and_preference_are_injected_not_live():
    binaries = {"vesktop": "/fake/bin/vesktop", "discord": "/fake/bin/discord"}
    clients = detect_discord_clients(
        which=lambda name: binaries.get(name),
        flatpak_installed=lambda app_id: app_id == "com.discordapp.Discord",
    )
    assert [item.client_id for item in clients] == ["vesktop", "discord", "discord-flatpak"]
    assert choose_discord_client(clients, "discord-flatpak").kind == "flatpak"
    assert choose_discord_client(clients, "auto").client_id == "vesktop"


def test_ptt_reports_unsupported_client_without_spawning():
    unsupported = DiscordClientInfo("discord", "Discord", "native", ("/fake/discord",), "discord", False)
    calls = []
    ok, message = send_vesktop_ptt(
        True,
        client=unsupported,
        runtime_dir="/does-not-exist",
        environ={},
        which=lambda _name: None,
        spawn_fn=lambda argv: calls.append(argv),
    )
    assert not ok and "does not support" in message
    assert calls == []


def test_ptt_uses_fake_vesktop_socket(tmp_path):
    path = tmp_path / "vesktop.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    received = []

    def accept_one():
        conn, _ = listener.accept()
        with conn:
            received.append(conn.recv(256))

    thread = threading.Thread(target=accept_one, daemon=True)
    thread.start()
    ok, message = send_vesktop_ptt(True, runtime_dir=tmp_path, environ={}, which=lambda _name: None)
    thread.join(timeout=1)
    listener.close()
    path.unlink(missing_ok=True)
    assert ok and "Vesktop socket" in message
    assert received == [b"run-shortcut pushToTalk:down\n"]


def test_presence_tracks_only_confirmed_running_session_and_clears_on_close(tmp_path):
    from discord_view import DiscordView

    class FakeLaunchView(QObject):
        changed = Signal()
        activeGame = None

    class FakePresence:
        def __init__(self):
            self.calls = []

        def set_activity(self, title, *, start_timestamp=None, pid=None):
            self.calls.append(("set", title, start_timestamp))
            return True

        def clear_activity(self, *, pid=None):
            self.calls.append(("clear",))
            return True

        def close(self):
            pass

    launch = FakeLaunchView()
    view = DiscordView(
        settings_path=tmp_path / "settings.json",
        launch_view=launch,
        runtime_dir=tmp_path / "runtime",
        environ={},
        which=lambda _name: None,
        flatpak_installed=lambda _app: False,
        offline=True,
    )
    fake = FakePresence()
    view._ipc_client = fake
    try:
        launch.activeGame = {"state": "launching", "title": "Not Yet Running"}
        assert not view.syncRichPresence()
        assert fake.calls == []
        launch.activeGame = {"state": "running", "title": "Fixture Game", "runningSince": 1234}
        assert view.syncRichPresence()
        assert fake.calls == [("set", "Fixture Game", 1234)]
        assert view.syncRichPresence()  # repeated monitor notifications do not resend
        assert len(fake.calls) == 1
        launch.activeGame = {"state": "closed", "title": "Fixture Game"}
        assert not view.syncRichPresence()
        assert fake.calls[-1] == ("clear",)
    finally:
        view.shutdown()


def test_presence_disabled_does_not_publish(tmp_path):
    from discord_view import DiscordView

    class FakeLaunchView(QObject):
        changed = Signal()
        activeGame = {"state": "running", "title": "Fixture Game", "runningSince": 1234}

    view = DiscordView(
        settings_path=tmp_path / "settings.json",
        launch_view=FakeLaunchView(),
        runtime_dir=tmp_path / "runtime",
        environ={},
        which=lambda _name: None,
        flatpak_installed=lambda _app: False,
        offline=True,
    )
    try:
        view.setRichPresenceEnabled(False)
        assert not view.syncRichPresence()
        assert view._active_published_key is None
    finally:
        view.shutdown()


def test_app_session_exposes_discord_surface_with_isolated_headless_runtime(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import QObject
    from app import create_session

    application = QGuiApplication.instance() or QGuiApplication([])
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    engine, _model, root, _warnings = create_session(
        application,
        headless=True,
        provider_jobs_enabled=False,
    )
    try:
        assert engine.discord_view is not None
        assert engine.discord_view.hasClient is False
        assert engine.rootContext().contextProperty("discordView") is engine.discord_view
        assert engine.discord_view._runtime_dir == engine.discord_view._settings_path.parent / "runtime"
        assert engine.discord_view._environ == {}
    finally:
        engine.discord_view.shutdown()
        root.close()
        engine.deleteLater()
        application.processEvents()


def test_no_application_id_means_no_ipc_connection(tmp_path):
    """Without a registered Application ID, Waystone never opens Discord's socket (no foreign app name shown)."""
    from native_services import discord_integration as di
    assert di.DEFAULT_DISCORD_APP_ID == ""
    (tmp_path / "discord-ipc-0").write_text("")  # would be a candidate if we tried to connect
    client = di.DiscordIpcClient(app_id="", runtime_dir=tmp_path, timeout=0.1)
    assert client._connect() is None
    assert client.set_activity("Some Game") is False
