"""Provider/account-scoped achievement normalization; no auth, network or unlock writes."""
from datetime import datetime,timezone


def _id(value):
    if not isinstance(value,str) or not value or len(value)>512:raise ValueError('Expected exact bounded identity')
    return value


def _feed(provider,game_id,product_id,account_id,status,items):
    unlocked=sum(x['unlocked'] is True for x in items)
    total=len(items) if status=='available' else None
    return {'provider':_id(provider),'canonical_game_id':_id(game_id),
        'provider_game_id':_id(product_id),'account_id':_id(account_id),'status':status,
        'achievements':items,'unlocked_count':unlocked if total is not None else None,
        'total_count':total,'completion_percent':100*unlocked/total if total else None,
        'live_connection_verified':False}


def normalize_steam(payload,*,game_id,app_id,account_id,schema=None):
    """Normalize GetPlayerAchievements response, bound to its request context.

    Source schema: https://partner.steamgames.com/doc/webapi/ISteamUserStats
    Parsing caller-supplied JSON is not proof of a live authenticated connection.
    """
    if not isinstance(payload,dict):raise ValueError('Expected Steam payload')
    stats=payload.get('playerstats')
    if not isinstance(stats,dict) or stats.get('success') is not True:
        return _feed('steam',game_id,app_id,account_id,'unavailable',[])
    if str(stats.get('steamID'))!=account_id:raise ValueError('Steam account does not match request')
    records=stats.get('achievements')
    if not isinstance(records,list) or len(records)>20000:raise ValueError('Invalid achievement records')
    labels={}
    if schema is not None:
        for item in schema.get('game',{}).get('availableGameStats',{}).get('achievements',[]):
            if isinstance(item,dict) and isinstance(item.get('name'),str):labels[item['name']]=item
    result=[];seen=set()
    for item in records:
        if not isinstance(item,dict):raise ValueError('Malformed achievement')
        ident=_id(item.get('apiname'))
        if ident in seen:raise ValueError('Duplicate provider achievement ID')
        seen.add(ident)
        if type(item.get('achieved')) is not int or item['achieved'] not in (0,1):
            raise ValueError('Invalid unlock flag')
        timestamp=item.get('unlocktime',0);when=None
        if type(timestamp) is not int or timestamp<0:raise ValueError('Invalid unlock timestamp')
        if timestamp and item['achieved']:
            try:when=datetime.fromtimestamp(timestamp,timezone.utc).isoformat()
            except (ValueError,OverflowError,OSError):raise ValueError('Invalid unlock timestamp') from None
        label=labels.get(ident,{}).get('displayName') or ident
        if not isinstance(label,str) or len(label)>4096:raise ValueError('Invalid achievement label')
        result.append({'achievement_id':ident,'label':label,'unlocked':bool(item['achieved']),
                       'unlocked_at':when})
    return _feed('steam',game_id,app_id,account_id,'available',result)


def normalize_imported(provider,records,*,game_id,product_id,account_id):
    """Explicit normalized observations for providers without a live adapter yet.

    This does NOT claim to implement a GOG/Xbox/RetroAchievements network API.
    Caller must supply exact provider-product-account binding, never a title join.
    """
    if not isinstance(records,list) or len(records)>20000:raise ValueError('Expected bounded observations')
    result=[];seen=set()
    for row in records:
        if not isinstance(row,dict):raise ValueError('Invalid observation')
        for field,expected in (('provider',provider),('account_id',account_id),('provider_game_id',product_id)):
            if field in row and row[field]!=expected:raise ValueError('Observation identity mismatch')
        ident=_id(row.get('achievement_id'))
        if ident in seen:raise ValueError('Duplicate provider achievement ID')
        seen.add(ident)
        if row.get('unlocked') is not None and type(row['unlocked']) is not bool:raise ValueError('Invalid unlock value')
        label=row.get('label',ident)
        if not isinstance(label,str) or len(label)>4096:raise ValueError('Invalid label')
        result.append({'achievement_id':ident,'label':label,'unlocked':row.get('unlocked'),
                       'unlocked_at':row.get('unlocked_at')})
    feed=_feed(provider,game_id,product_id,account_id,'imported',result)
    # Import availability/coverage is not established by an empty or partial file.
    feed['observed_count']=len(result)
    feed['observed_unlocked_count']=sum(x['unlocked'] is True for x in result)
    return feed


def account_overview(feeds):
    """Never transfer completion between storefronts/accounts or count snapshots twice."""
    accounts={};seen=set()
    for feed in feeds:
        key=(feed['provider'],feed['account_id'],feed['provider_game_id'])
        if key in seen:raise ValueError('Choose one current snapshot per provider/account/game')
        seen.add(key);scope=key[:2]
        row=accounts.setdefault(scope,{'provider':scope[0],'account_id':scope[1],
            'game_count':0,'known_unlocked':0,'unavailable_games':0})
        row['game_count']+=1
        if feed['unlocked_count'] is None:row['unavailable_games']+=1
        else:row['known_unlocked']+=feed['unlocked_count']
    return list(accounts.values())
