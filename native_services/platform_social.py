"""Provider/owner-qualified social directory over explicitly registered adapters."""
from copy import deepcopy
import threading
PROVIDERS={'steam':'Steam','xbox':'Xbox','battlenet':'Battle.net','epic':'Epic','gog':'GOG',
           'ea':'EA','ubisoft':'Ubisoft','playstation':'PlayStation','nintendo':'Nintendo'}

def ident(value):
    if type(value) is not str or not value or len(value)>256 or not value.isprintable():raise ValueError('Exact bounded ID required')
    return value

class PlatformSocial:
    def __init__(self,adapters=None):
        self._lock=threading.RLock();self._adapters=dict(adapters or {})
        if not set(self._adapters)<=set(PROVIDERS):raise ValueError('Unknown provider')
        self._data={p:{'provider':p,'label':label,'account_id':'','status':'not_connected' if p in self._adapters else 'integration_pending','friends':[],'error':''} for p,label in PROVIDERS.items()}
    def register(self,provider,adapter):
        with self._lock:
            if provider not in PROVIDERS:raise ValueError('Unknown provider')
            self._adapters[provider]=adapter
    def bind(self,provider,account_id):
        with self._lock:
            if provider not in PROVIDERS:raise ValueError('Unknown provider')
            account_id=ident(account_id);row=self._data[provider]
            if row['account_id']!=account_id:row.update(account_id=account_id,status='not_connected',friends=[],error='')
    def disconnect(self,provider):
        with self._lock:
            if provider not in PROVIDERS:raise ValueError('Unknown provider')
            self._data[provider].update(account_id='',status='not_connected',friends=[],error='')
    def providers(self):
        with self._lock:return [{k:deepcopy(v) for k,v in row.items() if k!='friends'} for row in self._data.values()]
    def friends(self):
        with self._lock:return deepcopy([f for row in self._data.values() for f in row['friends']])
    def _failure(self,provider,reason):
        row=self._data[provider]
        row.update(status='stale' if row['friends'] else 'unavailable',error=reason)
        for f in row['friends']:f.update(online=None,presence='Stale · presence unavailable')
    def unavailable(self,provider):
        with self._lock:
            if provider not in PROVIDERS:raise ValueError('Unknown provider')
            self._failure(provider,'Provider read unavailable; cached presence is not live')
    def refresh(self,provider):
        if provider not in PROVIDERS:raise ValueError('Unknown provider')
        with self._lock:
            adapter=self._adapters.get(provider);owner=self._data[provider]['account_id']
        if adapter is None:return self.providers()
        try:
            value=adapter.fetch_friends()
            if not isinstance(value,dict) or value.get('provider')!=provider:raise ValueError()
            status=value.get('status')
            if status=='unavailable':raise ValueError()
            if status not in ('live','cached','offline'):raise ValueError()
            account=ident(value.get('account_id'))
            if owner and owner!=account:raise ValueError()
            raw=value.get('friends')
            if not isinstance(raw,list) or len(raw)>2000:raise ValueError()
            rows=[];seen=set()
            for f in raw:
                if not isinstance(f,dict):raise ValueError()
                key=ident(f.get('friend_id'));name=ident(f.get('display_name'))
                if key in seen or (f.get('online') is not None and type(f.get('online')) is not bool):raise ValueError()
                seen.add(key)
                rows.append({'provider':provider,'account_id':account,'friend_id':key,
                    'row_id':provider+':'+account+':'+key,'display_name':name,
                    'online':f.get('online') if status=='live' else None,
                    'presence':str(f.get('presence') or 'Unknown')[:512] if status=='live' else 'Cached · presence unavailable',
                    'game':str(f.get('game') or '')[:512]})
            with self._lock:
                if self._data[provider]['account_id']!=owner:raise ValueError('Owner changed during read')
                self._data[provider].update(account_id=account,status=status,friends=rows,error='')
        except Exception:
            with self._lock:self._failure(provider,'Provider read unavailable; reconnect or check account/privacy settings')
        return self.providers()
