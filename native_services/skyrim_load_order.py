"""Skyrim Creation Engine (TES5 / SSE) Topological Load Order, plugins.txt Manager, and Pure Load-Order Checker (W9a).

Follows official Bethesda plugin hierarchy and topological load order rules:
1. Official Masters (Skyrim.esm, Update.esm, Dawnguard.esm, HearthFires.esm, Dragonborn.esm).
2. Essential Script Extender / Framework plugins (AddressLibrary, SKSE plugins).
3. Master tier (.esm files).
4. Light Master tier (.esl files, or .esp flagged as ESL).
5. Standard Plugin tier (.esp files).
6. Post-processing / weather / map / dynamic patcher plugins at the bottom.

Also provides pure load-order diagnostics (no LOOT required):
- Missing masters (``missing_master``)
- Plugins disabled but required by an enabled plugin (``disabled_master``)
- Plugins loaded after their dependents / master tier order violations (``order_violation``)
- Full ESP/ESM (254) and Light ESL (4096) slot limits (``slot_limit_exceeded``)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import heapq
from pathlib import Path
import struct
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

# Creation Engine slot limits: 0x00..0xFD (254 full slots; 0xFE is ESL space, 0xFF is dynamic runtime)
FULL_PLUGIN_SLOT_LIMIT = 254
LIGHT_PLUGIN_SLOT_LIMIT = 4096


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


@dataclass
class LoadOrderIssue:
    kind: str  # 'missing_master' | 'disabled_master' | 'order_violation' | 'slot_limit_exceeded'
    severity: str  # 'critical' | 'error' | 'warning'
    plugin: str
    message: str
    fix_suggestion: str
    related_plugin: str = ""
    culprit_mod_id: Optional[str] = None
    culprit_mod_name: Optional[str] = None
    recommended_fix: Optional[dict[str, Any]] = None


@dataclass
class LoadOrderReport:
    healthy: bool
    summary: str
    active_full_count: int
    full_slot_limit: int
    active_light_count: int
    light_slot_limit: int
    issues: List[LoadOrderIssue] = field(default_factory=list)


def read_plugin_header_masters(plugin_path: Path) -> Tuple[bool, bool, List[str]]:
    """Read a Creation Engine TES4 record header from an .esm/.esp/.esl file.

    Returns ``(is_master_flagged, is_light_flagged, masters)``.
    """
    ext = plugin_path.suffix.lower()
    default_master = ext == ".esm" or plugin_path.name.lower() in OFFICIAL_MASTERS
    default_light = ext == ".esl"
    try:
        with open(plugin_path, "rb") as f:
            header = f.read(24)
            if len(header) < 24 or header[:4] != b"TES4":
                return (default_master, default_light, [])
            data_size, flags = struct.unpack("<II", header[4:12])
            is_master = default_master or bool(flags & 0x0001)
            is_light = default_light or bool(flags & 0x0200)
            payload = f.read(min(data_size, 256 * 1024))
    except OSError:
        return (default_master, default_light, [])

    masters: List[str] = []
    offset = 0
    while offset + 6 <= len(payload):
        sub_type = payload[offset : offset + 4]
        (sub_len,) = struct.unpack("<H", payload[offset + 4 : offset + 6])
        offset += 6
        if offset + sub_len > len(payload):
            break
        if sub_type == b"MAST":
            raw_str = payload[offset : offset + sub_len].rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            if raw_str:
                masters.append(raw_str)
        offset += sub_len
    return (is_master, is_light, masters)


class SkyrimLoadOrderSorter:
    """Deterministic topological load order sorter and pure load-order checker for Creation Engine."""

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

    def check_load_order(
        self,
        plugins: List[SkyrimPlugin],
        *,
        known_mods: Optional[List[dict[str, Any]]] = None,
        full_slot_limit: int = FULL_PLUGIN_SLOT_LIMIT,
        light_slot_limit: int = LIGHT_PLUGIN_SLOT_LIMIT,
    ) -> LoadOrderReport:
        """Run pure load-order checks: missing masters, disabled required masters, order violations, and ESL/ESP limits."""
        # Map plugin filename -> owning mod in known_mods
        plugin_to_mod: Dict[str, dict[str, Any]] = {}
        plugin_meta_from_mods: Dict[str, dict[str, Any]] = {}
        for mod in (known_mods or []):
            if not isinstance(mod, dict):
                continue
            files = list(mod.get("installed_files") or [])
            extra = mod.get("extra_metadata") if isinstance(mod.get("extra_metadata"), dict) else {}
            files.extend(extra.get("plugins") or [])
            for f in files:
                base = Path(str(f).replace("\\", "/")).name.strip().lower()
                if base.endswith((".esp", ".esm", ".esl")):
                    plugin_to_mod[base] = mod
            if isinstance(extra.get("plugin_masters"), dict):
                for p_name, m_list in extra["plugin_masters"].items():
                    if isinstance(m_list, (list, tuple)):
                        plugin_meta_from_mods.setdefault(str(p_name).lower(), {})["masters"] = [str(x) for x in m_list]
            if isinstance(extra.get("light_plugins"), (list, tuple)):
                for p_name in extra["light_plugins"]:
                    plugin_meta_from_mods.setdefault(str(p_name).lower(), {})["is_light"] = True

        # Enrich plugins from data_dir TES4 headers or mod metadata if masters not already populated
        enriched: List[SkyrimPlugin] = []
        for p in plugins:
            key = p.clean_name.lower()
            masters = list(p.masters)
            load_after = list(p.load_after)
            is_master = p.is_master or p.extension == ".esm" or key in OFFICIAL_MASTERS
            is_light = p.is_light or p.extension == ".esl"

            if self.data_dir is not None:
                candidate = self.data_dir / p.clean_name
                if candidate.is_file():
                    hdr_master, hdr_light, hdr_masters = read_plugin_header_masters(candidate)
                    is_master = is_master or hdr_master
                    is_light = is_light or hdr_light
                    if not masters and hdr_masters:
                        masters = hdr_masters

            meta = plugin_meta_from_mods.get(key, {})
            if not masters and "masters" in meta:
                masters = list(meta["masters"])
            if meta.get("is_light"):
                is_light = True

            enriched.append(
                SkyrimPlugin(
                    name=p.clean_name,
                    enabled=p.enabled,
                    is_master=is_master,
                    is_light=is_light,
                    masters=masters,
                    load_after=load_after,
                )
            )

        by_name: Dict[str, SkyrimPlugin] = {p.clean_name.lower(): p for p in enriched}
        enabled_plugins: List[SkyrimPlugin] = [p for p in enriched if p.enabled]
        enabled_index: Dict[str, int] = {p.clean_name.lower(): idx for idx, p in enumerate(enabled_plugins)}

        issues: List[LoadOrderIssue] = []
        reported_order_pairs: Set[Tuple[str, str]] = set()

        def _mod_for(plugin_name: str) -> Tuple[Optional[str], Optional[str], Optional[dict[str, Any]]]:
            mod = plugin_to_mod.get(plugin_name.lower())
            if not mod:
                return (None, None, None)
            mid = str(mod.get("mod_id") or "") or None
            mname = str(mod.get("name") or "") or None
            if not mid:
                return (None, mname, None)
            fix = {
                "action": "disable_mod",
                "mod_id": mid,
                "mod_name": mname or mid,
                "plugin": plugin_name,
                "description": f"Disable '{mname or mid}' ({plugin_name})",
            }
            return (mid, mname, fix)

        first_non_master: Optional[SkyrimPlugin] = None

        for idx, p in enumerate(enabled_plugins):
            p_key = p.clean_name.lower()
            mid, mname, disable_fix = _mod_for(p.clean_name)

            # 1. Check masters: missing or disabled or loaded after dependent
            for master in p.masters:
                m_key = master.strip().lower()
                if not m_key or m_key == p_key:
                    continue
                master_plugin = by_name.get(m_key)
                if master_plugin is None:
                    issues.append(
                        LoadOrderIssue(
                            kind="missing_master",
                            severity="critical",
                            plugin=p.clean_name,
                            related_plugin=master,
                            message=f"'{p.clean_name}' is missing required master '{master}'.",
                            fix_suggestion=f"Install '{master}' or disable '{mname or p.clean_name}'.",
                            culprit_mod_id=mid,
                            culprit_mod_name=mname,
                            recommended_fix=disable_fix,
                        )
                    )
                elif not master_plugin.enabled:
                    issues.append(
                        LoadOrderIssue(
                            kind="disabled_master",
                            severity="critical",
                            plugin=p.clean_name,
                            related_plugin=master_plugin.clean_name,
                            message=f"'{p.clean_name}' requires master '{master_plugin.clean_name}', which is disabled.",
                            fix_suggestion=f"Enable '{master_plugin.clean_name}' or disable '{mname or p.clean_name}'.",
                            culprit_mod_id=mid,
                            culprit_mod_name=mname,
                            recommended_fix=disable_fix,
                        )
                    )
                else:
                    m_idx = enabled_index.get(m_key, -1)
                    if m_idx > idx:
                        pair = (master_plugin.clean_name.lower(), p_key)
                        if pair not in reported_order_pairs:
                            reported_order_pairs.add(pair)
                            issues.append(
                                LoadOrderIssue(
                                    kind="order_violation",
                                    severity="error",
                                    plugin=p.clean_name,
                                    related_plugin=master_plugin.clean_name,
                                    message=(
                                        f"Master '{master_plugin.clean_name}' loads after its dependent "
                                        f"'{p.clean_name}' (position {m_idx + 1} vs {idx + 1})."
                                    ),
                                    fix_suggestion=f"Move '{master_plugin.clean_name}' above '{p.clean_name}' or sort load order.",
                                    culprit_mod_id=mid,
                                    culprit_mod_name=mname,
                                    recommended_fix=disable_fix,
                                )
                            )

            # 2. Check explicit load_after rules
            for target in p.load_after:
                t_key = target.strip().lower()
                if not t_key or t_key == p_key:
                    continue
                t_idx = enabled_index.get(t_key, -1)
                if t_idx > idx:
                    target_plugin = by_name[t_key].clean_name
                    pair = (t_key, p_key)
                    if pair not in reported_order_pairs:
                        reported_order_pairs.add(pair)
                        issues.append(
                            LoadOrderIssue(
                                kind="order_violation",
                                severity="warning",
                                plugin=p.clean_name,
                                related_plugin=target_plugin,
                                message=(
                                    f"'{target_plugin}' loads after '{p.clean_name}', which requires loading after '{target_plugin}'."
                                ),
                                fix_suggestion=f"Move '{p.clean_name}' below '{target_plugin}' or sort load order.",
                                culprit_mod_id=mid,
                                culprit_mod_name=mname,
                                recommended_fix=disable_fix,
                            )
                        )

            # 3. Check Bethesda master tier rule (.esm / is_master loaded after a non-master .esp)
            if p.is_master and not p.is_light:
                if first_non_master is not None:
                    pair = (p_key, first_non_master.clean_name.lower())
                    if pair not in reported_order_pairs:
                        reported_order_pairs.add(pair)
                        issues.append(
                            LoadOrderIssue(
                                kind="order_violation",
                                severity="error",
                                plugin=p.clean_name,
                                related_plugin=first_non_master.clean_name,
                                message=(
                                    f"Master file '{p.clean_name}' loads after non-master plugin '{first_non_master.clean_name}'."
                                ),
                                fix_suggestion=f"Move master '{p.clean_name}' above '{first_non_master.clean_name}'.",
                                culprit_mod_id=mid,
                                culprit_mod_name=mname,
                                recommended_fix=disable_fix,
                            )
                        )
            else:
                if first_non_master is None and not p.is_light:
                    first_non_master = p

        # 4. Check full ESP/ESM (254) and light ESL (4096) slot limits
        active_full = [p for p in enabled_plugins if not p.is_light]
        active_light = [p for p in enabled_plugins if p.is_light]

        if len(active_full) > full_slot_limit:
            excess = len(active_full) - full_slot_limit
            last_full = active_full[-1]
            mid, mname, disable_fix = _mod_for(last_full.clean_name)
            issues.append(
                LoadOrderIssue(
                    kind="slot_limit_exceeded",
                    severity="critical",
                    plugin=last_full.clean_name,
                    message=(
                        f"Active full plugin count ({len(active_full)}/{full_slot_limit}) exceeds the "
                        f"Creation Engine ESM/ESP slot limit ({full_slot_limit} slots, 0x00–0xFD)."
                    ),
                    fix_suggestion=f"Disable or ESL-ify at least {excess} full ESP plugin(s).",
                    culprit_mod_id=mid,
                    culprit_mod_name=mname,
                    recommended_fix=disable_fix,
                )
            )

        if len(active_light) > light_slot_limit:
            excess = len(active_light) - light_slot_limit
            last_light = active_light[-1]
            mid, mname, disable_fix = _mod_for(last_light.clean_name)
            issues.append(
                LoadOrderIssue(
                    kind="slot_limit_exceeded",
                    severity="critical",
                    plugin=last_light.clean_name,
                    message=(
                        f"Active light plugin count ({len(active_light)}/{light_slot_limit}) exceeds the "
                        f"Creation Engine ESL slot limit ({light_slot_limit} slots, 0xFE000–0xFEFFF)."
                    ),
                    fix_suggestion=f"Disable or merge at least {excess} light (ESL) plugin(s).",
                    culprit_mod_id=mid,
                    culprit_mod_name=mname,
                    recommended_fix=disable_fix,
                )
            )

        healthy = len(issues) == 0
        if healthy:
            summary = (
                f"Load order healthy ({len(active_full)}/{full_slot_limit} full plugins, "
                f"{len(active_light)}/{light_slot_limit} light plugins)."
            )
        else:
            summary = (
                f"Detected {len(issues)} load-order problem(s) "
                f"({len(active_full)}/{full_slot_limit} full, {len(active_light)}/{light_slot_limit} light)."
            )

        return LoadOrderReport(
            healthy=healthy,
            summary=summary,
            active_full_count=len(active_full),
            full_slot_limit=full_slot_limit,
            active_light_count=len(active_light),
            light_slot_limit=light_slot_limit,
            issues=issues,
        )

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
