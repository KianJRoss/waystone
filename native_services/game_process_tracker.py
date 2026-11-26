"""Application lifetime tracking for Steam games, emulators, and native executables.

Tracks actual game processes rather than transient launcher PIDs.
Steam and runner processes are identified by targeted /proc/<pid>/environ inspection
(SteamAppId, SteamGameId, STEAM_COMPAT_APP_ID, LUTRIS_GAME_UUID, KIAN_GAME_SESSION_ID).
Only these specific variables are read; raw environment is never persisted or exposed.
Steam client, helper, and container wrapper processes (even when holding the App ID in URI/arguments)
are strictly excluded.
Adopts Lutris-informed process filtering concepts (UUID environment tagging, game folder scoping,
prelaunch PID diffing, and configurable include/exclude filters) with our own independent architecture.
Process identity uses (PID, starttime) pairs to prevent PID reuse aliasing.
Lifecycle states: launching -> running -> closed (or failed on startup timeout, unknown on permission denial).
Read-only inspection; never kills or modifies processes.
Injectable proc source and clock for offline testing.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import os
from pathlib import Path
import re
import time
from typing import Callable, Iterable
import uuid

# Steam client and container wrapper processes to strictly exclude
STEAM_WRAPPER_NAMES = frozenset({
    "systemd-run",
    "steam",
    "steamwebhelper",
    "steamservice",
    "steamrt64",
    "reaper",
    "pressure-vessel",
    "pressure-vessel-wrap",
    "pressure-vessel-adverb",
    "bwrap",
    "srt-bwrap",
    "pv-bwrap",
    "srt-logger",
    "steam-runtime",
    "steam-runtime-launch-client",
    "heroic",
    "heroicgameslauncher",
    "legendary",
    "gogdl",
    "nile",
})

# Targeted environment variable prefixes for game identification
TARGET_ENV_PREFIXES = (
    (b"SteamAppId=", "SteamAppId"),
    (b"SteamGameId=", "SteamGameId"),
    (b"STEAM_COMPAT_APP_ID=", "STEAM_COMPAT_APP_ID"),
    (b"LUTRIS_GAME_UUID=", "LUTRIS_GAME_UUID"),
    (b"KIAN_GAME_SESSION_ID=", "KIAN_GAME_SESSION_ID"),
)


class ProcPermissionError(PermissionError):
    """Raised when /proc inspection is blocked by kernel permissions."""


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    starttime: int
    comm: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrackedGameSession:
    session_id: str
    target_id: str                          # e.g. '1276390' or 'rom:switch:mario'
    provider_or_platform: str               # 'steam', 'switch', 'lutris-battlenet', etc.
    target_type: str                        # 'native_game', 'rom', 'custom'
    state: str                              # 'launching', 'running', 'closed', 'failed', 'unknown'
    launcher_pid: int | None = None
    launcher_pid_is_game: bool = True  # monitored owner wrappers are not themselves the game
    tracked_processes: dict[int, ProcessIdentity] = field(default_factory=dict)
    target_executable: str | None = None
    target_payload: str | None = None
    game_folder: str | None = None          # Folder-scoped runner matching
    include_processes: tuple[str, ...] = () # Process names to explicitly track (e.g. gamescope)
    exclude_processes: tuple[str, ...] = () # Runner/client processes to exclude
    prelaunch_pids: frozenset[int] | None = None  # PIDs running prior to launch
    launch_time: float = 0.0
    running_since: float | None = None
    closed_at: float | None = None
    startup_timeout: float = 30.0
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tracked_processes"] = {str(k): v.to_dict() for k, v in self.tracked_processes.items()}
        d["include_processes"] = list(self.include_processes)
        d["exclude_processes"] = list(self.exclude_processes)
        d["prelaunch_pids"] = list(self.prelaunch_pids) if self.prelaunch_pids is not None else None
        return d


class ProcReader:
    """Reads process state from /proc with bounded error handling.

    Injectable proc_dir allows 100% offline unit tests without host inspection.
    """

    def __init__(self, proc_dir: str | Path = "/proc"):
        self.proc_dir = Path(proc_dir)

    def list_pids(self) -> list[int]:
        pids: list[int] = []
        if not self.proc_dir.exists():
            return pids
        try:
            for entry in os.scandir(self.proc_dir):
                if entry.is_dir() and entry.name.isdigit():
                    pids.append(int(entry.name))
        except (OSError, PermissionError):
            pass
        return sorted(pids)

    def read_stat(self, pid: int) -> tuple[int, str, int] | None:
        """Returns (ppid, comm, starttime) from /proc/<pid>/stat, or None."""
        stat_path = self.proc_dir / str(pid) / "stat"
        try:
            with open(stat_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            lparen = content.find("(")
            rparen = content.rfind(")")
            if lparen == -1 or rparen == -1:
                return None
            comm = content[lparen + 1 : rparen]
            rest = content[rparen + 2 :].split()
            ppid = int(rest[1])          # 4th field in /proc/pid/stat
            starttime = int(rest[19])    # 22nd field in /proc/pid/stat
            return (ppid, comm, starttime)
        except PermissionError:
            raise ProcPermissionError(f"Permission denied reading stat for pid {pid}")
        except (OSError, ValueError, IndexError):
            return None

    def read_cmdline(self, pid: int) -> tuple[str, ...]:
        cmd_path = self.proc_dir / str(pid) / "cmdline"
        try:
            with open(cmd_path, "rb") as f:
                raw = f.read()
            if not raw:
                return ()
            parts = [p.decode("utf-8", errors="replace") for p in raw.split(b"\x00") if p]
            return tuple(parts)
        except PermissionError:
            raise ProcPermissionError(f"Permission denied reading cmdline for pid {pid}")
        except OSError:
            return ()

    def read_exe(self, pid: int) -> str | None:
        exe_path = self.proc_dir / str(pid) / "exe"
        try:
            return os.readlink(exe_path)
        except PermissionError:
            raise ProcPermissionError(f"Permission denied reading exe symlink for pid {pid}")
        except OSError:
            return None

    def read_cwd(self, pid: int) -> str | None:
        cwd_path = self.proc_dir / str(pid) / "cwd"
        try:
            return os.readlink(cwd_path)
        except PermissionError:
            raise ProcPermissionError(f"Permission denied reading cwd symlink for pid {pid}")
        except OSError:
            return None

    def read_targeted_env(self, pid: int) -> dict[str, str]:
        """Read ONLY target game identification variables from environ.

        Never reads, parses, logs, or persists any other environment variables.
        """
        env_path = self.proc_dir / str(pid) / "environ"
        env_vars: dict[str, str] = {}
        try:
            with open(env_path, "rb") as f:
                raw = f.read()
            for chunk in raw.split(b"\x00"):
                for prefix, var_name in TARGET_ENV_PREFIXES:
                    if chunk.startswith(prefix):
                        val = chunk[len(prefix) :].decode("utf-8", errors="replace").strip()
                        if val:
                            env_vars[var_name] = val
        except PermissionError:
            raise ProcPermissionError(f"Permission denied reading environ for pid {pid}")
        except OSError:
            pass
        return env_vars

    def read_steam_app_ids(self, pid: int) -> set[str]:
        """Convenience method returning set of Steam AppIDs for pid."""
        env_vars = self.read_targeted_env(pid)
        ids = set()
        for k in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
            if k in env_vars and env_vars[k].isdigit():
                ids.add(env_vars[k])
        return ids


def is_excluded_wrapper(
    comm: str,
    exe: str | None = None,
    cmdline: tuple[str, ...] = (),
    custom_exclusions: Iterable[str] = (),
) -> bool:
    """Returns True if process is a Steam client, runner wrapper, or excluded helper."""
    comm_lower = comm.lower()
    exclusions = STEAM_WRAPPER_NAMES | {c.lower() for c in custom_exclusions}

    if comm_lower in exclusions:
        return True
    if any(wrapper in comm_lower for wrapper in ("steamwebhelper", "pressure-vessel", "srt-bwrap")):
        return True
    if exe:
        exe_base = Path(exe).name.lower()
        if exe_base in exclusions or "steamwebhelper" in exe_base or "pressure-vessel" in exe_base:
            return True
    if cmdline:
        first = Path(cmdline[0]).name.lower() if cmdline[0] else ""
        if first in exclusions:
            return True
    return False


is_steam_wrapper = is_excluded_wrapper


class GameProcessTracker:
    """Tracks application lifetime from launch through active execution to shutdown."""

    def __init__(
        self,
        *,
        proc_reader: ProcReader | None = None,
        clock: Callable[[], float] | None = None,
        default_startup_timeout: float = 30.0,
    ):
        self.proc = proc_reader or ProcReader()
        self.clock = clock or time.time
        self.default_startup_timeout = default_startup_timeout
        self.sessions: dict[str, TrackedGameSession] = {}

    def capture_prelaunch_pids(self) -> frozenset[int]:
        """Convenience helper to record running PIDs right before launching a game."""
        return frozenset(self.proc.list_pids())

    def start_tracking(
        self,
        target_id: str,
        provider_or_platform: str,
        *,
        target_type: str = "rom",
        launcher_pid: int | None = None,
        launcher_pid_is_game: bool = True,
        target_executable: str | None = None,
        target_payload: str | None = None,
        game_folder: str | None = None,
        include_processes: Iterable[str] = (),
        exclude_processes: Iterable[str] = (),
        prelaunch_pids: Iterable[int] | None = None,
        startup_timeout: float | None = None,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> TrackedGameSession:
        """Register a newly launched game and begin lifecycle tracking."""
        sid = session_id or f"sess-{uuid.uuid4().hex[:12]}"
        timeout = startup_timeout if startup_timeout is not None else self.default_startup_timeout
        now = self.clock()

        p_pids = frozenset(prelaunch_pids) if prelaunch_pids is not None else None

        session = TrackedGameSession(
            session_id=sid,
            target_id=str(target_id),
            provider_or_platform=str(provider_or_platform),
            target_type=str(target_type),
            state="launching",
            launcher_pid=launcher_pid,
            launcher_pid_is_game=bool(launcher_pid_is_game),
            target_executable=target_executable,
            target_payload=target_payload,
            game_folder=str(game_folder) if game_folder else None,
            include_processes=tuple(include_processes),
            exclude_processes=tuple(exclude_processes),
            prelaunch_pids=p_pids,
            launch_time=now,
            startup_timeout=timeout,
            metadata=dict(metadata or {}),
        )

        self.sessions[sid] = session
        # Immediate poll to identify process if already spawned
        return self.poll_session(sid)

    def poll_session(self, session_id: str) -> TrackedGameSession:
        """Poll and transition state for a specific tracked session."""
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(f"No tracked session with ID '{session_id}'")

        if session.state in ("closed", "failed"):
            return session

        now = self.clock()
        pids = self.proc.list_pids()
        permission_denied_encountered = False

        # 1. If currently RUNNING: verify tracked processes are still active with matching starttime
        if session.state == "running":
            alive_processes: dict[int, ProcessIdentity] = {}
            for pid, identity in list(session.tracked_processes.items()):
                try:
                    stat_info = self.proc.read_stat(pid)
                    if stat_info is not None:
                        _, comm, starttime = stat_info
                        # Check starttime to prevent PID reuse aliasing
                        if starttime == identity.starttime:
                            alive_processes[pid] = identity
                except ProcPermissionError:
                    # Keep previously verified process alive if permission temporarily denied
                    alive_processes[pid] = identity
                except Exception:
                    pass

            # Also check for any new descendant/children processes spawned by the game
            new_descendants = self._scan_descendants(pids, set(alive_processes.keys()))
            alive_processes.update(new_descendants)

            if alive_processes:
                session.tracked_processes = alive_processes
                return session

            # All tracked processes have terminated
            session.state = "closed"
            session.closed_at = now
            session.tracked_processes = {}
            return session

        # 2. If LAUNCHING or UNKNOWN: scan for actual game process
        # If prelaunch_pids was captured, prioritize newly started PIDs
        candidate_pids = pids
        if session.prelaunch_pids:
            new_pids = [p for p in pids if p not in session.prelaunch_pids]
            if new_pids:
                candidate_pids = new_pids + [p for p in pids if p in session.prelaunch_pids]

        found_processes: dict[int, ProcessIdentity] = {}

        # Check targeted environment variables (SteamAppId, LUTRIS_GAME_UUID, KIAN_GAME_SESSION_ID)
        found_processes, permission_denied_encountered = self._find_tagged_processes(
            candidate_pids,
            target_id=session.target_id,
            session_id=session.session_id,
            custom_exclusions=session.exclude_processes,
            custom_inclusions=session.include_processes,
        )

        # Check native / emulator / folder-scoped processes
        if (
            session.target_executable or session.launcher_pid or session.target_payload or session.game_folder
        ):
            native_procs, perm_denied = self._find_native_or_emulator_processes(
                candidate_pids,
                launcher_pid=session.launcher_pid if session.launcher_pid_is_game else None,
                # A launch wrapper carries the game folder in its own argv: it and Waystone itself are never the game.
                excluded_pids=frozenset({os.getpid()} | (set() if session.launcher_pid_is_game or session.launcher_pid is None
                                                         else {session.launcher_pid})),
                target_executable=session.target_executable,
                target_payload=session.target_payload,
                game_folder=session.game_folder,
                custom_exclusions=session.exclude_processes,
                custom_inclusions=session.include_processes,
            )
            found_processes.update(native_procs)
            permission_denied_encountered = permission_denied_encountered or perm_denied

        if found_processes:
            session.state = "running"
            session.running_since = now
            session.tracked_processes = found_processes
            session.error = None
            return session

        # No matching game process found yet: check startup timeout
        if now - session.launch_time >= session.startup_timeout:
            if permission_denied_encountered:
                session.state = "unknown"
                session.error = "Process state unknown: permission denied reading system processes"
            else:
                session.state = "failed"
                session.error = (
                    f"Startup timeout: no game process detected for target '{session.target_id}' "
                    f"within {session.startup_timeout:.1f}s"
                )
        elif permission_denied_encountered and session.state == "unknown":
            session.state = "unknown"

        return session

    def poll_all(self) -> dict[str, TrackedGameSession]:
        """Poll all active sessions and update their states."""
        for sid in list(self.sessions.keys()):
            self.poll_session(sid)
        return dict(self.sessions)

    def get_session(self, session_id: str) -> TrackedGameSession | None:
        return self.sessions.get(session_id)

    def detect_active_games(self) -> list[dict]:
        """Snapshot /proc scan to discover currently running games on system reconnect.

        Returns detected Steam games, Lutris games, and running game processes without prior session.
        """
        pids = self.proc.list_pids()
        discovered: list[dict] = []

        tagged_games: dict[str, list[ProcessIdentity]] = {}
        for pid in pids:
            try:
                stat_info = self.proc.read_stat(pid)
                if not stat_info:
                    continue
                ppid, comm, starttime = stat_info
                exe = self.proc.read_exe(pid)
                cmdline = self.proc.read_cmdline(pid)

                # Skip client/runtime wrappers
                if is_excluded_wrapper(comm, exe, cmdline):
                    continue

                env_vars = self.proc.read_targeted_env(pid)
                identity = ProcessIdentity(pid=pid, starttime=starttime, comm=comm)

                # Check Steam App IDs
                for k in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
                    if k in env_vars and env_vars[k].isdigit():
                        tagged_games.setdefault(f"steam:{env_vars[k]}", []).append(identity)

                # Check Lutris game UUID
                if "LUTRIS_GAME_UUID" in env_vars:
                    tagged_games.setdefault(f"lutris:{env_vars['LUTRIS_GAME_UUID']}", []).append(identity)

                # Check Kian game session ID
                if "KIAN_GAME_SESSION_ID" in env_vars:
                    tagged_games.setdefault(f"session:{env_vars['KIAN_GAME_SESSION_ID']}", []).append(identity)

            except (ProcPermissionError, OSError):
                continue

        for key, identities in tagged_games.items():
            prefix, target_id = key.split(":", 1)
            discovered.append({
                "target_id": target_id,
                "provider_or_platform": prefix if prefix != "session" else "native",
                "target_type": "native_game",
                "pids": [i.pid for i in identities],
                "identities": [i.to_dict() for i in identities],
                "comm": identities[0].comm if identities else "game_process",
            })

        return discovered

    def _find_tagged_processes(
        self,
        pids: Iterable[int],
        *,
        target_id: str,
        session_id: str,
        custom_exclusions: tuple[str, ...],
        custom_inclusions: tuple[str, ...],
    ) -> tuple[dict[int, ProcessIdentity], bool]:
        """Scan processes for targeted SteamAppId, LUTRIS_GAME_UUID, or KIAN_GAME_SESSION_ID."""
        found: dict[int, ProcessIdentity] = {}
        permission_denied = False

        for pid in pids:
            try:
                stat_info = self.proc.read_stat(pid)
                if not stat_info:
                    continue
                ppid, comm, starttime = stat_info
                exe = self.proc.read_exe(pid)
                cmdline = self.proc.read_cmdline(pid)

                # Include overrides (e.g. gamescope) take priority over exclusions
                if custom_inclusions and comm.lower() in {c.lower() for c in custom_inclusions}:
                    found[pid] = ProcessIdentity(pid=pid, starttime=starttime, comm=comm)
                    continue

                # Exclude wrappers
                if is_excluded_wrapper(comm, exe, cmdline, custom_exclusions):
                    continue

                env_vars = self.proc.read_targeted_env(pid)
                matched = False

                # Match Steam IDs
                for k in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
                    if env_vars.get(k) == target_id:
                        matched = True
                        break

                # Match Lutris UUID
                if not matched and env_vars.get("LUTRIS_GAME_UUID") == target_id:
                    matched = True

                # Match Kian Session ID
                if not matched and env_vars.get("KIAN_GAME_SESSION_ID") in (session_id, target_id):
                    matched = True

                if matched:
                    found[pid] = ProcessIdentity(pid=pid, starttime=starttime, comm=comm)
            except ProcPermissionError:
                permission_denied = True
            except (OSError, ValueError):
                continue

        return found, permission_denied

    def _find_native_or_emulator_processes(
        self,
        pids: Iterable[int],
        *,
        launcher_pid: int | None,
        target_executable: str | None,
        target_payload: str | None,
        game_folder: str | None,
        custom_exclusions: tuple[str, ...],
        custom_inclusions: tuple[str, ...],
        excluded_pids: frozenset[int] = frozenset(),
    ) -> tuple[dict[int, ProcessIdentity], bool]:
        """Scan processes for matching executable, descendants of launcher_pid, payload, or game_folder."""
        found: dict[int, ProcessIdentity] = {}
        permission_denied = False
        target_exe_name = Path(target_executable).name.lower() if target_executable else None

        for pid in pids:
            if pid in excluded_pids:
                continue
            try:
                stat_info = self.proc.read_stat(pid)
                if not stat_info:
                    continue
                ppid, comm, starttime = stat_info
                exe = self.proc.read_exe(pid)
                cmdline = self.proc.read_cmdline(pid)

                # Check explicit inclusions
                if custom_inclusions and comm.lower() in {c.lower() for c in custom_inclusions}:
                    found[pid] = ProcessIdentity(pid=pid, starttime=starttime, comm=comm)
                    continue

                # Check exclusions
                if is_excluded_wrapper(comm, exe, cmdline, custom_exclusions):
                    continue

                matched = False
                # 1. Matches launcher PID directly
                if launcher_pid is not None and pid == launcher_pid:
                    matched = True
                # 2. Child of launcher PID
                elif launcher_pid is not None and ppid == launcher_pid:
                    matched = True
                # 3. Matches target executable name
                elif target_exe_name and comm.lower() == target_exe_name:
                    matched = True
                elif exe and target_executable and Path(exe).resolve() == Path(target_executable).resolve():
                    matched = True

                # 4. Check payload in command line
                if not matched and target_payload:
                    if any(target_payload in arg for arg in cmdline):
                        matched = True

                # 5. Check game_folder scoping (Lutris concept: game_folder in cmdline or cwd)
                if not matched and game_folder:
                    folder_str = str(Path(game_folder).resolve())
                    if any(folder_str in arg for arg in cmdline):
                        matched = True
                    else:
                        cwd = self.proc.read_cwd(pid)
                        if cwd:
                            cwd_resolved = str(Path(cwd).resolve())
                            if cwd_resolved == folder_str or cwd_resolved.startswith(folder_str + "/"):
                                matched = True

                if matched:
                    found[pid] = ProcessIdentity(pid=pid, starttime=starttime, comm=comm)
            except ProcPermissionError:
                permission_denied = True
            except (OSError, ValueError):
                continue

        return found, permission_denied

    def _scan_descendants(
        self, pids: Iterable[int], parent_pids: set[int]
    ) -> dict[int, ProcessIdentity]:
        """Find any child processes whose ppid is in parent_pids."""
        if not parent_pids:
            return {}
        descendants: dict[int, ProcessIdentity] = {}
        for pid in pids:
            if pid in parent_pids:
                continue
            try:
                stat_info = self.proc.read_stat(pid)
                if not stat_info:
                    continue
                ppid, comm, starttime = stat_info
                if ppid in parent_pids:
                    descendants[pid] = ProcessIdentity(pid=pid, starttime=starttime, comm=comm)
            except Exception:
                continue
        return descendants
