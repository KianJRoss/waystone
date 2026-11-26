"""Skyrim Creation Engine (TES5 / SSE) Topological Load Order & plugins.txt Manager.

Follows official Bethesda plugin hierarchy and LOOT topological load order rules:
1. Official Masters (Skyrim.esm, Update.esm, Dawnguard.esm, HearthFires.esm, Dragonborn.esm).
2. Essential Script Extender / Framework plugins (AddressLibrary, SKSE plugins).
3. Master tier (.esm files).
4. Light Master tier (.esl files, or .esp flagged as ESL).
5. Standard Plugin tier (.esp files).
6. Post-processing / weather / map / dynamic patcher plugins at the bottom.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


OFFICIAL_MASTERS = [
    "skyrim.esm",
    "update.esm",
    "dawnguard.esm",
    "hearthfires.esm",
    "dragonborn.esm",
]

CORE_FRAMEWORKS = [
    "unofficial skyrim special edition patch.esp",
    "ussep.esp",
    "addresslibrary.esl",
    "skse.esl",
]


@dataclass
class SkyrimPlugin:
    name: str
    enabled: bool = True
    is_master: bool = False
    is_light: bool = False
    masters: List[str] = field(default_factory=list)
    load_after: List[str] = field(default_factory=list)

    @property
    def extension(self) -> str:
        return Path(self.name).suffix.lower()

    @property
    def clean_name(self) -> str:
        return self.name.lstrip("*").strip()


class SkyrimLoadOrderSorter:
    """Deterministic topological load order sorter for Skyrim Creation Engine."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else None

    @staticmethod
    def parse_plugins_txt(content: str) -> List[SkyrimPlugin]:
        """Parse plugins.txt content. Lines starting with '*' are enabled plugins."""
        plugins: List[SkyrimPlugin] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            enabled = line.startswith("*")
            name = line.lstrip("*").strip()
            ext = Path(name).suffix.lower()
            is_master = ext == ".esm" or name.lower() in OFFICIAL_MASTERS
            is_light = ext == ".esl"
            plugins.append(
                SkyrimPlugin(
                    name=name,
                    enabled=enabled,
                    is_master=is_master,
                    is_light=is_light,
                )
            )
        return plugins

    @staticmethod
    def serialize_plugins_txt(plugins: List[SkyrimPlugin]) -> str:
        """Serialize a list of plugins to plugins.txt format."""
        lines = ["# Created by Waystone Mod Organizer (Topological Sorter)"]
        for p in plugins:
            clean = p.clean_name
            prefix = "*" if p.enabled else ""
            lines.append(f"{prefix}{clean}")
        return "\n".join(lines) + "\n"

    def sort_plugins(self, plugins: List[SkyrimPlugin]) -> List[SkyrimPlugin]:
        """Sort plugins topologically respecting Bethesda tiering and master dependencies."""
        plugin_map: Dict[str, SkyrimPlugin] = {p.clean_name.lower(): p for p in plugins}

        def get_priority_score(p: SkyrimPlugin) -> int:
            clean_lower = p.clean_name.lower()
            if clean_lower in OFFICIAL_MASTERS:
                return OFFICIAL_MASTERS.index(clean_lower)
            if clean_lower in CORE_FRAMEWORKS:
                return 10 + CORE_FRAMEWORKS.index(clean_lower)
            ext = p.extension
            if ext == ".esm" or p.is_master:
                return 100
            if ext == ".esl" or p.is_light:
                return 200
            return 300

        # Build adjacency graph for topological dependencies
        adj: Dict[str, Set[str]] = {p.clean_name.lower(): set() for p in plugins}
        in_degree: Dict[str, int] = {p.clean_name.lower(): 0 for p in plugins}

        # Dependencies: masters and load_after
        for p in plugins:
            p_key = p.clean_name.lower()
            dependencies = [m.lower() for m in p.masters] + [m.lower() for m in p.load_after]
            for dep in dependencies:
                if dep in adj:
                    adj[dep].add(p_key)

        for dep_set in adj.values():
            for target in dep_set:
                in_degree[target] += 1

        # Priority queue based sorting (Kahn's algorithm with priority tie-breaker)
        import heapq

        pq: List[Tuple[int, str, SkyrimPlugin]] = []
        for p in plugins:
            key = p.clean_name.lower()
            if in_degree[key] == 0:
                score = get_priority_score(p)
                heapq.heappush(pq, (score, p.clean_name, p))

        sorted_plugins: List[SkyrimPlugin] = []
        visited = set()

        while pq:
            score, name, plugin = heapq.heappop(pq)
            sorted_plugins.append(plugin)
            key = plugin.clean_name.lower()
            visited.add(key)

            for dependent in adj.get(key, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    dep_plugin = plugin_map[dependent]
                    dep_score = get_priority_score(dep_plugin)
                    heapq.heappush(pq, (dep_score, dep_plugin.clean_name, dep_plugin))

        # If cycles existed, append remaining plugins in tier order
        if len(sorted_plugins) < len(plugins):
            remaining = [p for p in plugins if p.clean_name.lower() not in visited]
            remaining.sort(key=get_priority_score)
            sorted_plugins.extend(remaining)

        return sorted_plugins

    def sync_load_order(self, plugins_txt_path: Path, new_plugins: Optional[List[str]] = None) -> List[SkyrimPlugin]:
        """Read plugins.txt, merge any new plugins, topologically sort, and write back."""
        p_path = Path(plugins_txt_path)
        existing_plugins: List[SkyrimPlugin] = []
        if p_path.is_file():
            existing_plugins = self.parse_plugins_txt(p_path.read_text(encoding="utf-8", errors="replace"))

        existing_names = {p.clean_name.lower() for p in existing_plugins}

        if new_plugins:
            for n in new_plugins:
                clean = n.lstrip("*").strip()
                if clean.lower() not in existing_names:
                    ext = Path(clean).suffix.lower()
                    existing_plugins.append(
                        SkyrimPlugin(
                            name=clean,
                            enabled=True,
                            is_master=ext == ".esm" or clean.lower() in OFFICIAL_MASTERS,
                            is_light=ext == ".esl",
                        )
                    )

        sorted_plugins = self.sort_plugins(existing_plugins)
        p_path.parent.mkdir(parents=True, exist_ok=True)
        p_path.write_text(self.serialize_plugins_txt(sorted_plugins), encoding="utf-8")
        return sorted_plugins
