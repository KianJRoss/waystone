"""Explicit Steam Guard QR sign-in and session-token read APIs.

Protocol mappings researched from Valve SteamKit schemas and MIT Irori Steam
client (see research/steam-social-source). No password/cookie scraping, API key
registration or local Steam launch. Refresh tokens may be handed to the app-owned
Secret Service store after explicit sign-in; no plaintext cache or token logging.
"""
import base64,json,re,struct,time
from datetime import datetime,timezone
from .steam_web import steam_id,app_id,SteamWebError

def varint(n):
    if type(n) is not int or n<0 or n>2**64-1:raise ValueError('Invalid protobuf integer')
    out=bytearray()
    while n>127:out.append((n&127)|128);n>>=7
    return bytes(out)+bytes([n])
def integer(tag,n):return varint(tag<<3)+varint(n)
def blob(tag,value):
    value=value.encode() if isinstance(value,str) else value
    return varint((tag<<3)|2)+varint(len(value))+value
def decode(data):
    if not isinstance(data,bytes) or len(data)>262144:raise SteamWebError('Invalid Steam sign-in response')
    pos=0;out={}
    def read():
        nonlocal pos
        value=0
        for shift in range(0,70,7):
            if pos>=len(data):raise ValueError('Truncated protobuf')
            b=data[pos];pos+=1;value|=(b&127)<<shift
            if not b&128:return value
        raise ValueError('Oversized protobuf varint')
    while pos<len(data):
        tag=read();field,wire=tag>>3,tag&7
        if not field:raise ValueError('Invalid protobuf tag')
        if wire==0:value=read()
        elif wire in (1,2,5):
            length=read() if wire==2 else 8 if wire==1 else 4
            if length>len(data)-pos:raise ValueError('Truncated protobuf field')
            value=data[pos:pos+length];pos+=length
        else:raise ValueError('Unsupported protobuf wire type')
        out.setdefault(field,[]).append(value)
    return out

def text(fields,tag):
    value=fields.get(tag,[b''])[-1]
    if not isinstance(value,bytes):raise ValueError('Invalid string field')
    return value.decode('utf-8')

class SteamQrSession:
    def __init__(self,*,request=None,clock=time.monotonic):
        self._request=request;self._clock=clock;self._client=None;self._request_id=None;self._expires=0;self._next=0;self._interval=5
        self.challenge_url=''
    def _auth(self,method,payload):
        request=self._request
        if request is None:
            import httpx
            request=httpx.request
        try:
            r=request('POST','https://api.steampowered.com/IAuthenticationService/'+method+'/v1',
                data={'input_protobuf_encoded':base64.b64encode(payload).decode()},timeout=10,follow_redirects=False)
            r.raise_for_status()
            if r.headers.get('x-eresult','1')!='1':raise ValueError()
            return decode(r.content)
        except Exception:raise SteamWebError('Steam sign-in request was refused or unavailable') from None
    def begin(self):
        details=blob(1,'Waystone on Linux')+integer(2,1)+integer(3,(2**64-203))+integer(4,1)
        fields=self._auth('BeginAuthSessionViaQR',blob(1,'Waystone on Linux')+integer(2,1)+blob(3,details)+blob(4,'Client'))
        challenge=text(fields,2)
        if not re.fullmatch(r'https://s\.team/q/[A-Za-z0-9_/?=&.%-]{1,500}',challenge):raise SteamWebError('Invalid Steam QR challenge')
        client=fields.get(1,[0])[-1];request_id=fields.get(3,[b''])[-1]
        if type(client) is not int or client<=0 or not isinstance(request_id,bytes) or not 1<=len(request_id)<=1024:raise SteamWebError('Invalid Steam sign-in session')
        self._client=client;self._request_id=request_id;self.challenge_url=challenge
        interval=struct.unpack('<f',fields[4][-1])[0] if 4 in fields and isinstance(fields[4][-1],bytes) and len(fields[4][-1])==4 else 5
        self._interval=interval if 2<=interval<=30 else 5
        self._expires=self._clock()+120;self._next=self._clock()+self._interval
        return {'challenge_url':challenge,'interval':self._interval}
    def cancel(self):
        self._client=None;self._request_id=None;self.challenge_url='';self._expires=0
    def poll(self):
        if self._client is None or self._clock()>=self._expires:
            self.cancel();raise SteamWebError('Steam sign-in expired; start a new QR session')
        if self._clock()<self._next:return None
        self._next=self._clock()+self._interval
        fields=self._auth('PollAuthSessionStatus',integer(1,self._client)+blob(2,self._request_id))
        challenge=text(fields,2)
        if challenge:
            if not re.fullmatch(r'https://s\.team/q/[A-Za-z0-9_/?=&.%-]{1,500}',challenge):raise SteamWebError('Invalid rotated challenge')
            self.challenge_url=challenge
        if fields.get(1):self._client=fields[1][-1]
        refresh=text(fields,3);access=text(fields,4)
        if not refresh:return None
        try:
            encoded=refresh.split('.')[1];claims=json.loads(base64.urlsafe_b64decode(encoded+'='*((-len(encoded))%4)))
            owner=steam_id(str(claims['sub']))
        except Exception:raise SteamWebError('Steam returned invalid account identity') from None
        if not access:
            response=self._auth('GenerateAccessTokenForApp',blob(1,refresh)+varint((2<<3)|1)+struct.pack('<Q',int(owner)))
            access=text(response,1)
        if not access:raise SteamWebError('Steam did not provide a read session')
        self.cancel()
        return SteamTokenAdapter(owner,access,refresh_token=refresh)

    def restore(self, owner, refresh):
        owner=steam_id(owner)
        if type(refresh) is not str or not 1<=len(refresh)<=16384:raise SteamWebError('Invalid saved Steam session')
        # Claims are only an account-consistency check, never authentication proof.
        try:
            encoded=refresh.split('.')[1]
            claims=json.loads(base64.urlsafe_b64decode(encoded+'='*((-len(encoded))%4)))
            if steam_id(str(claims['sub']))!=owner:raise ValueError()
        except Exception:raise SteamWebError('Saved Steam account does not match the session') from None
        response=self._auth('GenerateAccessTokenForApp',blob(1,refresh)+varint((2<<3)|1)+struct.pack('<Q',int(owner))+integer(3,1))
        access=text(response,1); renewed=text(response,2) or refresh
        if not access:raise SteamWebError('Steam session expired; sign in again')
        if renewed!=refresh:
            try:
                encoded=renewed.split('.')[1]
                claims=json.loads(base64.urlsafe_b64decode(encoded+'='*((-len(encoded))%4)))
                if steam_id(str(claims['sub']))!=owner:raise ValueError()
            except Exception:raise SteamWebError('Renewed Steam account did not match') from None
        return SteamTokenAdapter(owner,access,refresh_token=renewed)

class SteamTokenAdapter:
    def __init__(self,owner,token,*,request=None,refresh_token=None):
        self.account_id=steam_id(owner)
        if type(token) is not str or not token or len(token)>16384:raise SteamWebError('Invalid Steam read session')
        self._token=token;self._request=request
        self._refresh_token=refresh_token
    def session_record(self):
        if not self._refresh_token:return None
        return {'kind':'steam_refresh','account_id':self.account_id,'refresh_token':self._refresh_token}
    def _get(self,interface,method,**params):
        request=self._request
        if request is None:
            import httpx
            request=httpx.get
        try:
            r=request('https://api.steampowered.com/'+interface+'/'+method+'/v1/',params={'access_token':self._token,**params},timeout=10,follow_redirects=False)
            r.raise_for_status()
            if len(r.content)>4*1024*1024:raise ValueError()
            value=r.json()
            if not isinstance(value,dict) or not isinstance(value.get('response'),dict):raise ValueError()
            return value['response']
        except Exception:raise SteamWebError('Steam session read unavailable, expired or not shared') from None
    def fetch_friends(self):
        value=self._get('IFriendsListService','GetFriendsList')
        records=value.get('friendslist',{}).get('friends')
        if not isinstance(records,list) or len(records)>2000:raise SteamWebError('Steam did not share its friends list')
        ids=[steam_id(str(r['ulfriendid'])) for r in records if int(r.get('efriendrelationship',0))==3 and str(r.get('ulfriendid'))!=self.account_id]
        if len(set(ids))!=len(ids):raise SteamWebError('Duplicate Steam friend identity')
        rows=[]
        for i in range(0,len(ids),100):
            batch=ids[i:i+100];accounts=self._get('IPlayerService','GetPlayerLinkDetails',**{'steamids['+str(n)+']':key for n,key in enumerate(batch)}).get('accounts')
            if not isinstance(accounts,list):raise SteamWebError('Steam presence response unavailable')
            found=set()
            for account in accounts:
                public=account.get('public_data',{});private=account.get('private_data',{});key=steam_id(str(public.get('steamid')))
                if key not in batch or key in found:raise SteamWebError('Wrong Steam presence identity')
                found.add(key);state=private.get('persona_state');presence={0:'Offline',1:'Online',2:'Busy',3:'Away',4:'Snooze',5:'Looking to trade',6:'Looking to play'}.get(state,'Unknown')
                rows.append({'friend_id':key,'display_name':str(public.get('persona_name') or key)[:256],
                    'presence':presence,'online':None if presence=='Unknown' else bool(state),'game':str(private.get('game_extra_info') or '')[:256]})
            rows.extend({'friend_id':key,'display_name':key,'online':None,'presence':'Presence unavailable'} for key in batch if key not in found)
        return {'provider':'steam','account_id':self.account_id,'status':'live','friends':rows}
    def fetch_achievements(self,key,product):
        product=app_id(product)
        schema=self._get('IPlayerService','GetGameAchievements',appid=product,language='english')
        users=self._get('IPlayerService','GetUserAchievements',steamid=self.account_id,appid=product)
        definitions=schema.get('achievements');unlocks=users.get('achievements')
        if not isinstance(definitions,list) or not isinstance(unlocks,list) or len(definitions)>20000 or len(unlocks)>20000:raise SteamWebError('Steam achievement data unavailable or private')
        by_id={str(r['internal_key']):r for r in unlocks}
        if len(by_id)!=len(unlocks):raise SteamWebError('Duplicate Steam achievement identity')
        items=[];seen=set()
        for row in definitions:
            ident=str(row['internal_key'])
            if ident in seen:raise SteamWebError('Duplicate Steam schema identity')
            seen.add(ident);state=by_id.get(ident,{}).get('unlocked')
            if state is not None and type(state) is not bool:raise SteamWebError('Invalid Steam unlock flag')
            timestamp=by_id.get(ident,{}).get('unlock_time',0)
            items.append({'achievement_id':ident,'label':str(row.get('localized_name') or row.get('internal_name') or ident)[:4096],
                'unlocked':state,'unlocked_at':datetime.fromtimestamp(timestamp,timezone.utc).isoformat() if state is True and type(timestamp) is int and timestamp>0 else None})
        unlocked=sum(r['unlocked'] is True for r in items);unknown=any(r['unlocked'] is None for r in items)
        return {'game_key':key,'provider':'steam','product_id':product,'account_id':self.account_id,'status':'live','source':'Steam signed-in session',
            'observed_at':datetime.now(timezone.utc).isoformat(),'items':items,'total_count':len(items),'unlocked_count':unlocked,
            'completion_percent':None if not items or unknown else 100*unlocked/len(items),'live_connection_verified':True,'account_ownership_verified':True}
