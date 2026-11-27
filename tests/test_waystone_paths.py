from pathlib import Path
import pytest

from native_services import waystone_paths as wp

# This file asserts Linux XDG conventions; Windows AppData roots are exercised
# separately by tests/test_platform.py.
pytestmark = pytest.mark.linux_only


def test_new_install_uses_waystone_dir_and_xdg(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert wp.store("providers-v1", "gog") == tmp_path / ".local/share/waystone/providers-v1/gog"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert wp.store("saves-v1") == tmp_path / "data/waystone/saves-v1"
    monkeypatch.setenv("XDG_DATA_HOME", "relative/ignored")
    assert wp.data_dir() == tmp_path / ".local/share/waystone"


def test_explicit_home_is_used_for_fake_homes(tmp_path):
    assert wp.store("y", home=tmp_path) == tmp_path / ".local/share/waystone/y"


def test_launcher_scan_paths_share_xdg_resolver_and_ignore_relative_overrides(tmp_path):
    roots = wp.xdg_paths(tmp_path, {"XDG_DATA_HOME": str(tmp_path / "xdg-data"),
                                   "XDG_CONFIG_HOME": "relative-config"})
    assert roots == {"data": tmp_path / "xdg-data/waystone",
                     "config": tmp_path / ".config/waystone"}
