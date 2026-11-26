"""Tests for Universal AI-First Control MCP Tools across Mods, Settings, Controllers, and Logs."""
from __future__ import annotations

import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from native_services.catalog_link_store import CatalogLinkStore
from native_services.organizer_control import OrganizerControl
from native_services.organizer_store import OrganizerStore
from tools import waystone_mcp


class TestWaystoneMcpUniversalTools(unittest.IsolatedAsyncioTestCase):
    """Verify newly registered Universal AI-First tools in MCPServer."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self._home_patch = patch.object(Path, 'home', return_value=self.tmp_dir)
        self._home_patch.start()
        self.org_db = self.tmp_dir / "organizer.sqlite"
        self.link_db = self.tmp_dir / "catalog_links.sqlite"
        self.org_store = OrganizerStore.create(self.org_db, ["g1"])
        self.link_store = CatalogLinkStore.create(self.link_db, ["g1"])
        self.control = OrganizerControl(
            organizer_store=self.org_store,
            catalog_link_store=self.link_store,
        )
        from test_waystone_mcp_virtual_pad import OutputClient
        self.output_client = OutputClient()
        self.app = waystone_mcp.create_mcp_app(self.control, controller_client=self.output_client)

    def tearDown(self) -> None:
        self._home_patch.stop()
        self._tmp.cleanup()

    async def test_settings_tools(self) -> None:
        res = await self.app.call_tool("waystone_get_settings", {})
        self.assertFalse(res.is_error)
        data = json.loads(res.content[0].text)
        self.assertIn("general", data)
        self.assertIn("library", data)
        self.assertIn("controllers", data)

        cat_res = await self.app.call_tool("waystone_get_settings", {"category": "general"})
        self.assertFalse(cat_res.is_error)
        cat_data = json.loads(cat_res.content[0].text)
        self.assertIn("theme", cat_data)

        up_res = await self.app.call_tool(
            "waystone_update_settings",
            {"category": "general", "settings": {"theme": "light", "custom_test": True}},
        )
        self.assertFalse(up_res.is_error)
        up_data = json.loads(up_res.content[0].text)
        self.assertEqual(up_data["category"], "general")
        self.assertEqual(up_data["updated_settings"]["theme"], "light")

    async def test_controller_tools(self) -> None:
        # Controller status
        res = await self.app.call_tool("waystone_get_controller_status", {})
        self.assertFalse(res.is_error)
        status_data = json.loads(res.content[0].text)
        self.assertIn("running", status_data)

        # Virtual gamepad profile
        vp_res = await self.app.call_tool(
            "waystone_set_virtual_gamepad_profile",
            {"driver_type": "dualshock4"},
        )
        self.assertFalse(vp_res.is_error)
        vp_data = json.loads(vp_res.content[0].text)
        self.assertEqual(vp_data["virtual_gamepad_driver"], "dualshock4")

        # Deadband configuration
        db_res = await self.app.call_tool(
            "waystone_set_controller_deadband",
            {"deadband": 0.12},
        )
        self.assertFalse(db_res.is_error)
        db_data = json.loads(db_res.content[0].text)
        self.assertEqual(db_data["deadband"], 0.12)

    async def test_mod_instance_and_log_tools(self) -> None:
        # Create instance
        create_res = await self.app.call_tool(
            "waystone_create_mod_instance",
            {
                "game_id": "test_game_mcp",
                "name": "Prism Test Pack",
                "isolate_saves": True,
            },
        )
        self.assertFalse(create_res.is_error)
        create_data = json.loads(create_res.content[0].text)
        inst_id = create_data["profile_id"]
        self.assertEqual(create_data["display_name"], "Prism Test Pack")
        self.assertTrue(create_data["isolate_saves"])

        # List instances
        list_res = await self.app.call_tool(
            "waystone_list_mod_instances",
            {"game_id": "test_game_mcp"},
        )
        self.assertFalse(list_res.is_error)
        list_data = json.loads(list_res.content[0].text)
        self.assertTrue(any(p["profile_id"] == inst_id for p in list_data))

        # Conflict matrix
        conf_res = await self.app.call_tool(
            "waystone_detect_mod_conflicts",
            {"instance_id": inst_id},
        )
        self.assertFalse(conf_res.is_error)
        conf_data = json.loads(conf_res.content[0].text)
        self.assertIn("has_conflicts", conf_data)

        # Clone instance
        clone_res = await self.app.call_tool(
            "waystone_clone_mod_instance",
            {"instance_id": inst_id, "new_name": "Prism Test Pack Clone"},
        )
        self.assertFalse(clone_res.is_error)
        clone_data = json.loads(clone_res.content[0].text)
        clone_id = clone_data["profile_id"]
        self.assertEqual(clone_data["display_name"], "Prism Test Pack Clone")

        # Delete instances
        del1 = await self.app.call_tool("waystone_delete_mod_instance", {"instance_id": inst_id})
        del2 = await self.app.call_tool("waystone_delete_mod_instance", {"instance_id": clone_id})
        self.assertFalse(del1.is_error)
        self.assertFalse(del2.is_error)

        # Diagnose logs
        log_file = self.tmp_dir / "crash.log"
        log_file.write_text(
            "[Info : Unity] Loading level 1\n"
            "[Debug : Engine] Initializing renderer\n"
            "NullReferenceException: Object reference not set to an instance of an object.\n"
            "  at BepInEx.Plugin.Start () [0x00010] in <filename unknown>:0\n"
        )
        diag_res = await self.app.call_tool(
            "waystone_diagnose_logs",
            {"search_path": str(log_file)},
        )
        self.assertFalse(diag_res.is_error)
        diag_data = json.loads(diag_res.content[0].text)
        self.assertFalse(diag_data["healthy"])
        self.assertGreaterEqual(diag_data["raw_error_count"], 1)
        self.assertGreaterEqual(diag_data["filtered_line_count"], 2)

    async def test_archetype_tools(self) -> None:
        # 1. Get supported archetypes
        sup_res = await self.app.call_tool("waystone_get_supported_modding_archetypes", {})
        self.assertFalse(sup_res.is_error)
        sup_data = json.loads(sup_res.content[0].text)
        self.assertIn("bethesda_creation", sup_data)
        self.assertIn("redengine_cyberpunk", sup_data)
        self.assertIn("unreal_sml", sup_data)
        self.assertIn("rockstar_rage", sup_data)
        self.assertIn("minecraft_java", sup_data)
        self.assertIn("terraria_tmod", sup_data)

        # 2. Detect archetype for Cyberpunk
        det_res = await self.app.call_tool(
            "waystone_detect_game_mod_archetype",
            {"game_id": "steam:1091500", "game_title": "Cyberpunk 2077"}
        )
        self.assertFalse(det_res.is_error)
        det_data = json.loads(det_res.content[0].text)
        self.assertEqual(det_data["archetype"], "redengine_cyberpunk")
        self.assertIn("version", det_data["wine_dll_overrides"])

        # 3. Analyze mod archive for Skyrim
        import zipfile
        zip_path = self.tmp_dir / "test_skyrim_mod.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("MySkyrimMod/textures/diffuse.dds", "dds")
            z.writestr("MySkyrimMod/MyMod.esp", "esp")

        ana_res = await self.app.call_tool(
            "waystone_analyze_mod_archive",
            {"archive_path": str(zip_path), "game_title": "Skyrim Special Edition"}
        )
        self.assertFalse(ana_res.is_error)
        ana_data = json.loads(ana_res.content[0].text)
        self.assertEqual(ana_data["archetype"], "bethesda_creation")
        self.assertEqual(ana_data["stripped_prefix"], "MySkyrimMod/")
        destinations = [dest for _, dest in ana_data["file_mappings"]]
        self.assertIn("Data/textures/diffuse.dds", destinations)
        self.assertIn("Data/MyMod.esp", destinations)

    async def test_deep_mod_infrastructure_tools(self) -> None:
        # 1. Skyrim Load Order tool
        sample_txt = """*Update.esm
*Skyrim.esm
*Unofficial Skyrim Special Edition Patch.esp
*Alternate Start.esp
"""
        sky_res = await self.app.call_tool(
            "waystone_sort_skyrim_load_order",
            {"plugins_txt_content": sample_txt},
        )
        self.assertFalse(sky_res.is_error)
        sky_data = json.loads(sky_res.content[0].text)
        self.assertEqual(sky_data["status"], "success")
        self.assertEqual(sky_data["order"][0].lower(), "skyrim.esm")
        self.assertEqual(sky_data["order"][1].lower(), "update.esm")
        self.assertIn("*Skyrim.esm", sky_data["sorted_plugins_txt"])

        # 2. Satisfactory Resolver tool
        sat_dir = self.tmp_dir / "FactoryGameMods"
        sat_dir.mkdir()
        sml_p = sat_dir / "SML"
        sml_p.mkdir()
        (sml_p / "SML.uplugin").write_text(json.dumps({"SemVersion": "3.8.2"}))
        mod_p = sat_dir / "TestMod"
        mod_p.mkdir()
        (mod_p / "TestMod.uplugin").write_text(json.dumps({
            "SemVersion": "1.0.0",
            "Plugins": [{"Name": "SML", "SemVersion": ">=3.8.0", "Enabled": True}]
        }))
        sat_res = await self.app.call_tool(
            "waystone_resolve_satisfactory_plugins",
            {"mods_dir": str(sat_dir)},
        )
        self.assertFalse(sat_res.is_error)
        sat_data = json.loads(sat_res.content[0].text)
        self.assertTrue(sat_data["valid"])
        self.assertIn("SML", sat_data["load_order"])
        self.assertIn("TestMod", sat_data["load_order"])

        # 3. RDR2 LML Stager tool
        rdr_res = await self.app.call_tool(
            "waystone_stage_rdr2_lml_package",
            {
                "archive_file_list": ["install.xml", "stream/hat.ydr", "ScriptHookRDR2.ini"],
                "install_xml_content": "<Package><Name>HatPack</Name><Author>Arthur</Author></Package>"
            },
        )
        self.assertFalse(rdr_res.is_error)
        rdr_data = json.loads(rdr_res.content[0].text)
        self.assertEqual(rdr_data["package_info"]["name"], "HatPack")
        self.assertEqual(rdr_data["routes"]["stream/hat.ydr"], "lml/stream/hat.ydr")
        self.assertIn("WINEDLLOVERRIDES", rdr_data["proton_environment"])
