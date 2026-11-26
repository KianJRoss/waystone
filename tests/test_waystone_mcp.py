"""Focused offline unit and stdio integration tests for Organizer MCP conflict UX."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from native_services.catalog_link_store import CatalogLinkStore
from native_services.organizer_control import ConcurrencyConflict, ControlError, OrganizerControl
from native_services.organizer_store import OrganizerStore
from tools import waystone_mcp


class TestWaystoneMcpConflictUx(unittest.IsolatedAsyncioTestCase):
    """Verify ToolError mapping, path/exception sanitization, and real stdio conflict responses."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.org_db = self.tmp_dir / "organizer.sqlite"
        self.link_db = self.tmp_dir / "catalog_links.sqlite"
        self.org_store = OrganizerStore.create(self.org_db, ["g1", "g2", "g3"])
        self.link_store = CatalogLinkStore.create(self.link_db, ["g1", "g2", "g3"])
        self.control = OrganizerControl(
            organizer_store=self.org_store,
            catalog_link_store=self.link_store,
        )
        self.app = waystone_mcp.create_mcp_app(self.control)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def test_stale_revision_update_metadata_raises_actionable_tool_error(self) -> None:
        res1 = await self.app.call_tool(
            "waystone_update_metadata",
            {
                "canonical_id": "g1",
                "changes": {"tags": ["co-op"]},
                "request_id": "req-1",
                "expected_revision": 0,
            },
        )
        self.assertFalse(res1.is_error)

        with self.assertRaises(ToolError) as ctx:
            await self.app.call_tool(
                "waystone_update_metadata",
                {
                    "canonical_id": "g1",
                    "changes": {"tags": ["rpg"]},
                    "request_id": "req-2",
                    "expected_revision": 0,
                },
            )
        msg = str(ctx.exception)
        self.assertIn("Conflict:", msg)
        self.assertIn("Stale revision", msg)
        self.assertIn("expected revision 0", msg)
        self.assertIn("current database revision is 1", msg)
        self.assertIn("Refresh state", msg)
        self.assertNotIn(str(self.tmp_dir), msg)

    async def test_stale_revision_batch_and_duplicates_raise_tool_error_without_mutation(self) -> None:
        await self.app.call_tool(
            "waystone_update_metadata",
            {
                "canonical_id": "g1",
                "changes": {"tags": ["initial"]},
                "request_id": "req-init",
                "expected_revision": 0,
            },
        )
        with self.assertRaises(ToolError) as batch_ctx:
            await self.app.call_tool(
                "waystone_batch_update_metadata",
                {
                    "edits": [{"game_id": "g2", "changes": {"tags": ["stale"]}}],
                    "request_id": "req-batch-stale",
                    "expected_revision": 0,
                },
            )
        batch_text = str(batch_ctx.exception)
        self.assertIn("Conflict:", batch_text)
        self.assertIn("current database revision is 1", batch_text)
        self.assertNotIn("g2", self.org_store.snapshot()["overrides"])

        ok_dup = await self.app.call_tool(
            "waystone_apply_duplicates",
            {
                "groups": [{"canonical_ids": ["g1", "g2"], "representative_id": "g1"}],
                "request_id": "dup-1",
                "expected_revision": 0,
            },
        )
        self.assertFalse(ok_dup.is_error)
        with self.assertRaises(ToolError) as dup_ctx:
            await self.app.call_tool(
                "waystone_apply_duplicates",
                {
                    "groups": [{"canonical_ids": ["g1", "g3"], "representative_id": "g1"}],
                    "request_id": "dup-2",
                    "expected_revision": 0,
                },
            )
        dup_text = str(dup_ctx.exception)
        self.assertIn("Conflict:", dup_text)
        self.assertIn("Stale link revision", dup_text)
        self.assertIn("current database revision is 1", dup_text)

    async def test_control_error_and_internal_error_sanitize_paths_and_secrets(self) -> None:
        with self.assertRaises(ToolError) as missing_ctx:
            await self.app.call_tool("waystone_get_game", {"canonical_id": "missing-id"})
        self.assertIn("Control error:", str(missing_ctx.exception))
        self.assertIn("missing-id", str(missing_ctx.exception))

        with patch.object(
            self.control,
            "get_game",
            side_effect=ControlError(f"Failed reading {self.tmp_dir}/secret.sqlite"),
        ):
            with self.assertRaises(ToolError) as path_ctx:
                await self.app.call_tool("waystone_get_game", {"canonical_id": "g1"})
        self.assertIn("Control error: Invalid organizer request or operation rejected.", str(path_ctx.exception))
        self.assertNotIn(str(self.tmp_dir), str(path_ctx.exception))

        with patch.object(
            self.control,
            "get_game",
            side_effect=ControlError("wrapped private non-path secret-note-123"),
        ):
            with self.assertRaises(ToolError) as nonpath_ctx:
                await self.app.call_tool("waystone_get_game", {"canonical_id": "g1"})
        self.assertIn("Control error: Invalid organizer request or operation rejected.", str(nonpath_ctx.exception))
        self.assertNotIn("secret-note-123", str(nonpath_ctx.exception))

        with patch.object(
            self.control,
            "update_metadata",
            side_effect=ConcurrencyConflict("private-conflict-detail"),
        ):
            with self.assertRaises(ToolError) as conflict_ctx:
                await self.app.call_tool(
                    "waystone_update_metadata",
                    {"canonical_id": "g1", "changes": {"tags": ["x"]}, "request_id": "r1"},
                )
        self.assertIn("Conflict: Organizer state changed concurrently. Refresh state and retry with a new request ID.", str(conflict_ctx.exception))
        self.assertNotIn("private-conflict-detail", str(conflict_ctx.exception))

        with patch.object(
            self.control,
            "list_games",
            side_effect=RuntimeError("raw sqlite leak secret-token /home/testuser/private.db"),
        ):
            with self.assertRaises(ToolError) as internal_ctx:
                await self.app.call_tool("waystone_list_games", {})
        text = str(internal_ctx.exception)
        self.assertIn("Organizer operation failed due to an internal error.", text)
        self.assertNotIn("secret-token", text)
        self.assertNotIn("RuntimeError", text)
        self.assertNotIn("/home/testuser", text)

    async def test_real_stdio_transport_exposes_actionable_stale_revision_conflict(self) -> None:
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[
                str(REPO_ROOT / "tools" / "waystone_mcp.py"),
                "--organizer-db",
                str(self.org_db),
                "--catalog-link-db",
                str(self.link_db),
            ],
            cwd=str(REPO_ROOT),
        )
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_resp = await session.list_tools()
                names = {t.name for t in tools_resp.tools}
                self.assertIn("waystone_update_metadata", names)
                self.assertIn("waystone_apply_duplicates", names)

                ok_call = await session.call_tool(
                    "waystone_update_metadata",
                    {
                        "canonical_id": "g1",
                        "changes": {"tags": ["stdio-ok"]},
                        "request_id": "stdio-1",
                        "expected_revision": 0,
                    },
                )
                self.assertFalse(ok_call.is_error)

                stale_call = await session.call_tool(
                    "waystone_update_metadata",
                    {
                        "canonical_id": "g1",
                        "changes": {"tags": ["stdio-stale"]},
                        "request_id": "stdio-2",
                        "expected_revision": 0,
                    },
                )
                self.assertTrue(stale_call.is_error)
                err_text = stale_call.content[0].text
                self.assertIn("Conflict:", err_text)
                self.assertIn("Stale revision", err_text)
                self.assertIn("expected revision 0", err_text)
                self.assertIn("current database revision is 1", err_text)
                self.assertIn("Refresh state before applying changes", err_text)
                self.assertNotIn(str(self.tmp_dir), err_text)
                self.assertNotIn("Traceback", err_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
