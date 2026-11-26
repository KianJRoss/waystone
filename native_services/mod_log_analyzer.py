"""Universal Log Diagnostics & Rapid Crash Triaging for Games, Modpacks, and Runners.

Designed for modpack creators, players, and autonomous agents:
- Discovers logs across Unity/BepInEx, Unreal Engine/SML/UE4SS, Bethesda/SKSE, Proton/Wine, and Waystone.
- Pre-filters noise, repetitious debug spam, and benign notices.
- Pinpoints fatal stack traces, NullReferenceExceptions, missing DLLs, and unresolved assembly references.
- Correlates errors directly with active staged mods in the modpack profile.
- Emits structured, machine-executable remediation plans (one-click agent fixes).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

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


class ModLogAnalyzer:
    """Discovers, parses, and correlates game and runner logs to active mod items."""

    def __init__(self, search_roots: Optional[list[Path | str]] = None) -> None:
        self.search_roots = [Path(r).resolve() for r in (search_roots or []) if Path(r).exists()]

    def discover_logs(
        self,
        install_directory: Optional[Path | str] = None,
        compat_data_path: Optional[Path | str] = None,
        extra_paths: Optional[list[Path | str]] = None,
    ) -> list[Path]:
        """Find candidate log files across game install directories, Proton prefixes, and temporary dirs."""
        candidates: list[Path] = []
        roots: list[Path] = []

        if install_directory and Path(install_directory).exists():
            roots.append(Path(install_directory).resolve())
        if compat_data_path and Path(compat_data_path).exists():
            roots.append(Path(compat_data_path).resolve())
        for ep in extra_paths or []:
            if Path(ep).exists():
                roots.append(Path(ep).resolve())
        roots.extend(self.search_roots)

        # Standard log paths
        log_globs = [
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
            # Bethesda
            "SKSE/*.log",
            "F4SE/*.log",
            "crash-*.log",
            "NetScriptFramework/Crash/*.txt",
            # Wine / Proton
            "steam-*.log",
        ]

        seen = set()
        for root in roots:
            for pattern in log_globs:
                for match in root.glob(pattern):
                    if match.is_file() and str(match) not in seen:
                        seen.add(str(match))
                        candidates.append(match)

        # Sort by modification time descending (newest first)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates

    def analyze_log_content(
        self,
        content: str,
        log_name: str = "log",
        known_mods: Optional[list[dict[str, Any]]] = None,
    ) -> LogReport:
        """Parse raw log text, strip noise, identify crashes, and correlate with known mods."""
        lines = content.splitlines()
        findings: list[DiagnosticFinding] = []
        raw_error_count = 0
        filtered_lines = 0

        # Build reverse lookup map from mod DLLs and names to mod metadata
        mod_lookup: dict[str, dict[str, Any]] = {}
        for mod in (known_mods or []):
            name = str(mod.get('name') or '').lower()
            mid = str(mod.get('mod_id') or '').lower()
            if name:
                mod_lookup[name] = mod
            if mid:
                mod_lookup[mid] = mod
            for f in mod.get('installed_files', []):
                p = Path(f).name.lower()
                if p:
                    mod_lookup[p] = mod
                    if p.endswith('.dll'):
                        mod_lookup[p[:-4]] = mod

        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue

            # Skip ignorable debug noise
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
                    err_type = m.groupdict().get('type') or 'Crash'
                    err_msg = (m.groupdict().get('msg') or line).strip()

                    # Collect subsequent stack trace lines
                    stack: list[str] = []
                    j = i + 1
                    while j < min(len(lines), i + 25):
                        nxt = lines[j]
                        if re.match(r"^\s+at\s+", nxt) or re.match(r"^\s+in\s+", nxt) or "stack trace" in nxt.lower():
                            stack.append(nxt.strip())
                            j += 1
                        else:
                            break

                    # Correlate with known mods
                    culprit_id = None
                    culprit_name = None
                    search_corpus = (line + " " + " ".join(stack)).lower()
                    for token, mod_meta in mod_lookup.items():
                        if token in search_corpus:
                            culprit_id = str(mod_meta.get('mod_id', ''))
                            culprit_name = str(mod_meta.get('name', ''))
                            break

                    # Suggest automated fix
                    rec_fix = None
                    if culprit_id:
                        if "dllnotfoundexception" in err_type.lower() or "filenotfoundexception" in err_type.lower():
                            rec_fix = {
                                "action": "install_dependency",
                                "mod_id": culprit_id,
                                "description": f"Missing prerequisite assembly for '{culprit_name}'. Install required framework.",
                            }
                        else:
                            rec_fix = {
                                "action": "disable_mod",
                                "mod_id": culprit_id,
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

        is_healthy = len([f for f in findings if f.severity in ('critical', 'error')]) == 0
        if is_healthy:
            summary = "No crashes or critical mod exceptions detected in log."
        else:
            culprits = {f.culprit_mod_name for f in findings if f.culprit_mod_name}
            if culprits:
                summary = f"Detected {len(findings)} exception(s). Primary culprit mod(s): {', '.join(culprits)}."
            else:
                summary = f"Detected {len(findings)} unhandled exception(s) or crash signatures."

        return LogReport(
            log_path=log_name,
            file_size_bytes=len(content.encode('utf-8')),
            last_modified=datetime.now(timezone.utc).isoformat(),
            healthy=is_healthy,
            summary=summary,
            findings=findings,
            raw_error_count=raw_error_count,
            filtered_line_count=filtered_lines,
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
            stat = path.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()

            # For massive logs (>10MB), read the tail where crashes occur
            if size > max_bytes:
                with open(path, "rb") as f:
                    f.seek(size - max_bytes)
                    raw = f.read().decode('utf-8', errors='replace')
            else:
                raw = path.read_text(encoding='utf-8', errors='replace')

            report = self.analyze_log_content(raw, log_name=str(path), known_mods=known_mods)
            report.file_size_bytes = size
            report.last_modified = mtime
            return report
        except Exception as e:
            return LogReport(
                log_path=str(path),
                file_size_bytes=0,
                last_modified="",
                healthy=False,
                summary=f"Failed to read log {path}: {e}",
            )
