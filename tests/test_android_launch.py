import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("android_launch", Path(__file__).resolve().parents[1] / "tools/android_launch.py")
android_launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(android_launch)


def _proc(root, pid, cmd):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(cmd.encode() + b"\0--flag\0")


def test_app_pids_match_the_package_and_its_subprocesses_only(tmp_path):
    _proc(tmp_path, 10, "com.ea.game.pvz2_rfl")
    _proc(tmp_path, 11, "com.ea.game.pvz2_rfl:remote")
    _proc(tmp_path, 12, "com.ea.game.pvz2_rfl_other")
    _proc(tmp_path, 13, "/usr/bin/python3 android_launch.py --package com.ea.game.pvz2_rfl")
    (tmp_path / "self").mkdir()
    assert sorted(android_launch.app_pids("com.ea.game.pvz2_rfl", tmp_path)) == [10, 11]


def test_waydroid_runs_with_the_system_python_path():
    assert android_launch.waydroid_env()["PATH"] == android_launch.SYSTEM_PATH
