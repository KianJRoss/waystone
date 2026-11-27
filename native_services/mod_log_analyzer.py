"""Universal Log Diagnostics & Rapid Crash Triaging for Games, Modpacks, and Runners (W9a).

Designed for modpack creators, players, and autonomous agents:
- Built-in specialized crash-log readers for:
  1. Crash Logger SSE/AE/VR (``crash-*.log``)
  2. Trainwreck (``crash-*.log``)
  3. .NET Script Framework (``Crash_*.txt``)
  plus Unity/BepInEx, Unreal Engine/SML/UE4SS, and Proton/Wine logs.
- Discovers crash logs inside the game's Proton prefix (``My Games/<game>/SKSE``, ``Documents`` paths),
  the mod manager's overwrite/profile folders, and the game install folder.
- Ranks probable culprits (DLL modules on the exception site/call stack and plugins in ``Possible relevant objects``
  / ``REGISTERS`` / ``STACK``), mapped back to the staged mod that ships each file.
- Produces a one-line plain-language summary and structured remediation actions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any, Optional


# Common error pattern signatures across game engines and mod loaders
_CRASH_PATTERNS = [
    re.compile(r"(?P<type>NullReferenceException|InvalidOperationException|ArgumentNullException|IndexOutOfRangeException): (?P<msg>.*)", re.IGNORECASE),
    re.compile(r"(?P<type>DllNotFoundException|FileNotFoundException): Could not load file or assembly '(?P<msg>[^']+)'", re.IGNORECASE),
    re.compile(r"\[(?P<type>Fatal|Error)\]\s+(?P<msg>.*)", re.IGNORECASE),
    re.compile(r"CRASH:\s+(?P<msg>.*)", re.IGNORECASE),
    re.compile(r"Unhandled exception at 0x[0-9a-fA-F]+.*: (?P<msg>.*)", re.IGNORECASE),
    re.compile(r"Caught signal (?P<type>\d+ \([A-Z]+\))\s+(?P<msg>.*)", re.IGNORECASE),
    re.compile(r"\[(?P<type>SKSE|F4SE|UE4SS)\]\s+(?P<level>ERROR|FATAL): (?P<msg>.*)", re.IGNORECASE),
    re.compile(r"assertion failed: (?P<msg>.*)", re.IGNORECASE),
]

_IGNORABLE_NOISE = [
    re.compile(r"\[Debug\s*:\s*", re.IGNORECASE),
    re.compile(r"\[Info\s*:\s*", re.IGNORECASE),
    re.compile(r"Loading scene", re.IGNORECASE),
    re.compile(r"Compiling shader", re.IGNORECASE),
    re.compile(r"Shader compilation took", re.IGNORECASE),
    re.compile(r"Font cache", re.IGNORECASE),
    re.compile(r"Setting breakpad minidump", re.IGNORECASE),
]

# Base game / OS / runtime binaries and official masters that are not third-party mod culprits
_IGNORED_BINARIES = frozenset({
    "skyrimse.exe",
    "skyrimvr.exe",
    "skyrim.exe",
    "fallout4.exe",
    "starfield.exe",
    "oblivion.exe",
    "ntdll.dll",
    "kernel32.dll",
    "kernelbase.dll",
    "user32.dll",
    "gdi32.dll",
    "advapi32.dll",
    "msvcrt.dll",
    "ucrtbase.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "steam_api64.dll",
    "d3d11.dll",
    "d3d12.dll",
    "dxgi.dll",
    "xinput1_3.dll",
    "bink2w64.dll",
})

_IGNORED_BINARY_PREFIXES = (
    "skse64_",
    "sksevr_",
    "f4se_",
    "sfse_",
)

_OFFICIAL_MASTERS = frozenset({
    "skyrim.esm",
    "update.esm",
    "dawnguard.esm",
    "hearthfires.esm",
    "dragonborn.esm",
    "fallout4.esm",
    "dlcrobot.esm",
    "dlcworkshop01.esm",
    "dlccoast.esm",
    "dlcworkshop02.esm",
    "dlcworkshop03.esm",
    "dlcnukaworld.esm",
})

_RE_CL_UNHANDLED = re.compile(
    r'Unhandled exception\s+"(?P<exc>[^"]+)"\s+at\s+(?P<addr>0x[0-9A-Fa-f]+)(?:\s+(?P<mod>[A-Za-z0-9_.\-]+\.(?:dll|exe))\+(?P<off>[0-9A-Fa-f]+))?',
    re.IGNORECASE,
)
_RE_NSF_UNHANDLED = re.compile(
    r"Unhandled native exception occurred at\s+(?P<addr>0x[0-9A-Fa-f]+)\s*\((?P<mod>[A-Za-z0-9_.\-]+\.(?:dll|exe))\+(?P<off>[0-9A-Fa-f]+)\)",
    re.IGNORECASE,
)
_RE_STACK_MODULE = re.compile(
    r"(?P<mod>[A-Za-z0-9_.\-]+\.(?:dll|exe))\+(?P<off>[0-9A-Fa-f]+)",
    re.IGNORECASE,
)
_RE_FILE_ATTR = re.compile(
    r'File:\s*[`"\'](?P<files>[^`"\']+)[`"\']',
    re.IGNORECASE,
)
_RE_MODIFIED_BY = re.compile(
    r"Modified by:\s*(?P<chain>.+)",
    re.IGNORECASE,
)
_RE_PLUGIN_TOKEN = re.compile(
    r"(?P<plugin>[A-Za-z0-9_.\- ]+\.(?:esp|esm|esl))",
    re.IGNORECASE,
)
_RE_FILENAME_CRASH_TS = re.compile(
    r"crash[-_](?P<y>\d{4})[-_](?P<m>\d{1,2})[-_](?P<d>\d{1,2})[-_](?P<H>\d{1,2})[-_](?P<M>\d{1,2})[-_](?P<S>\d{1,2})",
    re.IGNORECASE,
)
_RE_HEADER_ISO_TS = re.compile(r"^\s*(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*$")
_RE_NSF_TIME = re.compile(r"^\s*Time:\s*(?P<ts>.+?)\s*$", re.IGNORECASE)


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _is_ignored_binary(name: str) -> bool:
    low = name.strip().casefold()
    if low in _IGNORED_BINARIES:
        return True
    return any(low.startswith(pref) for pref in _IGNORED_BINARY_PREFIXES)


def _is_official_master(name: str) -> bool:
    return name.strip().casefold() in _OFFICIAL_MASTERS


@dataclass
class CrashCulprit:
    rank: int
    artifact: str
    kind: str  # 'dll' | 'plugin'
    source: str  # 'exception_site' | 'call_stack' | 'relevant_objects'
    score: int
    reason: str
    mod_id: Optional[str] = None
    mod_name: Optional[str] = None
    enabled: bool = True
    recommended_fix: Optional[dict[str, Any]] = None


@dataclass
class CrashEntry:
    log_path: str
    format: str  # 'crash_logger_sse' | 'trainwreck' | 'net_script_framework' | 'generic'
    format_label: str
    when: str
    exception: str
    address: str
    summary: str
    culprits: list[CrashCulprit] = field(default_factory=list)
    call_stack: list[str] = field(default_factory=list)
    relevant_objects: list[str] = field(default_factory=list)


@dataclass
class DiagnosticFinding:
    severity: str  # 'critical', 'error', 'warning', 'info'
    error_type: str
    message: str
    culprit_mod_id: Optional[str] = None
    culprit_mod_name: Optional[str] = None
    source_file: Optional[str] = None
    line_number: Optional[int] = None
    stack_trace: list[str] = field(default_factory=list)
    recommended_fix: Optional[dict[str, Any]] = None


@dataclass
class LogReport:
    log_path: str
    file_size_bytes: int
    last_modified: str
    healthy: bool
    summary: str
    findings: list[DiagnosticFinding] = field(default_factory=list)
    raw_error_count: int = 0
    filtered_line_count: int = 0
    crash_format: str = "generic"
    crash_format_label: str = "Engine Log"
    when: str = ""
    exception: str = ""
    address: str = ""
    culprits: list[dict[str, Any]] = field(default_factory=list)
    call_stack: list[str] = field(default_factory=list)
    relevant_objects: list[str] = field(default_factory=list)
    crashes: list[dict[str, Any]] = field(default_factory=list)


def _build_mod_lookup(known_mods: Optional[list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Build exact and stem lookup map from staged mod files (DLLs, ESPs, ESMs, ESLs) and names to mod metadata."""
    lookup: dict[str, dict[str, Any]] = {}
    for mod in (known_mods or []):
        if not isinstance(mod, dict):
            continue
        name = str(mod.get("name") or "").strip()
        mid = str(mod.get("mod_id") or "").strip()
        if name:
            lookup[name.casefold()] = mod
            norm_name = _norm_key(name)
            if norm_name:
                lookup.setdefault(norm_name, mod)
        if mid:
            lookup[mid.casefold()] = mod
            norm_mid = _norm_key(mid)
            if norm_mid:
                lookup.setdefault(norm_mid, mod)

        files = list(mod.get("installed_files") or [])
        extra = mod.get("extra_metadata")
        if isinstance(extra, dict):
            files.extend(extra.get("plugins") or [])
            files.extend(extra.get("dlls") or [])
        src_file = mod.get("source_file")
        if isinstance(src_file, str) and src_file:
            files.append(src_file)

        for f in files:
            base = Path(str(f).replace("\\", "/")).name.strip()
            if not base:
                continue
            low = base.casefold()
            lookup[low] = mod
            stem = Path(base).stem
            if stem:
                lookup.setdefault(stem.casefold(), mod)
                norm_stem = _norm_key(stem)
                if norm_stem:
                    lookup.setdefault(norm_stem, mod)
    return lookup


def _match_artifact_to_mod(artifact: str, mod_lookup: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
    clean = Path(artifact.replace("\\", "/")).name.strip()
    if not clean:
        return None
    low = clean.casefold()
    if low in mod_lookup:
        return mod_lookup[low]
    stem = Path(clean).stem.casefold()
    if stem in mod_lookup:
        return mod_lookup[stem]
    norm = _norm_key(stem)
    if norm and norm in mod_lookup:
        return mod_lookup[norm]
    return None


def _extract_when_from_name_or_lines(log_name: str, lines: list[str], fallback_iso: str = "") -> str:
    for line in lines[:25]:
        m_nsf = _RE_NSF_TIME.match(line)
        if m_nsf:
            return m_nsf.group("ts").strip()
        m_iso = _RE_HEADER_ISO_TS.match(line)
        if m_iso:
            return m_iso.group("ts").strip()

    m_fn = _RE_FILENAME_CRASH_TS.search(Path(log_name).name)
    if m_fn:
        y, mo, d, h, mi, s = (
            int(m_fn.group("y")),
            int(m_fn.group("m")),
            int(m_fn.group("d")),
            int(m_fn.group("H")),
            int(m_fn.group("M")),
            int(m_fn.group("S")),
        )
        return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}"

    return fallback_iso or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def detect_crash_log_format(content: str, log_name: str = "") -> tuple[str, str]:
    """Detect ('crash_logger_sse' | 'trainwreck' | 'net_script_framework' | 'generic', label)."""
    head = "\n".join(content.splitlines()[:35])
    if "FrameworkName: NetScriptFramework" in head or "Possible relevant objects" in content:
        return ("net_script_framework", ".NET Script Framework")
    if re.search(r"\bTrainwreck\s+v\d", head, re.IGNORECASE):
        return ("trainwreck", "Trainwreck")
    if re.search(r"\bCrashLogger(?:SSE|AE|VR)?\b", head, re.IGNORECASE) or (
        "PROBABLE CALL STACK:" in content and _RE_CL_UNHANDLED.search(head)
    ):
        return ("crash_logger_sse", "Crash Logger SSE/AE/VR")
    return ("generic", "Engine Log")


def parse_structured_crash_log(
    content: str,
    *,
    log_name: str = "crash.log",
    known_mods: Optional[list[dict[str, Any]]] = None,
    fallback_when: str = "",
) -> Optional[CrashEntry]:
    """Parse Crash Logger SSE/AE/VR, Trainwreck, or .NET Script Framework crash log into a structured CrashEntry."""
    fmt, fmt_label = detect_crash_log_format(content, log_name)
    if fmt == "generic":
        return None

    lines = content.splitlines()
    when = _extract_when_from_name_or_lines(log_name, lines, fallback_when)
    mod_lookup = _build_mod_lookup(known_mods)

    exception = "Unhandled Native Exception"
    address = ""
    fault_module = ""

    for line in lines[:35]:
        m_cl = _RE_CL_UNHANDLED.search(line)
        if m_cl:
            exception = m_cl.group("exc").strip()
            raw_addr = m_cl.group("addr").strip()
            mod = (m_cl.group("mod") or "").strip()
            off = (m_cl.group("off") or "").strip()
            if mod and off:
                address = f"{raw_addr} ({mod}+{off})"
                fault_module = mod
            else:
                address = raw_addr
            break
        m_nsf = _RE_NSF_UNHANDLED.search(line)
        if m_nsf:
            exception = "Unhandled Native Exception"
            raw_addr = m_nsf.group("addr").strip()
            mod = m_nsf.group("mod").strip()
            off = m_nsf.group("off").strip()
            address = f"{raw_addr} ({mod}+{off})"
            fault_module = mod
            break

    # Parse sections: call stack & relevant objects / registers / stack
    call_stack: list[str] = []
    relevant_objects: list[str] = []
    section = ""

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped in ("{", "}"):
            continue
        upper = stripped.upper()
        if upper.startswith("PROBABLE CALL STACK") or upper.startswith("PROBABLE CALLSTACK"):
            section = "call_stack"
            continue
        if upper.startswith("POSSIBLE RELEVANT OBJECTS"):
            section = "relevant_objects"
            continue
        if upper.startswith("REGISTERS"):
            section = "registers"
            continue
        if upper.startswith("STACK"):
            section = "stack"
            continue
        if upper.startswith(("MODULES", "SKSE PLUGINS", "PLUGINS", "SYSTEM SPECS", "GAME CRASH")):
            section = "other"
            continue

        if section == "call_stack":
            if stripped.startswith("["):
                call_stack.append(stripped)
        elif section in ("relevant_objects", "registers", "stack"):
            if section == "relevant_objects" and stripped.startswith("["):
                relevant_objects.append(stripped)
            elif "File:" in stripped or "Modified by:" in stripped:
                relevant_objects.append(stripped)

    # Score & rank candidate culprits (DLLs and plugins)
    candidates: dict[str, dict[str, Any]] = {}

    def _add_candidate(artifact: str, kind: str, source: str, points: int, reason: str) -> None:
        clean = artifact.strip().strip("`'\"")
        if not clean:
            return
        if kind == "dll" and _is_ignored_binary(clean):
            return
        if kind == "plugin" and _is_official_master(clean):
            return
        key = clean.casefold()
        if key not in candidates:
            candidates[key] = {
                "artifact": clean,
                "kind": kind,
                "source": source,
                "score": points,
                "reasons": [reason],
            }
        else:
            # Repeat mentions add a bounded frequency bonus rather than doubling a register/stack score past callstack DLLs.
            bonus = min(12, max(4, points // 4))
            candidates[key]["score"] = max(candidates[key]["score"], points) + bonus
            if reason not in candidates[key]["reasons"]:
                candidates[key]["reasons"].append(reason)

    if fault_module and not _is_ignored_binary(fault_module):
        _add_candidate(
            fault_module,
            "dll",
            "exception_site",
            100,
            f"Faulting module at exception address ({address})",
        )

    for idx, frame in enumerate(call_stack):
        for m_mod in _RE_STACK_MODULE.finditer(frame):
            mod_name = m_mod.group("mod")
            off = m_mod.group("off")
            if not _is_ignored_binary(mod_name):
                pts = max(35, 85 - idx * 8)
                _add_candidate(
                    mod_name,
                    "dll",
                    "call_stack",
                    pts,
                    f"Call stack frame [{idx}] ({mod_name}+{off})",
                )

    for idx, obj_line in enumerate(relevant_objects):
        # 1. Check "Modified by: Skyrim.esm -> Dawnguard.esm -> Lux.esp" (last plugin in chain wins)
        m_chain = _RE_MODIFIED_BY.search(obj_line)
        if m_chain:
            parts = [p.strip() for p in m_chain.group("chain").split("->") if p.strip()]
            for p in reversed(parts):
                if not _is_official_master(p):
                    _add_candidate(
                        p,
                        "plugin",
                        "relevant_objects",
                        max(12, 48 - idx * 4),
                        f"Last overriding plugin in relevant object chain ({m_chain.group('chain').strip()})",
                    )
                    break

        # 2. Check File: "Lux.esp" or File: `OBIS SE.esp <- Skyrim.esm` (first non-official plugin in <- chain wins)
        for m_file in _RE_FILE_ATTR.finditer(obj_line):
            raw_files = m_file.group("files")
            parts = [p.strip() for p in re.split(r"<-|->", raw_files) if p.strip()]
            for p in parts:
                m_plug = _RE_PLUGIN_TOKEN.search(p)
                plug_name = m_plug.group("plugin").strip() if m_plug else p
                if not _is_official_master(plug_name):
                    _add_candidate(
                        plug_name,
                        "plugin",
                        "relevant_objects",
                        max(10, 45 - idx * 4),
                        f"Referenced in relevant objects/registers ({plug_name})",
                    )
                    break

    sorted_items = sorted(
        candidates.values(),
        key=lambda item: (-int(item["score"]), item["artifact"].casefold()),
    )

    culprits: list[CrashCulprit] = []
    for rank_idx, item in enumerate(sorted_items, start=1):
        artifact = item["artifact"]
        matched_mod = _match_artifact_to_mod(artifact, mod_lookup)
        mod_id = str(matched_mod.get("mod_id") or "") or None if matched_mod else None
        mod_name = str(matched_mod.get("name") or "") or None if matched_mod else None
        mod_enabled = bool(matched_mod.get("enabled", True)) if matched_mod else True
        rec_fix = None
        if mod_id:
            rec_fix = {
                "action": "disable_mod",
                "mod_id": mod_id,
                "mod_name": mod_name or mod_id,
                "artifact": artifact,
                "description": f"Disable '{mod_name or mod_id}' (provides {artifact})",
            }
        culprits.append(
            CrashCulprit(
                rank=rank_idx,
                artifact=artifact,
                kind=item["kind"],
                source=item["source"],
                score=int(item["score"]),
                reason="; ".join(item["reasons"]),
                mod_id=mod_id,
                mod_name=mod_name,
                enabled=mod_enabled,
                recommended_fix=rec_fix,
            )
        )

    if culprits:
        top = culprits[0]
        top_label = f"{top.mod_name} ({top.artifact})" if top.mod_name else top.artifact
        summary = f"{exception} at {address or 'unknown address'} — probable culprit: {top_label}."
    else:
        summary = f"{exception} at {address or 'unknown address'} (no third-party DLL or plugin identified on stack)."

    return CrashEntry(
        log_path=log_name,
        format=fmt,
        format_label=fmt_label,
        when=when,
        exception=exception,
        address=address,
        summary=summary,
        culprits=culprits,
        call_stack=call_stack,
        relevant_objects=relevant_objects,
    )


class ModLogAnalyzer:
    """Discovers, parses, and correlates game and runner logs to active mod items."""

    def __init__(self, search_roots: Optional[list[Path | str]] = None) -> None:
        self.search_roots = [Path(r).resolve() for r in (search_roots or []) if Path(r).exists()]

    def discover_logs(
        self,
        install_directory: Optional[Path | str] = None,
        compat_data_path: Optional[Path | str] = None,
        extra_paths: Optional[list[Path | str]] = None,
        *,
        wine_prefix: Optional[Path | str] = None,
        profile_directory: Optional[Path | str] = None,
        overwrite_directory: Optional[Path | str] = None,
    ) -> list[Path]:
        """Find candidate log files across Proton prefixes (My Games/<game>/SKSE), overwrite/profile folders, and install dirs."""
        candidates: list[Path] = []
        roots: list[Path] = []

        for raw_root in (install_directory, compat_data_path, wine_prefix, profile_directory, overwrite_directory):
            if raw_root and Path(raw_root).exists():
                roots.append(Path(raw_root).resolve())
        for ep in extra_paths or []:
            if ep and Path(ep).exists():
                roots.append(Path(ep).resolve())
        roots.extend(self.search_roots)

        # Standard log paths across Proton prefixes, MO2 overwrite/profiles, Bethesda/SKSE/.NET Script Framework, Unity, Unreal
        log_globs = [
            # Direct / flat folder
            "crash-*.log",
            "Crash_*.txt",
            "SKSE/*.log",
            "SKSE/Plugins/CrashLogs/crash-*.log",
            "F4SE/*.log",
            "F4SE/Plugins/CrashLogs/crash-*.log",
            "NetScriptFramework/Crash/*.txt",
            "NetScriptFramework/Crash/Crash_*.txt",
            "Data/NetScriptFramework/Crash/*.txt",
            "Data/SKSE/Plugins/CrashLogs/crash-*.log",
            # Mod manager overwrite & profile subtrees
            "overwrite/NetScriptFramework/Crash/*.txt",
            "overwrite/SKSE/Plugins/CrashLogs/crash-*.log",
            "overwrite/SKSE/*.log",
            "overwrite/crash-*.log",
            "*/overwrite/NetScriptFramework/Crash/*.txt",
            "*/overwrite/SKSE/Plugins/CrashLogs/crash-*.log",
            # Proton prefix (compatdata/<appid>/pfx/drive_c/users/<user>/Documents/My Games/<game>/SKSE)
            "pfx/drive_c/users/*/Documents/My Games/*/SKSE/*.log",
            "pfx/drive_c/users/*/Documents/My Games/*/SKSE/CrashLogs/*.log",
            "pfx/drive_c/users/*/Documents/My Games/*/F4SE/*.log",
            "pfx/drive_c/users/*/Documents/My Games/*/F4SE/CrashLogs/*.log",
            "pfx/drive_c/users/*/My Documents/My Games/*/SKSE/*.log",
            "pfx/drive_c/users/*/My Documents/My Games/*/SKSE/CrashLogs/*.log",
            "drive_c/users/*/Documents/My Games/*/SKSE/*.log",
            "drive_c/users/*/Documents/My Games/*/SKSE/CrashLogs/*.log",
            "drive_c/users/*/Documents/My Games/*/F4SE/*.log",
            "Documents/My Games/*/SKSE/*.log",
            "Documents/My Games/*/SKSE/CrashLogs/*.log",
            "My Games/*/SKSE/*.log",
            "My Games/*/SKSE/CrashLogs/*.log",
            # Unity / BepInEx
            "BepInEx/LogOutput.log",
            "BepInEx/LogOutput.log.1",
            "*/Player.log",
            "Player.log",
            "*_Data/output_log.txt",
            # Unreal / SML / UE4SS
            "*/Saved/Logs/*.log",
            "Saved/Logs/*.log",
            "UE4SS.log",
            "bin/UE4SS.log",
            # Wine / Proton
            "steam-*.log",
        ]

        seen: set[str] = set()
        for root in roots:
            if root.is_file():
                s = str(root)
                if s not in seen:
                    seen.add(s)
                    candidates.append(root)
                continue
            for pattern in log_globs:
                for match in root.glob(pattern):
                    if match.is_file() and str(match) not in seen:
                        seen.add(str(match))
                        candidates.append(match)

        # Sort by modification time descending (newest first), with filename reverse tie-breaker
        candidates.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
        return candidates

    def analyze_log_content(
        self,
        content: str,
        log_name: str = "log",
        known_mods: Optional[list[dict[str, Any]]] = None,
    ) -> LogReport:
        """Parse raw log text, handling Crash Logger SSE/AE/VR, Trainwreck, .NET Script Framework, and generic engine logs."""
        now_iso = datetime.now(timezone.utc).isoformat()
        structured = parse_structured_crash_log(
            content,
            log_name=log_name,
            known_mods=known_mods,
            fallback_when=now_iso,
        )
        if structured is not None:
            findings: list[DiagnosticFinding] = []
            if structured.culprits:
                for culp in structured.culprits:
                    findings.append(
                        DiagnosticFinding(
                            severity="critical" if culp.rank == 1 else "error",
                            error_type=structured.exception,
                            message=f"#{culp.rank} {culp.artifact} ({culp.reason})",
                            culprit_mod_id=culp.mod_id,
                            culprit_mod_name=culp.mod_name,
                            source_file=log_name,
                            line_number=1,
                            stack_trace=list(structured.call_stack[:15]),
                            recommended_fix=culp.recommended_fix,
                        )
                    )
            else:
                findings.append(
                    DiagnosticFinding(
                        severity="critical",
                        error_type=structured.exception,
                        message=f"{structured.exception} at {structured.address}",
                        source_file=log_name,
                        line_number=1,
                        stack_trace=list(structured.call_stack[:15]),
                    )
                )

            culprit_dicts = [asdict(c) for c in structured.culprits]
            crash_dict = asdict(structured)
            return LogReport(
                log_path=log_name,
                file_size_bytes=len(content.encode("utf-8")),
                last_modified=now_iso,
                healthy=False,
                summary=structured.summary,
                findings=findings,
                raw_error_count=max(1, len(findings)),
                filtered_line_count=0,
                crash_format=structured.format,
                crash_format_label=structured.format_label,
                when=structured.when,
                exception=structured.exception,
                address=structured.address,
                culprits=culprit_dicts,
                call_stack=list(structured.call_stack),
                relevant_objects=list(structured.relevant_objects),
                crashes=[crash_dict],
            )

        # Fallback: generic engine log parser (Unity/BepInEx, Unreal/UE4SS, generic SKSE)
        lines = content.splitlines()
        findings = []
        raw_error_count = 0
        filtered_lines = 0
        mod_lookup = _build_mod_lookup(known_mods)

        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue

            if any(pat.search(line) for pat in _IGNORABLE_NOISE):
                filtered_lines += 1
                i += 1
                continue

            matched = False
            for pat in _CRASH_PATTERNS:
                m = pat.search(line)
                if m:
                    matched = True
                    raw_error_count += 1
                    err_type = m.groupdict().get("type") or "Crash"
                    err_msg = (m.groupdict().get("msg") or line).strip()

                    stack: list[str] = []
                    j = i + 1
                    while j < min(len(lines), i + 25):
                        nxt = lines[j]
                        if re.match(r"^\s+at\s+", nxt) or re.match(r"^\s+in\s+", nxt) or "stack trace" in nxt.lower():
                            stack.append(nxt.strip())
                            j += 1
                        else:
                            break

                    culprit_id = None
                    culprit_name = None
                    search_corpus = (line + " " + " ".join(stack)).lower()
                    for token, mod_meta in mod_lookup.items():
                        if token and len(token) >= 3 and token in search_corpus:
                            culprit_id = str(mod_meta.get("mod_id", ""))
                            culprit_name = str(mod_meta.get("name", ""))
                            break

                    rec_fix = None
                    if culprit_id:
                        if "dllnotfoundexception" in err_type.lower() or "filenotfoundexception" in err_type.lower():
                            rec_fix = {
                                "action": "install_dependency",
                                "mod_id": culprit_id,
                                "mod_name": culprit_name or culprit_id,
                                "description": f"Missing prerequisite assembly for '{culprit_name}'. Install required framework.",
                            }
                        else:
                            rec_fix = {
                                "action": "disable_mod",
                                "mod_id": culprit_id,
                                "mod_name": culprit_name or culprit_id,
                                "description": f"Disable crashing mod '{culprit_name}' to restore stability.",
                            }

                    findings.append(
                        DiagnosticFinding(
                            severity="critical" if "crash" in err_type.lower() or "nullreference" in err_type.lower() or "fatal" in err_type.lower() else "error",
                            error_type=err_type,
                            message=err_msg,
                            culprit_mod_id=culprit_id,
                            culprit_mod_name=culprit_name,
                            source_file=log_name,
                            line_number=i + 1,
                            stack_trace=stack,
                            recommended_fix=rec_fix,
                        )
                    )
                    i = j
                    break

            if not matched:
                i += 1

        is_healthy = len([f for f in findings if f.severity in ("critical", "error")]) == 0
        if is_healthy:
            summary = "No crashes or critical mod exceptions detected in log."
        else:
            culprits = [f.culprit_mod_name for f in findings if f.culprit_mod_name]
            unique_culprits = list(dict.fromkeys(culprits))
            if unique_culprits:
                summary = f"Detected {len(findings)} exception(s). Primary culprit mod(s): {', '.join(unique_culprits)}."
            else:
                summary = f"Detected {len(findings)} unhandled exception(s) or crash signatures."

        generic_culprits: list[dict[str, Any]] = []
        for idx, f in enumerate([item for item in findings if item.culprit_mod_id], start=1):
            generic_culprits.append({
                "rank": idx,
                "artifact": f.culprit_mod_name or f.culprit_mod_id or "Mod",
                "kind": "mod",
                "source": "log_pattern",
                "score": max(10, 80 - idx * 10),
                "reason": f"{f.error_type}: {f.message}",
                "mod_id": f.culprit_mod_id,
                "mod_name": f.culprit_mod_name,
                "enabled": True,
                "recommended_fix": f.recommended_fix,
            })

        crashes_list: list[dict[str, Any]] = []
        if not is_healthy and findings:
            top_f = findings[0]
            crashes_list.append({
                "log_path": log_name,
                "format": "generic",
                "format_label": "Engine Log",
                "when": now_iso,
                "exception": top_f.error_type,
                "address": f"line {top_f.line_number or 1}",
                "summary": summary,
                "culprits": generic_culprits,
                "call_stack": list(top_f.stack_trace),
                "relevant_objects": [],
            })

        return LogReport(
            log_path=log_name,
            file_size_bytes=len(content.encode("utf-8")),
            last_modified=now_iso,
            healthy=is_healthy,
            summary=summary,
            findings=findings,
            raw_error_count=raw_error_count,
            filtered_line_count=filtered_lines,
            crash_format="generic",
            crash_format_label="Engine Log",
            when=now_iso,
            exception=findings[0].error_type if findings else "",
            address=f"line {findings[0].line_number or 1}" if findings else "",
            culprits=generic_culprits,
            call_stack=list(findings[0].stack_trace) if findings else [],
            relevant_objects=[],
            crashes=crashes_list,
        )

    def analyze_file(
        self,
        log_path: Path | str,
        known_mods: Optional[list[dict[str, Any]]] = None,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> LogReport:
        """Read and analyze a log file on disk."""
        path = Path(log_path)
        if not path.is_file():
            return LogReport(
                log_path=str(path),
                file_size_bytes=0,
                last_modified="",
                healthy=False,
                summary=f"Log file not found: {path}",
            )

        try:
            st = path.stat()
            size = st.st_size
            mtime = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()

            if size > max_bytes:
                with open(path, "rb") as f:
                    f.seek(size - max_bytes)
                    raw = f.read().decode("utf-8", errors="replace")
            else:
                raw = path.read_text(encoding="utf-8", errors="replace")

            report = self.analyze_log_content(raw, log_name=str(path), known_mods=known_mods)
            report.file_size_bytes = size
            report.last_modified = mtime
            if report.when == "" or report.crash_format == "generic":
                report.when = mtime
            return report
        except Exception as e:
            return LogReport(
                log_path=str(path),
                file_size_bytes=0,
                last_modified="",
                healthy=False,
                summary=f"Failed to read log {path}: {e}",
            )
