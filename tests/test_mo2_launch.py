import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import mo2_launch  # noqa: E402


def test_tree_follows_children_only():
    table = {1: (0, "init"), 10: (1, "proton run MO2"), 11: (10, "ModOrganizer.exe"), 12: (11, "SkyrimSE.exe"),
             20: (1, "other")}
    assert mo2_launch.tree(10, table) == {10, 11, 12}
    assert mo2_launch.tree(99, table) == set()


def test_game_window_vs_mod_organizer_windows():
    assert mo2_launch.game_window({"title": "Skyrim Special Edition"}, "SkyrimSE.exe")
    assert mo2_launch.game_window({"title": "Fallout4"}, "Fallout4.exe")
    assert not mo2_launch.game_window({"title": "ModOrganizer"}, "SkyrimSE.exe")
    assert not mo2_launch.game_window({"title": "Mod Organizer v2.4.4"}, "SkyrimSE.exe")
    assert not mo2_launch.game_window({"title": ""}, "SkyrimSE.exe")
