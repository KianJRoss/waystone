"""PulseAudio / PipeWire Audio Routing Service for Xodus Voice Engine.

Provides device inventory discovery, monitor source exclusion, default resolving,
and explicit stream redirection (sink-inputs and source-outputs) strictly filtered
by owned application process IDs (PIDs).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger("voice_audio_routes")

DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_OUTPUT_BYTES = 1024 * 1024  # 1 MB bound on CLI output buffer

# Typed safe error codes (no internal paths/stacktraces/sensitive details leaked)
ERR_PACTL_COMMAND_FAILED = "ERR_PACTL_COMMAND_FAILED"
ERR_PACTL_UNAVAILABLE = "ERR_PACTL_UNAVAILABLE"
ERR_OUTPUT_SIZE_EXCEEDED = "ERR_OUTPUT_SIZE_EXCEEDED"
ERR_NO_DEFAULT_SINK = "ERR_NO_DEFAULT_SINK"
ERR_NO_DEFAULT_SOURCE = "ERR_NO_DEFAULT_SOURCE"
ERR_TARGET_SINK_NOT_FOUND = "ERR_TARGET_SINK_NOT_FOUND"
ERR_TARGET_SOURCE_NOT_FOUND = "ERR_TARGET_SOURCE_NOT_FOUND"
ERR_STREAM_MOVE_FAILED = "ERR_STREAM_MOVE_FAILED"
ERR_INVALID_STREAM_INDEX = "ERR_INVALID_STREAM_INDEX"


def _default_subprocess_runner(args: List[str], timeout: float) -> Any:
    import subprocess
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _validate_index(val: Any) -> Optional[int]:
    """Strict positive/zero int stream index validation."""
    if isinstance(val, bool):
        return None
    if isinstance(val, int) and val >= 0:
        return val
    if isinstance(val, str) and re.fullmatch(r"[0-9]{1,10}", val.strip()):
        return int(val.strip())
    return None


def _validate_pids(pids: Optional[Iterable[Any]]) -> Set[int]:
    """Strict positive int PID validation, rejecting bools, floats, exponents, and negatives."""
    if not pids:
        return set()
    valid: Set[int] = set()
    for p in pids:
        if isinstance(p, bool):
            continue
        if isinstance(p, int) and p > 0:
            valid.add(p)
        elif isinstance(p, str) and re.fullmatch(r"[1-9][0-9]{0,9}", p.strip()):
            valid.add(int(p.strip()))
    return valid


def _is_monitor_source(source: Dict[str, Any]) -> bool:
    """Detect whether a PulseAudio/PipeWire source is a monitor rather than a mic."""
    name = str(source.get("name", ""))
    desc = str(source.get("description", ""))
    props = source.get("properties") or {}

    if props.get("device.class") == "monitor":
        return True

    mon = source.get("monitor_source")
    if mon is not None and str(mon).strip() != "":
        return True

    if source.get("monitor_of_sink") is not None:
        return True

    if name.endswith(".monitor") or desc.startswith("Monitor of "):
        return True

    return False


class VoiceAudioRouter:
    """Manages audio device discovery and explicit process-filtered stream routing via pactl."""

    def __init__(
        self,
        runner: Optional[Callable[[List[str], float], Any]] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        """Pure constructor with zero external side effects or subprocess calls."""
        self._runner = runner or _default_subprocess_runner
        self._timeout = max(0.1, float(timeout))
        self._max_output_bytes = max(1024, int(max_output_bytes))

    def _exec(self, args: List[str]) -> Tuple[int, str, str]:
        """Execute a pactl command safely with bounds and timeouts."""
        try:
            res = self._runner(args, self._timeout)
            code = getattr(res, "returncode", -1)
            stdout = getattr(res, "stdout", "") or ""
            stderr = getattr(res, "stderr", "") or ""

            if len(stdout) > self._max_output_bytes:
                logger.warning("pactl output exceeded limit (%s)", ERR_OUTPUT_SIZE_EXCEEDED)
                return -1, "", ERR_OUTPUT_SIZE_EXCEEDED

            return code, stdout, stderr
        except Exception:
            logger.warning("pactl execution failed (%s)", ERR_PACTL_UNAVAILABLE)
            return -1, "", ERR_PACTL_UNAVAILABLE

    def _parse_json(self, raw: str) -> Any:
        if not raw or not raw.strip():
            return []
        if len(raw) > self._max_output_bytes:
            raise ValueError(ERR_OUTPUT_SIZE_EXCEEDED)
        return json.loads(raw)

    def get_default_sink(self) -> Optional[str]:
        """Query current PulseAudio/PipeWire default audio output sink."""
        code, stdout, _ = self._exec(["pactl", "get-default-sink"])
        if code == 0 and stdout.strip():
            return stdout.strip().splitlines()[0].strip()
        return None

    def get_default_source(self) -> Optional[str]:
        """Query current PulseAudio/PipeWire default audio input source."""
        code, stdout, _ = self._exec(["pactl", "get-default-source"])
        if code == 0 and stdout.strip():
            return stdout.strip().splitlines()[0].strip()
        return None

    def get_devices(self) -> Dict[str, Any]:
        """Return categorized audio sinks and non-monitor sources with defaults.

        Propagates pactl command failures explicitly instead of pretending the system has 0 devices.
        """
        default_sink = self.get_default_sink()
        default_source = self.get_default_source()
        pactl_error: Optional[str] = None

        # Query sinks
        code_sinks, stdout_sinks, _ = self._exec(["pactl", "-f", "json", "list", "sinks"])
        if code_sinks != 0:
            pactl_error = ERR_PACTL_COMMAND_FAILED
            raw_sinks = []
        else:
            try:
                raw_sinks = self._parse_json(stdout_sinks)
                if not isinstance(raw_sinks, list): raise ValueError(ERR_PACTL_COMMAND_FAILED)
            except Exception:
                pactl_error = ERR_PACTL_COMMAND_FAILED
                raw_sinks = []

        # Query sources
        code_srcs, stdout_srcs, _ = self._exec(["pactl", "-f", "json", "list", "sources"])
        if code_srcs != 0:
            pactl_error = ERR_PACTL_COMMAND_FAILED
            raw_sources = []
        else:
            try:
                raw_sources = self._parse_json(stdout_srcs)
                if not isinstance(raw_sources, list): raise ValueError(ERR_PACTL_COMMAND_FAILED)
            except Exception:
                pactl_error = ERR_PACTL_COMMAND_FAILED
                raw_sources = []

        sinks: List[Dict[str, Any]] = []
        for s in raw_sinks:
            if not isinstance(s, dict):
                continue
            name = s.get("name")
            idx = _validate_index(s.get("index"))
            if not isinstance(name, str) or not name.strip() or idx is None:
                continue
            desc = s.get("description")
            if not isinstance(desc, str) or not desc.strip():
                desc = name
            sinks.append({
                "name": str(name).strip(),
                "description": str(desc).strip(),
                "index": idx,
                "state": s.get("state") if isinstance(s.get("state"), str) else None,
                "is_default": (name == default_sink),
            })

        sources: List[Dict[str, Any]] = []
        for src in raw_sources:
            if not isinstance(src, dict):
                continue
            name = src.get("name")
            idx = _validate_index(src.get("index"))
            if not isinstance(name, str) or not name.strip() or idx is None:
                continue
            # EXCLUDE monitor microphones
            if _is_monitor_source(src):
                continue

            desc = src.get("description")
            if not isinstance(desc, str) or not desc.strip():
                desc = name
            sources.append({
                "name": str(name).strip(),
                "description": str(desc).strip(),
                "index": idx,
                "state": src.get("state") if isinstance(src.get("state"), str) else None,
                "is_default": (name == default_source),
            })

        # Default source must only be valid if it exists in filtered non-monitor sources!
        known_source_names = {src["name"] for src in sources}
        valid_default_source = default_source if default_source in known_source_names else None

        known_sink_names = {snk["name"] for snk in sinks}
        valid_default_sink = default_sink if default_sink in known_sink_names else None

        return {
            "sinks": sinks,
            "sources": sources,
            "default_sink": valid_default_sink,
            "default_source": valid_default_source,
            "error": pactl_error,
        }

    def get_owned_streams(self, owned_pids: Optional[Iterable[Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Find active sink-inputs and source-outputs belonging strictly to trusted owned_pids."""
        pids = _validate_pids(owned_pids)
        if not pids:
            return {"sink_inputs": [], "source_outputs": []}

        # Query playback streams (sink-inputs)
        code, stdout, _ = self._exec(["pactl", "-f", "json", "list", "sink-inputs"])
        try:
            if code != 0: raise ValueError(ERR_PACTL_COMMAND_FAILED)
            raw_inputs = self._parse_json(stdout)
            if not isinstance(raw_inputs, list): raise ValueError(ERR_PACTL_COMMAND_FAILED)
        except Exception:
            return {"sink_inputs": [], "source_outputs": [], "error": ERR_PACTL_COMMAND_FAILED}

        matching_sink_inputs: List[Dict[str, Any]] = []
        for item in raw_inputs:
            if not isinstance(item, dict):
                continue
            idx = _validate_index(item.get("index"))
            if idx is None:
                continue
            props = item.get("properties")
            if not isinstance(props, dict):
                continue
            raw_pid = props.get("application.process.id")
            if isinstance(raw_pid, bool):
                continue

            pid_val = None
            if isinstance(raw_pid, int) and raw_pid > 0:
                pid_val = raw_pid
            elif isinstance(raw_pid, str) and re.fullmatch(r"[1-9][0-9]{0,9}", raw_pid.strip()):
                pid_val = int(raw_pid.strip())

            if pid_val is not None and pid_val in pids:
                matching_sink_inputs.append({
                    "index": idx,
                    "pid": pid_val,
                    "current_sink": item.get("sink"),
                    "app_name": props.get("application.name"),
                    "media_name": props.get("media.name"),
                })

        # Query recording streams (source-outputs)
        code, stdout, _ = self._exec(["pactl", "-f", "json", "list", "source-outputs"])
        try:
            if code != 0: raise ValueError(ERR_PACTL_COMMAND_FAILED)
            raw_outputs = self._parse_json(stdout)
            if not isinstance(raw_outputs, list): raise ValueError(ERR_PACTL_COMMAND_FAILED)
        except Exception:
            return {"sink_inputs": [], "source_outputs": [], "error": ERR_PACTL_COMMAND_FAILED}

        matching_source_outputs: List[Dict[str, Any]] = []
        for item in raw_outputs:
            if not isinstance(item, dict):
                continue
            idx = _validate_index(item.get("index"))
            if idx is None:
                continue
            props = item.get("properties")
            if not isinstance(props, dict):
                continue
            raw_pid = props.get("application.process.id")
            if isinstance(raw_pid, bool):
                continue

            pid_val = None
            if isinstance(raw_pid, int) and raw_pid > 0:
                pid_val = raw_pid
            elif isinstance(raw_pid, str) and re.fullmatch(r"[1-9][0-9]{0,9}", raw_pid.strip()):
                pid_val = int(raw_pid.strip())

            if pid_val is not None and pid_val in pids:
                matching_source_outputs.append({
                    "index": idx,
                    "pid": pid_val,
                    "current_source": item.get("source"),
                    "app_name": props.get("application.name"),
                    "media_name": props.get("media.name"),
                })

        return {
            "sink_inputs": matching_sink_inputs,
            "source_outputs": matching_source_outputs,
        }

    def route_selected(
        self,
        input_name: Optional[str] = None,
        output_name: Optional[str] = None,
        owned_pids: Optional[Iterable[Any]] = None,
    ) -> Dict[str, Any]:
        """Route owned audio streams to selected input/output devices.

        Validates destinations against inventory before performing any moves.
        Fails closed on unknown devices or pactl failures without touching streams.
        Avoids redundant repeated moves for streams already at target destination.
        """
        pids = _validate_pids(owned_pids)
        devices = self.get_devices()
        errors: List[str] = []

        # If pactl failed during device discovery, fail closed immediately
        if devices.get("error"):
            errors.append(devices["error"])
            return {
                "sink_inputs_moved": [],
                "source_outputs_moved": [],
                "target_sink": None,
                "target_source": None,
                "errors": errors,
            }

        target_sink: Optional[str] = None
        target_source: Optional[str] = None

        # Resolve and validate target output (sink)
        if output_name is not None:
            if output_name in ("", "default"):
                target_sink = devices.get("default_sink")
                if not target_sink:
                    errors.append(ERR_NO_DEFAULT_SINK)
            else:
                known_sinks = {s["name"] for s in devices["sinks"]}
                if output_name in known_sinks:
                    target_sink = output_name
                else:
                    errors.append(ERR_TARGET_SINK_NOT_FOUND)

        # Resolve and validate target input (source)
        if input_name is not None:
            if input_name in ("", "default"):
                target_source = devices.get("default_source")
                if not target_source:
                    errors.append(ERR_NO_DEFAULT_SOURCE)
            else:
                known_sources = {s["name"] for s in devices["sources"]}
                if input_name in known_sources:
                    target_source = input_name
                else:
                    errors.append(ERR_TARGET_SOURCE_NOT_FOUND)

        # Fail closed on destination errors
        if errors:
            return {
                "sink_inputs_moved": [],
                "source_outputs_moved": [],
                "target_sink": target_sink,
                "target_source": target_source,
                "errors": errors,
            }

        # If no streams can be owned, return empty moves
        if not pids:
            return {
                "sink_inputs_moved": [],
                "source_outputs_moved": [],
                "target_sink": target_sink,
                "target_source": target_source,
                "errors": [],
            }

        streams = self.get_owned_streams(pids)
        if streams.get('error'):
            return {'sink_inputs_moved': [], 'source_outputs_moved': [],
                    'target_sink': target_sink, 'target_source': target_source,
                    'errors': [ERR_PACTL_COMMAND_FAILED]}
        sink_inputs_moved: List[Dict[str, Any]] = []
        source_outputs_moved: List[Dict[str, Any]] = []

        # Index to name mappings for idempotency checks
        sink_index_to_name = {s["index"]: s["name"] for s in devices["sinks"]}
        sink_name_to_index = {s["name"]: s["index"] for s in devices["sinks"]}
        source_index_to_name = {s["index"]: s["name"] for s in devices["sources"]}
        source_name_to_index = {s["name"]: s["index"] for s in devices["sources"]}

        # Move sink inputs to target sink
        if target_sink and streams["sink_inputs"]:
            target_sink_idx = sink_name_to_index.get(target_sink)
            for si in streams["sink_inputs"]:
                idx = si.get("index")
                if idx is None:
                    continue
                current = si.get("current_sink")

                # Avoid redundant repeated move if stream is already at target sink
                if current == target_sink or current == target_sink_idx or sink_index_to_name.get(current) == target_sink:
                    continue

                code, _, _ = self._exec(["pactl", "move-sink-input", str(idx), target_sink])
                if code == 0:
                    sink_inputs_moved.append({
                        "index": idx,
                        "pid": si["pid"],
                        "target": target_sink,
                    })
                else:
                    errors.append(ERR_STREAM_MOVE_FAILED)

        # Move source outputs to target source
        if target_source and streams["source_outputs"]:
            target_src_idx = source_name_to_index.get(target_source)
            for so in streams["source_outputs"]:
                idx = so.get("index")
                if idx is None:
                    continue
                current = so.get("current_source")

                # Avoid redundant repeated move if stream is already at target source
                if current == target_source or current == target_src_idx or source_index_to_name.get(current) == target_source:
                    continue

                code, _, _ = self._exec(["pactl", "move-source-output", str(idx), target_source])
                if code == 0:
                    source_outputs_moved.append({
                        "index": idx,
                        "pid": so["pid"],
                        "target": target_source,
                    })
                else:
                    errors.append(ERR_STREAM_MOVE_FAILED)

        return {
            "sink_inputs_moved": sink_inputs_moved,
            "source_outputs_moved": source_outputs_moved,
            "target_sink": target_sink,
            "target_source": target_source,
            "errors": errors,
        }
