import subprocess
import zipfile
from pathlib import Path

import pytest

from native_services import game_mods
from native_services.nexus_client import parse_nxm, mod_files, download_url, NexusError


def make_zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


@pytest.mark.parametrize("members,root,sub", [
    ({"SkyUI_SE.esp": "p", "interface/a.swf": "x"}, "", "Data"),
    ({"SkyUI/Data/interface/a.swf": "x", "SkyUI/readme.txt": "r"}, "SkyUI/", ""),
    ({"Wrapper/meshes/a.nif": "m", "Wrapper/Plugin.esp": "p"}, "Wrapper/", "Data"),
    ({"BepInEx/plugins/a.dll": "d"}, "", ""),
])
def test_layout_bethesda(members, root, sub):
    assert game_mods._layout(list(members)) == (root, sub)


@pytest.mark.parametrize("members,root", [
    ({"Mod v2/bin/x64/thing.dll": "d", "Mod v2/r6/scripts/a.reds": "s"}, "Mod v2/"),
    ({"r6/scripts/a.reds": "s"}, ""),
    ({"archive/pc/mod/x.archive": "a"}, ""),
    ({"readme.txt": "r", "stuff/x": "y"}, ""),
])
def test_layout_other_games_follow_existing_folders(tmp_path, members, root):
    for d in ("bin", "r6", "archive", "engine"):
        (tmp_path / d).mkdir()
    assert game_mods._layout(list(members), tmp_path) == (root, "")


def test_plan_apply_undo_into_game_data(tmp_path):
    game = tmp_path / "Skyrim"; (game / "Data").mkdir(parents=True)
    (game / "Data" / "Skyrim.esm").write_text("vanilla")
    z = make_zip(tmp_path / "mod.zip", {"Cool/Cool.esp": "plugin", "Cool/meshes/a.nif": "mesh"})
    plan = game_mods.plan_game_install(z, game, mod_name="Cool")
    assert plan["target_root"] == str(game / "Data") and not plan["conflicts"]
    res = game_mods.apply_install(plan, journal_dir=tmp_path / "j")
    assert (game / "Data/Cool.esp").read_text() == "plugin" and (game / "Data/meshes/a.nif").exists()
    game_mods.undo_install(res["journal"])
    assert not (game / "Data/Cool.esp").exists() and not (game / "Data/meshes").exists()
    assert (game / "Data/Skyrim.esm").read_text() == "vanilla"


def test_overwriting_a_different_existing_file_is_a_conflict(tmp_path):
    game = tmp_path / "G"; (game / "Data").mkdir(parents=True)
    (game / "Data" / "Game.esm").write_text("vanilla")
    (game / "Data" / "x.esp").write_text("theirs")
    plan = game_mods.plan_game_install(make_zip(tmp_path / "m.zip", {"x.esp": "mine"}), game)
    assert plan["conflicts"] == [str(game / "Data/x.esp")]


# bsdtar conversion is the Linux packaging/toolchain path.
@pytest.mark.linux_only
def test_7z_is_converted_with_bsdtar(tmp_path):
    src = tmp_path / "src"; (src / "textures").mkdir(parents=True); (src / "textures/a.dds").write_text("t")
    archive = tmp_path / "m.7z"
    if subprocess.run(["7z", "a", str(archive), "./textures"], cwd=src, capture_output=True).returncode != 0:
        pytest.skip("7z not available")
    z = game_mods.as_zip(archive)
    assert zipfile.ZipFile(z).namelist() == ["textures/a.dds"]


def test_parse_nxm():
    d = parse_nxm("nxm://SkyrimSpecialEdition/mods/12604/files/749043?key=abc&expires=1795460000&user_id=7")
    assert d == {"domain": "skyrimspecialedition", "mod_id": 12604, "file_id": 749043, "key": "abc",
                 "expires": "1795460000", "user_id": "7"}
    with pytest.raises(NexusError):
        parse_nxm("https://example.com")


def test_mod_files_and_download_url_with_fake_fetch():
    calls = []
    def fetch(url, headers):
        calls.append((url, headers["apikey"]))
        if url.endswith("files.json"):
            return {"files": [
                {"file_id": 1, "file_name": "old.zip", "category_name": "OLD_VERSION", "uploaded_timestamp": 10, "size_kb": 1},
                {"file_id": 2, "file_name": "main.zip", "category_name": "MAIN", "is_primary": True, "uploaded_timestamp": 5, "size_kb": 2},
                {"file_id": 3, "file_name": "gone.zip", "category_name": "ARCHIVED"}]}
        return [{"name": "CDN", "URI": "https://cdn.example/f.zip"}]
    files = mod_files("skyrimspecialedition", 12604, "K", fetch=fetch)
    assert [f["file_id"] for f in files] == ["2", "1"] and files[0]["size"] == 2048
    url = download_url("skyrimspecialedition", 12604, 2, "K", nxm_key="abc", expires="9", fetch=fetch)
    assert url == "https://cdn.example/f.zip"
    assert calls[-1][0].endswith("/files/2/download_link.json?key=abc&expires=9") and calls[-1][1] == "K"
