import pytest

from native_services.steam_web import SteamWebAdapter, SteamWebError, steam_account_input

STEAM_ID = '76561198000000001'
KEY = '0123456789abcdef0123456789abcdef'


class Response:
    def __init__(self, value): self.value = value; self.content = b'{}'
    def raise_for_status(self): pass
    def json(self): return self.value


def test_account_input_accepts_numeric_profile_and_vanity_without_url_ambiguity():
    assert steam_account_input(STEAM_ID) == ('id', STEAM_ID)
    assert steam_account_input('https://steamcommunity.com/profiles/' + STEAM_ID + '/') == ('id', STEAM_ID)
    assert steam_account_input('https://www.steamcommunity.com/id/steam_name') == ('vanity', 'steam_name')
    assert steam_account_input('steam_name') == ('vanity', 'steam_name')
    for value in ('http://steamcommunity.com/id/name', 'https://evil.example/id/name',
                  'https://steamcommunity.com.evil.example/id/name', 'steamcommunity.com/id/name',
                  'https://user@steamcommunity.com/id/name', 'https://steamcommunity.com/id/name?next=evil',
                  'https://steamcommunity.com/id/name#fragment',
                  '../key', 'javascript:alert(1)'):
        with pytest.raises(SteamWebError): steam_account_input(value)


def test_get_owned_games_requests_names_free_games_and_playtime_with_one_https_call():
    calls = []
    def request(url, *, params, **kwargs):
        calls.append((url, params, kwargs))
        return Response({'response': {'game_count': 2, 'games': [
            {'appid': 10, 'name': 'Installed Game', 'playtime_forever': 42},
            {'appid': 20, 'name': 'Free Game', 'playtime_forever': 0}]}})
    result = SteamWebAdapter(STEAM_ID, KEY, request=request).fetch_owned_games()
    assert result == {'account_id': STEAM_ID, 'complete': True, 'games': [
        {'product_id': '10', 'title': 'Installed Game', 'playtime_seconds': 2520},
        {'product_id': '20', 'title': 'Free Game', 'playtime_seconds': 0}]}
    assert len(calls) == 1
    url, params, kwargs = calls[0]
    assert url == 'https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/'
    assert params == {'key': KEY, 'steamid': STEAM_ID, 'include_appinfo': 1, 'include_played_free_games': 1}
    assert kwargs == {'timeout': 10, 'follow_redirects': False}


@pytest.mark.parametrize('response', [
    {'response': {}},
    {'response': {'game_count': 1, 'games': []}},
    {'response': {'game_count': 1, 'games': [{'appid': 10, 'playtime_forever': 0}]}},
])
def test_private_incomplete_or_untitled_response_is_not_an_empty_complete_library(response):
    with pytest.raises(SteamWebError):
        SteamWebAdapter(STEAM_ID, KEY, request=lambda *_a, **_k: Response(response)).fetch_owned_games()


def test_vanity_resolver_uses_fixed_steam_host_and_redacts_request_failures():
    calls = []
    def request(url, *, params, **kwargs):
        calls.append((url, params, kwargs))
        return Response({'response': {'success': 1, 'steamid': STEAM_ID}})
    assert SteamWebAdapter.resolve_vanity('myname', KEY, request=request) == STEAM_ID
    assert calls == [('https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/',
                      {'key': KEY, 'vanityurl': 'myname'},
                      {'timeout': 10, 'follow_redirects': False})]
    with pytest.raises(SteamWebError) as error:
        SteamWebAdapter.resolve_vanity('myname', KEY, request=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError(KEY)))
    assert KEY not in str(error.value)
