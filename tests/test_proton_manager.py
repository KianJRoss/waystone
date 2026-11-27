import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from native_services import proton_manager as pm

# Proton discovery/configuration is a Linux/Steam compatibility-tool feature.
pytestmark = pytest.mark.linux_only


def make_home(tmp_path):
    paths = pm.Paths(tmp_path)
    for name in ("GE-Proton10-14", "GE-Proton11-11-x86_64"):
        (paths.compat / name).mkdir(parents=True)
        (paths.compat / name / "proton").write_text("#!/bin/sh\n")
    (paths.steam / "steamapps/common/Proton - Experimental").mkdir(parents=True)
    (paths.steam / "steamapps/common/Proton - Experimental/proton").write_text("")
    paths.steam_config.parent.mkdir(parents=True)
    paths.steam_config.write_text('"InstallConfigStore" { "Software" { "Valve" { "Steam" { "CompatToolMapping" {\n'
                                  '"1" { "name" "GE-Proton11-11-x86_64" "config" "" }\n'
                                  '"2" { "name" "GE-Proton11-11-x86_64" }\n"3" { "name" "" }\n} } } } }')
    paths.heroic_games.mkdir(parents=True)
    (paths.heroic_games / "Sugar.json").write_text(json.dumps({"Sugar": {"wineVersion": {
        "bin": str(paths.compat / "GE-Proton11-11-x86_64/proton"), "name": "GE-Proton11-11-x86_64", "type": "proton"}}}))
    paths.launch_profiles.parent.mkdir(parents=True)
    paths.launch_profiles.write_text(json.dumps({"profiles": [{"profile_id": "local-nolvus", "display_name": "Nolvus",
        "argv_template": ["--proton", str(paths.compat / "GE-Proton10-14/proton")]}]}))
    return paths


def test_overview_counts_users_per_store(tmp_path):
    rows = {r["name"]: r for r in pm.overview(make_home(tmp_path))}
    assert rows["GE-Proton11-11-x86_64"]["steam"] == 2 and rows["GE-Proton11-11-x86_64"]["heroic"] == ["Sugar"]
    assert rows["GE-Proton10-14"]["waystone"] == ["Nolvus"]
    assert rows["Proton - Experimental"]["kind"] == "steam" and not rows["Proton - Experimental"]["used"]
    assert rows["GE-Proton11-11-x86_64"]["newestInstalled"] and not rows["GE-Proton10-14"]["newestInstalled"]


def test_steam_internal_name_and_heroic_default_count_as_users(tmp_path):
    paths = make_home(tmp_path)
    tool = paths.compat / "proton-custom-folder"
    tool.mkdir()
    (tool / "proton").write_text("")
    (tool / "compatibilitytool.vdf").write_text('"compatibilitytools"\n{\n  "compat_tools"\n  {\n'
                                                '    "Custom-Internal" // Internal name of this tool\n    { }\n  }\n}\n')
    paths.steam_config.write_text(paths.steam_config.read_text().replace('"3" { "name" "" }',
                                                                         '"3" { "name" "Custom-Internal" }'))
    paths.heroic_config.write_text(json.dumps({"defaultSettings": {"wineVersion": {
        "bin": str(paths.compat / "GE-Proton10-14/proton"), "type": "proton"}}}))
    rows = {r["name"]: r for r in pm.overview(paths)}
    assert rows["proton-custom-folder"]["steam"] == 1 and rows["proton-custom-folder"]["used"]
    assert "steamName" not in rows["proton-custom-folder"]
    assert rows["GE-Proton10-14"]["heroic"] == ["Heroic default for new games"]
    with pytest.raises(pm.InstallError):
        pm.remove_runtime(paths, "proton-custom-folder")


def test_latest_release_picks_x86_64_tarball_with_checksum():
    releases = [{"tag_name": "GE-Proton11-14", "draft": False, "prerelease": False, "assets": [
        {"name": "GE-Proton11-14-aarch64.tar.gz", "browser_download_url": "https://github.com/a", "size": 1},
        {"name": "GE-Proton11-14-x86_64.tar.gz", "browser_download_url": "https://github.com/x", "size": 600},
        {"name": "GE-Proton11-14-x86_64.sha512sum", "browser_download_url": "https://github.com/s", "size": 1}]}]
    latest = pm.latest_ge(lambda url: releases)
    assert latest == {"tag": "GE-Proton11-14", "tarball": "https://github.com/x", "size": 600,
                      "sha512_url": "https://github.com/s"}


def _tarball(top="GE-Proton11-14-x86_64", extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in [(f"{top}/proton", b"#!/bin/sh\n"), (f"{top}/version", b"11-14")] + (extra or []):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _install(paths, raw, checksum=None, tag="GE-Proton11-14"):
    release = {"tag": tag, "tarball": "https://github.com/x", "size": len(raw), "sha512_url": "https://github.com/s"}
    digest = checksum or hashlib.sha512(raw).hexdigest()
    def download(url, dest, progress, cancelled, total):
        Path(dest).write_bytes(raw)
        progress(len(raw), len(raw))
    return pm.install_ge(release, paths, download=download, fetch_text=lambda u: f"{digest}  file.tar.gz\n",
                         free_bytes=lambda p: 10**12)


def test_install_verifies_checksum_and_moves_into_place(tmp_path):
    paths = make_home(tmp_path)
    assert _install(paths, _tarball()) == "GE-Proton11-14-x86_64"
    assert (paths.compat / "GE-Proton11-14-x86_64/proton").exists()
    assert not [p for p in paths.compat.iterdir() if p.name.startswith(".waystone-proton-")]   # temp cleaned up
    assert _install(paths, _tarball()) == "GE-Proton11-14-x86_64"                            # already there


def test_bad_checksum_or_unsafe_archive_installs_nothing(tmp_path):
    paths = make_home(tmp_path)
    with pytest.raises(pm.InstallError, match="Checksum mismatch"):
        _install(paths, _tarball(), checksum="0" * 128)
    with pytest.raises(pm.InstallError, match="Unexpected archive layout|Unsafe"):
        _install(paths, _tarball(extra=[("../evil", b"x")]))
    assert not (paths.compat / "GE-Proton11-14-x86_64").exists()


def test_not_enough_space_is_refused(tmp_path):
    paths = make_home(tmp_path)
    release = {"tag": "GE-Proton11-14", "tarball": "https://github.com/x", "size": 600 * 1024**2, "sha512_url": "https://github.com/s"}
    with pytest.raises(pm.InstallError, match="Not enough free space"):
        pm.install_ge(release, paths, free_bytes=lambda p: 10)


def test_switch_moves_heroic_and_waystone_with_backups_and_reports_steam(tmp_path):
    paths = make_home(tmp_path)
    _install(paths, _tarball())
    result = pm.switch_games(paths, "GE-Proton11-11-x86_64", "GE-Proton11-14-x86_64")
    assert result == {"heroic": ["Sugar"], "waystone": [], "steam": 2}
    sugar = json.loads((paths.heroic_games / "Sugar.json").read_text())["Sugar"]["wineVersion"]
    assert sugar["name"] == "GE-Proton11-14-x86_64" and sugar["bin"].endswith("GE-Proton11-14-x86_64/proton")
    assert list(paths.heroic_games.glob("Sugar.json.bak-*-proton-switch"))
    paths.heroic_config.write_text(json.dumps({"defaultSettings": {"wineVersion": {
        "bin": str(paths.compat / "GE-Proton10-14/proton"), "type": "proton"}}, "other": 1}))
    result = pm.switch_games(paths, "GE-Proton10-14", "GE-Proton11-14-x86_64")
    assert result["waystone"] == ["Nolvus"] and "GE-Proton11-14-x86_64" in paths.launch_profiles.read_text()
    assert result["heroic"] == ["Heroic default for new games"]
    heroic = json.loads(paths.heroic_config.read_text())
    assert heroic["other"] == 1 and heroic["defaultSettings"]["wineVersion"]["name"] == "GE-Proton11-14-x86_64"
    assert list(paths.heroic_config.parent.glob("config.json.bak-*-proton-switch"))
    assert not {r["name"]: r for r in pm.overview(paths)}["GE-Proton10-14"]["used"]


def test_remove_only_unused_custom_versions(tmp_path):
    paths = make_home(tmp_path)
    with pytest.raises(pm.InstallError, match="still use"):
        pm.remove_runtime(paths, "GE-Proton10-14")
    with pytest.raises(pm.InstallError, match="Only Proton versions"):
        pm.remove_runtime(paths, "Proton - Experimental")
    _install(paths, _tarball())
    pm.remove_runtime(paths, "GE-Proton11-14-x86_64")
    assert not (paths.compat / "GE-Proton11-14-x86_64").exists()


# ---------------- per-game version picker (W19b) ----------------

REAL_STYLE_VDF = (
    '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n'
    '\t\t\t\t"CompatToolMapping"\n\t\t\t\t{\n'
    '\t\t\t\t\t"0"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"proton_experimental"\n\t\t\t\t\t\t"config"\t\t""\n'
    '\t\t\t\t\t\t"priority"\t\t"75"\n\t\t\t\t\t}\n'
    '\t\t\t\t\t"3590"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"GE-Proton11-11-x86_64"\n\t\t\t\t\t\t"config"\t\t""\n'
    '\t\t\t\t\t\t"priority"\t\t"250"\n\t\t\t\t\t}\n'
    '\t\t\t\t}\n\t\t\t\t"Other"\t\t"{not a block}"\n\t\t\t}\n\t\t}\n\t}\n}\n')


def picker_home(tmp_path):
    paths = make_home(tmp_path)
    paths.steam_config.write_text(REAL_STYLE_VDF)
    return paths


def test_steam_entry_is_added_replaced_and_cleared_without_touching_the_rest(tmp_path):
    text = REAL_STYLE_VDF
    added = pm.set_steam_mapping_text(text, "489830", "GE-Proton10-14")
    assert '"489830"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"GE-Proton10-14"' in added
    assert added.replace('\t\t\t\t\t"489830"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"GE-Proton10-14"\n'
                         '\t\t\t\t\t\t"config"\t\t""\n\t\t\t\t\t\t"priority"\t\t"250"\n\t\t\t\t\t}\n', "") == text
    replaced = pm.set_steam_mapping_text(added, "3590", "GE-Proton10-14")
    assert replaced.count('"GE-Proton11-11-x86_64"') == 0 and replaced.count('"GE-Proton10-14"') == 2
    assert pm.set_steam_mapping_text(added, "489830", None) == text
    cleared = pm.set_steam_mapping_text(text, "3590", None)
    assert '"3590"' not in cleared and '"0"' in cleared and '"Other"\t\t"{not a block}"' in cleared
    assert pm.set_steam_mapping_text(text, "777", None) == text
    with pytest.raises(pm.InstallError):
        pm.set_steam_mapping_text("{}", "1", "x")


def test_steam_game_runtime_reads_internal_names_and_global_default(tmp_path):
    paths = picker_home(tmp_path)
    info = pm.game_runtime(paths, "steam", "3590")
    assert info["current"] == "GE-Proton11-11-x86_64" and info["default"] == "Proton - Experimental"
    assert pm.game_runtime(paths, "steam", "489830")["current"] == ""
    assert set(info["options"]) == {"GE-Proton10-14", "GE-Proton11-11-x86_64", "Proton - Experimental"}


def test_steam_change_waits_for_steam_to_close_and_uses_the_internal_name(tmp_path):
    paths = picker_home(tmp_path)
    before = paths.steam_config.read_text()
    with pytest.raises(pm.SteamRunning):
        pm.set_game_runtime(paths, "steam", "489830", "GE-Proton10-14", is_steam_running=lambda: True)
    assert paths.steam_config.read_text() == before
    assert pm.set_game_runtime(paths, "steam", "489830", "Proton - Experimental",
                               is_steam_running=lambda: False) == "Proton - Experimental"
    assert pm.steam_mapping(paths)["489830"] == "proton_experimental"
    assert list(paths.steam_config.parent.glob("config.vdf.bak-*"))
    assert pm.set_game_runtime(paths, "steam", "489830", None, is_steam_running=lambda: False) == "Proton - Experimental"
    assert "489830" not in pm.steam_mapping(paths)
    with pytest.raises(pm.InstallError):
        pm.set_game_runtime(paths, "steam", "489830", "GE-Proton9-1", is_steam_running=lambda: False)


def test_heroic_game_set_default_and_new_file(tmp_path):
    paths = picker_home(tmp_path)
    paths.heroic_config.write_text(json.dumps({"defaultSettings": {"wineVersion": {
        "bin": "/usr/bin/wine", "name": "wine-11.20 (Staging)", "type": "wine"}}}))
    (paths.heroic_games / "Sugar.json").write_text(json.dumps({"Sugar": {"wineVersion": {
        "bin": str(paths.compat / "GE-Proton11-11-x86_64/proton"), "type": "proton"}, "other": True}}))
    assert pm.game_runtime(paths, "heroic", "Sugar")["current"] == "GE-Proton11-11-x86_64"
    pm.set_game_runtime(paths, "heroic", "Sugar", "GE-Proton10-14")
    sugar = json.loads((paths.heroic_games / "Sugar.json").read_text())["Sugar"]
    assert sugar["other"] is True and sugar["wineVersion"]["bin"].endswith("GE-Proton10-14/proton")
    assert pm.set_game_runtime(paths, "heroic", "Sugar", None) == "bin"     # /usr/bin/wine -> folder 'bin'
    assert pm.game_runtime(paths, "heroic", "Sugar")["current"] == ""
    pm.set_game_runtime(paths, "heroic", "Catnip", "GE-Proton10-14")
    assert json.loads((paths.heroic_games / "Catnip.json").read_text())["Catnip"]["wineVersion"]["name"] == "GE-Proton10-14"
    with pytest.raises(pm.InstallError):
        pm.set_game_runtime(paths, "heroic", "../evil", "GE-Proton10-14")


def test_waystone_profile_changes_only_that_entry(tmp_path):
    paths = picker_home(tmp_path)
    data = json.loads(paths.launch_profiles.read_text())
    data["profiles"].append({"profile_id": "other", "argv_template": [str(paths.compat / "GE-Proton10-14/proton")]})
    data["game_associations"] = {"local:nolvus": "local-nolvus", "local:other": "other"}
    paths.launch_profiles.write_text(json.dumps(data))
    assert pm.profile_for_target(paths, "local:nolvus") == "local-nolvus"
    assert pm.game_runtime(paths, "waystone", "local-nolvus")["current"] == "GE-Proton10-14"
    pm.set_game_runtime(paths, "waystone", "local-nolvus", "GE-Proton11-11-x86_64")
    saved = {p["profile_id"]: p for p in json.loads(paths.launch_profiles.read_text())["profiles"]}
    assert saved["local-nolvus"]["argv_template"][1].endswith("GE-Proton11-11-x86_64/proton")
    assert saved["other"]["argv_template"][0].endswith("GE-Proton10-14/proton")
    with pytest.raises(pm.InstallError):
        pm.set_game_runtime(paths, "waystone", "local-nolvus", None)


def test_library_entries_map_to_their_launcher(tmp_path):
    paths = picker_home(tmp_path)
    legendary = paths.home / ".config/heroic/legendaryConfig/legendary/installed.json"
    legendary.parent.mkdir(parents=True)
    legendary.write_text(json.dumps({"Sugar": {}}))
    data = json.loads(paths.launch_profiles.read_text())
    data["game_associations"] = {"local:nolvus": "local-nolvus"}
    paths.launch_profiles.write_text(json.dumps(data))
    assert pm.ref_for_launch(paths, "Steam", "322330") == ("steam", "322330")
    assert pm.ref_for_launch(paths, "Epic", "Sugar") == ("heroic", "Sugar")
    assert pm.ref_for_launch(paths, "Epic", "NotInHeroic") is None
    assert pm.ref_for_launch(paths, "Local", "", "local:nolvus") == ("waystone", "local-nolvus")
    assert pm.ref_for_launch(paths, "Dolphin", "", "demo-racing") is None


def test_release_list_and_exact_version_match(tmp_path):
    def rel(tag):
        return {"tag_name": tag, "assets": [
            {"name": f"{tag}.tar.gz", "browser_download_url": f"https://github.com/{tag}", "size": 1},
            {"name": f"{tag}.sha512sum", "browser_download_url": f"https://github.com/{tag}.sha", "size": 1}]}
    releases = pm.ge_releases(lambda url: [rel("GE-Proton11-14"), {"tag_name": "x", "assets": []},
                                           rel("GE-Proton11-1"), dict(rel("GE-Proton11-0"), prerelease=True)])
    assert [r["tag"] for r in releases] == ["GE-Proton11-14", "GE-Proton11-1"]
    paths = make_home(tmp_path)
    _install(paths, _tarball("GE-Proton11-14"))
    raw = _tarball("GE-Proton11-1")
    assert _install(paths, raw, tag="GE-Proton11-1") == "GE-Proton11-1"          # 11-14 being installed does not count as 11-1


def test_running_checks_use_wine_processes_not_exe_names():
    procs = lambda: [("opencode.exe", "/x/opencode.exe serve", "/x/opencode.exe"), ("steam", "steam", "/s/steam")]
    assert pm.steam_running(procs) and not pm.game_running(procs)
    assert pm.game_running(lambda: [("SkyrimSE.exe", "C:\\Games\\SkyrimSE.exe", "/p/files/bin/wine64-preloader")])
    assert pm.game_running(lambda: [("reaper", "reaper SteamLaunch AppId=1 -- x", "/s/reaper")])


def test_close_steam_refuses_during_a_game_and_waits_for_exit():
    with pytest.raises(pm.InstallError):
        pm.close_steam(run=lambda argv: None, is_running=lambda: True, game_is_running=lambda: True)
    state = {"left": 3, "argv": None}
    def running():
        state["left"] -= 1
        return state["left"] > 0
    pm.close_steam(run=lambda argv: state.update(argv=argv), is_running=running, game_is_running=lambda: False,
                   sleep=lambda s: None)
    assert state["argv"][-1] == "-shutdown"
    with pytest.raises(pm.InstallError):
        pm.close_steam(run=lambda argv: None, is_running=lambda: True, game_is_running=lambda: False,
                       timeout=1, sleep=lambda s: None)
