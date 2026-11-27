"""Unit tests for native provider jobs backend (Legendary, GOGdl, Nile).

Verifies:
- Strict recipe validation (no outer whitespace, no option prefixes, provider-specific ID formats).
- Absolute path enforcement.
- Pure plan creation (zero side effects or process execution).
- Native CLI verb mapping:
    * Legendary: 'install' with '--update-only' or '--repair'
    * GOGdl: 'download', 'update', 'repair'
    * Nile: 'install', 'update', 'verify'
- Accurate destination directory projection (Legendary metadata folder vs explicit --game-folder).
- Secret hygiene (never emits raw stdout, URLs, or arbitrary exception text).
- Process group reap & clean cancellation.
- Immutable plan snapshot verification.
"""
import asyncio
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _fake_tool(name: str) -> Path:
    """Absolute fake executable path valid on both POSIX and Windows."""
    return Path(tempfile.gettempdir()) / "waystone-test-tools" / name

from native_services.provider_jobs import (
    ProviderExecutionError,
    ProviderInstallPlan,
    ProviderInstallRecipe,
    ProviderJobManager,
    ProviderJobProgress,
    ProviderToolConfig,
    ProviderValidationError,
    build_provider_command,
    default_managed_process_runner,
)


class MockProcessRunner:
    def __init__(self):
        self.calls = []
        self.simulated_percents = []
        self.returncode = 0
        self.delay = 0.0

    async def __call__(self, argv, cwd, env, progress_callback=None):
        self.calls.append({
            "argv": argv,
            "cwd": cwd,
            "env": dict(env),
        })
        for pct in self.simulated_percents:
            if progress_callback:
                progress_callback(pct)
            if self.delay > 0:
                await asyncio.sleep(self.delay)
        return self.returncode


class TestProviderJobs(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.install_root = Path(tempfile.gettempdir()) / "mock_games"
        self.mock_runner = MockProcessRunner()
        self.custom_configs = {
            "epic": ProviderToolConfig(binary_path=_fake_tool("legendary")),
            "gog": ProviderToolConfig(binary_path=_fake_tool("gogdl")),
            "amazon": ProviderToolConfig(binary_path=_fake_tool("nile")),
        }
        self.manager = ProviderJobManager(
            tool_configs=self.custom_configs,
            process_runner=self.mock_runner,
        )

    def test_strict_recipe_validation(self):
        # Unsupported provider
        with self.assertRaises(ProviderValidationError):
            ProviderInstallRecipe(
                provider="steam",
                product_id="12345",
                title="Invalid Provider Game",
                install_root=self.install_root,
            )

        # Leading or trailing whitespace
        with self.assertRaises(ProviderValidationError):
            ProviderInstallRecipe(
                provider="epic",
                product_id="  Fortnite  ",
                title="Game",
                install_root=self.install_root,
            )

        # Option-like product IDs
        with self.assertRaises(ProviderValidationError):
            ProviderInstallRecipe(
                provider="epic",
                product_id="--malicious-flag",
                title="Game",
                install_root=self.install_root,
            )
        with self.assertRaises(ProviderValidationError):
            ProviderInstallRecipe(
                provider="epic",
                product_id="/malicious/root",
                title="Game",
                install_root=self.install_root,
            )

        # Path traversal
        with self.assertRaises(ProviderValidationError):
            ProviderInstallRecipe(
                provider="epic",
                product_id="../traversal",
                title="Game",
                install_root=self.install_root,
            )

        # GOG requires positive numeric ID
        with self.assertRaises(ProviderValidationError):
            ProviderInstallRecipe(
                provider="gog",
                product_id="non_numeric_slug",
                title="GOG Game",
                install_root=self.install_root,
            )

        # Relative paths rejected
        with self.assertRaises(ProviderValidationError):
            ProviderInstallRecipe(
                provider="epic",
                product_id="ValidId",
                title="Game",
                install_root=Path("relative/path"),
            )

        with self.assertRaises(ProviderValidationError):
            ProviderInstallRecipe(
                provider="gog",
                product_id="123456",
                title="Game",
                install_root=self.install_root,
                auth_config_path=Path("relative/auth.json"),
            )

        # Relative tool path rejected
        with self.assertRaises(ProviderValidationError):
            ProviderToolConfig(binary_path=Path("relative/bin"))

    def test_plan_creation_is_pure_and_does_not_execute(self):
        recipe = ProviderInstallRecipe(
            provider="epic",
            product_id="SugarApp",
            title="Sugar Title",
            install_root=self.install_root,
        )
        plan = self.manager.create_plan(recipe)
        self.assertIsInstance(plan, ProviderInstallPlan)
        # Runner must NOT have been called
        self.assertEqual(len(self.mock_runner.calls), 0)
        # When game-folder is omitted for Legendary, destination_dir is None
        self.assertIsNone(plan.destination_dir)
        self.assertIn("SugarApp", plan.argv)

    def test_native_verbs_and_flags_epic(self):
        # Install
        r_inst = ProviderInstallRecipe(
            provider="epic", product_id="App1", title="Title", install_root=self.install_root,
            game_folder="CustomFolder", with_dlcs=True,
        )
        argv, dest = build_provider_command(r_inst, self.custom_configs["epic"])
        self.assertEqual(argv, (
            str(self.custom_configs["epic"].binary_path), "install", "App1", "--base-path", str(self.install_root),
            "--game-folder", "CustomFolder", "--with-dlcs", "-y",
        ))
        self.assertEqual(dest, self.install_root / "CustomFolder")

        # Update
        r_up = ProviderInstallRecipe(
            provider="epic", product_id="App1", title="Title", install_root=self.install_root,
            operation="update",
        )
        argv, dest = build_provider_command(r_up, self.custom_configs["epic"])
        self.assertEqual(argv, (
            str(self.custom_configs["epic"].binary_path), "install", "App1", "--base-path", str(self.install_root),
            "--update-only", "-y",
        ))
        self.assertIsNone(dest)

        # Repair
        r_rep = ProviderInstallRecipe(
            provider="epic", product_id="App1", title="Title", install_root=self.install_root,
            operation="repair",
        )
        argv, dest = build_provider_command(r_rep, self.custom_configs["epic"])
        self.assertEqual(argv, (
            str(self.custom_configs["epic"].binary_path), "install", "App1", "--base-path", str(self.install_root),
            "--repair", "-y",
        ))

    def test_native_verbs_and_flags_gog(self):
        # GOG Install -> verb 'download'
        r_inst = ProviderInstallRecipe(
            provider="gog", product_id="1424375862", title="Witcher 3", install_root=self.install_root,
            operation="install", with_dlcs=True,
        )
        argv, dest = build_provider_command(r_inst, self.custom_configs["gog"])
        self.assertEqual(argv, (
            str(self.custom_configs["gog"].binary_path), "download", "1424375862", "--path", str(self.install_root / "1424375862"), "--with-dlcs",
        ))
        self.assertEqual(dest, self.install_root / "1424375862")

        # GOG Update -> verb 'update'
        r_up = ProviderInstallRecipe(
            provider="gog", product_id="1424375862", title="Witcher 3", install_root=self.install_root,
            operation="update",
        )
        argv, dest = build_provider_command(r_up, self.custom_configs["gog"])
        self.assertEqual(argv[1], "update")

        # GOG Repair -> verb 'repair'
        r_rep = ProviderInstallRecipe(
            provider="gog", product_id="1424375862", title="Witcher 3", install_root=self.install_root,
            operation="repair",
        )
        argv, dest = build_provider_command(r_rep, self.custom_configs["gog"])
        self.assertEqual(argv[1], "repair")

    def test_native_verbs_and_flags_amazon(self):
        # Nile Install -> verb 'install'
        r_inst = ProviderInstallRecipe(
            provider="amazon", product_id="amzn1.test", title="Game", install_root=self.install_root,
            operation="install",
        )
        argv, dest = build_provider_command(r_inst, self.custom_configs["amazon"])
        self.assertEqual(argv, (
            str(self.custom_configs["amazon"].binary_path), "install", "amzn1.test", "--path", str(self.install_root / "amzn1.test"),
        ))

        # Nile Update -> verb 'update'
        r_up = ProviderInstallRecipe(
            provider="amazon", product_id="amzn1.test", title="Game", install_root=self.install_root,
            operation="update",
        )
        argv, dest = build_provider_command(r_up, self.custom_configs["amazon"])
        self.assertEqual(argv[1], "update")

        # Nile Repair -> verb 'verify'
        r_rep = ProviderInstallRecipe(
            provider="amazon", product_id="amzn1.test", title="Game", install_root=self.install_root,
            operation="repair",
        )
        argv, dest = build_provider_command(r_rep, self.custom_configs["amazon"])
        self.assertEqual(argv[1], "verify")

    async def test_job_execution_success_and_progress_parsing(self):
        recipe = ProviderInstallRecipe(
            provider="epic",
            product_id="Hades",
            title="Hades",
            install_root=self.install_root,
        )
        plan = self.manager.create_plan(recipe)
        self.mock_runner.simulated_percents = [25.5, 75.0]
        self.mock_runner.returncode = 0

        progress_history = []
        result = await self.manager.execute_job(plan, progress_callback=progress_history.append)

        self.assertEqual(result.phase, "completed")
        self.assertEqual(result.percent, 100.0)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(self.mock_runner.calls), 1)

        percents = [p.percent for p in progress_history if p.percent is not None]
        self.assertIn(25.5, percents)
        self.assertIn(75.0, percents)
        self.assertIn(100.0, percents)

    async def test_job_execution_failure_sanitizes_error(self):
        recipe = ProviderInstallRecipe(
            provider="gog",
            product_id="123456",
            title="Failing Game",
            install_root=self.install_root,
        )
        plan = self.manager.create_plan(recipe)
        self.mock_runner.returncode = 2

        result = await self.manager.execute_job(plan)
        self.assertEqual(result.phase, "failed")
        self.assertEqual(result.exit_code, 2)
        # Error must be a sanitized typed code
        self.assertEqual(result.error, "ERR_PROCESS_FAILED_2")
        self.assertEqual(result.message, "Process exited with error code 2")

    async def test_plan_forgery_rejected(self):
        recipe = ProviderInstallRecipe(
            provider="amazon",
            product_id="amzn1.real",
            title="Real Game",
            install_root=self.install_root,
        )
        legit_plan = self.manager.create_plan(recipe)

        # Attempt to forge argv with arbitrary command
        forged_plan = ProviderInstallPlan(
            recipe=recipe,
            tool_binary=legit_plan.tool_binary,
            argv=(str(legit_plan.tool_binary), "evil_injected_command"),
            cwd=legit_plan.cwd,
            destination_dir=legit_plan.destination_dir,
        )
        with self.assertRaises(ProviderValidationError):
            await self.manager.execute_job(forged_plan)

    async def test_plan_tampering_rejected_via_replace(self):
        recipe = ProviderInstallRecipe(
            provider="amazon",
            product_id="amzn1.real",
            title="Real Game",
            install_root=self.install_root,
        )
        legit_plan = self.manager.create_plan(recipe)

        # Tampered cwd
        with self.assertRaises(ProviderValidationError):
            await self.manager.execute_job(replace(legit_plan, cwd=self.install_root / "etc"))

        # Tampered env (e.g. LD_PRELOAD)
        with self.assertRaises(ProviderValidationError):
            await self.manager.execute_job(replace(legit_plan, env={"LD_PRELOAD": "/evil.so"}))

        # Tampered destination_dir
        with self.assertRaises(ProviderValidationError):
            await self.manager.execute_job(replace(legit_plan, destination_dir=self.install_root / "bin"))

        # Tampered tool_binary
        with self.assertRaises(ProviderValidationError):
            await self.manager.execute_job(replace(legit_plan, tool_binary=_fake_tool("evil")))

        # Tampered argv
        with self.assertRaises(ProviderValidationError):
            await self.manager.execute_job(replace(legit_plan, argv=("/bin/echo", "hacked")))

    async def test_root_creation_only_on_explicit_execute(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            non_existent_root = Path(tmp_dir) / "non_existent_subdir" / "Games"
            self.assertFalse(non_existent_root.exists())

            recipe = ProviderInstallRecipe(
                provider="amazon",
                product_id="amzn1.root_test",
                title="Root Test Game",
                install_root=non_existent_root,
            )
            # Pure plan creation must NOT create the directory on disk
            plan = self.manager.create_plan(recipe)
            self.assertFalse(non_existent_root.exists())

            # Explicit execute creates verified install root and destination dir
            res = await self.manager.execute_job(plan)
            self.assertEqual(res.phase, "completed")
            self.assertTrue(non_existent_root.exists())
            self.assertTrue((non_existent_root / "amzn1.root_test").exists())

    async def test_real_subprocess_drain_large_no_newline_stdout_no_deadlock(self):
        # Emits >64KiB without newlines to verify pipe does not deadlock
        script = (
            "import sys; "
            "sys.stdout.write('X' * 80000 + ' 73.5% ' + 'Y' * 20000); "
            "sys.stdout.flush()"
        )
        argv = (sys.executable, "-c", script)
        captured_percents = []

        ret = await default_managed_process_runner(
            argv=argv,
            cwd=Path.cwd(),
            env={},
            progress_callback=captured_percents.append,
        )
        self.assertEqual(ret, 0)
        self.assertIn(73.5, captured_percents)

    async def test_real_subprocess_nonzero_exit_code(self):
        script = "import sys; sys.exit(42)"
        argv = (sys.executable, "-c", script)

        ret = await default_managed_process_runner(
            argv=argv,
            cwd=Path.cwd(),
            env={},
        )
        self.assertEqual(ret, 42)

    async def test_real_subprocess_cancellation_and_group_reap(self):
        # Spawns a harmless sleeping child and ensures cancellation reaps process group
        script = "import time; time.sleep(30)"
        argv = (sys.executable, "-c", script)

        task = asyncio.create_task(
            default_managed_process_runner(
                argv=argv,
                cwd=Path.cwd(),
                env={},
            )
        )
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_job_cancellation(self):
        recipe = ProviderInstallRecipe(
            provider="amazon",
            product_id="slow_game",
            title="Slow Game",
            install_root=self.install_root,
        )
        plan = self.manager.create_plan(recipe)

        self.mock_runner.delay = 0.05
        self.mock_runner.simulated_percents = [10.0, 20.0, 30.0]

        exec_task = asyncio.create_task(self.manager.execute_job(plan))
        await asyncio.sleep(0.02)

        cancelled = await self.manager.cancel_job("amazon", "slow_game")
        self.assertTrue(cancelled)

        with self.assertRaises(asyncio.CancelledError):
            await exec_task

    async def test_cancel_reaps_descendant_even_after_process_leader_exited(self):
        import tempfile,os,time
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'child.pid'
            child="import time;time.sleep(30)"
            parent=("import subprocess,sys,pathlib; p=subprocess.Popen([sys.executable,'-c',"+repr(child)+"]); "
                    "pathlib.Path("+repr(str(marker))+").write_text(str(p.pid))")
            task=asyncio.create_task(default_managed_process_runner((sys.executable,'-c',parent),Path(folder),{}))
            try:
                for _ in range(100):
                    if marker.exists():break
                    await asyncio.sleep(.01)
                self.assertTrue(marker.exists())
                pid=int(marker.read_text());await asyncio.sleep(.05)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,3)
                proc=Path('/proc')/str(pid)/'stat'
                # A short-lived reparented zombie is not a running orphan.
                self.assertTrue(not proc.exists() or proc.read_text().split(') ',1)[1].split()[0]=='Z')
            finally:
                if not task.done():
                    task.cancel();await asyncio.gather(task,return_exceptions=True)


if __name__ == "__main__":
    unittest.main()


def test_default_provider_engines_are_waystone_owned_not_heroic_fallback():
    from native_services.provider_jobs import DEFAULT_TOOL_PATHS, MANAGED_ENGINE_DIR
    from native_services import waystone_paths
    assert set(DEFAULT_TOOL_PATHS) == {"epic", "gog", "amazon"}
    # the app's own copy, else Waystone's own checksum-verified download (W22: helpers are not bundled publicly)
    assert all(path.parent == MANAGED_ENGINE_DIR or str(path).startswith(str(waystone_paths.data_dir() / "provider-engines"))
               for path in DEFAULT_TOOL_PATHS.values())
    assert all("/Heroic/" not in str(path) for path in DEFAULT_TOOL_PATHS.values())
