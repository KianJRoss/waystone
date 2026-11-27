"""Unit tests for W15 read-only game update detection and update job safety (fakes only)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from native_services.game_updates import (
    GameUpdateDetector,
    GameUpdateInfo,
    UPDATE_CHECK_INTERVAL_SECONDS,
    build_catalog_product_index,
    check_legendary_config_updates,
    check_nile_config_updates,
    config_root_lock,
    is_heroic_running,
    parse_legendary_installed_updates,
    parse_steam_appmanifest_update,
    sanitize_store_error_text,
    scan_steam_pending_updates,
)
from native_services.provider_jobs import (
    ProviderInstallRecipe,
    ProviderJobManager,
    ProviderToolConfig,
)


class TestGameUpdatesBackend(unittest.IsolatedAsyncioTestCase):
    def test_schedule_interval_is_six_hours(self):
        self.assertEqual(UPDATE_CHECK_INTERVAL_SECONDS, 6 * 3600)

    def test_epic_legendary_detection_waystone_and_heroic_configs(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws_cfg = base / "waystone_epic"
            hr_cfg = base / "heroic_epic"
            ws_cfg.mkdir(parents=True)
            hr_cfg.mkdir(parents=True)

            ws_bin = base / "ws_legendary"
            hr_bin = base / "hr_legendary"
            ws_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            hr_bin.write_text("#!/bin/sh\n", encoding="utf-8")

            (ws_cfg / "installed.json").write_text(
                json.dumps(
                    {
                        "Sugar": {
                            "app_name": "Sugar",
                            "title": "Rocket League",
                            "version": "1.0.0",
                            "install_path": str(base / "Games" / "RocketLeague"),
                            "platform": "Windows",
                        },
                        "UpToDateApp": {
                            "app_name": "UpToDateApp",
                            "title": "Clean Game",
                            "version": "2.0.0",
                            "install_path": str(base / "Games" / "CleanGame"),
                        },
                    }
                ),
                encoding="utf-8",
            )
            (hr_cfg / "installed.json").write_text(
                json.dumps(
                    {
                        "HeroicOnlyApp": {
                            "app_name": "HeroicOnlyApp",
                            "title": "Alan Wake Remastered",
                            "version": "1.1.0",
                            "install_path": str(base / "HeroicGames" / "AlanWake"),
                            "platform": "Windows",
                        }
                    }
                ),
                encoding="utf-8",
            )

            calls = []

            def fake_runner(argv, env=None):
                calls.append((list(argv), dict(env or {})))
                cfg_path = (env or {}).get("LEGENDARY_CONFIG_PATH", "")
                if cfg_path == str(ws_cfg):
                    return (
                        0,
                        json.dumps(
                            [
                                {
                                    "app_name": "Sugar",
                                    "title": "Rocket League",
                                    "version": "1.0.0",
                                    "remote_version": "1.2.0",
                                    "update_available": True,
                                    "install_path": str(base / "Games" / "RocketLeague"),
                                },
                                {
                                    "app_name": "UpToDateApp",
                                    "title": "Clean Game",
                                    "version": "2.0.0",
                                    "remote_version": "2.0.0",
                                    "update_available": False,
                                },
                            ]
                        ),
                        "",
                    )
                if cfg_path == str(hr_cfg):
                    return (
                        0,
                        json.dumps(
                            [
                                {
                                    "app_name": "HeroicOnlyApp",
                                    "title": "Alan Wake Remastered",
                                    "version": "1.1.0",
                                    "remote_version": "1.4.0",
                                }
                            ]
                        ),
                        "",
                    )
                return 1, "", "unknown config"

            detector = GameUpdateDetector(
                waystone_epic_config=ws_cfg,
                waystone_epic_binary=ws_bin,
                heroic_epic_config=hr_cfg,
                heroic_epic_binary=hr_bin,
                waystone_nile_config=base / "empty_nile",
                steam_library_dirs=[],
                runner=fake_runner,
                heroic_running_fn=lambda: False,
            )

            updates = detector.detect_all()
            self.assertEqual(len(updates), 2)
            by_id = {u.product_id: u for u in updates}

            self.assertIn("Sugar", by_id)
            sugar = by_id["Sugar"]
            self.assertEqual(sugar.provider, "epic")
            self.assertEqual(sugar.scope, "waystone")
            self.assertEqual(sugar.installed_version, "1.0.0")
            self.assertEqual(sugar.available_version, "1.2.0")
            self.assertEqual(sugar.config_root, str(ws_cfg))
            self.assertEqual(sugar.binary_path, str(ws_bin))
            self.assertTrue(sugar.blocks_launch)
            self.assertTrue(sugar.can_update_in_waystone)
            self.assertEqual(sugar.status_label, "Update available")

            self.assertIn("HeroicOnlyApp", by_id)
            heroic_app = by_id["HeroicOnlyApp"]
            self.assertEqual(heroic_app.provider, "epic")
            self.assertEqual(heroic_app.scope, "heroic")
            self.assertEqual(heroic_app.installed_version, "1.1.0")
            self.assertEqual(heroic_app.available_version, "1.4.0")
            self.assertEqual(heroic_app.config_root, str(hr_cfg))
            self.assertEqual(heroic_app.binary_path, str(hr_bin))
            self.assertTrue(heroic_app.blocks_launch)

    def test_epic_skips_heroic_config_when_heroic_is_running_or_config_locked(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            hr_cfg = base / "heroic_epic"
            hr_cfg.mkdir(parents=True)
            hr_bin = base / "hr_legendary"
            hr_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            (hr_cfg / "installed.json").write_text(
                json.dumps({"App1": {"app_name": "App1", "title": "Game 1", "version": "1.0"}}),
                encoding="utf-8",
            )

            calls = []

            def fake_runner(argv, env=None):
                calls.append(argv)
                return 0, json.dumps([{"app_name": "App1", "update_available": True}]), ""

            # 1. Skipped when Heroic is running
            res_busy = check_legendary_config_updates(
                hr_cfg,
                hr_bin,
                scope="heroic",
                runner=fake_runner,
                heroic_running_fn=lambda: True,
            )
            self.assertEqual(res_busy, [])
            self.assertEqual(len(calls), 0)

            # 2. Skipped when the config root lock is already held by another Legendary operation
            lock = config_root_lock(hr_cfg)
            self.assertTrue(lock.acquire(blocking=False))
            try:
                res_locked = check_legendary_config_updates(
                    hr_cfg,
                    hr_bin,
                    scope="heroic",
                    runner=fake_runner,
                    heroic_running_fn=lambda: False,
                )
                self.assertEqual(res_locked, [])
                self.assertEqual(len(calls), 0)
            finally:
                lock.release()

            # 3. Succeeds once Heroic is not running and lock is free
            res_ok = check_legendary_config_updates(
                hr_cfg,
                hr_bin,
                scope="heroic",
                runner=fake_runner,
                heroic_running_fn=lambda: False,
            )
            self.assertEqual(len(res_ok), 1)
            self.assertEqual(res_ok[0].product_id, "App1")
            self.assertEqual(len(calls), 1)

    def test_amazon_nile_list_updates_detection(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            nile_cfg = base / "amazon"
            (nile_cfg / "nile").mkdir(parents=True)
            nile_bin = base / "nile"
            nile_bin.write_text("#!/bin/sh\n", encoding="utf-8")

            (nile_cfg / "nile" / "installed.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "amzn1.adg.product.update_me",
                            "title": "Spelunky",
                            "version": "v1",
                            "path": str(base / "Games" / "Spelunky"),
                        },
                        {
                            "id": "amzn1.adg.product.current",
                            "title": "Current Game",
                            "version": "v5",
                            "path": str(base / "Games" / "Current"),
                        },
                    ]
                ),
                encoding="utf-8",
            )

            def fake_runner(argv, env=None):
                self.assertEqual(list(argv), [str(nile_bin), "list-updates", "--json"])
                self.assertEqual((env or {}).get("NILE_CONFIG_PATH"), str(nile_cfg))
                return 0, json.dumps(["amzn1.adg.product.update_me"]), ""

            updates = check_nile_config_updates(nile_cfg, nile_bin, runner=fake_runner)
            self.assertEqual(len(updates), 1)
            u = updates[0]
            self.assertEqual(u.provider, "amazon")
            self.assertEqual(u.product_id, "amzn1.adg.product.update_me")
            self.assertEqual(u.title, "Spelunky")
            self.assertEqual(u.installed_version, "v1")
            self.assertEqual(u.install_path, str(base / "Games" / "Spelunky"))
            self.assertEqual(u.status_label, "Update available")
            self.assertTrue(u.can_update_in_waystone)
            self.assertFalse(u.blocks_launch)

    def test_steam_appmanifest_pending_updates_detection(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "SteamLib"
            steamapps = lib / "steamapps"
            steamapps.mkdir(parents=True)

            # 1. StateFlags bit 2 (UpdateRequired: 4 | 2 = 6)
            (steamapps / "appmanifest_620.acf").write_text(
                '"AppState"\n{\n'
                '  "appid" "620"\n'
                '  "name" "Portal 2"\n'
                '  "installdir" "Portal 2"\n'
                '  "StateFlags" "6"\n'
                '  "UpdateResult" "0"\n'
                '  "BytesToDownload" "0"\n'
                '  "BytesDownloaded" "0"\n'
                '}\n',
                encoding="utf-8",
            )
            # 2. UpdateResult non-zero
            (steamapps / "appmanifest_440.acf").write_text(
                '"AppState"\n{\n'
                '  "appid" "440"\n'
                '  "name" "Team Fortress 2"\n'
                '  "installdir" "Team Fortress 2"\n'
                '  "StateFlags" "4"\n'
                '  "UpdateResult" "2"\n'
                '  "BytesToDownload" "0"\n'
                '  "BytesDownloaded" "0"\n'
                '}\n',
                encoding="utf-8",
            )
            # 3. BytesToDownload > BytesDownloaded
            (steamapps / "appmanifest_570.acf").write_text(
                '"AppState"\n{\n'
                '  "appid" "570"\n'
                '  "name" "Dota 2"\n'
                '  "installdir" "dota 2 beta"\n'
                '  "StateFlags" "4"\n'
                '  "UpdateResult" "0"\n'
                '  "BytesToDownload" "1048576"\n'
                '  "BytesDownloaded" "262144"\n'
                '}\n',
                encoding="utf-8",
            )
            # 4. Fully installed, no update pending
            (steamapps / "appmanifest_730.acf").write_text(
                '"AppState"\n{\n'
                '  "appid" "730"\n'
                '  "name" "Counter-Strike 2"\n'
                '  "installdir" "Counter-Strike Global Offensive"\n'
                '  "StateFlags" "4"\n'
                '  "UpdateResult" "0"\n'
                '  "BytesToDownload" "0"\n'
                '  "BytesDownloaded" "0"\n'
                '}\n',
                encoding="utf-8",
            )

            updates = scan_steam_pending_updates(library_dirs=[lib])
            by_id = {u.product_id: u for u in updates}
            self.assertEqual(set(by_id.keys()), {"620", "440", "570"})
            for appid in ("620", "440", "570"):
                info = by_id[appid]
                self.assertEqual(info.provider, "steam")
                self.assertEqual(info.status_label, "Update pending in Steam")
                self.assertFalse(info.can_update_in_waystone)
                self.assertFalse(info.blocks_launch)

    def test_steam_scan_defaults_to_the_users_home(self):
        """Live regression: GameUpdateDetector() with no steam_home crashed in steam_libraries(None)."""
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            lib = home / ".local" / "share" / "Steam"
            (lib / "steamapps").mkdir(parents=True)
            (lib / "steamapps" / "libraryfolders.vdf").write_text(
                '"libraryfolders"\n{\n  "0"\n  {\n    "path" "%s"\n  }\n}\n' % lib, encoding="utf-8")
            (lib / "steamapps" / "appmanifest_620.acf").write_text(
                '"AppState"\n{\n  "appid" "620"\n  "name" "Portal 2"\n  "StateFlags" "6"\n}\n', encoding="utf-8")
            with mock.patch("pathlib.Path.home", return_value=home):
                updates = scan_steam_pending_updates()
            self.assertEqual([u.product_id for u in updates], ["620"])

    def test_one_failing_store_does_not_hide_the_others(self):
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "SteamLib"
            (lib / "steamapps").mkdir(parents=True)
            (lib / "steamapps" / "appmanifest_620.acf").write_text(
                '"AppState"\n{\n  "appid" "620"\n  "name" "Portal 2"\n  "StateFlags" "6"\n}\n', encoding="utf-8")
            detector = GameUpdateDetector(
                waystone_epic_config=Path(tmp) / "epic",
                heroic_epic_config=Path(tmp) / "heroic",
                waystone_nile_config=Path(tmp) / "nile",
                steam_library_dirs=[lib],
                heroic_running_fn=lambda: False,
            )
            with mock.patch("native_services.game_updates.check_legendary_config_updates",
                            side_effect=RuntimeError("legendary exploded")), \
                    self.assertLogs("waystone.game_updates", level="ERROR"):
                updates = detector.detect_all()
            self.assertEqual([u.product_id for u in updates], ["620"])

    def test_epic_check_reads_legendary_csv_update_columns(self):
        """Legendary 0.21's --json has no latest version; the CSV (real output captured 2026-11-25) does."""
        import tempfile

        csv_out = (
            "App name,App title,Installed version,Available version,Update available,Install size,Install path,Platform\n"
            "Catnip,Borderlands 3,1.0.26_CL_3758051_Borderlands_3,1.0.26_CL_3758051_Borderlands_3,False,102663877896,/games/Catnip,Windows\n"
            "Sugar,Rocket League\u00ae,++Prime+Update60.1-CL-533021,++Prime+Update61-CL-540000,True,44267336990,/games/rocketleague,Windows\n"
        )
        calls = []

        def runner(argv, env=None):
            calls.append(list(argv))
            return 0, csv_out, ""

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "legendary"
            cfg.mkdir()
            (cfg / "installed.json").write_text(json.dumps({
                "Catnip": {"app_name": "Catnip", "title": "Borderlands 3", "version": "1"},
                "Sugar": {"app_name": "Sugar", "title": "Rocket League", "version": "1"},
            }), encoding="utf-8")
            updates = check_legendary_config_updates(cfg, Path("/bin/legendary"), scope="heroic", runner=runner,
                                                     heroic_running_fn=lambda: False)
        self.assertEqual(calls[0][1:], ["list-installed", "--check-updates", "--csv"])
        self.assertEqual(len(updates), 1)
        info = updates[0]
        self.assertEqual((info.product_id, info.title, info.scope), ("Sugar", "Rocket League\u00ae", "heroic"))
        self.assertEqual(info.available_version, "++Prime+Update61-CL-540000")
        self.assertEqual(info.install_path, "/games/rocketleague")
        self.assertTrue(info.blocks_launch)

    def test_is_heroic_running_fake_proc(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            p1 = proc / "999991"
            p1.mkdir()
            (p1 / "comm").write_text("bash\n", encoding="utf-8")
            (p1 / "cmdline").write_bytes(b"/bin/bash\x00")
            self.assertFalse(is_heroic_running(proc_root=proc))

            p2 = proc / "999992"
            p2.mkdir()
            (p2 / "comm").write_text("heroic\n", encoding="utf-8")
            (p2 / "cmdline").write_bytes(b"/opt/Heroic/heroic\x00")
            self.assertTrue(is_heroic_running(proc_root=proc))

    async def test_failed_update_shows_plain_store_error_text_and_runs_sequentially_per_store(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Games"
            active_count = 0
            max_concurrent = 0

            async def fake_runner(argv, cwd, env, progress_callback=None):
                nonlocal active_count, max_concurrent
                active_count += 1
                max_concurrent = max(max_concurrent, active_count)
                await asyncio.sleep(0.02)
                active_count -= 1
                if "FailApp" in argv:
                    return (
                        1,
                        "[cli] INFO: Checking manifest...\n"
                        "[cli] ERROR: Not enough disk space to apply update (14.2 GiB required) https://epic.example/token?secret=1\n",
                    )
                if progress_callback:
                    progress_callback(100.0)
                return 0

            manager = ProviderJobManager(
                tool_configs={"epic": ProviderToolConfig(binary_path=root / "tools" / "legendary")},
                process_runner=fake_runner,
            )

            plan_fail = manager.create_plan(
                ProviderInstallRecipe(
                    provider="epic",
                    product_id="FailApp",
                    title="Failing Update Game",
                    install_root=root,
                    operation="update",
                )
            )
            plan_ok = manager.create_plan(
                ProviderInstallRecipe(
                    provider="epic",
                    product_id="OkApp",
                    title="Second Update Game",
                    install_root=root,
                    operation="update",
                )
            )

            res_fail, res_ok = await asyncio.gather(
                manager.execute_job(plan_fail),
                manager.execute_job(plan_ok),
            )

            # Updates for the same store must run sequentially (never >1 active runner at once)
            self.assertEqual(max_concurrent, 1)

            # Failed update surfaces the store's own error text plainly with any URL redacted
            self.assertEqual(res_fail.phase, "failed")
            self.assertIn("Not enough disk space to apply update", res_fail.message)
            self.assertNotIn("https://epic.example", res_fail.message)
            self.assertEqual(res_ok.phase, "completed")


if __name__ == "__main__":
    unittest.main()
