"""Game Modding Archetypes, Type 1 Fast Heuristics & Deterministic Router.

Provides:
- Fast Type 1 static classification of game engines and modding frameworks.
- Automated package inspection, wrapper directory stripping, and destination routing.
- FOMOD / complex installer detection and agent escalation schemas.
- Deep support for Skyrim, Cyberpunk 2077, Satisfactory, Red Dead Redemption 2, Minecraft, and Terraria.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
import os
from pathlib import Path
import re
import tarfile
from typing import Any, Dict, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET
import zipfile


class GameModArchetype(str, Enum):
    BETHESDA_CREATION = "bethesda_creation"     # Skyrim SE/AE/VR, Fallout 4, Starfield
    REDENGINE_CYBERPUNK = "redengine_cyberpunk" # Cyberpunk 2077, Witcher 3
    UNREAL_SML = "unreal_sml"                   # Satisfactory (SML v3+)
    ROCKSTAR_RAGE = "rockstar_rage"             # Red Dead Redemption 2, GTA V
    MINECRAFT_JAVA = "minecraft_java"           # Minecraft Java (Fabric/Forge/NeoForge/Quilt)
    TERRARIA_TMOD = "terraria_tmod"             # Terraria (tModLoader)
    UNITY_BEPINEX = "unity_bepinex"             # Valheim, Lethal Company, RoR2, DSP
    GENERIC = "generic"                         # Fallback loose-file modding


@dataclass
class GameArchetypeInfo:
    archetype: GameModArchetype
    display_name: str
    primary_extender: str
    staging_subpath: str
    save_isolation_subpath: str
    wine_dll_overrides: Dict[str, str] = field(default_factory=dict)
    log_globs: List[str] = field(default_factory=list)
    sentinel_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['archetype'] = self.archetype.value
        return d


ARCHETYPE_SPECS: Dict[GameModArchetype, GameArchetypeInfo] = {
    GameModArchetype.BETHESDA_CREATION: GameArchetypeInfo(
        archetype=GameModArchetype.BETHESDA_CREATION,
        display_name="Bethesda Creation Engine (Skyrim / Fallout)",
        primary_extender="SKSE64 / F4SE + Address Library",
        staging_subpath="Data",
        save_isolation_subpath="saves",
        wine_dll_overrides={"dinput8": "n,b", "bink2w64": "n,b"},
        log_globs=["SKSE/*.log", "F4SE/*.log", "crash-*.log"],
        sentinel_files=["SkyrimSE.exe", "SkyrimVR.exe", "Fallout4.exe", "TESV.exe", "Data/Skyrim.esm"],
    ),
    GameModArchetype.REDENGINE_CYBERPUNK: GameArchetypeInfo(
        archetype=GameModArchetype.REDENGINE_CYBERPUNK,
        display_name="CDPR REDengine 4 (Cyberpunk 2077)",
        primary_extender="Cyber Engine Tweaks (CET), REDscript, ArchiveXL, TweakXL",
        staging_subpath="",
        save_isolation_subpath="saves",
        wine_dll_overrides={"version": "n,b", "winmm": "n,b"},
        log_globs=["bin/x64/plugins/cyber_engine_tweaks/cyber_engine_tweaks.log", "r6/logs/*.log"],
        sentinel_files=["bin/x64/Cyberpunk2077.exe", "r6/config/", "archive/pc/content/"],
    ),
    GameModArchetype.UNREAL_SML: GameArchetypeInfo(
        archetype=GameModArchetype.UNREAL_SML,
        display_name="Unreal Engine 5 (Satisfactory Mod Loader)",
        primary_extender="SML (Satisfactory Mod Loader v3.x)",
        staging_subpath="FactoryGame/Mods",
        save_isolation_subpath="Saved/SaveGames",
        wine_dll_overrides={"msdia140": "n,b", "xinput1_3": "n,b"},
        log_globs=["FactoryGame/Saved/Logs/*.log", "Saved/Logs/*.log"],
        sentinel_files=["FactoryGameSteam.exe", "FactoryGame.exe", "FactoryGame/Binaries/Win64/FactoryGame-Win64-Shipping.exe"],
    ),
    GameModArchetype.ROCKSTAR_RAGE: GameArchetypeInfo(
        archetype=GameModArchetype.ROCKSTAR_RAGE,
        display_name="Rockstar RAGE Engine (Red Dead Redemption 2)",
        primary_extender="Script Hook RDR2 + Lenny's Mod Loader (LML)",
        staging_subpath="",
        save_isolation_subpath="saves",
        wine_dll_overrides={"dinput8": "n,b", "version": "n,b"},
        log_globs=["ScriptHookRDR2.log", "vfs.log"],
        sentinel_files=["RDR2.exe", "PlayRDR2.exe", "GTA5.exe"],
    ),
    GameModArchetype.MINECRAFT_JAVA: GameArchetypeInfo(
        archetype=GameModArchetype.MINECRAFT_JAVA,
        display_name="Minecraft Java Edition",
        primary_extender="Fabric / Forge / NeoForge / Quilt",
        staging_subpath=".minecraft/mods",
        save_isolation_subpath=".minecraft/saves",
        wine_dll_overrides={},
        log_globs=[".minecraft/logs/latest.log", ".minecraft/crash-reports/*.txt"],
        sentinel_files=["instance.cfg", "mmc-pack.json", ".minecraft/mods/"],
    ),
    GameModArchetype.TERRARIA_TMOD: GameArchetypeInfo(
        archetype=GameModArchetype.TERRARIA_TMOD,
        display_name="Terraria (tModLoader)",
        primary_extender="tModLoader (64-bit CoreCLR)",
        staging_subpath="Mods",
        save_isolation_subpath="Players",
        wine_dll_overrides={},
        log_globs=["tModLoader-Logs/*.log", "Logs/*.log"],
        sentinel_files=["tModLoader.dll", "Terraria.exe", "tModLoader.exe"],
    ),
    GameModArchetype.UNITY_BEPINEX: GameArchetypeInfo(
        archetype=GameModArchetype.UNITY_BEPINEX,
        display_name="Unity Engine (BepInEx)",
        primary_extender="BepInEx 5 / 6 + Doorstop",
        staging_subpath="BepInEx/plugins",
        save_isolation_subpath="saves",
        wine_dll_overrides={"winhttp": "n,b"},
        log_globs=["BepInEx/LogOutput.log", "Player.log"],
        sentinel_files=["UnityPlayer.dll", "doorstop_config.ini", "BepInEx/core/BepInEx.Preloader.dll"],
    ),
    GameModArchetype.GENERIC: GameArchetypeInfo(
        archetype=GameModArchetype.GENERIC,
        display_name="Generic PC Game",
        primary_extender="Standard Loose File Replacement",
        staging_subpath="",
        save_isolation_subpath="saves",
        wine_dll_overrides={},
        log_globs=["*.log"],
        sentinel_files=[],
    ),
}


class GameArchetypeDetector:
    """Type 1 Fast Heuristic Detector for Game Modding Archetypes."""

    @classmethod
    def detect_archetype(
        cls,
        game_path: Optional[Path | str] = None,
        game_id: str = "",
        game_title: str = "",
    ) -> GameArchetypeInfo:
        """Rapidly classify a game into its modding archetype."""
        id_lower = (game_id or "").lower()
        title_lower = (game_title or "").lower()

        # Fast path 1: Recognized title & game_id tokens
        if any(t in id_lower or t in title_lower for t in ["skyrim", "tesv", "fallout4", "starfield", "oblivion"]):
            return ARCHETYPE_SPECS[GameModArchetype.BETHESDA_CREATION]
        if any(t in id_lower or t in title_lower for t in ["cyberpunk", "witcher3", "cp2077"]):
            return ARCHETYPE_SPECS[GameModArchetype.REDENGINE_CYBERPUNK]
        if any(t in id_lower or t in title_lower for t in ["satisfactory"]):
            return ARCHETYPE_SPECS[GameModArchetype.UNREAL_SML]
        if any(t in id_lower or t in title_lower for t in ["red dead", "rdr2", "gta5", "gtav"]):
            return ARCHETYPE_SPECS[GameModArchetype.ROCKSTAR_RAGE]
        if any(t in id_lower or t in title_lower for t in ["minecraft", "prism"]):
            return ARCHETYPE_SPECS[GameModArchetype.MINECRAFT_JAVA]
        if any(t in id_lower or t in title_lower for t in ["terraria", "tmodloader"]):
            return ARCHETYPE_SPECS[GameModArchetype.TERRARIA_TMOD]

        # Fast path 2: File system sentinels if path exists
        if game_path:
            p = Path(game_path).resolve()
            if p.exists() and p.is_dir():
                # Check Bethesda
                if (p / "SkyrimSE.exe").exists() or (p / "Data" / "Skyrim.esm").exists() or (p / "TESV.exe").exists():
                    return ARCHETYPE_SPECS[GameModArchetype.BETHESDA_CREATION]
                # Check Cyberpunk
                if (p / "bin" / "x64" / "Cyberpunk2077.exe").exists() or (p / "r6" / "config").exists():
                    return ARCHETYPE_SPECS[GameModArchetype.REDENGINE_CYBERPUNK]
                # Check Satisfactory
                if (p / "FactoryGameSteam.exe").exists() or (p / "FactoryGame").exists():
                    return ARCHETYPE_SPECS[GameModArchetype.UNREAL_SML]
                # Check RDR2
                if (p / "RDR2.exe").exists() or (p / "PlayRDR2.exe").exists():
                    return ARCHETYPE_SPECS[GameModArchetype.ROCKSTAR_RAGE]
                # Check Terraria
                if (p / "tModLoader.dll").exists() or (p / "Terraria.exe").exists():
                    return ARCHETYPE_SPECS[GameModArchetype.TERRARIA_TMOD]
                # Check Unity / BepInEx
                if (p / "UnityPlayer.dll").exists() or any(p.glob("*_Data/Managed")):
                    return ARCHETYPE_SPECS[GameModArchetype.UNITY_BEPINEX]

        return ARCHETYPE_SPECS[GameModArchetype.GENERIC]


@dataclass
class ModRoutingPlan:
    archetype: GameModArchetype
    mod_name: str
    stripped_prefix: str
    file_mappings: List[Tuple[str, str]] = field(default_factory=list) # [(src_relpath_in_archive, dest_relpath_in_game)]
    wine_dll_overrides: Dict[str, str] = field(default_factory=dict)
    has_fomod: bool = False
    fomod_xml_path: Optional[str] = None
    fomod_groups: List[Dict[str, Any]] = field(default_factory=list)
    requires_agent_decision: bool = False
    decision_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['archetype'] = self.archetype.value
        return d


class ModArchiveClassifier:
    """Type 1 Fast Archive Inspector and Intelligent Path Router."""

    @classmethod
    def list_archive_contents(cls, archive_path: Path | str) -> List[str]:
        """List normalized relative file paths in an archive without full extraction."""
        p = Path(archive_path).resolve()
        if not p.exists():
            return []

        paths: List[str] = []
        if p.is_dir():
            for root, _, files in os.walk(p):
                for f in files:
                    rel = Path(root, f).relative_to(p)
                    paths.append(str(rel).replace("\\", "/"))
        elif zipfile.is_zipfile(p):
            with zipfile.ZipFile(p, 'r') as z:
                paths = [name.replace("\\", "/") for name in z.namelist() if not name.endswith("/")]
        elif tarfile.is_tarfile(p):
            with tarfile.open(p, 'r:*') as t:
                paths = [m.name.replace("\\", "/") for m in t.getmembers() if m.isfile()]
        return paths

    @classmethod
    def inspect_and_route(
        cls,
        archive_path: Path | str,
        archetype: Optional[GameModArchetype] = None,
        game_title: str = "",
        mod_name_hint: str = "",
    ) -> ModRoutingPlan:
        """Inspect archive contents, strip wrapper paths, and compute optimal destination routes."""
        p = Path(archive_path).resolve()
        mod_name = mod_name_hint or p.stem
        # Strip common archive suffixes from mod name
        mod_name = re.sub(r"[-_](v?\d+[\d._-]*|main|release|master)$", "", mod_name, flags=re.IGNORECASE)

        raw_files = cls.list_archive_contents(p)
        if not raw_files:
            return ModRoutingPlan(
                archetype=archetype or GameModArchetype.GENERIC,
                mod_name=mod_name,
                stripped_prefix="",
                decision_reasons=["Empty or unreadable archive"],
                requires_agent_decision=True,
            )

        # 1. Detect wrapper directory
        # e.g. all files start with "MyMod-1.0/" or "package/"
        first_segments = [f.split("/")[0] for f in raw_files if "/" in f]
        stripped_prefix = ""
        if len(first_segments) == len(raw_files) and len(set(first_segments)) == 1:
            candidate = first_segments[0]
            # Verify candidate isn't a standard root game directory
            standard_roots = {"bin", "r6", "archive", "data", "factorygame", "mods", "bepinex", "lml", "engine", ".minecraft"}
            if candidate.lower() not in standard_roots:
                stripped_prefix = candidate + "/"

        normalized_files: List[Tuple[str, str]] = [] # [(raw_path, stripped_path)]
        for f in raw_files:
            stripped = f[len(stripped_prefix):] if f.startswith(stripped_prefix) else f
            normalized_files.append((f, stripped))

        # 2. Check for FOMOD ModuleConfig.xml
        has_fomod = False
        fomod_xml = None
        fomod_groups: List[Dict[str, Any]] = []
        for raw, stripped in normalized_files:
            if stripped.lower().endswith("fomod/moduleconfig.xml"):
                has_fomod = True
                fomod_xml = raw
                fomod_groups = cls._parse_fomod_preview(p, raw)
                break

        # If archetype is unset, infer from files
        if not archetype:
            archetype = cls._infer_archetype_from_files(normalized_files)

        spec = ARCHETYPE_SPECS.get(archetype, ARCHETYPE_SPECS[GameModArchetype.GENERIC])
        file_mappings: List[Tuple[str, str]] = []
        requires_agent = has_fomod
        reasons: List[str] = ["FOMOD installer detected with configurable options"] if has_fomod else []

        # 3. Archetype-specific intelligent routing
        if archetype == GameModArchetype.REDENGINE_CYBERPUNK:
            for raw, stripped in normalized_files:
                # Strip fomod files from game deployment
                if stripped.lower().startswith("fomod/"):
                    continue
                # If stripped already has valid root prefix (bin/, r6/, archive/, mods/)
                if any(stripped.lower().startswith(r) for r in ["bin/", "r6/", "archive/", "mods/"]):
                    file_mappings.append((raw, stripped))
                elif stripped.endswith(".archive") or stripped.endswith(".xl"):
                    file_mappings.append((raw, f"archive/pc/mod/{Path(stripped).name}"))
                elif stripped.endswith(".reds"):
                    file_mappings.append((raw, f"r6/scripts/{mod_name}/{Path(stripped).name}"))
                elif stripped.endswith(".yaml") or stripped.endswith(".yml"):
                    file_mappings.append((raw, f"r6/tweaks/{mod_name}/{Path(stripped).name}"))
                elif stripped.endswith(".lua"):
                    file_mappings.append((raw, f"bin/x64/plugins/cyber_engine_tweaks/mods/{mod_name}/{stripped}"))
                else:
                    file_mappings.append((raw, stripped))

        elif archetype == GameModArchetype.BETHESDA_CREATION:
            for raw, stripped in normalized_files:
                if stripped.lower().startswith("fomod/"):
                    continue
                # If stripped already starts with Data/, keep it; otherwise prefix Data/
                if stripped.lower().startswith("data/"):
                    file_mappings.append((raw, stripped))
                else:
                    file_mappings.append((raw, f"Data/{stripped}"))

        elif archetype == GameModArchetype.UNREAL_SML:
            for raw, stripped in normalized_files:
                if stripped.lower().startswith("factorygame/mods/"):
                    file_mappings.append((raw, stripped))
                else:
                    file_mappings.append((raw, f"FactoryGame/Mods/{mod_name}/{stripped}"))

        elif archetype == GameModArchetype.ROCKSTAR_RAGE:
            for raw, stripped in normalized_files:
                if stripped.endswith(".asi") or stripped.lower() in ("scripthookrdr2.dll", "dinput8.dll"):
                    file_mappings.append((raw, Path(stripped).name))
                elif stripped.lower().startswith("lml/"):
                    file_mappings.append((raw, stripped))
                else:
                    file_mappings.append((raw, f"lml/{mod_name}/{stripped}"))

        elif archetype == GameModArchetype.MINECRAFT_JAVA:
            for raw, stripped in normalized_files:
                if stripped.endswith(".jar"):
                    file_mappings.append((raw, f".minecraft/mods/{Path(stripped).name}"))
                else:
                    file_mappings.append((raw, f".minecraft/{stripped}"))

        elif archetype == GameModArchetype.TERRARIA_TMOD:
            for raw, stripped in normalized_files:
                if stripped.endswith(".tmod"):
                    file_mappings.append((raw, f"Mods/{Path(stripped).name}"))
                else:
                    file_mappings.append((raw, f"Mods/{stripped}"))

        elif archetype == GameModArchetype.UNITY_BEPINEX:
            for raw, stripped in normalized_files:
                if stripped.lower().startswith("bepinex/"):
                    file_mappings.append((raw, stripped))
                elif stripped.endswith(".dll"):
                    file_mappings.append((raw, f"BepInEx/plugins/{Path(stripped).name}"))
                else:
                    file_mappings.append((raw, f"BepInEx/plugins/{mod_name}/{stripped}"))

        else: # GENERIC
            for raw, stripped in normalized_files:
                file_mappings.append((raw, stripped))

        return ModRoutingPlan(
            archetype=archetype,
            mod_name=mod_name,
            stripped_prefix=stripped_prefix,
            file_mappings=file_mappings,
            wine_dll_overrides=spec.wine_dll_overrides,
            has_fomod=has_fomod,
            fomod_xml_path=fomod_xml,
            fomod_groups=fomod_groups,
            requires_agent_decision=requires_agent,
            decision_reasons=reasons,
        )

    @classmethod
    def _infer_archetype_from_files(cls, normalized_files: List[Tuple[str, str]]) -> GameModArchetype:
        """Infer target game archetype from file extensions and path structures."""
        for _, stripped in normalized_files:
            s_lower = stripped.lower()
            if s_lower.endswith(".reds") or s_lower.startswith("bin/x64/plugins/cyber_engine_tweaks"):
                return GameModArchetype.REDENGINE_CYBERPUNK
            if s_lower.endswith(".esp") or s_lower.endswith(".esm") or s_lower.endswith(".esl") or s_lower.startswith("skse/"):
                return GameModArchetype.BETHESDA_CREATION
            if s_lower.endswith(".uplugin"):
                return GameModArchetype.UNREAL_SML
            if s_lower.endswith(".asi") or s_lower.startswith("lml/"):
                return GameModArchetype.ROCKSTAR_RAGE
            if s_lower.endswith(".tmod"):
                return GameModArchetype.TERRARIA_TMOD
            if s_lower.endswith("fabric.mod.json") or s_lower.startswith("meta-inf/mods.toml"):
                return GameModArchetype.MINECRAFT_JAVA
            if s_lower.startswith("bepinex/"):
                return GameModArchetype.UNITY_BEPINEX
        return GameModArchetype.GENERIC

    @classmethod
    def _parse_fomod_preview(cls, archive_path: Path, xml_relpath: str) -> List[Dict[str, Any]]:
        """Extract steps and option groups from FOMOD ModuleConfig.xml for Type 2 agent inspection."""
        groups: List[Dict[str, Any]] = []
        try:
            xml_bytes = b""
            if archive_path.is_dir():
                xml_bytes = (archive_path / xml_relpath).read_bytes()
            elif zipfile.is_zipfile(archive_path):
                with zipfile.ZipFile(archive_path, 'r') as z:
                    xml_bytes = z.read(xml_relpath)

            if not xml_bytes:
                return []

            root = ET.fromstring(xml_bytes)
            # Find installSteps / step / group
            for step in root.findall(".//installStep"):
                step_name = step.attrib.get("name", "Step")
                for group in step.findall(".//group"):
                    grp_name = group.attrib.get("name", "Options")
                    grp_type = group.attrib.get("type", "SelectAny") # SelectExactOne, SelectAtLeastOne, SelectAny
                    options = []
                    for plugin in group.findall(".//plugin"):
                        p_name = plugin.attrib.get("name", "Choice")
                        desc = (plugin.findtext("description") or "").strip()
                        options.append({"name": p_name, "description": desc})
                    groups.append({
                        "step": step_name,
                        "group_name": grp_name,
                        "group_type": grp_type,
                        "options": options
                    })
        except Exception:
            pass
        return groups
