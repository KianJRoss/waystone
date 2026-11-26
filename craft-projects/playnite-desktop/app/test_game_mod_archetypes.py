"""Unit tests for Game Mod Archetype Classifier, Type 1 Heuristic Routing, and FOMOD Parser."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from native_services.game_mod_archetypes import (
    ARCHETYPE_SPECS,
    GameArchetypeDetector,
    GameModArchetype,
    ModArchiveClassifier,
)


class TestGameModArchetypes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detect_archetypes_by_id_and_title(self):
        # Skyrim
        info_skyrim = GameArchetypeDetector.detect_archetype(game_id="steam:489830", game_title="Skyrim Special Edition")
        self.assertEqual(info_skyrim.archetype, GameModArchetype.BETHESDA_CREATION)
        self.assertIn("dinput8", info_skyrim.wine_dll_overrides)
        self.assertEqual(info_skyrim.staging_subpath, "Data")

        # Cyberpunk
        info_cp = GameArchetypeDetector.detect_archetype(game_id="steam:1091500", game_title="Cyberpunk 2077")
        self.assertEqual(info_cp.archetype, GameModArchetype.REDENGINE_CYBERPUNK)
        self.assertIn("version", info_cp.wine_dll_overrides)

        # Satisfactory
        info_sat = GameArchetypeDetector.detect_archetype(game_id="steam:526870", game_title="Satisfactory")
        self.assertEqual(info_sat.archetype, GameModArchetype.UNREAL_SML)
        self.assertIn("FactoryGame/Mods", info_sat.staging_subpath)

        # Red Dead Redemption 2
        info_rdr = GameArchetypeDetector.detect_archetype(game_id="epic:Heather", game_title="Red Dead Redemption 2")
        self.assertEqual(info_rdr.archetype, GameModArchetype.ROCKSTAR_RAGE)
        self.assertIn("dinput8", info_rdr.wine_dll_overrides)

        # Minecraft
        info_mc = GameArchetypeDetector.detect_archetype(game_id="prism:mc_1.20", game_title="Minecraft")
        self.assertEqual(info_mc.archetype, GameModArchetype.MINECRAFT_JAVA)

        # Terraria
        info_terraria = GameArchetypeDetector.detect_archetype(game_id="steam:105600", game_title="Terraria")
        self.assertEqual(info_terraria.archetype, GameModArchetype.TERRARIA_TMOD)

    def test_detect_archetypes_by_filesystem_sentinels(self):
        # Cyberpunk folder
        cp_dir = self.root / "cp_game"
        (cp_dir / "bin" / "x64").mkdir(parents=True)
        (cp_dir / "bin" / "x64" / "Cyberpunk2077.exe").write_bytes(b"exe")
        info = GameArchetypeDetector.detect_archetype(game_path=cp_dir)
        self.assertEqual(info.archetype, GameModArchetype.REDENGINE_CYBERPUNK)

        # Skyrim folder
        skyrim_dir = self.root / "skyrim_game"
        (skyrim_dir / "Data").mkdir(parents=True)
        (skyrim_dir / "SkyrimSE.exe").write_bytes(b"exe")
        (skyrim_dir / "Data" / "Skyrim.esm").write_bytes(b"esm")
        info_s = GameArchetypeDetector.detect_archetype(game_path=skyrim_dir)
        self.assertEqual(info_s.archetype, GameModArchetype.BETHESDA_CREATION)

    def test_cyberpunk_mod_archive_routing(self):
        # Create a mock zip with wrapper folder and Cyberpunk components
        zip_path = self.root / "CyberpunkMod-v1.2.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("CyberpunkMod-v1.2/r6/scripts/MyFeature/mod.reds", "redscript code")
            z.writestr("CyberpunkMod-v1.2/archive/pc/mod/textures.archive", "archive bytes")
            z.writestr("CyberpunkMod-v1.2/archive/pc/mod/textures.xl", "xl descriptor")
            z.writestr("CyberpunkMod-v1.2/bin/x64/plugins/cyber_engine_tweaks/mods/MyFeature/init.lua", "lua code")

        plan = ModArchiveClassifier.inspect_and_route(zip_path, archetype=GameModArchetype.REDENGINE_CYBERPUNK)
        self.assertEqual(plan.archetype, GameModArchetype.REDENGINE_CYBERPUNK)
        self.assertEqual(plan.stripped_prefix, "CyberpunkMod-v1.2/")
        self.assertIn("version", plan.wine_dll_overrides)

        destinations = [dest for _, dest in plan.file_mappings]
        self.assertIn("r6/scripts/MyFeature/mod.reds", destinations)
        self.assertIn("archive/pc/mod/textures.archive", destinations)
        self.assertIn("archive/pc/mod/textures.xl", destinations)
        self.assertIn("bin/x64/plugins/cyber_engine_tweaks/mods/MyFeature/init.lua", destinations)

    def test_skyrim_mod_archive_routing_and_fomod_detection(self):
        zip_path = self.root / "ImmersiveArmors.zip"
        fomod_xml = """<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
            <moduleName>Immersive Armors</moduleName>
            <installStep name="Resolution">
                <optionalFileGroups>
                    <group name="Texture Quality" type="SelectExactOne">
                        <plugins>
                            <plugin name="2K Textures"><description>High resolution textures</description></plugin>
                            <plugin name="4K Textures"><description>Ultra resolution textures</description></plugin>
                        </plugins>
                    </group>
                </optionalFileGroups>
            </installStep>
        </config>"""

        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("ImmersiveArmors/meshes/armor.nif", "mesh bytes")
            z.writestr("ImmersiveArmors/textures/armor.dds", "texture bytes")
            z.writestr("ImmersiveArmors/ImmersiveArmors.esp", "plugin bytes")
            z.writestr("ImmersiveArmors/fomod/ModuleConfig.xml", fomod_xml)

        plan = ModArchiveClassifier.inspect_and_route(zip_path, archetype=GameModArchetype.BETHESDA_CREATION)
        self.assertEqual(plan.archetype, GameModArchetype.BETHESDA_CREATION)
        self.assertTrue(plan.has_fomod)
        self.assertTrue(plan.requires_agent_decision)
        self.assertEqual(len(plan.fomod_groups), 1)
        self.assertEqual(plan.fomod_groups[0]["group_name"], "Texture Quality")
        self.assertEqual(len(plan.fomod_groups[0]["options"]), 2)

        destinations = [dest for _, dest in plan.file_mappings]
        self.assertIn("Data/meshes/armor.nif", destinations)
        self.assertIn("Data/textures/armor.dds", destinations)
        self.assertIn("Data/ImmersiveArmors.esp", destinations)

    def test_satisfactory_mod_archive_routing(self):
        zip_path = self.root / "RefinedPower.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("RefinedPower.uplugin", json.dumps({"Name": "Refined Power", "Version": "3.2.0"}))
            z.writestr("Content/Paks/RefinedPower-WindowsShipping.pak", "pak bytes")

        plan = ModArchiveClassifier.inspect_and_route(zip_path, archetype=GameModArchetype.UNREAL_SML, mod_name_hint="RefinedPower")
        self.assertEqual(plan.archetype, GameModArchetype.UNREAL_SML)
        destinations = [dest for _, dest in plan.file_mappings]
        self.assertIn("FactoryGame/Mods/RefinedPower/RefinedPower.uplugin", destinations)
        self.assertIn("FactoryGame/Mods/RefinedPower/Content/Paks/RefinedPower-WindowsShipping.pak", destinations)

    def test_rdr2_mod_archive_routing(self):
        zip_path = self.root / "RampageTrainer.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("Rampage.asi", "asi bytes")
            z.writestr("RampageFiles/config.ini", "ini bytes")
            z.writestr("lml/stream/horse.ytd", "texture bytes")

        plan = ModArchiveClassifier.inspect_and_route(zip_path, archetype=GameModArchetype.ROCKSTAR_RAGE)
        destinations = [dest for _, dest in plan.file_mappings]
        self.assertIn("Rampage.asi", destinations)
        self.assertIn("lml/stream/horse.ytd", destinations)


if __name__ == "__main__":
    unittest.main()
