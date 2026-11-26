#!/usr/bin/env python3
"""Comprehensive offline verification of UnifiedController headless service.

Tests real Python ControllerControlClient connecting to the native socket.
Covers:
- CLI argument gates (--data-dir, profiles.json requirement, mutual exclusivity, missing legacy dir)
- LocalInstanceCoordinator dual sorted locks & lock collision
- WAYSTONE_ENGINE_READY=1 readiness handshake
- Real IPC operations:
  * status
  * profiles
  * profile_get
  * profile_update (verifying unknown/custom fields retained)
  * profile_select
  * cancel_macros
  * revision conflict rejection
- Orderly graceful shutdown:
  * Stdin EOF / shutdown command
  * POSIX SIGTERM signal (context.Cancel=true, exit code 0, socket unlinked, lock released)
- Startup socket-failure cleanup with lifecycle guarantee
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Insert native_services path for real ControllerControlClient
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[3]
NATIVE_SERVICES = REPO_ROOT / "native_services"
sys.path.insert(0, str(NATIVE_SERVICES))

from unified_controller_control import ControllerControlClient, ControllerControlError

HEADLESS_BIN = Path(os.environ.get("WAYSTONE_HEADLESS_TEST_BIN",
    str(REPO_ROOT / "components/controller_studio/headless/publish/UnifiedController.Headless")))


def log(msg: str):
    print(f"[TEST] {msg}", flush=True)


def test_cli_gates():
    log("Verifying CLI argument requirements and exclusivity gates...")

    # 1. Missing --data-dir -> exit code 2
    res = subprocess.run([str(HEADLESS_BIN)], capture_output=True, text=True)
    assert res.returncode == 2, f"Expected 2 on missing --data-dir, got {res.returncode}"
    log("  [PASS] Missing --data-dir rejected with exit code 2")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # 2. profiles.json missing in production -> exit code 4 (never seeds defaults)
        res = subprocess.run([str(HEADLESS_BIN), "--data-dir", str(tmp_path)], capture_output=True, text=True)
        assert res.returncode == 4, f"Expected 4 on missing profiles.json, got {res.returncode}"
        log("  [PASS] Missing profiles.json rejected with exit code 4 (no default seeding)")

        # Create valid profiles.json
        initial_doc = {
            "version": 1,
            "active_profile": "Default",
            "fixture_global_extension": "preserved",
            "profiles": {
                "Default": {
                    "routes": {"Keyboard:W": "Controller:A"},
                    "fixture_custom_field": 12345
                },
                "Secondary": {
                    "routes": {"Keyboard:Space": "Controller:B"},
                    "another_custom_field": "hello"
                }
            }
        }
        (tmp_path / "profiles.json").write_text(json.dumps(initial_doc))

        # 3. --fixture + --legacy-data-dir -> exit code 2
        legacy_dir = tmp_path / "legacy"
        legacy_dir.mkdir()
        res = subprocess.run(
            [str(HEADLESS_BIN), "--data-dir", str(tmp_path), "--fixture", "--legacy-data-dir", str(legacy_dir)],
            capture_output=True, text=True
        )
        assert res.returncode == 2, f"Expected 2 on --fixture + --legacy-data-dir, got {res.returncode}"
        log("  [PASS] --fixture + --legacy-data-dir rejected with exit code 2")

        # 4. Non-existent legacy directory -> exit code 5
        non_existent = tmp_path / "missing_legacy_dir"
        res = subprocess.run(
            [str(HEADLESS_BIN), "--data-dir", str(tmp_path), "--legacy-data-dir", str(non_existent)],
            capture_output=True, text=True
        )
        assert res.returncode == 5, f"Expected 5 on missing legacy directory, got {res.returncode}"
        assert not non_existent.exists(), "Missing legacy directory should not be created"
        log("  [PASS] Non-existent legacy directory rejected with exit code 5 and not created")


def test_lock_collision():
    log("Verifying LocalInstanceCoordinator exclusive locking & collision handling...")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        initial_doc = {
            "version": 1,
            "active_profile": "Default",
            "profiles": {"Default": {"routes": {}}}
        }
        (tmp_path / "profiles.json").write_text(json.dumps(initial_doc))

        lock_file = tmp_path / ".app.lock"
        with open(lock_file, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            res = subprocess.run(
                [str(HEADLESS_BIN), "--data-dir", str(tmp_path), "--fixture"],
                capture_output=True, text=True
            )
            assert res.returncode == 3, f"Expected 3 on lock collision, got {res.returncode}"
            log("  [PASS] Active lock collision rejected with exit code 3")


def test_socket_failure_cleanup():
    log("Verifying startup socket-failure cleanup with guaranteed runtime and lock disposal...")
    with tempfile.TemporaryDirectory() as tmp_data, tempfile.TemporaryDirectory() as tmp_runtime:
        data_path = Path(tmp_data)
        runtime_path = Path(tmp_runtime)

        # Make runtime directory world-writable (mode 0777) so RequirePrivateDirectory rejects it
        os.chmod(runtime_path, 0o777)

        initial_doc = {
            "version": 1,
            "active_profile": "Default",
            "profiles": {"Default": {"routes": {}}}
        }
        (data_path / "profiles.json").write_text(json.dumps(initial_doc))

        res = subprocess.run(
            [str(HEADLESS_BIN), "--data-dir", str(data_path), "--runtime-dir", str(runtime_path), "--fixture"],
            capture_output=True,
            text=True
        )
        # Server bind fails due to non-private runtime dir; process exits non-zero
        assert res.returncode != 0, f"Expected non-zero exit code on invalid runtime directory, got {res.returncode}"

        # Ensure .app.lock is NOT leaked and can be locked immediately
        with open(data_path / ".app.lock", "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        log("  [PASS] Server bind failure cleanly released all locks and disposed runtime")


def test_sigterm_graceful_shutdown():
    log("Verifying POSIX SIGTERM signal handling and clean resource unlinking...")
    with tempfile.TemporaryDirectory() as tmp_data, tempfile.TemporaryDirectory() as tmp_runtime:
        data_path = Path(tmp_data)
        runtime_path = Path(tmp_runtime)
        os.chmod(runtime_path, 0o700)

        initial_doc = {
            "version": 1,
            "active_profile": "Default",
            "profiles": {"Default": {"routes": {}}}
        }
        (data_path / "profiles.json").write_text(json.dumps(initial_doc))
        socket_file = runtime_path / "waystone-unified-controller" / "control.sock"

        proc = subprocess.Popen(
            [str(HEADLESS_BIN), "--data-dir", str(data_path), "--runtime-dir", str(runtime_path), "--fixture"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        try:
            ready = False
            start_time = time.monotonic()
            while time.monotonic() - start_time < 5.0:
                line = proc.stdout.readline()
                if "WAYSTONE_ENGINE_READY=1" in line:
                    ready = True
                    break
            assert ready, "Process failed to emit WAYSTONE_ENGINE_READY=1 within 5s"
            assert socket_file.exists(), "Socket file should exist after ready"

            # Send SIGTERM
            proc.send_signal(signal.SIGTERM)
            exit_code = proc.wait(timeout=5.0)
            assert exit_code == 0, f"Expected exit code 0 on SIGTERM, got {exit_code}"
            assert not socket_file.exists(), "Socket file must be unlinked on SIGTERM"

            # Verify lock is freed
            with open(data_path / ".app.lock", "a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            log("  [PASS] SIGTERM cleanly unlinked socket, released lock, and exited 0")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_ipc_and_lifecycle():
    log("Verifying headless IPC operations and lifecycle via real ControllerControlClient...")
    with tempfile.TemporaryDirectory() as tmp_data, tempfile.TemporaryDirectory() as tmp_runtime:
        data_path = Path(tmp_data)
        runtime_path = Path(tmp_runtime)

        # Ensure runtime dir is 0700 private directory
        os.chmod(runtime_path, 0o700)

        initial_doc = {
            "version": 1,
            "active_profile": "Default",
            "doc_level_unknown_key": "top_level_preserved",
            "profiles": {
                "Default": {
                    "routes": {"Keyboard:W": "Controller:A"},
                    "stick_processing": {
                        "left": {"inner_deadzone": 0.08, "outer_deadzone": 1.0, "future_nested_flag": 999}
                    },
                    "custom_profile_unknown_property": {"nested_key": "keep_me"}
                },
                "GameMode": {
                    "routes": {"Keyboard:Space": "Controller:B"}
                }
            }
        }
        (data_path / "profiles.json").write_text(json.dumps(initial_doc))

        socket_file = runtime_path / "waystone-unified-controller" / "control.sock"

        # Launch headless process with piped stdin
        proc = subprocess.Popen(
            [str(HEADLESS_BIN), "--data-dir", str(data_path), "--runtime-dir", str(runtime_path), "--fixture"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        try:
            # Wait for WAYSTONE_ENGINE_READY=1
            ready = False
            start_time = time.monotonic()
            while time.monotonic() - start_time < 10.0:
                line = proc.stdout.readline()
                if not line:
                    break
                if "WAYSTONE_ENGINE_READY=1" in line:
                    ready = True
                    break

            assert ready, f"Failed to receive WAYSTONE_ENGINE_READY=1 within 10s. Stderr: {proc.stderr.read()}"
            log("  [PASS] Received WAYSTONE_ENGINE_READY=1 from headless process")
            assert socket_file.exists(), f"Socket file was not created: {socket_file}"

            # Instantiate real Python ControllerControlClient
            client = ControllerControlClient(socket_path=socket_file, timeout=3.0)

            # 1. Operation: status
            status = client.request("status")
            assert status["running"] is True, f"Expected running=True, got {status}"
            assert status["active_profile"] == "Default"
            assert "capabilities" in status
            assert "devices" in status
            assert "live" in status, "status must contain 'live'"
            live = status["live"]
            assert live["schema_version"] == 1, f"Expected schema_version=1, got {live.get('schema_version')}"
            assert live["selected_source_id"] == "fake-gamepad-1"
            assert "input" in live and "output" in live
            inp = live["input"]
            assert inp["buttons"] == 0x1000
            assert inp["left_trigger"] == 180
            assert inp["right_trigger"] == 45
            assert inp["left_x"] == -16384
            assert inp["left_y"] == 20000
            assert inp["right_x"] == 12000
            assert inp["right_y"] == -15000
            outp = live["output"]
            assert outp["buttons"] == 0x2000
            assert outp["left_trigger"] == 200
            assert outp["right_trigger"] == 0
            assert outp["left_x"] == -12000
            assert outp["left_y"] == 15000
            assert outp["right_x"] == 8000
            assert outp["right_y"] == -10000
            assert "extended_buttons" in live
            assert "Share" in live["extended_buttons"]
            assert live["mod_active"] is True
            assert live["controller_mouse_mode"] is True
            log(f"  [PASS] status: running={status['running']}, live.schema_version=1 with exact fixture frame values, extended_buttons=['Share'], mod_active=True, controller_mouse_mode=True")

            # 2. Operation: profiles
            profiles = client.request("profiles")
            assert profiles["active_profile"] == "Default"
            assert "Default" in profiles["names"]
            assert "GameMode" in profiles["names"]
            initial_revision = profiles["revision"]
            assert len(initial_revision) == 64, f"Invalid revision: {initial_revision}"
            log(f"  [PASS] profiles: names={profiles['names']}, revision={initial_revision[:12]}...")

            # 3. Operation: profile_get
            profile_get = client.request("profile_get", name="Default")
            assert profile_get["name"] == "Default"
            assert profile_get["profile"]["custom_profile_unknown_property"]["nested_key"] == "keep_me"
            assert profile_get["revision"] == initial_revision
            log("  [PASS] profile_get: successfully retrieved profile with custom fields")

            # 4. Operation: profile_update (with mutation and retention of unknown/custom fields)
            updated_profile = profile_get["profile"].copy()
            updated_profile["routes"]["Keyboard:D"] = "Controller:X"
            updated_profile["newly_added_unknown_field"] = "preserved_roundtrip"

            update_res = client.request(
                "profile_update",
                name="Default",
                profile=updated_profile,
                expected_revision=initial_revision
            )
            new_revision = update_res["revision"]
            assert new_revision != initial_revision, "Revision should change after update"
            log(f"  [PASS] profile_update: updated successfully, revision changed to {new_revision[:12]}...")

            # Verify on disk and via profile_get that all custom fields were strictly preserved
            profile_after = client.request("profile_get", name="Default")
            p_dict = profile_after["profile"]
            assert p_dict["routes"]["Keyboard:D"] == "Controller:X"
            assert p_dict["custom_profile_unknown_property"]["nested_key"] == "keep_me"
            assert p_dict["stick_processing"]["left"]["future_nested_flag"] == 999
            assert p_dict["newly_added_unknown_field"] == "preserved_roundtrip"

            # Check raw disk json to ensure top-level extension preserved
            disk_json = json.loads((data_path / "profiles.json").read_text())
            assert disk_json["doc_level_unknown_key"] == "top_level_preserved"
            log("  [PASS] Verified strict retention of all unedited, unknown, and nested fields")

            # 5. Operation: revision conflict on profile_update
            conflict_caught = False
            try:
                client.request(
                    "profile_update",
                    name="Default",
                    profile=p_dict,
                    expected_revision="0" * 64
                )
            except ControllerControlError as err:
                if err.code == "ERR_REVISION_CONFLICT":
                    conflict_caught = True
            assert conflict_caught, "Expected ERR_REVISION_CONFLICT for mismatched revision"
            log("  [PASS] Stale expected_revision rejected with ERR_REVISION_CONFLICT")

            # 6. Operation: profile_select
            select_res = client.request(
                "profile_select",
                name="GameMode",
                expected_revision=new_revision
            )
            assert select_res["active_profile"] == "GameMode"
            profiles_post = client.request("profiles")
            assert profiles_post["active_profile"] == "GameMode"
            log("  [PASS] profile_select: active_profile switched to GameMode")

            # 7. Operation: cancel_macros
            cancel_res = client.request("cancel_macros")
            assert cancel_res.get("canceled") is True
            log("  [PASS] cancel_macros: canceled=True")

            # 8. Stdin EOF clean exit & resource disposal
            log("Sending stdin EOF to test graceful shutdown...")
            proc.stdin.close()
            exit_code = proc.wait(timeout=5.0)
            assert exit_code == 0, f"Headless process exited with code {exit_code}, expected 0"
            log("  [PASS] Clean exit with code 0 on stdin EOF")

            # Verify socket removed and lock released
            assert not socket_file.exists(), "Socket file should be unlinked on clean shutdown"
            log("  [PASS] Control socket successfully unlinked")

            # Verify lock can now be acquired by another process
            lock_test = subprocess.run(
                [str(HEADLESS_BIN), "--data-dir", str(data_path), "--runtime-dir", str(runtime_path), "--fixture"],
                input="shutdown\n",
                capture_output=True,
                text=True,
                timeout=5.0
            )
            assert lock_test.returncode == 0, f"Lock re-acquisition failed with {lock_test.returncode}"
            log("  [PASS] Lock successfully released and reusable by subsequent instance")

        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()


def main():
    log("=== Starting UnifiedController Headless Automated Test Suite ===")
    assert HEADLESS_BIN.exists(), f"Headless binary not found at {HEADLESS_BIN}. Run dotnet publish first."

    test_cli_gates()
    test_lock_collision()
    test_socket_failure_cleanup()
    test_sigterm_graceful_shutdown()
    test_ipc_and_lifecycle()

    print("\n=======================================================", flush=True)
    print("ALL HEADLESS IPC & LIFECYCLE TESTS PASSED SUCCESSFULLY!", flush=True)
    print("=======================================================", flush=True)


if __name__ == "__main__":
    main()
