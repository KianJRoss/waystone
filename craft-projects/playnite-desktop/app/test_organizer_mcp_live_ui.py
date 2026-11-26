"""End-to-end offline test: real Organizer MCP stdio edits -> live open Waystone UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = Path(__file__).resolve().parent
for p in (REPO_ROOT, APP_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import create_session, HERE
from native_services.catalog_link_store import CatalogLinkStore
from native_services.organizer_store import OrganizerStore


class _McpStdioClient:
    """Minimal JSON-RPC 2.0 stdio client driving real tools/waystone_mcp.py."""

    def __init__(self, organizer_db: Path, catalog_link_db: Path) -> None:
        # The test interpreter already has mcp (requirements-test.txt); no machine-specific uv path.
        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "waystone_mcp.py"),
            "--organizer-db",
            str(organizer_db),
            "--catalog-link-db",
            str(catalog_link_db),
        ]
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._next_id = 1
        init_res = self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "waystone-ui-e2e-test", "version": "1.0"},
            },
        )
        assert "result" in init_res, f"initialize failed: {init_res}"
        self._notify("notifications/initialized", {})

    def _notify(self, method: str, params: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict) -> dict:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        req_id = self._next_id
        self._next_id += 1
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n"
        )
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr = self.proc.stderr.read() if self.proc.stderr else ""
                raise RuntimeError(f"MCP server closed stdout unexpectedly: {stderr}")
            msg = json.loads(line)
            if msg.get("id") == req_id:
                return msg

    def call_tool(self, name: str, arguments: dict) -> tuple[bool, str]:
        resp = self._request("tools/call", {"name": name, "arguments": arguments})
        if "error" in resp:
            return True, json.dumps(resp["error"])
        result = resp["result"]
        is_error = bool(result.get("isError"))
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return is_error, "\n".join(texts)

    def close(self) -> None:
        if self.proc.poll() is None:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()


class OrganizerMcpLiveUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(["organizer-mcp-live-ui-tests"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.data = json.loads((HERE / "fixture-library.json").read_bytes())
        self.data["catalog_kind"] = "playnite-display-selected-copy-v1"
        self.catalog = self.base / "catalog.json"
        self.catalog.write_text(json.dumps(self.data), encoding="utf-8")
        self.ids = [g["id"] for g in self.data["games"]]

    def _wait_for(self, check, msg: str) -> None:
        for _ in range(100):
            if check():
                return
            QTest.qWait(40)
        self.fail(msg)

    def test_mcp_stdio_metadata_and_group_edits_live_refresh_conflict_and_undo(self):
        org_db = self.base / "organizer.sqlite"
        link_db = self.base / "links.sqlite"
        OrganizerStore.create(org_db, self.ids)
        CatalogLinkStore.create(link_db, self.ids)

        engine, model, root, warnings = create_session(
            self.app,
            headless=True,
            catalog=self.catalog,
            organizer_state=org_db,
            link_state=link_db,
            provider_jobs_enabled=False,
        )

        def cleanup_ui():
            engine.live_updates.stop()
            root.close()
            root.deleteLater()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()

        self.addCleanup(cleanup_ui)

        self.assertEqual(warnings, [])
        original_title = model.overview(self.ids[0])["title"]
        root.setProperty("selectedId", self.ids[1])

        mcp = _McpStdioClient(org_db, link_db)
        self.addCleanup(mcp.close)

        # 1. External MCP metadata edit updates open UI without restart
        err, out = mcp.call_tool(
            "waystone_update_metadata",
            {
                "canonical_id": self.ids[0],
                "changes": {
                    "title": "MCP Live Edited Title",
                    "platform": "Nintendo Switch",
                    "tags": ["MCP Verified"],
                },
                "request_id": "mcp-meta-1",
                "expected_revision": 0,
            },
        )
        self.assertFalse(err, out)
        meta_res = json.loads(out)
        meta_audit_id = meta_res["audit_id"]

        self._wait_for(lambda: model._organizer_revision == 1, "UI did not refresh after MCP metadata edit")
        self.assertEqual(model.overview(self.ids[0])["title"], "MCP Live Edited Title")
        self.assertEqual(model.overview(self.ids[0])["platforms"], "Nintendo Switch")
        self.assertIn("MCP Verified", model.overview(self.ids[0])["tags"])
        self.assertIn("Nintendo Switch", engine.rootContext().contextProperty("platformOptions"))

        # 2. Stale-revision metadata write is rejected without mutating UI
        err_stale, out_stale = mcp.call_tool(
            "waystone_update_metadata",
            {
                "canonical_id": self.ids[0],
                "changes": {"title": "Stale Overwrite"},
                "request_id": "mcp-meta-stale",
                "expected_revision": 0,
            },
        )
        self.assertTrue(err_stale)
        self.assertIn("Stale revision", out_stale)
        self.assertEqual(model.overview(self.ids[0])["title"], "MCP Live Edited Title")

        # 3. External MCP duplicate group edit updates open UI without restart
        err_grp, out_grp = mcp.call_tool(
            "waystone_apply_duplicates",
            {
                "groups": [{"canonical_ids": self.ids[:2], "representative_id": self.ids[0]}],
                "request_id": "mcp-grp-1",
                "expected_revision": 0,
            },
        )
        self.assertFalse(err_grp, out_grp)
        grp_audit_id = json.loads(out_grp)["audit_id"]

        self._wait_for(
            lambda: len(model.games) == len(self.ids) - 1,
            "UI did not refresh after MCP duplicate group edit",
        )
        self.assertEqual(root.property("selectedId"), self.ids[0])
        self.assertEqual(root.property("selectedGame")["title"], "MCP Live Edited Title")

        # 4. Stale-revision duplicate group write is rejected
        err_grp_stale, out_grp_stale = mcp.call_tool(
            "waystone_apply_duplicates",
            {
                "groups": [{"canonical_ids": self.ids[:2], "representative_id": self.ids[1]}],
                "request_id": "mcp-grp-stale",
                "expected_revision": 0,
            },
        )
        self.assertTrue(err_grp_stale)
        self.assertIn("Stale link revision", out_grp_stale)

        # 5. Undo metadata edit via MCP updates open UI without restart
        err_undo_meta, out_undo_meta = mcp.call_tool(
            "waystone_undo",
            {"store_type": "organizer", "audit_id": meta_audit_id, "request_id": "mcp-undo-meta-1"},
        )
        self.assertFalse(err_undo_meta, out_undo_meta)
        self._wait_for(lambda: model._organizer_revision == 2, "UI did not refresh after MCP metadata undo")
        self.assertEqual(model.overview(self.ids[0])["title"], original_title)

        # 6. Undo group edit via MCP updates open UI without restart
        err_undo_grp, out_undo_grp = mcp.call_tool(
            "waystone_undo",
            {"store_type": "catalog_link", "audit_id": grp_audit_id, "request_id": "mcp-undo-grp-1"},
        )
        self.assertFalse(err_undo_grp, out_undo_grp)
        self._wait_for(
            lambda: len(model.games) == len(self.ids),
            "UI did not refresh after MCP duplicate group undo",
        )

        # 7. Clean teardown stops live_updates watcher timer
        engine.live_updates.stop()
        self.assertFalse(engine.live_updates._timer.isActive())


if __name__ == "__main__":
    unittest.main()
