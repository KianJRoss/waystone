import json

from native_services import anticheat_games as ac

ENTRIES = [{"name": "Rocket League®", "anticheats": ["Easy Anti-Cheat"], "storeIds": {"steam": "252950"}},
           {"name": "Celeste", "anticheats": []}]


def test_titles_match_without_trademarks_case_or_punctuation():
    index = ac.AntiCheatIndex(ENTRIES)
    assert index.anticheats("rocket league") == ["Easy Anti-Cheat"]
    assert index.anticheats("Rocket League™") == ["Easy Anti-Cheat"]
    assert index.anticheats(steam_app_id="252950") == ["Easy Anti-Cheat"]
    assert index.anticheats("Celeste") == [] and index.anticheats("Unknown") == []


def test_waystone_copy_wins_then_heroic_copy(tmp_path):
    assert len(ac.load_index(tmp_path)) == 0
    heroic = ac.heroic_cache(tmp_path); heroic.parent.mkdir(parents=True); heroic.write_text(json.dumps(ENTRIES))
    assert ac.load_index(tmp_path).anticheats("Rocket League") == ["Easy Anti-Cheat"]
    own = ac.waystone_cache(tmp_path); own.parent.mkdir(parents=True)
    own.write_text(json.dumps([{"name": "Other", "anticheats": ["BattlEye"]}]))
    index = ac.load_index(tmp_path)
    assert index.anticheats("Other") == ["BattlEye"] and index.anticheats("Rocket League") == []


def test_refresh_downloads_weekly_and_rejects_bad_data(tmp_path):
    calls = []
    def get(url):
        calls.append(url)
        return json.dumps(ENTRIES).encode()
    assert ac.refresh_cache(tmp_path, get=get, now=lambda: 10 ** 10) is True
    assert calls == [ac.SOURCE_URL] and ac.waystone_cache(tmp_path).exists()
    assert ac.refresh_cache(tmp_path, get=get, now=lambda: ac.waystone_cache(tmp_path).stat().st_mtime + 60) is False
    other = tmp_path / "other"
    assert ac.refresh_cache(other, get=lambda url: b"<html>", now=lambda: 10 ** 10) is False
    assert ac.refresh_cache(other, get=lambda url: b"[]", now=lambda: 10 ** 10) is False
    def boom(url): raise OSError("offline")
    assert ac.refresh_cache(other, get=boom, now=lambda: 10 ** 10) is False and not ac.waystone_cache(other).exists()
