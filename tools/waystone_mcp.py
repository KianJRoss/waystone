"""Official Waystone MCP server for Organizer and live app controls.

Provides a live-updating, AI-writable Model Context Protocol (MCP) transport using
the official MCP Python SDK (`mcp.server.mcpserver.MCPServer`):
- Real stdio transport over standard input/output streams.
- Strictly app-owned database writes committing to the same SQLite files Waystone watches.
- Scoped metadata edits (title, platform, tags, categories, libraries, etc.).
- Optimistic concurrency control via `expected_revision` checks and `request_id` idempotency.
- Duplicate proposal, application, and 1-step audited rollback.
- Strict integration with catalog_reconciliation.load_assignments.
- Live play/status/stop is brokered only through the running Waystone desktop's private local socket.
- Zero arbitrary shell or subprocess execution; no install actions are exposed here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Callable

# Ensure native_services package is resolvable regardless of invocation cwd
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Transparently bootstrap into cached official MCP SDK via uv if not in sys.path
try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    if __name__ == "__main__":
        uv_bin = shutil.which("uv")
        if uv_bin and not os.environ.get("_WAYSTONE_MCP_BOOTSTRAPPED"):
            os.environ["_WAYSTONE_MCP_BOOTSTRAPPED"] = "1"
            script_path = str(Path(__file__).resolve())
            os.execv(uv_bin, ["uv", "run", "--offline", "--with", "mcp", "python", script_path] + sys.argv[1:])
    MCPServer = None

    class ToolError(Exception):
        """Fallback ToolError when MCP SDK is unavailable outside __main__."""

from native_services.organizer_store import OrganizerStore
from native_services.catalog_link_store import CatalogLinkStore
from native_services.organizer_control import OrganizerControl, ControlError, ConcurrencyConflict

_STALE_REV_RE = re.compile(
    r"^Stale (link )?revision: caller expected revision (\d+), "
    r"but current database revision is (\d+)\. Refresh state before applying changes\.$"
)
_GAME_NOT_FOUND_RE = re.compile(
    r"^Game with canonical ID '([A-Za-z0-9._:-]{1,128})' not found in organizer catalog$"
)
_FIXED_CONTROL_MESSAGES = {
    "Must supply at least one edit": "Must supply at least one edit.",
    "CatalogLinkStore is not configured": "CatalogLinkStore is not configured.",
    "Each group needs canonical_ids and representative_id": "Each group needs canonical_ids and representative_id.",
}


def _format_conflict_error(exc: ConcurrencyConflict) -> str:
    """Return an actionable conflict message using only validated patterns or fixed text."""
    raw = str(exc).strip()
    m = _STALE_REV_RE.fullmatch(raw)
    if m:
        kind = "link " if m.group(1) else ""
        expected_rev = int(m.group(2))
        current_rev = int(m.group(3))
        return (
            f"Conflict: Stale {kind}revision: caller expected revision {expected_rev}, "
            f"but current database revision is {current_rev}. Refresh state before applying changes."
        )
    if raw == "Request ID already used for a different operation":
        return "Conflict: Request ID already used for a different operation."
    return "Conflict: Organizer state changed concurrently. Refresh state and retry with a new request ID."


def _format_control_error(exc: ControlError) -> str:
    """Allowlist known safe ControlError messages; return fixed text for unrecognized/wrapped errors."""
    raw = str(exc).strip()
    if raw in _FIXED_CONTROL_MESSAGES:
        return f"Control error: {_FIXED_CONTROL_MESSAGES[raw]}"
    m_not_found = _GAME_NOT_FOUND_RE.fullmatch(raw)
    if m_not_found:
        return f"Control error: Game with canonical ID '{m_not_found.group(1)}' not found in organizer catalog."
    if raw.startswith("Unsupported metadata override fields:"):
        return "Control error: Request contains unsupported metadata override fields."
    if raw.startswith("Unknown store type ") and raw.endswith("; choose 'organizer' or 'catalog_link'"):
        return "Control error: Unknown store type; choose 'organizer' or 'catalog_link'."
    return "Control error: Invalid organizer request or operation rejected."


def _run_tool(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """Execute an OrganizerControl call and map errors to safe, actionable ToolError."""
    try:
        res = fn(*args, **kwargs)
    except ConcurrencyConflict as exc:
        raise ToolError(_format_conflict_error(exc)) from None
    except ControlError as exc:
        raise ToolError(_format_control_error(exc)) from None
    except Exception:
        raise ToolError("Organizer operation failed due to an internal error.") from None

    if isinstance(res, list):
        payload = [item.to_dict() if hasattr(item, "to_dict") else item for item in res]
    elif hasattr(res, "to_dict"):
        payload = res.to_dict()
    else:
        payload = res
    return json.dumps(payload, indent=2, ensure_ascii=False)


def create_mcp_app(control: OrganizerControl, live_client=None, *, controller_client=None) -> MCPServer:
    """Build and register official MCPServer tools bound to OrganizerControl."""
    app = MCPServer(
        "waystone-organizer",
        description="Waystone library organizer and live control of the running desktop app",
    )

    if live_client is None:
        from native_services.waystone_control_client import WaystoneSocketClient
        live_client = WaystoneSocketClient()

    def _live_tool(action: str, **fields: Any) -> str:
        try:
            result = live_client.request(action, **fields)
        except ConnectionError:
            raise ToolError("Waystone is not running; open the desktop app before using live game controls.") from None
        except ValueError:
            raise ToolError("Waystone rejected the live control request. Check the game ID, query, and limit.") from None
        except Exception:
            raise ToolError("Waystone live control failed. Check that the desktop app is running.") from None
        return json.dumps(result, indent=2, ensure_ascii=False)

    @app.tool(
        name="waystone_play_game",
        description="Play an installed library game by its exact Waystone game ID using the running app's normal launch flow. Does not accept paths or commands; refuses while another game is active.",
    )
    async def waystone_play_game(game_id: str) -> str:
        """Ask the running Waystone app to play this installed library item."""
        if not isinstance(game_id, str) or not 1 <= len(game_id) <= 256:
            raise ToolError("A valid Waystone game ID is required.")
        return _live_tool("play", game_id=game_id)

    @app.tool(
        name="waystone_running_game",
        description="Get the active game status, launch detail, and current Play label from the running Waystone app.",
    )
    async def waystone_running_game() -> str:
        """Read the running app's active LaunchView status."""
        return _live_tool("status")

    @app.tool(
        name="waystone_stop_game",
        description="Request that the running Waystone app stop its tracked active game; only the existing game-session stop path is used.",
    )
    async def waystone_stop_game() -> str:
        """Stop the active tracked game through LaunchView.stopActiveGame."""
        return _live_tool("stop")

    @app.tool(
        name="waystone_find_installed_games",
        description="Search installed Waystone library games by title, provider, or platform; returns canonical game IDs for waystone_play_game.",
    )
    async def waystone_find_installed_games(query: str = "", limit: int = 20) -> str:
        """Search the running app's installed launch options without launching anything."""
        if not isinstance(query, str) or len(query) > 256:
            raise ToolError("Search query must be text up to 256 characters.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ToolError("Search limit must be an integer from 1 to 100.")
        return _live_tool("search", query=query, limit=limit)

    @app.tool(
        name="waystone_list_games",
        description="List games from the Waystone catalog with active overrides, duplicate links, and reconciliation applied.",
    )
    async def waystone_list_games(
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        platform: str | None = None,
        tag: str | None = None,
        category: str | None = None,
        library: str | None = None,
    ) -> str:
        """List catalog games matching filters."""
        return _run_tool(
            control.list_games,
            limit=limit,
            offset=offset,
            query=query,
            platform=platform,
            tag=tag,
            category=category,
            library=library,
        )

    @app.tool(
        name="waystone_get_game",
        description="Retrieve full game details including canonical ID, active overrides, and duplicate status.",
    )
    async def waystone_get_game(canonical_id: str) -> str:
        """Get game record by canonical ID."""
        return _run_tool(control.get_game, canonical_id)

    @app.tool(
        name="waystone_update_metadata",
        description="Apply atomic metadata overrides (title, platform, tags, categories, libraries, etc.) to a game.",
    )
    async def waystone_update_metadata(
        canonical_id: str,
        changes: dict[str, Any],
        request_id: str,
        expected_revision: int | None = None,
    ) -> str:
        """Apply metadata overrides to one game."""
        return _run_tool(
            control.update_metadata,
            canonical_id=canonical_id,
            changes=changes,
            request_id=request_id,
            expected_revision=expected_revision,
        )

    @app.tool(
        name="waystone_batch_update_metadata",
        description="Apply multiple game metadata edits atomically (1..500 edits) with optimistic concurrency checks.",
    )
    async def waystone_batch_update_metadata(
        edits: list[dict[str, Any]],
        request_id: str,
        expected_revision: int | None = None,
    ) -> str:
        """Apply batch metadata edits atomically."""
        return _run_tool(
            control.batch_update_metadata,
            edits=edits,
            request_id=request_id,
            expected_revision=expected_revision,
        )

    @app.tool(
        name="waystone_propose_duplicates",
        description="Generate a dry-run duplicate resolution preview plan without committing changes.",
    )
    async def waystone_propose_duplicates(groups: list[dict[str, Any]]) -> str:
        """Preview duplicate group links."""
        return _run_tool(control.propose_duplicates, groups)

    @app.tool(
        name="waystone_apply_duplicates",
        description="Commit duplicate representative links to the app-owned CatalogLinkStore.",
    )
    async def waystone_apply_duplicates(
        groups: list[dict[str, Any]],
        request_id: str,
        expected_revision: int | None = None,
    ) -> str:
        """Apply duplicate links with revision validation."""
        return _run_tool(
            control.apply_duplicates,
            groups=groups,
            request_id=request_id,
            expected_revision=expected_revision,
        )

    @app.tool(
        name="waystone_undo",
        description="Reverse the latest applied operation on the specified store using audit history.",
    )
    async def waystone_undo(
        store_type: str,
        audit_id: int,
        request_id: str,
    ) -> str:
        """Undo the latest applied change."""
        return _run_tool(
            control.undo,
            store_type=store_type,
            audit_id=audit_id,
            request_id=request_id,
        )

    @app.tool(
        name="waystone_audit_log",
        description="Query recent audit log entries from OrganizerStore and CatalogLinkStore.",
    )
    async def waystone_audit_log(
        store_type: str = "all",
        limit: int = 50,
    ) -> str:
        """Retrieve audit history."""
        return _run_tool(control.audit_log, store_type=store_type, limit=limit)

    # ========================================================
    # Universal AI-First Control: Modpacks & Instance Management
    # ========================================================
    @app.tool(
        name="waystone_list_mod_instances",
        description="List all configured mod instances/modpacks (Prism Launcher style), optionally filtered by game ID.",
    )
    async def waystone_list_mod_instances(game_id: str | None = None) -> str:
        """List mod instances, optionally filtered by game ID."""
        try:
            from native_services.mod_management import ModManagerService
            svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
            from dataclasses import asdict
            profiles = svc.list_profiles()
            if game_id:
                profiles = [p for p in profiles if str(getattr(p, "game_id", None) or getattr(p, "canonical_game_id", None) or "") == game_id]
            data = [asdict(p) if hasattr(p, "__dataclass_fields__") else p for p in profiles]
            return json.dumps(data, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Failed to list mod instances: {e}") from None

    @app.tool(
        name="waystone_create_mod_instance",
        description="Create a new isolated mod instance (Prism Launcher style) with optional private save directory.",
    )
    async def waystone_create_mod_instance(
        game_id: str,
        name: str,
        isolate_saves: bool = False,
        copy_from_id: str | None = None,
    ) -> str:
        """Create a new mod instance."""
        try:
            from native_services.mod_management import ModManagerService
            from dataclasses import asdict
            svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
            prof = svc.create_instance(game_id=game_id, name=name, isolate_saves=isolate_saves, copy_from_id=copy_from_id)
            return json.dumps(asdict(prof), indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Failed to create mod instance: {e}") from None

    @app.tool(
        name="waystone_clone_mod_instance",
        description="Clone an existing mod instance into a new named instance.",
    )
    async def waystone_clone_mod_instance(instance_id: str, new_name: str) -> str:
        """Clone an existing mod instance."""
        try:
            from native_services.mod_management import ModManagerService
            from dataclasses import asdict
            svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
            prof = svc.clone_instance(instance_id, new_name)
            return json.dumps(asdict(prof), indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Failed to clone mod instance: {e}") from None

    @app.tool(
        name="waystone_delete_mod_instance",
        description="Delete a mod instance and remove all its bindings.",
    )
    async def waystone_delete_mod_instance(instance_id: str) -> str:
        """Delete a mod instance."""
        try:
            from native_services.mod_management import ModManagerService
            svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
            ok = svc.delete_profile(instance_id)
            return json.dumps({"ok": ok, "deleted_instance_id": instance_id}, indent=2)
        except Exception as e:
            raise ToolError(f"Failed to delete mod instance: {e}") from None

    @app.tool(
        name="waystone_toggle_mod",
        description="Enable or disable a specific mod inside an instance.",
    )
    async def waystone_toggle_mod(instance_id: str, mod_id: str, enabled: bool | None = None) -> str:
        """Toggle or set mod enabled status in an instance."""
        try:
            from native_services.mod_management import ModManagerService
            svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
            prof = svc.profiles.get(instance_id)
            if not prof:
                raise ToolError(f"Instance '{instance_id}' not found")
            for m in prof.mods:
                if m.mod_id == mod_id:
                    if enabled is None:
                        m.enabled = not m.enabled
                    else:
                        m.enabled = enabled
                    svc.save()
                    return json.dumps({"instance_id": instance_id, "mod_id": mod_id, "enabled": m.enabled}, indent=2)
            raise ToolError(f"Mod '{mod_id}' not found in instance '{instance_id}'")
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Failed to toggle mod: {e}") from None

    @app.tool(
        name="waystone_reorder_mods",
        description="Set the load order priority sequence for mods in an instance.",
    )
    async def waystone_reorder_mods(instance_id: str, mod_ids: list[str]) -> str:
        """Set explicit mod load order."""
        try:
            from native_services.mod_management import ModManagerService
            svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
            ok = svc.reorder_mods(instance_id, mod_ids)
            return json.dumps({"instance_id": instance_id, "ok": ok, "load_order": mod_ids}, indent=2)
        except Exception as e:
            raise ToolError(f"Failed to reorder mods: {e}") from None

    @app.tool(
        name="waystone_detect_mod_conflicts",
        description="Compute file-level conflict matrix (+/- overrides) across active mods in an instance.",
    )
    async def waystone_detect_mod_conflicts(instance_id: str) -> str:
        """Detect file-level mod conflicts."""
        try:
            from native_services.mod_management import ModManagerService
            svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
            conflicts = svc.detect_conflicts(instance_id)
            return json.dumps(conflicts, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Failed to detect conflicts: {e}") from None

    @app.tool(
        name="waystone_export_modpack",
        description="Export a mod instance to a portable .wsmodpack.json file.",
    )
    async def waystone_export_modpack(instance_id: str, dest_path: str) -> str:
        """Export instance as a modpack bundle."""
        try:
            from native_services.mod_management import ModManagerService
            svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
            out = svc.export_modpack(instance_id, Path(dest_path))
            return json.dumps({"exported_file": str(out), "instance_id": instance_id}, indent=2)
        except Exception as e:
            raise ToolError(f"Failed to export modpack: {e}") from None

    @app.tool(
        name="waystone_import_modpack",
        description="Import a .wsmodpack.json file as a new instance for a game.",
    )
    async def waystone_import_modpack(game_id: str, pack_path: str) -> str:
        """Import a modpack bundle."""
        try:
            from native_services.mod_management import ModManagerService
            from dataclasses import asdict
            svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
            prof = svc.import_modpack(game_id, Path(pack_path))
            return json.dumps(asdict(prof), indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Failed to import modpack: {e}") from None

    # ========================================================
    # Universal AI-First Control: Log Diagnostics & Crash Triage
    # ========================================================
    @app.tool(
        name="waystone_diagnose_logs",
        description="Auto-discover and triage game/runner logs (Unity/BepInEx, Unreal/SML, SKSE, Proton), filter noise, isolate crashes, correlate to active mods, and suggest remediation.",
    )
    async def waystone_diagnose_logs(
        search_path: str | None = None,
        game_id: str | None = None,
    ) -> str:
        """Diagnose game and mod logs with automated crash triaging."""
        try:
            from native_services.mod_log_analyzer import ModLogAnalyzer
            from native_services.mod_management import ModManagerService
            from dataclasses import asdict
            analyzer = ModLogAnalyzer()
            known_mods = []
            if game_id:
                try:
                    svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
                    bound = svc.get_bound_profile_for_game(game_id)
                    if bound:
                        known_mods = [asdict(m) for m in bound.mods]
                except Exception:
                    pass

            if search_path and Path(search_path).is_file():
                report = analyzer.analyze_file(Path(search_path), known_mods=known_mods)
                return json.dumps(asdict(report), indent=2, ensure_ascii=False)

            extra = [Path(search_path)] if search_path and Path(search_path).is_dir() else []
            candidates = analyzer.discover_logs(extra_paths=extra)
            if not candidates:
                return json.dumps({"status": "no_logs_found", "message": "No relevant game or runner logs located."}, indent=2)

            target_log = candidates[0]
            report = analyzer.analyze_file(target_log, known_mods=known_mods)
            return json.dumps(asdict(report), indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Log diagnosis failed: {e}") from None

    @app.tool(
        name="waystone_apply_mod_diagnostic_fix",
        description="Execute an automated remediation fix (e.g., disable crashing mod) recommended by log diagnosis.",
    )
    async def waystone_apply_mod_diagnostic_fix(
        instance_id: str,
        action: str,
        mod_id: str | None = None,
    ) -> str:
        """Apply an AI diagnostic remediation fix."""
        try:
            from native_services.mod_management import ModManagerService
            svc = ModManagerService(Path.home() / ".local/share/waystone/mods-v1/config.json")
            if action == "disable_mod" and mod_id:
                prof = svc.profiles.get(instance_id)
                if not prof:
                    raise ToolError(f"Instance '{instance_id}' not found")
                for m in prof.mods:
                    if m.mod_id == mod_id:
                        m.enabled = False
                        svc.save()
                        return json.dumps({
                            "status": "applied",
                            "action": action,
                            "mod_id": mod_id,
                            "message": f"Disabled mod '{m.name or mod_id}' to restore engine stability."
                        }, indent=2)
                raise ToolError(f"Mod '{mod_id}' not found in instance '{instance_id}'")
            raise ToolError(f"Unsupported fix action: {action}")
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Failed to apply fix: {e}") from None

    # ========================================================
    # Universal AI-First Control: Settings & Configuration
    # ========================================================
    @app.tool(
        name="waystone_get_settings",
        description="Retrieve Waystone configuration and preferences across General, Library, Controller, Emulation, Saves, and System.",
    )
    async def waystone_get_settings(category: str | None = None) -> str:
        """Get settings dictionary, optionally filtered by category."""
        try:
            p = Path.home() / ".local/share/waystone/settings.json"
            defaults = {
                "general": {"theme": "dark", "start_in_fullscreen": False, "minimize_to_tray": True},
                "library": {"default_install_root": "/mnt/linux-data/Games/Waystone", "scan_on_startup": True},
                "controllers": {"virtual_gamepad_profile": "xbox360", "global_deadband": 0.08, "rumble_enabled": True},
                "emulation": {"roms_directory": "/mnt/linux-data/Emulation/roms", "dolphin_backend": "vulkan"},
                "saves": {"cloud_sync": True, "auto_backup_on_launch": True, "backup_directory": "~/.local/share/waystone/backups"},
                "system": {"hardware_acceleration": True, "enable_agent_remote_control": True}
            }
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    for k, v in data.items():
                        if k in defaults and isinstance(v, dict):
                            defaults[k].update(v)
                except Exception:
                    pass
            if category:
                if category in defaults:
                    return json.dumps(defaults[category], indent=2, ensure_ascii=False)
                raise ToolError(f"Unknown settings category '{category}'. Valid: {list(defaults.keys())}")
            return json.dumps(defaults, indent=2, ensure_ascii=False)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Failed to get settings: {e}") from None

    @app.tool(
        name="waystone_update_settings",
        description="Update settings for a category in ~/.local/share/waystone/settings.json.",
    )
    async def waystone_update_settings(category: str, settings: dict[str, Any]) -> str:
        """Update category settings."""
        try:
            p = Path.home() / ".local/share/waystone/settings.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            current = {}
            if p.is_file():
                try:
                    current = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
            cat_data = current.setdefault(category, {})
            cat_data.update(settings)
            p.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
            return json.dumps({"category": category, "updated_settings": cat_data}, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Failed to update settings: {e}") from None

    # ========================================================
    # Universal AI-First Control: Controllers & Virtual Gamepad
    # ========================================================
    @app.tool(
        name="waystone_get_controller_status",
        description="Inspect connected controllers, active mapping profile, deadband, modes, and virtual gamepad daemon status.",
    )
    async def waystone_get_controller_status() -> str:
        """Inspect unified controller engine state."""
        try:
            from native_services.unified_controller_control import ControllerControlClient
            client = ControllerControlClient()
            res = client.request("status")
            return json.dumps(res, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "running": False,
                "error": str(e),
                "socket": "/run/user/1000/waystone-unified-controller/control.sock"
            }, indent=2)

    def output_client():
        if controller_client is not None:
            return controller_client
        from native_services.unified_controller_control import ControllerControlClient
        return ControllerControlClient()

    def get_output():
        from native_services.unified_controller_control import validate_output_state
        return validate_output_state(output_client().request("output_get"))

    @app.tool(
        name="waystone_get_virtual_gamepad_profile",
        description="Read actual virtual pad type, configured default, supported types and Waystone tracked-game guard state from the existing engine.",
    )
    async def waystone_get_virtual_gamepad_profile() -> str:
        try:
            return json.dumps(await asyncio.to_thread(get_output), indent=2)
        except Exception:
            raise ToolError("Virtual pad state unavailable. Start the controller engine, then read again.") from None

    @app.tool(
        name="waystone_set_virtual_gamepad_profile",
        description=("Set a virtual pad type. With `profile`: save it as that controller profile's pad type (games "
                     "using the profile get it from their next launch; 'passthrough' gives those games the real controller with no virtual pad; 'default' clears it). Without "
                     "`profile`: apply and persist the default pad type through the engine; active or unknown game "
                     "observations block switching unless force is explicitly true. Never starts the engine."),
    )
    async def waystone_set_virtual_gamepad_profile(driver_type: str, force: bool = False,
                                                   profile: str | None = None) -> str:
        from native_services.unified_controller_control import VIRTUAL_PAD_TYPES, validate_output_state
        dt = driver_type.lower().strip()
        if profile is not None:
            if dt not in (*VIRTUAL_PAD_TYPES, "default", "passthrough"):
                raise ToolError(f"Invalid driver type. Valid choices: {list(VIRTUAL_PAD_TYPES) + ['passthrough', 'default']}")
            value = None if dt == "default" else dt

            def save_profile():
                client = output_client()
                try:
                    current = client.request("profile_get", name=profile)
                except Exception:
                    raise ToolError("Controller engine unavailable or profile not found; nothing was changed.") from None
                try:
                    client.request("profile_update", name=profile, profile={"virtual_gamepad_driver": value},
                                   expected_revision=current["revision"])
                except Exception as exc:
                    code = getattr(exc, "code", "")
                    code = code if isinstance(code, str) and re.fullmatch(r"ERR_[A-Z0-9_]{1,60}", code) else "ERR_PROFILE_UPDATE_FAILED"
                    raise ToolError(f"Pad type change failed ({code}); read the profile before trying again.") from None
                observed = client.request("profile_get", name=profile)["profile"].get("virtual_gamepad_driver")
                if observed != value:
                    raise ToolError("Pad type change not confirmed by the engine readback.")
                return {"profile": profile, "virtual_gamepad_driver": value or "default", "status": "saved",
                        "applies": "from the next launch of games using this profile"}

            return json.dumps(await asyncio.to_thread(save_profile), indent=2)
        if dt not in VIRTUAL_PAD_TYPES:
            raise ToolError(f"Invalid driver type. Valid choices: {list(VIRTUAL_PAD_TYPES)}")
        if type(force) is not bool:
            raise ToolError("force must be boolean.")

        def apply():
            client = output_client()
            failure = None
            try:
                client.request("output_set", type=dt, force=force, persist_default=True)
            except Exception as exc:
                failure = exc
            # A mutation error/timeout is not proof of rollback: always read back,
            # without replaying the write or writing a competing local JSON file.
            try:
                observed = validate_output_state(client.request("output_get"))
            except Exception:
                raise ToolError("Virtual pad outcome unverified. Read state before another explicit change; no retry was sent.") from None
            if failure is not None:
                code = getattr(failure, "code", "")
                code = code if isinstance(code, str) and re.fullmatch(r"ERR_[A-Z0-9_]{1,60}", code) else "ERR_OUTPUT_REJECTED"
                raise ToolError(f"Virtual pad change failed ({code}); actual={observed['current_type']}, default={observed['default_type']}. No retry was sent.") from None
            if observed["current_type"] != dt or observed["default_type"] != dt:
                raise ToolError("Virtual pad change not verified by native readback. Read state before another change; no retry was sent.")
            return dict(observed, virtual_gamepad_driver=dt, status="applied")

        return json.dumps(await asyncio.to_thread(apply), indent=2)

    @app.tool(
        name="waystone_set_controller_deadband",
        description=(
            "Set stick deadzones in a controller profile (the engine's stick_processing): inner deadzone "
            "0.0-0.5 and optional anti-deadzone 0.0-0.5, for the left, right or both sticks. Edits the active "
            "profile unless profile is given; saved through the engine with a revision check and read back."
        ),
    )
    async def waystone_set_controller_deadband(
        deadband: float,
        stick: str = "both",
        profile: str | None = None,
        anti_deadzone: float | None = None,
        device_id: str | None = None,
    ) -> str:
        """Configure stick deadzones in a controller profile (never the motion calibration file)."""
        if device_id:
            raise ToolError("Deadzones belong to controller profiles, not single devices. Pass profile instead.")
        if isinstance(deadband, bool) or not isinstance(deadband, (int, float)) or not (0.0 <= deadband <= 0.5):
            raise ToolError("Deadband must be between 0.0 and 0.5")
        if anti_deadzone is not None and (isinstance(anti_deadzone, bool) or not isinstance(anti_deadzone, (int, float))
                                          or not (0.0 <= anti_deadzone <= 0.5)):
            raise ToolError("anti_deadzone must be between 0.0 and 0.5")
        side = str(stick or "both").strip().lower()
        sides = {"both": ("left", "right"), "left": ("left",), "right": ("right",)}.get(side)
        if sides is None:
            raise ToolError("stick must be left, right or both")

        def apply():
            client = output_client()
            try:
                name = profile or client.request("profiles")["active_profile"]
                current = client.request("profile_get", name=name)
            except Exception:
                raise ToolError("Controller engine unavailable or profile not found; nothing was changed.") from None
            processing = dict(current["profile"].get("stick_processing") or {})
            for key in sides:
                entry = dict(processing.get(key) or {})
                entry["inner_deadzone"] = float(deadband)
                if anti_deadzone is not None:
                    entry["anti_deadzone"] = float(anti_deadzone)
                processing[key] = entry
            try:
                client.request("profile_update", name=name, profile={"stick_processing": processing},
                               expected_revision=current["revision"])
            except Exception as exc:
                code = getattr(exc, "code", "")
                code = code if isinstance(code, str) and re.fullmatch(r"ERR_[A-Z0-9_]{1,60}", code) else "ERR_PROFILE_UPDATE_FAILED"
                raise ToolError(f"Deadzone change failed ({code}); read the profile before trying again.") from None
            observed = client.request("profile_get", name=name)["profile"].get("stick_processing") or {}
            for key in sides:
                if observed.get(key, {}).get("inner_deadzone") != float(deadband):
                    raise ToolError("Deadzone change not confirmed by the engine readback.")
            return {"profile": name, "stick_processing": {k: observed.get(k) for k in ("left", "right")},
                    "deadband": float(deadband), "status": "applied"}

        return json.dumps(await asyncio.to_thread(apply), indent=2)

    # ========================================================
    # Universal AI-First Control: Game Mod Archetypes & Type 1 Heuristics
    # ========================================================
    @app.tool(
        name="waystone_detect_game_mod_archetype",
        description="Classify game engine and modding infrastructure (Skyrim Creation, Cyberpunk REDengine, Satisfactory SML, RDR2 RAGE, Minecraft, Terraria) from game directory, ID, or title.",
    )
    async def waystone_detect_game_mod_archetype(
        game_path: str | None = None,
        game_id: str | None = None,
        game_title: str | None = None,
    ) -> str:
        """Classify a game into its modding archetype."""
        try:
            from native_services.game_mod_archetypes import GameArchetypeDetector
            info = GameArchetypeDetector.detect_archetype(
                game_path=game_path,
                game_id=game_id or "",
                game_title=game_title or "",
            )
            return json.dumps(info.to_dict(), indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Failed to detect archetype: {e}") from None

    @app.tool(
        name="waystone_analyze_mod_archive",
        description="Inspect a mod archive (.zip, .7z, .tar.gz), strip wrapper directories, detect FOMOD options, and compute destination file mappings using Type 1 heuristics.",
    )
    async def waystone_analyze_mod_archive(
        archive_path: str,
        game_id: str | None = None,
        game_title: str | None = None,
        mod_name_hint: str | None = None,
    ) -> str:
        """Analyze mod archive and compute destination file routes."""
        try:
            from native_services.game_mod_archetypes import GameArchetypeDetector, ModArchiveClassifier
            archetype_info = GameArchetypeDetector.detect_archetype(
                game_id=game_id or "",
                game_title=game_title or "",
            )
            plan = ModArchiveClassifier.inspect_and_route(
                archive_path=archive_path,
                archetype=archetype_info.archetype,
                game_title=game_title or "",
                mod_name_hint=mod_name_hint or "",
            )
            return json.dumps(plan.to_dict(), indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Failed to analyze mod archive: {e}") from None

    @app.tool(
        name="waystone_get_supported_modding_archetypes",
        description="List all supported game modding archetypes and their loader/Proton/staging specifications.",
    )
    async def waystone_get_supported_modding_archetypes() -> str:
        """List all registered game modding archetypes."""
        try:
            from native_services.game_mod_archetypes import ARCHETYPE_SPECS
            data = {k.value: v.to_dict() for k, v in ARCHETYPE_SPECS.items()}
            return json.dumps(data, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Failed to get archetypes: {e}") from None

    @app.tool(
        name="waystone_sort_skyrim_load_order",
        description="Topologically sort Skyrim Creation Engine plugins.txt, strictly prioritizing official masters and engine frameworks.",
    )
    async def waystone_sort_skyrim_load_order(plugins_txt_content: str) -> str:
        """Parse, topologically sort, and return valid Skyrim plugins.txt content."""
        try:
            from native_services.skyrim_load_order import SkyrimLoadOrderSorter
            sorter = SkyrimLoadOrderSorter()
            plugins = sorter.parse_plugins_txt(plugins_txt_content)
            sorted_plugins = sorter.sort_plugins(plugins)
            serialized = sorter.serialize_plugins_txt(sorted_plugins)
            return json.dumps({
                "status": "success",
                "count": len(sorted_plugins),
                "sorted_plugins_txt": serialized,
                "order": [p.clean_name for p in sorted_plugins],
            }, indent=2)
        except Exception as e:
            raise ToolError(f"Failed to sort Skyrim load order: {e}") from None

    @app.tool(
        name="waystone_resolve_satisfactory_plugins",
        description="Inspect Satisfactory UE5 SML v3.8+ FactoryGame/Mods/ .uplugin manifests, validate SemVer dependencies, and compute topological plugin load order.",
    )
    async def waystone_resolve_satisfactory_plugins(mods_dir: str) -> str:
        """Resolve Satisfactory mods directory dependencies and load order."""
        try:
            from native_services.satisfactory_resolver import SatisfactoryPluginResolver
            p_mods = Path(mods_dir).resolve()
            if not p_mods.is_dir():
                raise ToolError(f"Directory not found: {mods_dir}")
            resolver = SatisfactoryPluginResolver(p_mods)
            result = resolver.resolve()
            return json.dumps({
                "valid": result.valid,
                "load_order": result.load_order,
                "missing_dependencies": result.missing_dependencies,
                "version_conflicts": result.version_conflicts,
                "errors": result.errors,
            }, indent=2)
        except Exception as e:
            raise ToolError(f"Failed to resolve Satisfactory plugins: {e}") from None

    @app.tool(
        name="waystone_stage_rdr2_lml_package",
        description="Parse Red Dead Redemption 2 Lenny's Mod Loader install.xml manifest and compute target paths and Proton WINEDLLOVERRIDES.",
    )
    async def waystone_stage_rdr2_lml_package(archive_file_list: list[str], install_xml_content: str | None = None) -> str:
        """Compute staging routes and Proton Wine DLL overrides for RDR2 LML mod package."""
        try:
            from native_services.rdr2_lml_stager import Rdr2LmlStager
            routes = Rdr2LmlStager.classify_rdr2_files(archive_file_list)
            pkg_info = None
            if install_xml_content:
                parsed = Rdr2LmlStager.parse_install_xml(install_xml_content)
                if parsed:
                    pkg_info = {
                        "name": parsed.name,
                        "author": parsed.author,
                        "version": parsed.version,
                        "description": parsed.description,
                        "mappings_count": len(parsed.mappings),
                    }
            proton_env = Rdr2LmlStager.get_proton_environment()
            return json.dumps({
                "package_info": pkg_info,
                "routes": routes,
                "proton_environment": proton_env,
            }, indent=2)
        except Exception as e:
            raise ToolError(f"Failed to stage RDR2 LML package: {e}") from None

    # ---------------- Mods: find installs, search, download, install, undo (for AI assistants) ----------------
    def _mods_home() -> Path:
        return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "waystone/mods"

    def _downloads_dir() -> Path:
        return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "waystone/mods/downloads"

    def _waystone_root() -> str | None:
        try:
            return json.loads((Path.home() / ".local/share/waystone/install-settings.json").read_text()).get("install_root") or None
        except (OSError, ValueError):
            return None

    @app.tool(
        name="waystone_find_game_install",
        description="Find where a game is installed (Steam incl. libraries on other drives, Heroic Epic/GOG/Amazon, Lutris, Waystone installs). Returns install folder, Proton prefix and store. Exact title match only (sequels never match).",
    )
    async def waystone_find_game_install(title: str) -> str:
        try:
            from native_services import game_installs
            installs = game_installs.find_installs(Path.home(), waystone_root=_waystone_root(), scan_mounts=True)
            return json.dumps({"title": title, "matches": game_installs.match_install(title, installs)}, indent=2)
        except Exception as e:
            raise ToolError(f"Failed to find installs: {e}") from None

    @app.tool(
        name="waystone_search_mods",
        description="Search mods. site: nexus (most PC games; give game_domain e.g. skyrimspecialedition, cyberpunk2077, reddeadredemption2, fallout4, subnautica, slimerancher), gamebanana (emulators; give gamebanana_game_id) or modrinth (Minecraft Java). Empty query lists the most popular.",
    )
    async def waystone_search_mods(site: str, query: str = "", game_domain: str | None = None,
                                   gamebanana_game_id: int | None = None, limit: int = 20) -> str:
        try:
            from native_services import mod_catalog as c
            if site == "nexus":
                res = c.search_nexus(query, game_domain=game_domain, count=limit)
            elif site == "gamebanana":
                res = c.search_gamebanana(query, game_id=gamebanana_game_id)
            elif site == "modrinth":
                res = c.search_modrinth(query, limit=limit)
            else:
                raise ValueError("site must be nexus, gamebanana or modrinth")
            return json.dumps(res, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Mod search failed: {e}") from None

    @app.tool(
        name="waystone_find_mod_site_game",
        description="Find a game's id on a mod site: nexus -> domain names, gamebanana -> game ids. Use before waystone_search_mods.",
    )
    async def waystone_find_mod_site_game(site: str, name: str) -> str:
        try:
            from native_services import mod_catalog as c
            found = c.find_nexus_games(name) if site == "nexus" else c.find_gamebanana_games(name)
            return json.dumps(found[:10], indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Lookup failed: {e}") from None

    @app.tool(
        name="waystone_list_mod_files",
        description="List a mod's downloadable files (newest/main first). nexus needs game_domain and uses the user's Nexus API key from the keyring.",
    )
    async def waystone_list_mod_files(site: str, mod_id: str, game_domain: str | None = None) -> str:
        try:
            from native_services import mod_downloads as d
            if site == "nexus":
                from native_services import nexus_client as nx
                key = nx.api_key()
                if not key:
                    raise ValueError("no Nexus API key in the keyring (service waystone, username nexus)")
                files = nx.mod_files(game_domain or "", mod_id, key)
            elif site == "gamebanana":
                files = d.gamebanana_files(mod_id)
            else:
                files = d.modrinth_files(mod_id)
            return json.dumps(files, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ToolError(f"Listing files failed: {e}") from None

    @app.tool(
        name="waystone_download_mod",
        description="Download one mod file (checksum-verified) into Waystone's cache and return its path. GameBanana/Modrinth: pass url, file_name and md5/sha1 from waystone_list_mod_files. Nexus: give game_domain, mod_id and file_id; Premium accounts download directly, free accounts get the nexusmods.com page to open (the user presses 'Mod manager download' and Waystone downloads it).",
    )
    async def waystone_download_mod(site: str, mod_id: str, file_name: str = "", url: str = "", md5: str = "",
                                    sha1: str = "", game_domain: str | None = None, file_id: str | None = None) -> str:
        try:
            if site == "nexus":
                page = f"https://www.nexusmods.com/{game_domain}/mods/{mod_id}?tab=files" + (f"&file_id={file_id}&nmm=1" if file_id else "")
                from native_services import nexus_client as nx
                key = nx.api_key()
                if key and file_id and game_domain:
                    try:
                        cdn = nx.download_url(game_domain, mod_id, file_id, key)   # works for Premium accounts
                    except nx.NexusError:
                        cdn = ""
                    if cdn:
                        files = nx.mod_files(game_domain, mod_id, key)
                        name = next((f["name"] for f in files if str(f["file_id"]) == str(file_id)), file_name or f"nexus-{file_id}.zip")
                        from native_services import mod_downloads as d
                        path = d.download(cdn, _downloads_dir() / "nexus" / f"{game_domain}-{mod_id}", filename=name)
                        return json.dumps({"path": str(path)})
                return json.dumps({"action": "open_in_browser", "url": page,
                                   "note": "Press 'Mod manager download'; Waystone receives the nxm link and downloads it."})
            from native_services import mod_downloads as d
            path = d.download(url, _downloads_dir() / site / str(mod_id), filename=file_name or "mod.zip", md5=md5, sha1=sha1)
            return json.dumps({"path": str(path)})
        except Exception as e:
            raise ToolError(f"Download failed: {e}") from None

    @app.tool(
        name="waystone_install_mod",
        description="Install a downloaded mod archive. target: 'game' (PC game folder; give game_dir or game_title to auto-find it) or an emulator id: eden, ryujinx, azahar, cemu, dolphin (title_id when the zip lacks it). PREVIEWS by default (apply=false); call again with apply=true to write files. Existing different files are conflicts and block the install. Returns a journal path for undo.",
    )
    async def waystone_install_mod(archive_path: str, target: str, game_dir: str | None = None,
                                   game_title: str | None = None, title_id: str = "", mod_name: str = "",
                                   apply: bool = False) -> str:
        try:
            if target == "game":
                from native_services import game_mods, game_installs
                if not game_dir and game_title:
                    matches = game_installs.match_install(game_title, game_installs.find_installs(
                        Path.home(), waystone_root=_waystone_root(), scan_mounts=True))
                    if not matches:
                        raise ValueError(f"could not find an install of {game_title}; pass game_dir")
                    game_dir = sorted(matches, key=lambda m: (not m.get("installed"), not m.get("prefix")))[0]["install_dir"]
                plan = game_mods.plan_game_install(archive_path, game_dir or "", mod_name=mod_name)
                installer = game_mods
            else:
                from native_services import emulator_mods
                plan = emulator_mods.plan_install(archive_path, target, title_id=title_id,
                                                  mod_name=mod_name or Path(archive_path).stem, home=Path.home())
                installer = emulator_mods
            summary = {"target_root": plan["target_root"], "files": len(plan["files"]),
                       "conflicts": plan["conflicts"], "sample": [f["target"] for f in plan["files"][:10]]}
            if not apply:
                return json.dumps({"preview": summary}, indent=2)
            if plan["conflicts"]:
                raise ValueError("conflicts: existing different files would be replaced")
            result = installer.apply_install(plan, journal_dir=_mods_home() / "install-journal")
            return json.dumps({"installed": summary, **result}, indent=2)
        except Exception as e:
            raise ToolError(f"Install failed: {e}") from None

    @app.tool(
        name="waystone_undo_mod_install",
        description="Undo an install by its journal path. Files the user changed since are kept and listed.",
    )
    async def waystone_undo_mod_install(journal_path: str) -> str:
        try:
            from native_services import emulator_mods
            return json.dumps(emulator_mods.undo_install(journal_path), indent=2)
        except Exception as e:
            raise ToolError(f"Undo failed: {e}") from None

    return app


def main():
    parser = argparse.ArgumentParser(description="Official Waystone Library Organizer MCP stdio server")
    parser.add_argument("--organizer-db", required=True, help="Path to app-owned Organizer SQLite database")
    parser.add_argument("--catalog-link-db", help="Path to app-owned CatalogLink SQLite database")
    parser.add_argument("--library-db", help="Optional path to base library SQLite database")
    parser.add_argument("--reconciliation-json", help="Optional path to library reconciliation JSON file")

    args = parser.parse_args()

    org_path = Path(args.organizer_db).resolve()
    if not org_path.is_file():
        sys.stderr.write(f"Error: Organizer database '{org_path}' does not exist.\n")
        sys.exit(1)

    org_store = OrganizerStore(org_path)

    link_store = None
    if args.catalog_link_db:
        link_path = Path(args.catalog_link_db).resolve()
        if link_path.is_file():
            link_store = CatalogLinkStore(link_path)

    control = OrganizerControl(
        organizer_store=org_store,
        catalog_link_store=link_store,
        library_db_path=args.library_db,
        reconciliation_path=args.reconciliation_json,
    )

    app = create_mcp_app(control)
    # Run the official MCP stdio transport loop
    app.run("stdio")


if __name__ == "__main__":
    main()
