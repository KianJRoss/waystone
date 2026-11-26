import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "craft-projects/playnite-desktop/app"))

from native_services import local_games as lg  # noqa: E402
from native_services.launch_profiles import LaunchProfileStore  # noqa: E402


def base_catalog():
    meta = {"genres": ["RPG"], "tags": ["Open World"], "description": "Dragons.", "playtime_seconds": 999,
            "last_activity": "2026-01-01", "available_fields": ["genres", "description"], "favorite": True}
    variant = {"source": "Steam", "source_id": "e659", "provider_game_id": "489830", "platform": "PC (Windows)",
               "status": "unknown", "metadata": meta, "artwork": {"cover": {"status": "local_present",
               "relative_reference": "e659/cover.jpg"}}}
    return {"schema_version": 2, "games": [{"id": "steam:489830", "title": "Skyrim SE", "variants": [variant]}]}


def test_upsert_load_and_merge_reuses_base_art_and_metadata_not_activity(tmp_path):
    path = tmp_path / "local.json"
    lg.upsert(path, {"id": "local:nolvus-ascension", "title": "Skyrim SE: Nolvus Ascension",
                     "based_on": "steam:489830", "tags": ["Modlist"]})
    lg.upsert(path, {"id": "local:nolvus-ascension", "title": "Skyrim SE: Nolvus Ascension",
                     "based_on": "steam:489830", "tags": ["Modlist", "Nolvus"]})  # replace, not duplicate
    entries = lg.load(path)
    assert len(entries) == 1
    data = base_catalog()
    assert lg.merge_into_catalog(data, entries) == 1
    work = data["games"][-1]
    v = work["variants"][0]
    assert work["id"] == "local:nolvus-ascension" and v["status"] == "installed" and v["source"] == "Local"
    assert v["provider_game_id"] == "local:nolvus-ascension"
    assert v["artwork"]["cover"]["relative_reference"] == "e659/cover.jpg"
    assert v["metadata"]["genres"] == ["RPG"] and v["metadata"]["description"] == "Dragons."
    assert v["metadata"]["tags"] == ["Modlist", "Nolvus", "Open World"]
    assert v["metadata"]["playtime_seconds"] is None and v["metadata"]["favorite"] is None
    data["games"][0]["variants"][0]["metadata"]["genres"].append("changed")
    assert v["metadata"]["genres"] == ["RPG"]  # deep copy, base untouched by later edits
    assert lg.merge_into_catalog(data, entries) == 0  # idempotent


def test_merged_catalog_passes_library_validation_and_shows_play(tmp_path):
    from library_model import validate_catalog
    data = base_catalog()
    lg.merge_into_catalog(data, [{"id": "local:mygame", "title": "My Game"}])  # no base game
    validate_catalog(data)
    assert data["games"][-1]["variants"][0]["metadata"]["genres"] == []


@pytest.mark.parametrize("bad", [{"id": "nolvus", "title": "x"}, {"id": "local:Upper", "title": "x"},
                                 {"id": "local:ok", "title": " "}, {"id": "local:ok", "title": "x", "tags": "a"}])
def test_invalid_entries_are_rejected(tmp_path, bad):
    with pytest.raises(lg.LocalGamesError):
        lg.upsert(tmp_path / "l.json", bad)


def test_add_local_game_tool_writes_entry_and_associated_profile(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import add_local_game
    exe = tmp_path / "proton"; exe.write_text("#!/bin/sh\n"); exe.chmod(0o755)
    local, profiles = tmp_path / "local.json", tmp_path / "profiles"
    args = ["--id", "local:nolvus-ascension", "--title", "Skyrim SE: Nolvus Ascension", "--based-on", "steam:489830",
            "--exe", str(exe), "--arg", "run", "--arg", "/games/MO2/ModOrganizer.exe", "--arg", "moshortcut://:SKSE",
            "--cwd", "/games/MO2", "--env", "SteamAppId=489830", "--local-games", str(local),
            "--profiles-dir", str(profiles)]
    assert add_local_game.main(args) == 0
    assert add_local_game.main(args) == 0  # re-run updates instead of failing
    assert lg.load(local)[0]["based_on"] == "steam:489830"
    store = LaunchProfileStore(profiles)
    default, _ = store.resolve_profiles(target_id="local:nolvus-ascension", platform_or_provider="local",
                                        target_type="native_game")
    assert default.profile_id == "local-nolvus-ascension"
    assert default.argv_template == ("run", "/games/MO2/ModOrganizer.exe", "moshortcut://:SKSE")
    assert default.env == {"SteamAppId": "489830"} and default.cwd == "/games/MO2"
