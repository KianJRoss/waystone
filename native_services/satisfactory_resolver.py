"""Satisfactory Unreal Engine 5 SML v3.8+ Plugin Dependency Resolver.

Inspects FactoryGame/Mods/<ModName>/<ModName>.uplugin JSON manifests,
validates SemVer dependencies, detects missing plugins, and computes
topological plugin loading order for UE5 engine registration.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class UPluginDependency:
    name: str
    sem_version: str = ">=0.0.0"
    enabled: bool = True
    optional: bool = False


@dataclass
class UPluginManifest:
    name: str
    version: str
    sem_version: str
    path: Path
    description: str = ""
    dependencies: List[UPluginDependency] = field(default_factory=list)


@dataclass
class SatisfactoryResolutionResult:
    valid: bool
    load_order: List[str]
    missing_dependencies: Dict[str, List[str]]
    version_conflicts: List[str]
    errors: List[str]


def parse_semver(v_str: str) -> Tuple[int, int, int]:
    """Extract (major, minor, patch) integers from a version string."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", v_str)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def satisfies_semver(version: str, constraint: str) -> bool:
    """Basic SemVer range check (supporting >=, >, <=, <, ==, ^)."""
    if not constraint or constraint in ("*", ">=0.0.0"):
        return True
    v = parse_semver(version)
    clean_c = constraint.strip()

    if clean_c.startswith("^"):
        req = parse_semver(clean_c[1:])
        return v[0] == req[0] and v >= req
    if clean_c.startswith(">="):
        return v >= parse_semver(clean_c[2:])
    if clean_c.startswith(">"):
        return v > parse_semver(clean_c[1:])
    if clean_c.startswith("<="):
        return v <= parse_semver(clean_c[2:])
    if clean_c.startswith("<"):
        return v < parse_semver(clean_c[1:])
    if clean_c.startswith("=="):
        return v == parse_semver(clean_c[2:])

    return v >= parse_semver(clean_c)


class SatisfactoryPluginResolver:
    """Resolves SML v3.8+ plugin manifests in FactoryGame/Mods/."""

    def __init__(self, mods_dir: Optional[Path] = None):
        self.mods_dir = Path(mods_dir) if mods_dir else None

    @staticmethod
    def parse_uplugin(path: Path) -> Optional[UPluginManifest]:
        """Parse a single .uplugin JSON manifest."""
        p = Path(path)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None

        name = data.get("Name") or p.stem
        sem_ver = data.get("SemVersion") or str(data.get("Version") or "1.0.0")
        ver = str(data.get("Version") or sem_ver)

        deps: List[UPluginDependency] = []
        raw_plugins = data.get("Plugins") or []
        for pl in raw_plugins:
            if isinstance(pl, dict):
                p_name = pl.get("Name")
                if p_name:
                    deps.append(
                        UPluginDependency(
                            name=p_name,
                            sem_version=str(pl.get("SemVersion") or ">=0.0.0"),
                            enabled=bool(pl.get("Enabled", True)),
                            optional=bool(pl.get("Optional", False)),
                        )
                    )

        return UPluginManifest(
            name=name,
            version=ver,
            sem_version=sem_ver,
            path=p,
            description=data.get("Description", ""),
            dependencies=deps,
        )

    def scan_mods(self, mods_dir: Optional[Path] = None) -> Dict[str, UPluginManifest]:
        """Scan FactoryGame/Mods/ for all .uplugin files."""
        target_dir = Path(mods_dir or self.mods_dir or "")
        manifests: Dict[str, UPluginManifest] = {}
        if not target_dir.is_dir():
            return manifests

        for uplugin_file in target_dir.glob("*/*.uplugin"):
            m = self.parse_uplugin(uplugin_file)
            if m:
                manifests[m.name.lower()] = m

        for root_uplugin in target_dir.glob("*.uplugin"):
            m = self.parse_uplugin(root_uplugin)
            if m and m.name.lower() not in manifests:
                manifests[m.name.lower()] = m

        return manifests

    def resolve(self, mods_dir: Optional[Path] = None) -> SatisfactoryResolutionResult:
        """Validate dependencies and compute topological plugin load order."""
        manifests = self.scan_mods(mods_dir)
        missing: Dict[str, List[str]] = {}
        version_conflicts: List[str] = []
        errors: List[str] = []

        # Built-in or engine plugins that may not be in Mods/ folder
        BUILTIN_PLUGINS = {"sml", "factorygame", "contentlib"}

        adj: Dict[str, Set[str]] = {k: set() for k in manifests}
        in_degree: Dict[str, int] = {k: 0 for k in manifests}

        for name, m in manifests.items():
            for dep in m.dependencies:
                dep_name_lower = dep.name.lower()
                if not dep.enabled:
                    continue
                if dep_name_lower in BUILTIN_PLUGINS and dep_name_lower not in manifests:
                    # Satisfied by game engine / core SML
                    continue

                if dep_name_lower not in manifests:
                    if not dep.optional:
                        missing.setdefault(m.name, []).append(f"{dep.name} ({dep.sem_version})")
                else:
                    dep_manifest = manifests[dep_name_lower]
                    if not satisfies_semver(dep_manifest.sem_version, dep.sem_version):
                        version_conflicts.append(
                            f"{m.name} requires {dep.name} {dep.sem_version}, but found {dep_manifest.sem_version}"
                        )
                    # Dependency must load before m: dep -> m
                    adj[dep_name_lower].add(name)

        for dep_set in adj.values():
            for target in dep_set:
                in_degree[target] += 1

        import heapq
        pq: List[str] = [k for k, deg in in_degree.items() if deg == 0]
        heapq.heapify(pq)

        load_order: List[str] = []
        visited = set()

        while pq:
            curr = heapq.heappop(pq)
            load_order.append(manifests[curr].name)
            visited.add(curr)
            for neighbor in adj.get(curr, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    heapq.heappush(pq, neighbor)

        if len(load_order) < len(manifests):
            remaining = [manifests[k].name for k in manifests if k not in visited]
            errors.append(f"Circular dependency detected among plugins: {', '.join(remaining)}")
            load_order.extend(remaining)

        is_valid = len(missing) == 0 and len(version_conflicts) == 0 and len(errors) == 0
        return SatisfactoryResolutionResult(
            valid=is_valid,
            load_order=load_order,
            missing_dependencies=missing,
            version_conflicts=version_conflicts,
            errors=errors,
        )
