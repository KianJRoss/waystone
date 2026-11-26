"""Fake-only tests for W11a: Waystone library in Moonlight via Sunshine host integration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, QMetaObject, QObject, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from app import create_session, schedule_startup_play, try_handoff_play_to_running_app
from native_services.ribbon_artwork import RibbonCache
from native_services.sunshine_library import (
    SUNSHINE_COVER_HEIGHT,
    SUNSHINE_COVER_WIDTH,
    SunshineRestClient,
    detect_sunshine_host,
    probe_local_sunshine_serverinfo,
    publish_to_sunshine,
    remove_from_sunshine,
    sync_sunshine_rest,
)
from native_services.waystone_control import AppJournal, WaystoneControlServer
from native_services.waystone_control_client import WaystoneSocketClient


def _make_fake_png_bytes(width: int = 320, height: int = 480, color: str = "#3a6ea5") -> bytes:
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(color))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    assert image.save(buf, "PNG")
    return bytes(buf.data())


class FakeSunshineRestClient:
    """In-memory fake for Sunshine's local REST API (:47990/api/apps)."""

    def __init__(self, initial_apps: list[dict] | None = None) -> None:
        self.apps: list[dict] = [dict(a) for a in (initial_apps or [])]
        self.saved_calls: int = 0
        self.deleted_indices: list[int] = []

    def list_apps(self) -> list[dict]:
        return [dict(a) for a in self.apps]

    def save_app(self, app_entry: dict, index: int = -1) -> None:
        self.saved_calls += 1
        entry = dict(app_entry)
        entry.pop("index", None)
        if 0 <= index < len(self.apps):
            self.apps[index] = entry
        else:
            self.apps.append(entry)

    def delete_app(self, index: int) -> None:
        self.deleted_indices.append(index)
        if 0 <= index < len(self.apps):
            self.apps.pop(index)


def _sample_catalog_games() -> list[dict]:
    return [
        {
            "id": "game-alpha",
            "title": "Alpha Odyssey",
            "variants": [
                {
                    "source": "Steam",
                    "source_id": "steam-101",
                    "provider_game_id": "101",
                    "platform": "PC",
                    "status": "installed",
                    "metadata": {
                        "favorite": True,
                        "categories": ["RPG", "Co-op"],
                        "available_fields": ["favorite", "categories"],
                    },
                }
            ],
        },
        {
            "id": "game-beta",
            "title": "Beta Horizon",
            "variants": [
                {
                    "source": "GOG",
                    "source_id": "gog-202",
                    "provider_game_id": "202",
                    "platform": "PC",
                    "status": "installed",
                    "metadata": {
                        "favorite": False,
                        "categories": ["Action"],
                        "available_fields": ["favorite", "categories"],
                    },
                }
            ],
        },
        {
            "id": "game-gamma",
            "title": "Gamma Tactics",
            "variants": [
                {
                    "source": "Epic",
                    "source_id": "epic-303",
                    "provider_game_id": "303",
                    "platform": "PC",
                    "status": "not-installed",
                    "metadata": {
                        "favorite": True,
                        "categories": ["RPG"],
                        "available_fields": ["favorite", "categories"],
                    },
                }
            ],
        },
    ]


class SunshineStreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_detect_sunshine_and_apollo_fork_with_fakes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xdg = root / "config"
            apollo_dir = xdg / "apollo"
            apollo_dir.mkdir(parents=True)
            (apollo_dir / "apollo.conf").write_text("sunshine_name = ApolloBox\n", encoding="utf-8")

            status = detect_sunshine_host(
                xdg_config_home=xdg,
                home_dir=root,
                which=lambda name: "/usr/bin/apollo" if name == "apollo" else None,
                status_probe=lambda: {"running": True, "client_connected": True},
                offline=True,
            )
            self.assertTrue(status.installed)
            self.assertTrue(status.running)
            self.assertTrue(status.client_connected)
            self.assertEqual(status.flavor, "Apollo")
            self.assertEqual(status.config_dir, apollo_dir)
            self.assertIn("Apollo (Sunshine fork)", status.summary)
            self.assertIn("Moonlight client connected", status.summary)
            self.assertEqual(status.big_screen_label, "On · client connected")

    def test_publish_backs_up_apps_json_preserves_user_entries_writes_600x900_png_and_syncs_fake_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sunshine_dir = root / "sunshine"
            sunshine_dir.mkdir(parents=True)
            apps_json = sunshine_dir / "apps.json"

            original_user_doc = {
                "env": {"PATH": "$(PATH):$(HOME)/.local/bin", "CUSTOM_VAR": "keep-me"},
                "apps": [
                    {
                        "name": "Desktop",
                        "image-path": "desktop.png",
                        "prep-cmd": [{"do": "focus-desktop", "undo": ""}],
                    },
                    {
                        "name": "Steam Big Picture",
                        "detached": ["setsid steam steam://open/bigpicture"],
                        "image-path": "steam.png",
                    },
                ],
            }
            original_raw = json.dumps(original_user_doc, indent=2) + "\n"
            apps_json.write_text(original_raw, encoding="utf-8")

            # Fake user cover in covers/ that must never be deleted by Waystone
            covers_dir = sunshine_dir / "covers"
            covers_dir.mkdir(parents=True)
            user_cover = covers_dir / "custom-user-cover.png"
            user_cover.write_bytes(_make_fake_png_bytes(100, 100))

            fake_rest = FakeSunshineRestClient(initial_apps=original_user_doc["apps"])
            fake_art_bytes = _make_fake_png_bytes(300, 450, "#c04040")

            res1 = publish_to_sunshine(
                sunshine_dir,
                [
                    {"id": "game-alpha", "title": "Alpha Odyssey", "cover_source": fake_art_bytes},
                    {"id": "game-beta", "title": "Beta Horizon", "cover_source": None},
                ],
                rest_client=fake_rest,
            )
            self.assertTrue(res1["backed_up"])
            self.assertEqual(res1["published_count"], 2)
            self.assertEqual(res1["user_count"], 2)
            self.assertEqual(res1["covers_written"], 1)
            self.assertTrue(res1["rest_synced"])

            # 1. Verify backup matches exact pre-Waystone bytes
            backup_file = sunshine_dir / "apps.json.waystone.bak"
            self.assertTrue(backup_file.is_file())
            self.assertEqual(backup_file.read_text(encoding="utf-8"), original_raw)

            # 2. Verify user entries and top-level env survived untouched
            written_doc = json.loads(apps_json.read_text(encoding="utf-8"))
            self.assertEqual(written_doc["env"], original_user_doc["env"])
            self.assertEqual(len(written_doc["apps"]), 4)
            self.assertEqual(written_doc["apps"][:2], original_user_doc["apps"])

            # 3. Verify Waystone entries and 600x900 PNG cover art
            ws_alpha = written_doc["apps"][2]
            ws_beta = written_doc["apps"][3]
            self.assertEqual(ws_alpha["name"], "Alpha Odyssey")
            self.assertEqual(ws_alpha["cmd"], "waystone --play game-alpha")
            self.assertTrue(ws_alpha["waystone_managed"])
            self.assertEqual(ws_alpha["waystone_id"], "game-alpha")
            cover_path = Path(ws_alpha["image-path"])
            self.assertTrue(cover_path.is_file())
            self.assertTrue(cover_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            loaded_cover = QImage(str(cover_path))
            self.assertFalse(loaded_cover.isNull())
            self.assertEqual(loaded_cover.width(), SUNSHINE_COVER_WIDTH)
            self.assertEqual(loaded_cover.height(), SUNSHINE_COVER_HEIGHT)

            self.assertEqual(ws_beta["name"], "Beta Horizon")
            self.assertEqual(ws_beta["cmd"], "waystone --play game-beta")
            self.assertEqual(ws_beta["image-path"], "")

            # 4. Verify fake REST client also kept user entries and synced Waystone entries
            self.assertEqual(len(fake_rest.apps), 4)
            self.assertEqual(fake_rest.apps[:2], original_user_doc["apps"])
            self.assertEqual(fake_rest.apps[2]["waystone_id"], "game-alpha")
            self.assertEqual(fake_rest.apps[3]["waystone_id"], "game-beta")

            # 5. Second publish does not overwrite original backup
            res2 = publish_to_sunshine(
                sunshine_dir,
                [{"id": "game-alpha", "title": "Alpha Odyssey", "cover_source": fake_art_bytes}],
                rest_client=fake_rest,
            )
            self.assertFalse(res2["backed_up"])
            self.assertEqual(backup_file.read_text(encoding="utf-8"), original_raw)
            self.assertEqual(len(fake_rest.apps), 3)

            # 6. Remove Waystone games from Moonlight undoes it cleanly while preserving user entries & user cover
            rem = remove_from_sunshine(sunshine_dir, rest_client=fake_rest)
            self.assertEqual(rem["removed_count"], 1)
            self.assertEqual(rem["user_count"], 2)
            after_remove_doc = json.loads(apps_json.read_text(encoding="utf-8"))
            self.assertEqual(after_remove_doc["apps"], original_user_doc["apps"])
            self.assertEqual(fake_rest.apps, original_user_doc["apps"])
            self.assertFalse(cover_path.exists())
            self.assertTrue(user_cover.is_file())

    def test_settings_streaming_ui_filters_auto_republish_and_big_screen_status_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            sunshine_dir = root_dir / "sunshine"
            sunshine_dir.mkdir(parents=True)
            apps_json = sunshine_dir / "apps.json"
            user_entry = {"name": "Desktop", "image-path": "desktop.png"}
            apps_json.write_text(json.dumps({"apps": [user_entry]}), encoding="utf-8")

            catalog_path = root_dir / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "catalog_kind": "playnite-display-selected-copy-v1",
                        "games": _sample_catalog_games(),
                    }
                ),
                encoding="utf-8",
            )

            # Populate fake artwork cache for Steam app 101 (Alpha Odyssey)
            cache_dir = root_dir / "artwork-cache"
            ribbon_cache = RibbonCache(cache_dir)
            ribbon_cache.store("steam", "101", {"cover": (_make_fake_png_bytes(400, 600, "#208050"), "png")})

            probe_state = {"running": True, "client_connected": False}
            fake_rest = FakeSunshineRestClient(initial_apps=[user_entry])

            engine, model, qml_root, warnings = create_session(
                self.app,
                headless=True,
                catalog=catalog_path,
                provider_jobs_enabled=False,
                sunshine_config_dir=sunshine_dir,
                sunshine_status_probe=lambda: dict(probe_state),
                sunshine_rest_client=fake_rest,
                sunshine_which=lambda name: "/usr/bin/sunshine" if name == "sunshine" else None,
            )
            try:
                model.configureRibbons(ribbon_cache, None)
                engine.sunshine_settings._ribbon_cache = ribbon_cache
                qml_root.setVisible(True)
                QTest.qWait(40)

                # Open Settings > Streaming (category index 5)
                qml_root.setProperty("activePage", "settings")
                dashboard = qml_root.findChild(QObject, "settingsDashboard")
                self.assertIsNotNone(dashboard)
                dashboard.setProperty("selectedCategory", 5)
                QTest.qWait(40)

                card = qml_root.findChild(QQuickItem, "settingsStreamingCard")
                self.assertIsNotNone(card)
                self.assertTrue(card.isVisible())

                status_text = qml_root.findChild(QObject, "settingsStreamingStatus")
                self.assertIn("Sunshine detected", status_text.property("text"))
                self.assertEqual(engine.sunshine_settings.filterMode, "installed")

                # Turn on "Publish library to Moonlight" (default filter: installed games only -> alpha & beta)
                engine.sunshine_settings.setPublishEnabled(True)
                QTest.qWait(30)
                doc = json.loads(apps_json.read_text(encoding="utf-8"))
                self.assertEqual(
                    [a["name"] for a in doc["apps"]],
                    ["Desktop", "Alpha Odyssey", "Beta Horizon"],
                )
                # Alpha Odyssey got its 600x900 PNG cover from Waystone's artwork cache
                alpha_entry = next(a for a in doc["apps"] if a.get("waystone_id") == "game-alpha")
                self.assertTrue(Path(alpha_entry["image-path"]).is_file())
                self.assertEqual(QImage(alpha_entry["image-path"]).width(), 600)
                self.assertEqual(QImage(alpha_entry["image-path"]).height(), 900)

                # Switch filter to "favorites" -> Alpha Odyssey & Gamma Tactics
                engine.sunshine_settings.setFilterMode("favorites")
                QTest.qWait(20)
                doc = json.loads(apps_json.read_text(encoding="utf-8"))
                self.assertEqual(
                    [a["name"] for a in doc["apps"]],
                    ["Desktop", "Alpha Odyssey", "Gamma Tactics"],
                )

                # Switch filter to "category" ("Action") -> Beta Horizon only
                engine.sunshine_settings.setFilterCategory("Action")
                engine.sunshine_settings.setFilterMode("category")
                QTest.qWait(20)
                doc = json.loads(apps_json.read_text(encoding="utf-8"))
                self.assertEqual([a["name"] for a in doc["apps"]], ["Desktop", "Beta Horizon"])

                # Switch back to "installed" and verify automatic re-publish after library refresh
                engine.sunshine_settings.setFilterMode("installed")
                updated_games = _sample_catalog_games()
                updated_games[2]["variants"][0]["status"] = "installed"  # Gamma Tactics becomes installed
                model.replaceCatalog(updated_games)
                engine.sunshine_settings.republishIfEnabled()
                QTest.qWait(20)
                doc = json.loads(apps_json.read_text(encoding="utf-8"))
                self.assertEqual(
                    [a["name"] for a in doc["apps"]],
                    ["Desktop", "Alpha Odyssey", "Beta Horizon", "Gamma Tactics"],
                )

                # Verify Big Screen detail "Streaming" status row
                qml_root.toggleConsole()
                QTest.qWait(40)
                console_lib = qml_root.findChild(QObject, "consoleLibrary")
                self.assertIsNotNone(console_lib)
                console_lib.openGamePage("game-alpha")
                QTest.qWait(40)
                streaming_val = qml_root.findChild(QObject, "gameStreamingValue")
                self.assertIsNotNone(streaming_val)
                self.assertEqual(streaming_val.property("text"), "On")

                # Simulate Moonlight client connecting to Sunshine
                probe_state["client_connected"] = True
                engine.sunshine_settings.refreshStatus()
                QTest.qWait(30)
                self.assertEqual(streaming_val.property("text"), "On · client connected")

                # Remove Waystone games from Moonlight and stop Sunshine -> Big Screen row shows "Off"
                probe_state["running"] = False
                probe_state["client_connected"] = False
                remove_btn = qml_root.findChild(QObject, "settingsStreamingRemove")
                self.assertIsNotNone(remove_btn)
                assert QMetaObject.invokeMethod(remove_btn, "clicked", Qt.DirectConnection)
                QTest.qWait(30)
                self.assertFalse(engine.sunshine_settings.publishEnabled)
                self.assertEqual(streaming_val.property("text"), "Off")
                doc = json.loads(apps_json.read_text(encoding="utf-8"))
                self.assertEqual(doc["apps"], [user_entry])
                self.assertEqual(warnings, [])
            finally:
                qml_root.setVisible(False)
                engine.social_view.shutdown()
                engine.live_updates.stop()
                engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.app.processEvents()

    def test_cli_play_handoff_over_live_control_socket_and_cold_start(self):
        class _FakeLaunch:
            activeGame = {}
            launchDetail = ""
            playLabel = "Play"
            lastError = ""

        class _FakeModel:
            games = [{"id": "game-alpha", "title": "Alpha Odyssey"}]

            @staticmethod
            def representativeFor(work_id: str) -> str:
                return "game-alpha" if work_id in ("game-alpha", "game-alpha-member") else ""

        class _FakeRoot:
            def __init__(self) -> None:
                self.played: list[str] = []

            def playGameById(self, game_id: str) -> bool:
                if game_id == "game-alpha":
                    self.played.append(game_id)
                    return True
                return False

        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            runtime_dir = root_dir / "runtime"
            runtime_dir.mkdir(mode=0o700)
            os.chmod(runtime_dir, 0o700)

            fake_launch = _FakeLaunch()
            fake_model = _FakeModel()
            fake_root = _FakeRoot()

            server = WaystoneControlServer(
                launch_view=fake_launch,
                library_model=fake_model,
                qml_root=fake_root,
                runtime_dir=runtime_dir,
                journal=AppJournal(root_dir / "journal.jsonl"),
                parent=self.app,
            )
            try:
                # 1. Cold start path when control server is not yet listening returns None
                no_server_code = try_handoff_play_to_running_app(
                    "game-alpha",
                    client_factory=lambda: WaystoneSocketClient(path=runtime_dir / "waystone/control.sock"),
                )
                self.assertIsNone(no_server_code)

                # 2. Live control socket handoff when Waystone is open (including member canonical ID resolution)
                server.start()
                import threading

                result_holder: dict[str, int | None] = {}

                def _client_worker():
                    result_holder["code"] = try_handoff_play_to_running_app(
                        "game-alpha-member",
                        client_factory=lambda: WaystoneSocketClient(path=server.socket_path, timeout=2.0),
                    )

                t = threading.Thread(target=_client_worker)
                t.start()
                for _ in range(40):
                    self.app.processEvents()
                    QTest.qWait(10)
                    if not t.is_alive():
                        break
                t.join(timeout=2.0)
                self.assertEqual(result_holder.get("code"), 0)
                self.assertEqual(fake_root.played, ["game-alpha"])

                # 3. Cold-start schedule_startup_play helper launches through Waystone
                schedule_startup_play(fake_root, fake_model, None, "game-alpha")
                QTest.qWait(160)
                self.assertEqual(fake_root.played, ["game-alpha", "game-alpha"])
            finally:
                server.close()
                self.app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.app.processEvents()

    def test_sunshine_rest_client_and_serverinfo_probe_with_fake_http_server(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        state = {
            "busy": False,
            "apps": [{"name": "Desktop", "image-path": "desktop.png"}],
            "last_auth": "",
        }

        class _FakeSunshineHandler(BaseHTTPRequestHandler):
            def log_message(self, _fmt, *_args):
                return

            def do_GET(self):
                if self.path == "/serverinfo":
                    st = "SUNSHINE_SERVER_BUSY" if state["busy"] else "SUNSHINE_SERVER_FREE"
                    cg = "42" if state["busy"] else "0"
                    xml = f'<?xml version="1.0" encoding="utf-8"?><root status_code="200"><state>{st}</state><currentgame>{cg}</currentgame></root>'
                    body = xml.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/xml")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/api/apps":
                    state["last_auth"] = self.headers.get("Authorization", "")
                    body = json.dumps({"apps": state["apps"]}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                if self.path == "/api/apps":
                    state["last_auth"] = self.headers.get("Authorization", "")
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    idx = payload.pop("index", -1)
                    if isinstance(idx, int) and 0 <= idx < len(state["apps"]):
                        state["apps"][idx] = payload
                    else:
                        state["apps"].append(payload)
                    body = b'{"status":"true"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def do_DELETE(self):
                if self.path.startswith("/api/apps/"):
                    state["last_auth"] = self.headers.get("Authorization", "")
                    idx = int(self.path.rsplit("/", 1)[1])
                    if 0 <= idx < len(state["apps"]):
                        state["apps"].pop(idx)
                    body = b'{"status":"true"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

        httpd = HTTPServer(("127.0.0.1", 0), _FakeSunshineHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            # 1. Serverinfo probe when idle vs client connected
            idle_probe = probe_local_sunshine_serverinfo(http_port=port, https_port=port)
            self.assertEqual(idle_probe, {"running": True, "client_connected": False})
            state["busy"] = True
            busy_probe = probe_local_sunshine_serverinfo(http_port=port, https_port=port)
            self.assertEqual(busy_probe, {"running": True, "client_connected": True})

            # 2. REST API sync preserves user entries, pushes Waystone entries, and sends Basic Auth
            client = SunshineRestClient(
                base_url=f"http://127.0.0.1:{port}",
                username="sunshine-admin",
                password="fake-password",
            )
            sync_sunshine_rest(
                client,
                [
                    {
                        "name": "Alpha Odyssey",
                        "cmd": "waystone --play game-alpha",
                        "image-path": "",
                        "output": "",
                        "waystone_managed": True,
                        "waystone_id": "game-alpha",
                    }
                ],
            )
            self.assertTrue(state["last_auth"].startswith("Basic "))
            self.assertEqual([a["name"] for a in state["apps"]], ["Desktop", "Alpha Odyssey"])

            # Removing clears only Waystone entries
            sync_sunshine_rest(client, [])
            self.assertEqual([a["name"] for a in state["apps"]], ["Desktop"])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)


def test_sunshine_command_uses_an_absolute_waystone_path(tmp_path):
    """Sunshine runs apps with its own environment; entries must not depend on the user's PATH."""
    from native_services import sunshine_library as sl
    local = tmp_path / ".local/bin/waystone"
    local.parent.mkdir(parents=True)
    local.write_text("#!/bin/sh\n")
    local.chmod(0o755)
    assert sl.resolve_waystone_command(which=lambda name: None, home=tmp_path) == str(local)
    assert sl.resolve_waystone_command(which=lambda name: None, home=tmp_path / "nobody") == "waystone"
    assert sl.build_waystone_command("steam:1", waystone_command=str(local)) == f"{local} --play steam:1"
    assert sl.build_waystone_command("x y", waystone_command="/opt/my apps/waystone") == "'/opt/my apps/waystone' --play 'x y'"
