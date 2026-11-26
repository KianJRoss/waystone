"""Steam Web API read-only friends/achievements using an explicitly supplied key.

Never obtains/registers keys or reads launcher/browser credentials. Constructor is
inert. Keys stay in memory and never enter observations, reprs, caches or logs.
"""
import re
from datetime import datetime,timezone
from urllib.parse import urlsplit
from .achievement_sources import normalize_steam

class SteamWebError(ValueError):pass

def steam_id(value):
    if type(value) is not str or not re.fullmatch(r'76561[0-9]{12}',value):
        raise SteamWebError('Use a numeric SteamID64')
    return value

def app_id(value):
    if type(value) is not str or not re.fullmatch(r'[1-9][0-9]{0,9}',value) or int(value)>4294967295:
        raise SteamWebError('Use an exact Steam app ID')
    return value

def steam_account_input(value):
    """Return ('id', SteamID64) or ('vanity', name); accept only Steam URLs."""
    if type(value) is not str:
        raise SteamWebError('Enter a SteamID64, Steam profile URL, or vanity name')
    value=value.strip()
    if re.fullmatch(r'76561[0-9]{12}',value):return ('id',value)
    if '://' in value:
        try:parts=urlsplit(value)
        except ValueError:raise SteamWebError('Invalid Steam profile URL') from None
        try:port=parts.port
        except ValueError:raise SteamWebError('Invalid Steam profile URL') from None
        if (parts.scheme!='https' or parts.hostname not in ('steamcommunity.com','www.steamcommunity.com')
                or port not in (None,443) or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise SteamWebError('Use an https://steamcommunity.com profile URL')
        path=parts.path.strip('/').split('/')
        if len(path)==2 and path[0]=='profiles' and re.fullmatch(r'76561[0-9]{12}',path[1]):return ('id',path[1])
        if len(path)==2 and path[0]=='id' and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',path[1]):return ('vanity',path[1])
        raise SteamWebError('Steam profile URL must use /profiles/<SteamID64> or /id/<vanity>')
    if re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value):return ('vanity',value)
    raise SteamWebError('Enter a SteamID64, Steam profile URL, or vanity name')

class SteamWebAdapter:
    def __init__(self,account_id,key,*,request=None):
        self.account_id=steam_id(account_id)
        if type(key) is not str or not re.fullmatch(r'[a-fA-F0-9]{32}',key):raise SteamWebError('Enter an existing Steam Web API key')
        self._key=key;self._request=request
    def _get(self,interface,method,version,**params):
        url=f'https://api.steampowered.com/{interface}/{method}/{version}/'
        try:
            request=self._request
            if request is None:
                import httpx
                request=httpx.get
            response=request(url,params={'key':self._key,**params},timeout=10,follow_redirects=False)
            response.raise_for_status()
            if len(response.content)>4*1024*1024:raise ValueError()
            value=response.json()
            if not isinstance(value,dict):raise ValueError()
            return value
        except Exception:
            # HTTP exceptions can contain the authenticated URL: never relay them.
            raise SteamWebError('Steam refused the read or the connection failed; check key and profile privacy') from None
    def fetch_friends(self):
        value=self._get('ISteamUser','GetFriendList','v1',steamid=self.account_id,relationship='friend')
        records=value.get('friendslist',{}).get('friends')
        if not isinstance(records,list) or len(records)>2000:raise SteamWebError('Steam friend list unavailable')
        ids=[steam_id(r.get('steamid')) for r in records if isinstance(r,dict)]
        if len(ids)!=len(records) or len(set(ids))!=len(ids):raise SteamWebError('Invalid friend identities')
        rows=[]
        for i in range(0,len(ids),100):
            batch=ids[i:i+100]
            players=self._get('ISteamUser','GetPlayerSummaries','v2',steamids=','.join(batch)).get('response',{}).get('players')
            if not isinstance(players,list):raise SteamWebError('Steam presence unavailable')
            seen=set()
            for player in players:
                ident=steam_id(player.get('steamid'))
                if ident not in batch or ident in seen:raise SteamWebError('Wrong Steam friend returned')
                seen.add(ident)
                state=player.get('personastate');presence={0:'Offline',1:'Online',2:'Busy',3:'Away',4:'Snooze',5:'Looking to trade',6:'Looking to play'}.get(state,'Unknown')
                rows.append({'friend_id':ident,'display_name':str(player.get('personaname') or ident)[:256],
                    'presence':presence,'online':None if presence=='Unknown' else bool(state),'game':str(player.get('gameextrainfo') or '')[:256]})
            # Missing/private summaries do not delete friends or fabricate offline.
            rows.extend({'friend_id':ident,'display_name':ident,'presence':'Presence unavailable','online':None,'game':''} for ident in batch if ident not in seen)
        return {'provider':'steam','account_id':self.account_id,'status':'live','friends':rows}

    def fetch_owned_games(self):
        """Fetch one bounded, explicit owned-games snapshot; malformed/private is not empty."""
        response=self._get('IPlayerService','GetOwnedGames','v1',steamid=self.account_id,
                           include_appinfo=1,include_played_free_games=1)
        games=response.get('response',{}).get('games')
        count=response.get('response',{}).get('game_count')
        if type(count) is not int or count<0 or count>30000 or type(games) is not list or len(games)!=count:
            raise SteamWebError('Steam library is private, incomplete, or unavailable; no ownership changes were applied')
        result=[];seen=set()
        for item in games:
            if type(item) is not dict:
                raise SteamWebError('Steam returned an invalid library record; no ownership changes were applied')
            try:ident=app_id(str(item.get('appid')))
            except SteamWebError:raise SteamWebError('Steam returned an invalid library record; no ownership changes were applied') from None
            title=item.get('name')
            if not isinstance(title,str) or not title.strip() or len(title)>512 or ident in seen:
                raise SteamWebError('Steam returned an incomplete library; no ownership changes were applied')
            seen.add(ident)
            minutes=item.get('playtime_forever',0)
            if type(minutes) is not int or minutes<0 or minutes>2**31:
                raise SteamWebError('Steam returned invalid playtime; no ownership changes were applied')
            result.append({'product_id':ident,'title':title.strip(),'playtime_seconds':minutes*60})
        return {'account_id':self.account_id,'games':result,'complete':True}

    @staticmethod
    def resolve_vanity(value,key,*,request=None):
        if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value):
            raise SteamWebError('Invalid Steam vanity name')
        if type(key) is not str or not re.fullmatch(r'[a-fA-F0-9]{32}',key):
            raise SteamWebError('Enter an existing 32-character Steam Web API key')
        try:
            if request is None:
                import httpx
                request=httpx.get
            response=request('https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/',
                             params={'key':key,'vanityurl':value},timeout=10,follow_redirects=False)
            response.raise_for_status()
            if len(response.content)>1024*1024:raise ValueError()
            data=response.json()
            account=data.get('response',{}).get('steamid') if isinstance(data,dict) else None
            success=data.get('response',{}).get('success') if isinstance(data,dict) else None
            if success!=1 or not isinstance(account,str):raise ValueError()
            return steam_id(account)
        except Exception:
            raise SteamWebError('Steam could not resolve that profile; check the name, key, and connection') from None
    def fetch_achievements(self,game_key,product_id):
        product_id=app_id(product_id)
        stats=self._get('ISteamUserStats','GetPlayerAchievements','v1',steamid=self.account_id,appid=product_id,l='english')
        schema=self._get('ISteamUserStats','GetSchemaForGame','v2',appid=product_id,l='english')
        feed=normalize_steam(stats,game_id=game_key,app_id=product_id,account_id=self.account_id,schema=schema)
        if feed['status']!='available':raise SteamWebError('Achievement data is private, unavailable, or unsupported for this game')
        return {'game_key':game_key,'provider':'steam','product_id':product_id,'account_id':self.account_id,
            'status':'live','source':'Steam Web API','observed_at':datetime.now(timezone.utc).isoformat(),
            'items':feed['achievements'],'total_count':feed['total_count'],'unlocked_count':feed['unlocked_count'],
            'completion_percent':feed['completion_percent'],'live_connection_verified':True}
