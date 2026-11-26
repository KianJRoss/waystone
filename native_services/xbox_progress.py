"""Reuse Xodus' existing account/achievement reader; exact Store PFN→TitleId join.

No sign-in, licence/achievement mutation, registry edits, launches or credential
extraction. Public Store metadata never receives Xbox authentication material.
"""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import json,re,subprocess
class XboxProgress:
    def __init__(self,binary,*,runner=None,get=None):
        self.binary=Path(binary) if binary else None;self._runner=runner or subprocess.run;self._get=get
        self.account_id='';self._bindings={}
    def _read(self,*args):
        if self.binary is None or not self.binary.is_file():raise ValueError('Existing Xodus helper unavailable')
        r=self._runner([str(self.binary),*args,'--json'],capture_output=True,text=True,timeout=35)
        if r.returncode or len(r.stdout.encode())>4*1024*1024:raise ValueError('Xbox read unavailable')
        value=json.loads(r.stdout)
        if not isinstance(value,dict) or type(value.get('account')) is not str or not value['account'] or len(value['account'])>512:raise ValueError('Xbox owner identity unavailable')
        return value
    def discover(self,games):
        owned=self._read('library');account=owned['account'];records=owned.get('entitlements')
        if not isinstance(records,list) or len(records)>2000:raise ValueError('Invalid Xbox entitlements')
        products=sorted({r['productId'] for r in records if isinstance(r,dict) and isinstance(r.get('productId'),str) and re.fullmatch(r'[A-Za-z0-9]{12}',r['productId'])})
        by_pfn={}
        get=self._get
        if get is None:
            import httpx
            get=httpx.get
        for i in range(0,len(products),100):
            response=get('https://displaycatalog.md.mp.microsoft.com/v7.0/products',params={'bigIds':','.join(products[i:i+100]),'market':'US','languages':'en-us'},timeout=15,follow_redirects=False)
            response.raise_for_status()
            if len(response.content)>16*1024*1024:raise ValueError('Store metadata too large')
            payload=response.json()
            if not isinstance(payload,dict) or not isinstance(payload.get('Products'),list):raise ValueError('Store metadata unavailable')
            for product in payload['Products']:
                if product.get('ProductId') not in products[i:i+100]:raise ValueError('Wrong Store product returned')
                pfn=product.get('Properties',{}).get('PackageFamilyName')
                ids={str(a['Value']) for a in product.get('AlternateIds',[]) if a.get('IdType')=='XboxTitleId' and str(a.get('Value','')).isdigit() and 0<int(a['Value'])<=4294967295}
                if isinstance(pfn,str) and pfn and len(ids)==1:by_pfn.setdefault(pfn,set()).update(ids)
        bindings={};result=[]
        for game in games:
            ids=set()
            for variant in game.get('variants',[]):
                if str(variant.get('source','')).lower() in ('xbox','microsoft store'):
                    matches=by_pfn.get(variant.get('provider_game_id'),set())
                    if len(matches)==1:ids.update(matches)
            if ids:
                bindings[game['id']]=ids
                result.append({'provider':'xbox','game_key':game['id'],'title':game.get('title') or game['id'],'products':sorted(ids)})
        self.account_id=account;self._bindings=bindings
        return {'account_id':account,'games':result}
    def fetch_achievements(self,key,product):
        if product not in self._bindings.get(key,set()):raise ValueError('Exact Xbox TitleId binding required')
        value=self._read('achievements',product)
        if value.get('schema')!=1 or value['account']!=self.account_id or str(value.get('titleId'))!=product:raise ValueError('Xbox account/title changed')
        records=value.get('items');partial=value.get('partial')
        if not isinstance(records,list) or len(records)>20000 or type(partial) is not bool:raise ValueError('Invalid Xbox observations')
        items=[];seen=set()
        for row in records:
            ident=row.get('id')
            if type(ident) is not str or not ident or ident in seen:raise ValueError('Invalid Xbox achievement ID')
            seen.add(ident);state=row.get('state');unlocked=True if state=='unlocked' else False if state=='locked' else None
            if row.get('revoked') is True:unlocked=False
            items.append({'achievement_id':ident,'label':str(row.get('name') or ident)[:4096],'unlocked':unlocked,'unlocked_at':row.get('unlockedAt') if unlocked else None})
        count=sum(r['unlocked'] is True for r in items);total=None if partial else len(items)
        return {'provider':'xbox','account_id':self.account_id,'game_key':key,'product_id':product,'source':'Existing Xodus achievement reader',
            'status':'partial' if partial else 'live','observed_at':datetime.now(timezone.utc).isoformat(),'items':items,'unlocked_count':count,'total_count':total,
            'completion_percent':100*count/total if total and not any(r['unlocked'] is None for r in items) else None,
            'live_connection_verified':True,'account_ownership_verified':True}
