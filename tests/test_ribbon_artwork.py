import os

from native_services.ribbon_artwork import (ALLOWED_HOSTS, ALL_IMAGE_HOSTS, GOG_IMAGE_HOSTS,
                                            SGDB_IMAGE_HOSTS, RibbonCache, RibbonFetcher, _http_get,
                                            cache_root, candidate_urls, epic_metadata_roots, epic_urls, fetch_ribbon, gog_urls,
                                            steamgriddb_urls, validate_image)

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 200
JPG = b"\xff\xd8\xff" + b"y" * 300


def size_ok(_raw):
    return (1920, 620)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def test_steam_urls_only_for_numeric_ids_and_only_allowed_hosts():
    urls = candidate_urls("Steam", "239030")
    assert set(urls) == {"hero", "logo", "cover"}
    from urllib.parse import urlsplit
    assert all(urlsplit(u).hostname in ALLOWED_HOSTS for us in urls.values() for u in us)
    assert candidate_urls("steam", "../etc") == {}
    assert candidate_urls("epic", "../etc") == {}


def test_artwork_roots_use_xdg_and_documented_heroic_user_data(monkeypatch, tmp_path):
    import native_services.ribbon_artwork as ribbons
    home = tmp_path / 'home'
    monkeypatch.setattr(ribbons.Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg-data'))
    assert cache_root() == tmp_path / 'xdg-data/waystone/artwork-cache'
    assert epic_metadata_roots() == (
        tmp_path / 'xdg-data/waystone/providers-v1/epic/legendary',
        home / '.config/heroic/legendaryConfig/legendary')


def test_http_get_refuses_other_hosts_and_plain_http():
    assert _http_get("http://cdn.cloudflare.steamstatic.com/x.jpg") is None
    assert _http_get("https://example.com/x.jpg") is None
    assert all(host in ALL_IMAGE_HOSTS for host in GOG_IMAGE_HOSTS | SGDB_IMAGE_HOSTS)


def test_epic_urls_read_exact_metadata_and_select_roles(tmp_path):
    import json
    root = tmp_path / 'legendary'
    meta = root / 'metadata'
    meta.mkdir(parents=True)
    (meta / 'app-1.json').write_text(json.dumps({'metadata': {'keyImages': [
        {'type': 'DieselGameBox', 'url': 'https://cdn1.epicgames.com/hero.jpg'},
        {'type': 'DieselGameBoxTall', 'url': 'https://cdn1.epicgames.com/cover.jpg'},
        {'type': 'DieselGameBoxLogo', 'url': 'https://cdn1.epicgames.com/logo.png'},
        {'type': 'OfferImageWide', 'url': 'https://evil.example/fallback.jpg'},
    ]}}))
    assert epic_urls('app-1', [root]) == {
        'hero': ['https://cdn1.epicgames.com/hero.jpg'],
        'logo': ['https://cdn1.epicgames.com/logo.png'],
        'cover': ['https://cdn1.epicgames.com/cover.jpg'],
    }
    assert epic_urls('../app-1', [root]) == {}
    assert epic_urls('missing', [root]) == {}


def test_gog_public_product_images_are_https_host_checked():
    seen = []
    def get_json(url, **kwargs):
        seen.append((url, kwargs['hosts']))
        return {'images': {'background': '//images-3.gog.com/bg.jpg', 'logo2x': '//images-2.gog.com/logo.png', 'boxart': '//images.gog.com/cover.jpg'}}
    assert gog_urls('1207658691', get_json=get_json) == {
        'hero': ['https://images-3.gog.com/bg.jpg'], 'logo': ['https://images-2.gog.com/logo.png'],
        'cover': ['https://images.gog.com/cover.jpg']}
    assert seen[0][0] == 'https://api.gog.com/products/1207658691'
    assert gog_urls('../x', get_json=get_json) == {}
    assert gog_urls('3', get_json=lambda *_a, **_k: {'images': {'background': 'http://evil.example/x'}}) == {}


def test_steamgriddb_requires_key_exact_match_and_orders_score(monkeypatch):
    import native_services.ribbon_artwork as ribbons
    monkeypatch.setattr(ribbons, '_SGDB_DELAY', 0)
    calls = []
    def get_json(url, **kwargs):
        calls.append((url, kwargs))
        if '/games/steam/' in url:
            return {'success': True, 'data': {'id': 42}}
        if '/heroes/game/' in url:
            return {'success': True, 'data': [
                {'url': 'https://cdn2.steamgriddb.com/low.jpg', 'score': 1},
                {'url': 'https://cdn2.steamgriddb.com/high.jpg', 'score': 9}]}
        if '/logos/game/' in url:
            return {'success': True, 'data': [{'url': 'https://cdn2.steamgriddb.com/logo.png', 'score': 3}]}
        if '/grids/game/' in url:
            return {'success': True, 'data': [{'url': 'https://cdn2.steamgriddb.com/cover.png', 'score': 8}]}
        return {'success': True, 'data': [{'id': 42, 'name': 'Not the requested game'},
                                          {'id': 43, 'name': 'Game Name'}]}
    assert steamgriddb_urls('Game Name', api_key='fixture-api-key', steam_app_id='123', get_json=get_json) == {
        'hero': ['https://cdn2.steamgriddb.com/high.jpg', 'https://cdn2.steamgriddb.com/low.jpg'],
        'logo': ['https://cdn2.steamgriddb.com/logo.png'],
        'cover': ['https://cdn2.steamgriddb.com/cover.png']}
    assert any('games/steam/123' in call[0] for call in calls)
    assert all(call[1]['headers']['Authorization'] == 'Bearer fixture-api-key' for call in calls)
    exact = steamgriddb_urls('Game Name', api_key='fixture-api-key', get_json=get_json)
    assert exact['hero'][0].endswith('high.jpg')
    assert steamgriddb_urls('Game Name', api_key='', get_json=get_json) == {}


def test_fetch_gog_image_payload_is_validated():
    def get_json(_url, **_kwargs):
        return {'images': {'background': 'https://images.gog.com/bg.jpg', 'logo': 'https://evil.example/logo.png'}}
    found = fetch_ribbon('gog', '1207658691', get=lambda _url: JPG, decode=size_ok, get_json=get_json)
    assert found == {'hero': (JPG, 'jpg')}


def test_validation_needs_real_magic_and_sane_size():
    assert validate_image(PNG, size_ok) == "png"
    assert validate_image(JPG, size_ok) == "jpg"
    assert validate_image(b"<html>not found</html>", size_ok) is None
    assert validate_image(PNG, lambda _r: None) is None            # does not decode
    assert validate_image(PNG, lambda _r: (10, 10)) is None         # too small
    assert validate_image(PNG, lambda _r: (10000, 10000)) is None   # too many pixels


def test_fetch_tries_the_second_cdn_when_the_first_has_nothing():
    calls = []

    def get(url):
        calls.append(url)
        if "store_item_assets" in url:
            return None
        return JPG if url.endswith(".jpg") else PNG
    found = fetch_ribbon("steam", "239030", get=get, decode=size_ok)
    assert set(found) == {"hero", "logo", "cover"} and found["hero"][1] == "jpg" and found["logo"][1] == "png" and found["cover"][1] == "jpg"
    assert len(calls) == 6


def test_cache_stores_looks_up_and_remembers_misses(tmp_path):
    clock = Clock()
    cache = RibbonCache(tmp_path, clock=clock)
    assert cache.needs_fetch("steam", "1")
    paths = cache.store("steam", "1", {"hero": (JPG, "jpg"), "logo": (PNG, "png")})
    assert set(paths) == {"hero", "logo"} and os.path.isfile(paths["hero"])
    assert not cache.needs_fetch("steam", "1")
    cache.store("steam", "2", {})                      # nothing found
    assert not cache.needs_fetch("steam", "2")
    clock.now += 8 * 86400                              # a week later it may try again
    assert cache.needs_fetch("steam", "2")
    assert RibbonCache(tmp_path, clock=clock).lookup("steam", "1") == paths   # survives a restart


def test_cache_prunes_oldest_first_above_the_cap(tmp_path):
    clock = Clock()
    cache = RibbonCache(tmp_path, max_bytes=700, clock=clock)
    cache.store("steam", "old", {"hero": (JPG, "jpg")})
    clock.now += 10
    cache.store("steam", "mid", {"hero": (JPG, "jpg")})
    clock.now += 10
    cache.store("steam", "new", {"hero": (JPG, "jpg")})   # 3 x 303 bytes > 700: "old" goes
    assert cache.lookup("steam", "old") == {}
    assert cache.lookup("steam", "new")


def test_fetcher_queues_once_and_reports_ready(tmp_path):
    ready = []
    cache = RibbonCache(tmp_path)
    fetcher = RibbonFetcher(cache, on_ready=lambda p, i, paths: ready.append((p, i, set(paths))),
                            fetch=lambda p, i: {"hero": (JPG, "jpg")}, start=False)
    assert fetcher.request("Steam", "239030")
    assert not fetcher.request("steam", "239030")       # already pending
    assert not fetcher.request("unknown", "Sugar")      # no unapproved source
    assert fetcher.run_once() and not fetcher.run_once()
    assert ready == [("steam", "239030", {"hero"})]
    assert not fetcher.request("steam", "239030")       # partial artwork waits the miss window


def test_optional_no_key_fetch_is_not_cached_as_a_week_long_miss(tmp_path):
    cache = RibbonCache(tmp_path)
    fetcher = RibbonFetcher(cache, fetch=lambda _p, _i: None, start=False)
    item = ('steamgriddb', '{"title":"Fixture Game"}')
    assert fetcher.request(*item)
    assert fetcher.run_once()
    assert cache.needs_fetch(*item)
