"""Offline tests for the injected-transport Waystone mod catalog clients."""
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from native_services import mod_catalog
from native_services.mod_catalog import (
    NEXUS_GRAPHQL,
    USER_AGENT,
    CatalogError,
    find_gamebanana_games,
    find_nexus_games,
    http_json,
    platforms_for,
    search_gamebanana,
    search_modrinth,
    search_nexus,
)

ITEM_KEYS = {
    "platform", "mod_id", "title", "summary", "author", "updated_at",
    "thumbnail_url", "downloads", "likes", "url", "game",
}

MODRINTH_PAYLOAD = {
    "total_hits": 42,
    "hits": [
        {
            "project_id": "abc123",
            "slug": "sodium",
            "title": "Sodium",
            "description": "Fast rendering",
            "author": "jellysquid",
            "date_modified": "2024-02-03T04:05:06.000Z",
            "icon_url": "https://cdn.example/sodium.png",
            "downloads": 1000,
            "follows": 50,
        },
        {
            "project_id": "def456",
            "slug": "no-icon",
            "title": "No Icon",
        },
    ],
}

GAMEBANANA_PAYLOAD = {
    "_aMetadata": {"_nRecordCount": 7},
    "_aRecords": [
        {
            "_idRow": 123,
            "_sName": "My Mod",
            "_sProfileUrl": "https://gamebanana.com/mods/123",
            "_aGame": {"_sName": "Super Mario Odyssey"},
            "_aSubmitter": {"_sName": "alice"},
            "_tsDateModified": 1700000000,
            "_nViewCount": 900,
            "_nLikeCount": 30,
            "_aPreviewMedia": {
                "_aImages": [{
                    "_sBaseUrl": "https://images.example",
                    "_sFile220": "thumb.jpg",
                }],
            },
        },
        {
            "_idRow": 124,
            "_sName": "No Date",
            "_sProfileUrl": "https://gamebanana.com/mods/124",
            "_aGame": {"_sName": "Mario Kart 8"},
            "_aSubmitter": {"_sName": "bob"},
            "_tsDateModified": None,
            "_nViewCount": 5,
            "_nLikeCount": 0,
            "_aPreviewMedia": {"_aImages": []},
        },
    ],
}

NEXUS_PAYLOAD = {
    "data": {
        "mods": {
            "totalCount": 3,
            "nodes": [
                {
                    "modId": 1234,
                    "name": "Cool Mod",
                    "summary": "Does things",
                    "author": "carol",
                    "updatedAt": "2024-05-06T07:08:09Z",
                    "downloads": 5000,
                    "endorsements": 120,
                    "thumbnailUrl": "https://nexus.example/thumb.png",
                    "game": {
                        "domainName": "skyrimspecialedition",
                        "name": "Skyrim Special Edition",
                    },
                },
                {
                    "modId": 99,
                    "name": "Bare Mod",
                    "game": {"domainName": "fallout4", "name": "Fallout 4"},
                },
            ],
        },
    },
}

NEXUS_MODS_QUERY = (
    "query($filter: ModsFilter, $sort: [ModsSort!], $count: Int, $offset: Int)"
    "{ mods(filter: $filter, sort: $sort, count: $count, offset: $offset){ totalCount "
    "nodes { modId name summary author updatedAt downloads endorsements "
    "thumbnailUrl game { domainName name } } } }"
)

NEXUS_GAMES_QUERY = (
    "query($f: GamesSearchFilter){ games(filter: $f, count: 10){ "
    "nodes { id domainName name } } }"
)


class FakeFetch:
    """Records every (url, body) call and replays canned payloads in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, *, body=None, timeout=15.0):
        self.calls.append((url, body))
        index = len(self.calls) - 1
        if index < len(self.responses):
            response = self.responses[index]
            if isinstance(response, Exception):
                raise response
            return response
        return self.responses[-1] if self.responses else {}


class FakeResponse:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.closed = False

    def read(self):
        return self.raw

    def close(self):
        self.closed = True


def test_http_json_get_sends_headers_and_parses_json(monkeypatch):
    seen = {}
    response = FakeResponse(b'{"ok": true}')

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return response

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert http_json("https://example.test/api", timeout=3.5) == {"ok": True}
    request = seen["request"]
    assert request.full_url == "https://example.test/api"
    assert request.get_method() == "GET"
    assert request.data is None
    assert request.get_header("User-agent") == USER_AGENT
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("Content-type") is None
    assert seen["timeout"] == 3.5
    assert response.closed is True


def test_http_json_post_sends_json_body(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        return FakeResponse(b'{"ok": 1}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = http_json("https://example.test/api", body={"query": "x"})
    assert result == {"ok": 1}
    request = seen["request"]
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"query": "x"}
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("User-agent") == USER_AGENT
    assert request.get_header("Accept") == "application/json"


def test_http_json_wraps_urlerror_in_catalog_error(monkeypatch):
    def fail(request, timeout):
        raise urllib.error.URLError("no route to catalog")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(CatalogError) as exc:
        http_json("https://example.test/api")
    assert "no route to catalog" in str(exc.value)


def test_http_json_wraps_invalid_json_in_catalog_error(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda request, timeout: FakeResponse(b"not-json"),
    )
    with pytest.raises(CatalogError):
        http_json("https://example.test/api")


def test_modrinth_search_url_encodes_query_limit_offset_and_facets():
    fetch = FakeFetch(MODRINTH_PAYLOAD)
    search_modrinth("skyrim ruins", project_type="modpack", limit=7, offset=14,
                    fetch=fetch)
    url, body = fetch.calls[0]
    assert body is None
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.modrinth.com"
    assert parsed.path == "/v2/search"
    params = parse_qs(parsed.query)
    assert params["query"] == ["skyrim ruins"]
    assert params["limit"] == ["7"]
    assert params["offset"] == ["14"]
    assert json.loads(params["facets"][0]) == [["project_type:modpack"]]
    assert "%5B%5B%22project_type%3Amodpack%22%5D%5D" in url


def test_modrinth_item_mapping_includes_url_and_defaults_missing_icon():
    fetch = FakeFetch(MODRINTH_PAYLOAD)
    result = search_modrinth("sodium", fetch=fetch)
    assert result["total"] == 42
    assert len(result["items"]) == 2
    first, second = result["items"]
    assert set(first) == ITEM_KEYS
    assert set(second) == ITEM_KEYS
    assert first == {
        "platform": "modrinth",
        "mod_id": "abc123",
        "title": "Sodium",
        "summary": "Fast rendering",
        "author": "jellysquid",
        "updated_at": "2024-02-03T04:05:06.000Z",
        "thumbnail_url": "https://cdn.example/sodium.png",
        "downloads": 1000,
        "likes": 50,
        "url": "https://modrinth.com/mod/sodium",
        "game": "Minecraft",
    }
    assert second["mod_id"] == "def456"
    assert second["thumbnail_url"] == ""
    assert second["summary"] == ""
    assert second["author"] == ""
    assert second["updated_at"] == ""
    assert second["downloads"] == 0
    assert second["likes"] == 0
    assert second["url"] == "https://modrinth.com/mod/no-icon"


def test_gamebanana_search_url_with_query_and_game_id():
    fetch = FakeFetch(GAMEBANANA_PAYLOAD)
    search_gamebanana("mario", game_id=42, page=3, fetch=fetch)
    url, body = fetch.calls[0]
    assert body is None
    parsed = urlparse(url)
    assert parsed.path == "/apiv11/Util/Search/Results"
    assert parse_qs(parsed.query) == {
        "_sModelName": ["Mod"],
        "_sOrder": ["best_match"],
        "_sSearchString": ["mario"],
        "_nPage": ["3"],
        "_idGameRow": ["42"],
    }


def test_gamebanana_search_url_without_game_id():
    fetch = FakeFetch(GAMEBANANA_PAYLOAD)
    search_gamebanana("mario", fetch=fetch)
    url, _ = fetch.calls[0]
    params = parse_qs(urlparse(url).query)
    assert params["_nPage"] == ["1"]
    assert "_idGameRow" not in params


def test_gamebanana_subfeed_url_when_query_empty():
    fetch = FakeFetch(GAMEBANANA_PAYLOAD)
    search_gamebanana(game_id=42, page=2, fetch=fetch)
    url, body = fetch.calls[0]
    assert body is None
    parsed = urlparse(url)
    assert parsed.path == "/apiv11/Game/42/Subfeed"
    assert parse_qs(parsed.query) == {"_nPage": ["2"], "_sSort": ["default"]}


def test_gamebanana_both_empty_skips_fetch():
    fetch = FakeFetch()
    assert search_gamebanana(fetch=fetch) == {"total": 0, "items": []}
    assert fetch.calls == []


def test_gamebanana_thumbnail_and_iso_dates():
    fetch = FakeFetch(GAMEBANANA_PAYLOAD)
    result = search_gamebanana("mario", fetch=fetch)
    assert result["total"] == 7
    first, second = result["items"]
    assert set(first) == ITEM_KEYS
    assert first == {
        "platform": "gamebanana",
        "mod_id": "123",
        "title": "My Mod",
        "summary": "",
        "author": "alice",
        "updated_at": "2023-11-14T22:13:20+00:00",
        "thumbnail_url": "https://images.example/thumb.jpg",
        "downloads": 900,
        "likes": 30,
        "url": "https://gamebanana.com/mods/123",
        "game": "Super Mario Odyssey",
    }
    assert datetime.fromisoformat(first["updated_at"]) == datetime(
        2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc,
    )
    assert second["updated_at"] == ""
    assert second["thumbnail_url"] == ""
    assert second["mod_id"] == "124"


def test_gamebanana_skips_non_mod_records():
    payload = {
        "_aMetadata": {"_nRecordCount": 2},
        "_aRecords": [
            {"_idRow": 1, "_sName": "A Mod", "_sModelName": "Mod"},
            {"_idRow": 2, "_sName": "A Wip", "_sModelName": "Wip"},
            {"_idRow": 3, "_sName": "No Model Field"},
        ],
    }
    fetch = FakeFetch(payload)
    result = search_gamebanana("mario", fetch=fetch)
    assert [item["title"] for item in result["items"]] == ["A Mod", "No Model Field"]


def test_find_gamebanana_games_orders_results_and_builds_url():
    payload = {"_aRecords": [
        {"_idRow": "42", "_sName": "Super Mario Odyssey"},
        {"_idRow": 77, "_sName": "Mario Kart 8"},
    ]}
    fetch = FakeFetch(payload)
    assert find_gamebanana_games("mario", fetch=fetch) == [
        {"id": 42, "name": "Super Mario Odyssey"},
        {"id": 77, "name": "Mario Kart 8"},
    ]
    url, body = fetch.calls[0]
    assert body is None
    parsed = urlparse(url)
    assert parsed.path == "/apiv11/Util/Search/Results"
    assert parse_qs(parsed.query) == {
        "_sModelName": ["Game"],
        "_sOrder": ["best_match"],
        "_sSearchString": ["mario"],
        "_nPage": ["1"],
    }


def test_search_nexus_body_filter_and_item_mapping():
    fetch = FakeFetch(NEXUS_PAYLOAD)
    result = search_nexus("cool mod", game_domain="skyrimspecialedition",
                          count=5, offset=10, fetch=fetch)
    assert len(fetch.calls) == 1
    url, body = fetch.calls[0]
    assert url == NEXUS_GRAPHQL
    assert body == {
        "query": NEXUS_MODS_QUERY,
        "variables": {
            "filter": {
                "name": {"value": "cool mod", "op": "WILDCARD"},
                "gameDomainName": {
                    "value": "skyrimspecialedition",
                    "op": "EQUALS",
                },
            },
            "sort": [{"endorsements": {"direction": "DESC"}}],
            "count": 5,
            "offset": 10,
        },
    }
    assert body["query"] == (
        "query($filter: ModsFilter, $sort: [ModsSort!], $count: Int, $offset: Int)"
        "{ mods(filter: $filter, sort: $sort, count: $count, offset: $offset){ totalCount "
        "nodes { modId name summary author updatedAt downloads endorsements "
        "thumbnailUrl game { domainName name } } } }"
    )
    assert result["total"] == 3
    first, second = result["items"]
    assert set(first) == ITEM_KEYS
    assert first == {
        "platform": "nexus",
        "mod_id": "1234",
        "title": "Cool Mod",
        "summary": "Does things",
        "author": "carol",
        "updated_at": "2024-05-06T07:08:09Z",
        "thumbnail_url": "https://nexus.example/thumb.png",
        "downloads": 5000,
        "likes": 120,
        "url": "https://www.nexusmods.com/skyrimspecialedition/mods/1234",
        "game": "Skyrim Special Edition",
    }
    assert set(second) == ITEM_KEYS
    assert second["mod_id"] == "99"
    assert second["thumbnail_url"] == ""
    assert second["summary"] == ""
    assert second["author"] == ""
    assert second["updated_at"] == ""
    assert second["downloads"] == 0
    assert second["likes"] == 0
    assert second["url"] == "https://www.nexusmods.com/fallout4/mods/99"
    assert second["game"] == "Fallout 4"


def test_search_nexus_without_filters_uses_empty_filter():
    fetch = FakeFetch({"data": {"mods": {"totalCount": 0, "nodes": []}}})
    result = search_nexus("", fetch=fetch)
    assert result == {"total": 0, "items": []}
    assert fetch.calls[0][1]["variables"] == {
        "filter": {}, "sort": [{"endorsements": {"direction": "DESC"}}], "count": 20, "offset": 0,
    }


def test_search_nexus_errors_raise_catalog_error():
    fetch = FakeFetch({"errors": [{"message": "Invalid API key"}]})
    with pytest.raises(CatalogError, match="Invalid API key"):
        search_nexus("anything", fetch=fetch)


def test_find_nexus_games_exact_first_then_dedupe_by_domain():
    exact = {"data": {"games": {"nodes": [
        {"id": 1, "domainName": "skyrim", "name": "Skyrim"},
        {"id": 2, "domainName": "fallout4", "name": "Fallout 4"},
    ]}}}
    partial = {"data": {"games": {"nodes": [
        {"id": 3, "domainName": "skyrim", "name": "Skyrim Again"},
        {"id": 4, "domainName": "skyrimspecialedition",
         "name": "Skyrim Special Edition"},
    ]}}}
    fetch = FakeFetch(exact, partial)
    assert find_nexus_games("skyrim", fetch=fetch) == [
        {"id": 1, "domain": "skyrim", "name": "Skyrim"},
        {"id": 2, "domain": "fallout4", "name": "Fallout 4"},
        {"id": 4, "domain": "skyrimspecialedition",
         "name": "Skyrim Special Edition"},
    ]
    assert len(fetch.calls) == 2
    first, second = fetch.calls
    for url, body in (first, second):
        assert url == NEXUS_GRAPHQL
        assert body["query"] == NEXUS_GAMES_QUERY
    assert first[1]["variables"] == {
        "f": {"name": {"value": "skyrim", "op": "EQUALS"}},
    }
    assert second[1]["variables"] == {
        "f": {"name": {"value": "skyrim", "op": "MATCHES"}},
    }


def test_platforms_for_minecraft_uses_modrinth():
    assert platforms_for({"title": "Minecraft Java", "platforms": ["PC"]}) == ["modrinth"]
    assert platforms_for({"title": "MINECRAFT Dungeons"}) == ["modrinth"]


@pytest.mark.parametrize("platform", [
    "Nintendo Switch", "PlayStation 5", "Sony PSP", "Sega Genesis",
    "Xbox 360", "Wii U", "GameCube", "Nintendo 3DS",
])
def test_platforms_for_emulator_consoles_use_gamebanana(platform):
    assert platforms_for({"title": "Some Game", "platforms": [platform]}) == ["gamebanana"]


@pytest.mark.parametrize("source", ["Emulation"])
def test_platforms_for_emulation_sources_use_gamebanana(source):
    assert platforms_for({"title": "ROM Game", "source": source}) == ["gamebanana"]


def test_platforms_for_pc_games_default_to_nexus_and_gamebanana():
    assert platforms_for({"title": "Skyrim", "platforms": ["PC"]}) == [
        "nexus", "gamebanana",
    ]
    assert platforms_for({}) == ["nexus", "gamebanana"]


def test_thumbnails_are_https_only():
    from native_services.mod_catalog import _https_image
    assert _https_image("https://staticdelivery.nexusmods.com/x.jpg") == "https://staticdelivery.nexusmods.com/x.jpg"
    for bad in ("http://images.gamebanana.com/x.jpg", "file:///home/x.png", "data:image/png;base64,AA", "//cdn/x.png",
                "https://u:p@cdn.example/x.png", None, 7):
        assert _https_image(bad) == ""
