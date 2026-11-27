"""Async, keyring-backed personal ITAD lookup for Waystone game details."""
from __future__ import annotations
from native_services.platform import set_private_file_mode
import json
import os
from pathlib import Path
import re
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from PySide6.QtCore import QObject, Property, Signal, Slot, QThread, QUrl
from PySide6.QtGui import QDesktopServices
from native_services.itad_prices import ITADClient, ITADError
from native_services.public_offer_scraper import PublicPageScraper
from native_services import waystone_paths as _wpaths

try:
    from private_addons import (
        AVAILABLE as _ADDON_STORE_TAB_AVAILABLE,
        STORE_CATALOG_KEY as _ADDON_STORE_TAB_KEY,
        STORE_BUY_TAB_LABEL as _ADDON_STORE_TAB_LABEL,
        STORE_BUY_TAB_HINT as _ADDON_STORE_TAB_HINT,
        STORE_IGNORE_FILTER_LABEL as _ADDON_STORE_FILTER_LABEL,
    )
except ImportError:
    _ADDON_STORE_TAB_AVAILABLE = False
    _ADDON_STORE_TAB_KEY = ''
    _ADDON_STORE_TAB_LABEL = ''
    _ADDON_STORE_TAB_HINT = ''
    _ADDON_STORE_FILTER_LABEL = ''

_MARKETS=[
    {'code':'US','name':'United States'}, {'code':'CA','name':'Canada'},
    {'code':'GB','name':'United Kingdom'}, {'code':'AU','name':'Australia'},
    {'code':'DE','name':'Germany'}, {'code':'FR','name':'France'},
    {'code':'JP','name':'Japan'}, {'code':'BR','name':'Brazil'},
    {'code':'PL','name':'Poland'}, {'code':'NL','name':'Netherlands'},
    {'code':'SE','name':'Sweden'}, {'code':'NO','name':'Norway'},
    {'code':'DK','name':'Denmark'}, {'code':'FI','name':'Finland'},
    {'code':'IT','name':'Italy'}, {'code':'ES','name':'Spain'},
    {'code':'MX','name':'Mexico'},
]
_MARKET_CODES={row['code'] for row in _MARKETS}
_HOST=re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$")

_OFFICIAL_STORES = {
    'steam', 'gog', 'gog.com', 'epic game store', 'epic games store', 'epic',
    'microsoft store', 'xbox', 'ea app', 'origin', 'ubisoft store',
    'ubisoft connect', 'uplay', 'battle.net', 'blizzard', 'playstation store',
    'nintendo eshop', 'apple app store', 'google play'
}
_CDKEY_MARKETPLACES = {
    'g2a', 'kinguin', 'cdkeys', 'cdkeys.com', 'eneba', 'instant gaming',
    'instant-gaming', 'gamivo', 'k4g', 'hrk', 'hrk game', 'cjs cd keys',
    'mtcgame', 'g2play', 'yuplay', 'driffle', 'royalcdkeys', 'mmoga',
    'allkeyshop', 'gg.deals', 'cdkey'
}
_CHEAPSHARK_STORES = {
    '1': ('Steam', 'https://store.steampowered.com'),
    '2': ('GamersGate', 'https://www.gamersgate.com'),
    '3': ('GreenManGaming', 'https://www.greenmangaming.com'),
    '7': ('GOG', 'https://www.gog.com'),
    '8': ('EA App', 'https://www.ea.com'),
    '11': ('Humble Store', 'https://www.humblebundle.com/store'),
    '13': ('Ubisoft Store', 'https://store.ubisoft.com'),
    '15': ('Fanatical', 'https://www.fanatical.com'),
    '21': ('WinGameStore', 'https://www.wingamestore.com'),
    '23': ('GameBillet', 'https://www.gamebillet.com'),
    '24': ('Voidu', 'https://www.voidu.com'),
    '25': ('Epic Game Store', 'https://store.epicgames.com'),
    '27': ('GamesPlanet', 'https://us.gamesplanet.com'),
    '28': ('Gamesload', 'https://www.gamesload.com'),
    '29': ('2Game', 'https://2game.com'),
    '30': ('IndieGala', 'https://www.indiegala.com'),
    '33': ('DLGamer', 'https://www.dlgamer.com'),
    '34': ('Noctre', 'https://www.noctre.com'),
    '35': ('Dreamgame', 'https://www.dreamgame.com'),
}
_CDKEY_DISCLAIMER_BANNER = (
    "⚠ CD-Key & Third-Party Key Site Disclaimer: Offers badged 'CD-KEY RETAILER' or "
    "'CD-KEY MARKETPLACE' sell third-party activation keys (e.g. Steam/Epic/GOG keys) "
    "rather than direct account licenses. Always verify regional activation locks, target "
    "launcher DRM, seller authorization, and note that CD-keys are non-refundable once revealed."
)

def _https_image(url) -> str:
    """Store artwork from the deals API reaches QML only as a plain https URL (no file:, data:, http: or relative)."""
    from urllib.parse import urlsplit
    if not isinstance(url, str):
        return ''
    parts = urlsplit(url.strip())
    return url.strip() if parts.scheme == 'https' and parts.netloc and not parts.username and not parts.password else ''


def classify_store_offer(shop_name: str, url: str = '') -> dict:
    sname = (shop_name or 'Store').strip()
    low = sname.casefold()
    host = (urlsplit(url).hostname or '').casefold() if isinstance(url, str) and url else ''
    if any(k in low or (host and k in host) for k in _CDKEY_MARKETPLACES):
        return {
            'storeCategory': 'cdkey_marketplace',
            'isCdKeySite': True,
            'cdKeyBadge': '⚠ CD-KEY MARKETPLACE',
            'cdKeyDisclaimer': (
                'CD-Key Marketplace Disclaimer: Unverified third-party key reseller / grey-market site. '
                'Keys may be region-locked, unauthorized by the publisher, carry revocation risk, or void refund rights.'
            ),
        }
    if low in _OFFICIAL_STORES:
        return {
            'storeCategory': 'official_store',
            'isCdKeySite': False,
            'cdKeyBadge': 'DIRECT STORE',
            'cdKeyDisclaimer': '',
        }
    return {
        'storeCategory': 'cdkey_retailer',
        'isCdKeySite': True,
        'cdKeyBadge': 'CD-KEY SITE',
        'cdKeyDisclaimer': (
            'CD-Key Site Disclaimer: Third-party key seller (delivers an activation key such as a Steam/Epic/GOG key). '
            'Verify region lock, target DRM launcher, and note that keys cannot be refunded once revealed.'
        ),
    }


class _PriceJob(QThread):
    result=Signal(dict)
    def __init__(self, factory, parent=None):
        super().__init__(parent); self._factory=factory
    def run(self):
        try: self.result.emit(self._factory())
        except ITADError as exc: self.result.emit({'ok':False,'message':str(exc)})
        except ValueError as exc: self.result.emit({'ok':False,'message':str(exc)})
        except Exception: self.result.emit({'ok':False,'message':'ITAD lookup failed. Check keyring and connection.'})
        finally: self._factory=None

class PriceComparisonView(QObject):
    changed=Signal()
    def __init__(self, session_store, *, client_factory=ITADClient, scraper_factory=PublicPageScraper, scraper_config_path=None, market_config_path=None, url_opener=None, parent=None):
        super().__init__(parent); self._store=session_store; self._client_factory=client_factory; self._scraper_factory=scraper_factory
        self._url_opener=url_opener or QDesktopServices.openUrl
        self._scraper_config_path=Path(scraper_config_path or (Path.home()/'.local/share/waystone/store-scraper-sources-v1.json'))
        self._market_config_path=Path(market_config_path or (Path.home()/'.local/share/waystone/store-market-v1.json'))
        self._country,self._include_vouchers=self._load_market_preferences()
        self._hosts=self._load_hosts(); self._scraped_offers=[]; self._scraped_title=''; self._auto_scraped_deals=[]
        self._scraper=self._scraper_factory(self._hosts) if self._hosts else None
        self._worker=None; self._preload_worker=None; self._busy=False; self._candidates=[]; self._source_rows=[]; self._selected_title=''; self._lookup_cache={}
        self._message='Connect an ITAD API key to compare store prices.'
        # Do not touch the desktop keyring during generic app startup; consult it
        # only when the user explicitly asks for price comparison.
        self._configured=False
        self._card_rev=0
        self._use_disk_card_cache=(client_factory is ITADClient and market_config_path is None and scraper_config_path is None)
        self._card_cache_path=Path(os.environ.get('WAYSTONE_STORE_PRICE_CACHE') or _wpaths.store('store-price-cache-v1.json'))
        self._card_prices=self._load_card_cache() if self._use_disk_card_cache else {}
        self._feed_cache_path=Path(os.environ.get('WAYSTONE_STORE_FEED_CACHE') or _wpaths.store('store-feed-cache-v1.json'))
        self._owned_titles=set()
        self._secondary_titles=set()
        self._secondary_raw_titles=[]
        self._exclude_owned=True
        self._exclude_secondary=True
        self._exclude_cd_keys=False
        self._store_sort='-trending'
        self._store_filter=''
        self._store_feeds=self._load_feed_cache() if self._use_disk_card_cache else {}
        self._feed_offsets={k:len(v) for k,v in self._store_feeds.items() if isinstance(v,list)}
    def _load_market_preferences(self):
        try:
            data=json.loads(self._market_config_path.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or set(data) not in ({'country'},{'country','include_vouchers'}):return 'US',False
            country=data.get('country')
            vouchers=data.get('include_vouchers',False)
            return (country if isinstance(country,str) and country in _MARKET_CODES else 'US',
                    vouchers if type(vouchers) is bool else False)
        except (OSError,ValueError,TypeError):
            return 'US',False
    def _save_market_preferences(self,country,vouchers):
        path=self._market_config_path;temp=None
        try:
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd,temp=tempfile.mkstemp(prefix='.store-market-',dir=path.parent)
            set_private_file_mode(fd, 0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump({'country':country,'include_vouchers':vouchers},stream,separators=(',',':'));stream.flush();os.fsync(stream.fileno())
            os.replace(temp,path);temp=None
            dirfd=os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
            try:os.fsync(dirfd)
            finally:os.close(dirfd)
            return True
        except Exception:
            if temp:
                try:os.unlink(temp)
                except OSError:pass
            return False
    @Slot(str,result=bool)
    def setMarketCountry(self,country):
        if self._busy or not isinstance(country,str) or country not in _MARKET_CODES:return False
        if country==self._country:return True
        if not self._save_market_preferences(country,self._include_vouchers):
            self._message='Could not save the store price region.'
            self.changed.emit();return False
        self._country=country
        self._source_rows=[];self._selected_title=''
        self._message=f'Store price region set to {country}. Check prices again for this market.'
        self.changed.emit();return True
    @Slot(bool,result=bool)
    def setIncludeVouchers(self,enabled):
        if self._busy or type(enabled) is not bool:return False
        if enabled==self._include_vouchers:return True
        if not self._save_market_preferences(self._country,enabled):
            self._message='Could not save the voucher-price preference.'
            self.changed.emit();return False
        self._include_vouchers=enabled
        self._source_rows=[];self._selected_title=''
        self._message='Voucher prices enabled. Check prices again.' if enabled else 'Voucher prices excluded. Check prices again.'
        self.changed.emit();return True
    def _load_hosts(self):
        try:
            data=json.loads(self._scraper_config_path.read_text(encoding='utf-8'))
            hosts=data.get('hosts',[]) if isinstance(data,dict) and set(data)=={'hosts'} else []
            if not isinstance(hosts,list) or len(hosts)>50:return []
            return sorted({h for h in hosts if isinstance(h,str) and _HOST.fullmatch(h)})
        except (OSError,ValueError,TypeError):return []
    def _has_key(self):
        if self._store is None:return False
        try:return bool(self._store.load('itad'))
        except Exception:return False
    def _load_card_cache(self):
        try:
            if self._card_cache_path.is_file() and not self._card_cache_path.is_symlink() and self._card_cache_path.stat().st_size<=4*1024*1024:
                data=json.loads(self._card_cache_path.read_text(encoding='utf-8'))
                if isinstance(data,dict):return data
        except Exception:pass
        return {}
    def _save_card_cache(self):
        if not self._use_disk_card_cache:return
        try:
            self._card_cache_path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self._card_cache_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self._card_prices,indent=2)+'\n',encoding='utf-8')
            os.replace(tmp,self._card_cache_path)
        except Exception:pass
    def _record_card_price(self,title,rows,candidates=None,game_id=None):
        if not title or not rows:return
        nt=' '.join(title.casefold().split())
        best_deal=None;best_amt=None
        for r in rows:
            if not isinstance(r,dict):continue
            for d in r.get('deals',[]):
                if not isinstance(d,dict):continue
                pr=d.get('price') or {}
                try:amt=Decimal(str(pr.get('amount')))
                except Exception:continue
                if not amt.is_finite() or amt<0:continue
                if best_amt is None or amt<best_amt:
                    best_amt=amt;best_deal=d
        if best_deal is not None:
            pr=best_deal.get('price') or {}
            curr=pr.get('currency','USD')
            shop=(best_deal.get('shop') or {}).get('name','Store')
            cut=best_deal.get('cut')
            sym='$' if curr=='USD' else (str(curr)+' ')
            cut_str=f' (-{cut}%)' if isinstance(cut,int) and cut>0 else ''
            badge=f'{sym}{best_amt:.2f}{cut_str} · {shop}'
            self._card_prices[nt]={'badge':badge,'amount':f'{best_amt:.2f}','currency':curr,'shop':shop,'cut':cut,'game_id':game_id,'title':title,'rows':rows,'candidates':candidates or []}
            self._card_rev+=1
    def _norm_title(self,t):
        s=re.sub(r"['’`]",'',str(t or '').casefold())
        s=re.sub(r'\s+v\d[\w.()-]*','',s)
        s=re.sub(r'\s*\((?:pcvr|vr|\d{4})\)','',s)
        s=re.sub(r'[:\-\s]*(?:\((?:ultimate|complete|definitive|deluxe|game of the year|goty|enhanced|anniversary|collectors|standard)\s+edition\)|(?:ultimate|complete|definitive|deluxe|game of the year|goty|enhanced|anniversary|collectors|standard)\s+edition)\b','',s)
        s=re.sub(r'\s+vr$','',s)
        s=re.sub(r'^the\s+','',s)
        return ' '.join(re.findall(r'[a-z0-9]+',s))
    def setOwnedTitles(self,titles):
        self._owned_titles={self._norm_title(t) for t in (titles or []) if isinstance(t,str) and t.strip()}
        self._card_rev+=1
        self.changed.emit()
    def setLibraryOwnership(self,bought_titles,secondary_titles=None):
        owned={self._norm_title(t) for t in (bought_titles or []) if isinstance(t,str) and t.strip()}
        owned.discard('')
        sec_raw=[]
        sec_norm=set()
        for t in (secondary_titles or []):
            if not isinstance(t,str) or not t.strip():continue
            nt=self._norm_title(t)
            if nt and nt not in owned and nt not in sec_norm:
                sec_norm.add(nt)
                sec_raw.append(t.strip())
        self._owned_titles=owned
        self._secondary_titles=sec_norm
        self._secondary_raw_titles=sorted(sec_raw)
        self._card_rev+=1
        self.changed.emit()
    def _load_feed_cache(self):
        try:
            if self._feed_cache_path.is_file() and not self._feed_cache_path.is_symlink() and self._feed_cache_path.stat().st_size<=8*1024*1024:
                data=json.loads(self._feed_cache_path.read_text(encoding='utf-8'))
                if isinstance(data,dict):return data
        except Exception:pass
        return {}
    def _save_feed_cache(self):
        if not self._use_disk_card_cache:return
        try:
            self._feed_cache_path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self._feed_cache_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self._store_feeds,indent=2)+'\n',encoding='utf-8')
            os.replace(tmp,self._feed_cache_path)
        except Exception:pass
    def _format_deal_item(self,item):
        if not isinstance(item,dict) or not item.get('id') or not item.get('title'):return None
        title=item['title'].strip()
        nt=self._norm_title(title)
        assets=item.get('assets') or {}
        cover=_https_image(assets.get('boxart') or assets.get('banner400') or assets.get('banner300') or assets.get('banner145') or '')
        deal=item.get('deal') or {}
        price=deal.get('price') or {}
        reg=deal.get('regular') or {}
        hist=deal.get('historyLow') or {}
        shop=(deal.get('shop') or {}).get('name','Store')
        cut=deal.get('cut') or 0
        curr=price.get('currency','USD')
        sym='$' if curr=='USD' else (str(curr)+' ')
        try:
            amt=Decimal(str(price.get('amount',0)))
            amt_str='FREE' if amt==0 else f'{sym}{amt:.2f}'
        except Exception:amt_str='?'
        try:
            reg_amt=Decimal(str(reg.get('amount',0)))
            reg_str=f'{sym}{reg_amt:.2f}' if reg_amt>0 else ''
        except Exception:reg_str=''
        try:
            hist_amt=Decimal(str(hist.get('amount',0)))
            hist_str=f'{sym}{hist_amt:.2f}' if hist_amt>0 else ''
        except Exception:hist_str=''
        cut_str=f' (-{cut}%)' if isinstance(cut,int) and cut>0 else ''
        badge=f'{amt_str}{cut_str}'
        return {'gameId':item['id'],'title':title,'normTitle':nt,'coverUrl':cover,'priceBadge':badge,'priceAmount':amt_str,'regularPrice':reg_str,'historyLow':hist_str,'cut':int(cut) if isinstance(cut,int) else 0,'shopName':shop,'currency':curr,'dealUrl':deal.get('url') or '','rawDeal':deal}
    def _visible_store_items(self):
        needle=self._store_filter.strip().casefold()
        sec_key=_ADDON_STORE_TAB_KEY
        if _ADDON_STORE_TAB_AVAILABLE and sec_key and self._store_sort==sec_key:
            seen_ids=set()
            raw_list=[]
            for feed_key in (sec_key,'-trending','-cut','-hot','-time'):
                for it in self._store_feeds.get(feed_key,[]):
                    if not isinstance(it,dict):continue
                    gid=it.get('gameId')
                    nt=self._norm_title(it.get('title',''))
                    if gid and gid not in seen_ids and nt in self._secondary_titles and nt not in self._owned_titles:
                        seen_ids.add(gid)
                        raw_list.append(it)
        else:
            raw_list=self._store_feeds.get(self._store_sort,[])
        out=[]
        for item in raw_list:
            if not isinstance(item,dict):continue
            nt=self._norm_title(item.get('title',''))
            in_lib=nt in self._owned_titles
            in_sec=nt in self._secondary_titles
            if not (_ADDON_STORE_TAB_AVAILABLE and sec_key and self._store_sort==sec_key):
                if self._exclude_owned and in_lib:continue
                if self._exclude_secondary and in_sec:continue
            if needle and needle not in (item.get('title','').casefold()) and needle not in (item.get('shopName','').casefold()):
                continue
            cls=classify_store_offer(item.get('shopName','Store'),item.get('dealUrl',''))
            if self._exclude_cd_keys and cls['isCdKeySite']:continue
            row=dict(item)
            row['normTitle']=nt
            row['inLibrary']=in_lib
            row['inSecondary']=in_sec
            if _ADDON_STORE_TAB_AVAILABLE and sec_key:
                row['in'+sec_key.capitalize()]=in_sec
            row['isCdKeySite']=cls['isCdKeySite']
            row['cdKeyBadge']=cls['cdKeyBadge']
            row['storeCategory']=cls['storeCategory']
            out.append(row)
        return out
    storeCatalogItems=Property('QVariantList',lambda s:s._visible_store_items(),notify=changed)
    excludeOwned=Property(bool,lambda s:s._exclude_owned,notify=changed)
    excludeSecondary=Property(bool,lambda s:s._exclude_secondary,notify=changed)
    excludeCdKeys=Property(bool,lambda s:s._exclude_cd_keys,notify=changed)
    storeSort=Property(str,lambda s:s._store_sort,notify=changed)
    ownedLibraryCount=Property(int,lambda s:len(s._owned_titles),notify=changed)
    secondaryLibraryCount=Property(int,lambda s:len(s._secondary_titles),notify=changed)
    secondaryTabAvailable=Property(bool,lambda s:bool(_ADDON_STORE_TAB_AVAILABLE),constant=True)
    secondaryTabKey=Property(str,lambda s:str(_ADDON_STORE_TAB_KEY),constant=True)
    secondaryTabLabel=Property(str,lambda s:str(_ADDON_STORE_TAB_LABEL),constant=True)
    secondaryTabHint=Property(str,lambda s:str(_ADDON_STORE_TAB_HINT),constant=True)
    secondaryFilterLabel=Property(str,lambda s:str(_ADDON_STORE_FILTER_LABEL),constant=True)
    @Slot(bool)
    def setExcludeOwned(self,enabled):
        val=bool(enabled)
        if val!=self._exclude_owned:
            self._exclude_owned=val;self._card_rev+=1;self.changed.emit()
    @Slot(bool)
    def setExcludeSecondary(self,enabled):
        val=bool(enabled)
        if val!=self._exclude_secondary:
            self._exclude_secondary=val;self._card_rev+=1;self.changed.emit()
    @Slot(bool)
    def setExcludeCdKeys(self,enabled):
        val=bool(enabled)
        if val!=self._exclude_cd_keys:
            self._exclude_cd_keys=val;self._card_rev+=1;self.changed.emit()
    @Slot(str)
    def setStoreFilter(self,text):
        val=(text or '')[:200]
        if val!=self._store_filter:
            self._store_filter=val;self._card_rev+=1;self.changed.emit()
    @Slot(str,bool,result=bool)
    @Slot(str,result=bool)
    def loadStoreCatalog(self,sort_mode='-trending',load_more=False):
        sec_key=_ADDON_STORE_TAB_KEY
        allowed_modes=('-trending','-cut','-hot','-time','price','title') + ((sec_key,) if _ADDON_STORE_TAB_AVAILABLE and sec_key else ())
        if sort_mode in allowed_modes:
            self._store_sort=sort_mode
        existing=self._store_feeds.get(self._store_sort,[])
        if existing and not load_more:
            self._card_rev+=1;self.changed.emit();return True
        if not self._use_disk_card_cache:
            self._card_rev+=1;self.changed.emit();return False
        if self._preload_worker is not None and self._preload_worker.isRunning():return False
        if not self._has_key():return False
        self._configured=True
        s_mode=self._store_sort
        offset=self._feed_offsets.get(s_mode,len(existing)) if load_more else 0
        country=self._country
        if _ADDON_STORE_TAB_AVAILABLE and sec_key and s_mode==sec_key:
            batch_titles=self._secondary_raw_titles[offset:offset+24]
            if not batch_titles:
                self._card_rev+=1;self.changed.emit();return True
            def fetch_secondary_feed():
                record=self._store.load('itad');client=self._client_factory(record['api_key'])
                cands_by_id={}
                for t in batch_titles:
                    try:res=client.search(t,results=3)
                    except Exception:continue
                    if not res:continue
                    nt=self._norm_title(t)
                    chosen=next((c for c in res if self._norm_title(c.get('title',''))==nt),res[0])
                    if chosen.get('id'):cands_by_id[chosen['id']]=chosen
                if not cands_by_id:
                    return {'ok':True,'sort':sec_key,'load_more':load_more,'offset':offset,'list':[],'nextOffset':offset+len(batch_titles)}
                prows=client.prices(list(cands_by_id.keys()),country=country,capacity=10,vouchers=self._include_vouchers)
                raw_items=[]
                for pr in prows:
                    gid=pr.get('id')
                    cand=cands_by_id.get(gid) or {}
                    deals=pr.get('deals') or []
                    if not gid or not deals:continue
                    best=min(deals,key=lambda d:float((d.get('price') or {}).get('amount',999999)))
                    deal_copy=dict(best)
                    hlow=(pr.get('historyLow') or {}).get('all')
                    if hlow and 'historyLow' not in deal_copy:deal_copy['historyLow']=hlow
                    raw_items.append({'id':gid,'title':cand.get('title',''),'assets':cand.get('assets') or {},'deal':deal_copy})
                return {'ok':True,'sort':sec_key,'load_more':load_more,'offset':offset,'list':raw_items,'nextOffset':offset+len(batch_titles)}
            self._preload_worker=_PriceJob(fetch_secondary_feed,self)
            self._preload_worker.result.connect(self._finish_store_feed)
            self._preload_worker.start()
            self.changed.emit()
            return True
        def fetch_feed():
            record=self._store.load('itad');client=self._client_factory(record['api_key'])
            data=client.deals(country=country,offset=offset,limit=160,sort=s_mode,nondeals=True)
            return {'ok':True,'sort':s_mode,'load_more':load_more,'offset':offset,'list':data.get('list',[]),'nextOffset':data.get('nextOffset',offset+160)}
        self._preload_worker=_PriceJob(fetch_feed,self)
        self._preload_worker.result.connect(self._finish_store_feed)
        self._preload_worker.start()
        self.changed.emit()
        return True
    def _finish_store_feed(self,result):
        if not isinstance(result,dict) or not result.get('ok'):return
        s_mode=result.get('sort',self._store_sort)
        current=list(self._store_feeds.get(s_mode,[])) if result.get('load_more') else []
        seen={x.get('gameId') for x in current if isinstance(x,dict)}
        for raw in result.get('list',[]):
            fmt=self._format_deal_item(raw)
            if fmt and fmt['gameId'] not in seen:
                seen.add(fmt['gameId'])
                current.append(fmt)
        self._store_feeds[s_mode]=current
        self._feed_offsets[s_mode]=int(result.get('nextOffset',len(current)))
        self._card_rev+=1
        self._save_feed_cache()
        self.changed.emit()
    @Slot(str,str,result=bool)
    def selectStoreGame(self,game_id,title):
        if not isinstance(game_id,str) or not game_id:
            return self.compareTitle(title) if title else False
        # First display the deal from the feed immediately if available, then fetch all cross-store offers for game_id
        for items in self._store_feeds.values():
            for it in (items or []):
                if isinstance(it,dict) and it.get('gameId')==game_id:
                    raw_deal=it.get('rawDeal')
                    if isinstance(raw_deal,dict) and raw_deal.get('price'):
                        self._selected_title=it.get('title',title)
                        self._source_rows=[{'id':game_id,'historyLow':{'all':raw_deal.get('historyLow')},'deals':[raw_deal]}]
                        self._message=f'Loading all store offers for {self._selected_title}…'
                        self.changed.emit()
                    break
        if self._busy or not self._has_key():return True
        self._configured=True
        return self._fetch_candidate({'id':game_id,'title':title or self._selected_title},force=False)
    priceCacheRevision=Property(int,lambda s:s._card_rev,notify=changed)
    cardPrices=Property('QVariantMap',lambda s:dict(s._card_prices),notify=changed)
    @Slot(str,int,result=str)
    @Slot(str,result=str)
    def priceBadgeForTitle(self,title,_rev=0):
        if not isinstance(title,str) or not title.strip():return ''
        nt=' '.join(title.casefold().split())
        entry=self._card_prices.get(nt)
        return str(entry.get('badge','')) if isinstance(entry,dict) else ''
    @Slot('QVariantList',result=bool)
    def preloadCatalogTitles(self,titles):
        if not self._use_disk_card_cache or not isinstance(titles,list):return False
        if self._preload_worker is not None and self._preload_worker.isRunning():return False
        missing=[]
        for t in titles:
            if not isinstance(t,str) or not t.strip():continue
            nt=' '.join(t.casefold().split())
            if nt not in self._card_prices and t.strip() not in missing:
                missing.append(t.strip())
            if len(missing)>=12:break
        if not missing or not self._has_key():return False
        self._configured=True
        country=self._country;vouchers=self._include_vouchers
        def batch_fetch():
            record=self._store.load('itad');client=self._client_factory(record['api_key'])
            id_map={}
            for t in missing:
                nt=' '.join(t.casefold().split())
                try:cands=client.search(t,results=4)
                except Exception:continue
                if not cands:continue
                exact=[c for c in cands if c.get('title','').strip().casefold()==t.casefold()]
                chosen=exact[0] if exact else cands[0]
                gid=chosen.get('id')
                if gid:id_map.setdefault(gid,[]).append((t,nt,chosen.get('title',t),cands))
            if not id_map:return {'ok':False}
            rows=client.prices(list(id_map.keys()),country=country,capacity=20,vouchers=vouchers)
            return {'ok':True,'id_map':id_map,'rows':rows}
        self._preload_worker=_PriceJob(batch_fetch,self)
        self._preload_worker.result.connect(self._finish_preload)
        self._preload_worker.start()
        return True
    def _finish_preload(self,result):
        if not isinstance(result,dict) or not result.get('ok'):return
        id_map=result.get('id_map') or {}
        rows=result.get('rows') or []
        by_id={r['id']:r for r in rows if isinstance(r,dict) and r.get('id')}
        updated=False
        for gid,tlist in id_map.items():
            row=by_id.get(gid)
            if not row:continue
            for orig_t,nt,ctitle,cands in tlist:
                self._record_card_price(orig_t,[row],cands,gid)
                if ctitle and ctitle.casefold()!=orig_t.casefold():
                    self._record_card_price(ctitle,[row],cands,gid)
                updated=True
        if updated:
            self._save_card_cache()
            self.changed.emit()
    busy=Property(bool,lambda s:s._busy,notify=changed)
    marketCountry=Property(str,lambda s:s._country,notify=changed)
    includeVouchers=Property(bool,lambda s:s._include_vouchers,notify=changed)
    marketOptions=Property('QVariantList',lambda s:[dict(row) for row in _MARKETS],notify=changed)
    keyConfigured=Property(bool,lambda s:s._configured,notify=changed)
    scrapeAvailable=Property(bool,lambda s:bool(s._hosts),notify=changed)
    scraperHosts=Property('QVariantList',lambda s:list(s._hosts),notify=changed)
    scrapedOffers=Property('QVariantList',lambda s:list(s._scraped_offers),notify=changed)
    scrapedTitle=Property(str,lambda s:s._scraped_title,notify=changed)
    message=Property(str,lambda s:s._message,notify=changed)
    candidates=Property('QVariantList',lambda s:list(s._candidates),notify=changed)
    sourceRows=Property('QVariantList',lambda s:list(s._source_rows),notify=changed)
    offers=Property('QVariantList',lambda s:[deal for row in s._source_rows if isinstance(row,dict) for deal in row.get('deals',[]) if isinstance(deal,dict)],notify=changed)
    comparisonOffers=Property('QVariantList',lambda s:s._build_comparison_offers(),notify=changed)
    hasCdKeyOffers=Property(bool,lambda s:any(o.get('isCdKeySite') for o in s._build_comparison_offers()) or bool(s._scraped_offers),notify=changed)
    cdKeyDisclaimer=Property(str,lambda s:_CDKEY_DISCLAIMER_BANNER,notify=changed)
    lowestOffers=Property('QVariantList',lambda s:s._lowest_offers(),notify=changed)
    def _build_comparison_offers(self):
        itad_deals=[deal for row in self._source_rows if isinstance(row,dict) for deal in row.get('deals',[]) if isinstance(deal,dict)]
        scraped_by_shop={}
        for sdeal in (self._auto_scraped_deals or []):
            if not isinstance(sdeal,dict):continue
            sname=((sdeal.get('shop') or {}).get('name') or '').strip().casefold()
            if sname and sname not in scraped_by_shop:
                scraped_by_shop[sname]=sdeal
        out=[]
        seen_shops=set()
        for deal in itad_deals:
            shop_name=((deal.get('shop') or {}).get('name') or 'Store').strip()
            skey=shop_name.casefold()
            seen_shops.add(skey)
            price=deal.get('price') or {}
            reg=deal.get('regular') or {}
            url=deal.get('url') or ''
            origin='ITAD API + Scraper Verified' if skey in scraped_by_shop else (deal.get('sourceOrigin') or 'ITAD API')
            cls=classify_store_offer(shop_name,url)
            if self._exclude_cd_keys and cls['isCdKeySite']:continue
            out.append({
                'shop':deal.get('shop') or {'name':shop_name},
                'shopName':shop_name,
                'price':price,
                'amount':price.get('amount','?'),
                'currency':price.get('currency','USD'),
                'regular':reg,
                'regularAmount':reg.get('amount') if isinstance(reg,dict) and reg.get('amount') is not None else '',
                'cut':deal.get('cut') or 0,
                'url':url,
                'sourceOrigin':origin,
                'storeCategory':cls['storeCategory'],
                'isCdKeySite':cls['isCdKeySite'],
                'cdKeyBadge':cls['cdKeyBadge'],
                'cdKeyDisclaimer':cls['cdKeyDisclaimer'],
            })
        for skey,sdeal in scraped_by_shop.items():
            if skey in seen_shops:continue
            shop_name=((sdeal.get('shop') or {}).get('name') or 'Store').strip()
            price=sdeal.get('price') or {}
            reg=sdeal.get('regular') or {}
            url=sdeal.get('url') or ''
            cls=classify_store_offer(shop_name,url)
            if self._exclude_cd_keys and cls['isCdKeySite']:continue
            out.append({
                'shop':sdeal.get('shop') or {'name':shop_name},
                'shopName':shop_name,
                'price':price,
                'amount':price.get('amount','?'),
                'currency':price.get('currency','USD'),
                'regular':reg,
                'regularAmount':reg.get('amount') if isinstance(reg,dict) and reg.get('amount') is not None else '',
                'cut':sdeal.get('cut') or 0,
                'url':url,
                'sourceOrigin':sdeal.get('sourceOrigin') or 'Multi-Store Scraper',
                'storeCategory':cls['storeCategory'],
                'isCdKeySite':cls['isCdKeySite'],
                'cdKeyBadge':cls['cdKeyBadge'],
                'cdKeyDisclaimer':cls['cdKeyDisclaimer'],
            })
        def _sort_key(item):
            try:return (0,float(item.get('amount',999999)))
            except Exception:return (1,999999.0)
        return sorted(out,key=_sort_key)
    def _scrape_multi_store_offers(self,title,country='US'):
        if not self._use_disk_card_cache or not isinstance(title,str) or not title.strip():
            return []
        from urllib.parse import quote
        from urllib.request import Request, urlopen
        nt=self._norm_title(title)
        deals=[]
        headers={'User-Agent':'WaystoneStoreComparison/1.0','Accept':'application/json'}
        # 1. Direct Steam Store Scraper
        try:
            s_url=f'https://store.steampowered.com/api/storesearch/?term={quote(title.strip())}&l=english&cc={country}'
            with urlopen(Request(s_url,headers=headers),timeout=3.5) as resp:
                s_data=json.loads(resp.read(262144).decode('utf-8','replace'))
            for item in (s_data.get('items') or [])[:5]:
                if not isinstance(item,dict):continue
                if self._norm_title(item.get('name',''))==nt and item.get('id'):
                    pr=item.get('price') or {}
                    final_cents=pr.get('final')
                    init_cents=pr.get('initial')
                    curr=pr.get('currency') or 'USD'
                    if isinstance(final_cents,(int,float)) and final_cents>=0:
                        amt=round(float(final_cents)/100.0,2)
                        reg=round(float(init_cents)/100.0,2) if isinstance(init_cents,(int,float)) and init_cents>0 else amt
                        cut=int(round((1.0-amt/reg)*100)) if reg>amt and reg>0 else 0
                        deals.append({
                            'shop':{'name':'Steam'},
                            'price':{'amount':amt,'currency':curr},
                            'regular':{'amount':reg,'currency':curr},
                            'cut':cut,
                            'url':f"https://store.steampowered.com/app/{item['id']}",
                            'sourceOrigin':'Direct Store Scraper (Steam)',
                        })
                    break
        except Exception:
            pass
        # 2. Direct GOG Catalog Scraper
        try:
            g_url=f'https://catalog.gog.com/v1/catalog?query=like:{quote(title.strip())}&limit=5&countryCode={country}&locale=en-US&currency=USD'
            with urlopen(Request(g_url,headers=headers),timeout=3.5) as resp:
                g_data=json.loads(resp.read(262144).decode('utf-8','replace'))
            for prod in (g_data.get('products') or [])[:5]:
                if not isinstance(prod,dict):continue
                if self._norm_title(prod.get('title',''))==nt:
                    pr=prod.get('price') or {}
                    f_money=pr.get('finalMoney') or {}
                    b_money=pr.get('baseMoney') or {}
                    amt=float(f_money.get('amount'))
                    reg=float(b_money.get('amount',amt))
                    curr=f_money.get('currency') or 'USD'
                    cut=int(round((1.0-amt/reg)*100)) if reg>amt and reg>0 else 0
                    slug=prod.get('slug') or ''
                    url=f'https://www.gog.com/en/game/{slug.replace("-","_")}' if slug else 'https://www.gog.com'
                    deals.append({
                        'shop':{'name':'GOG'},
                        'price':{'amount':round(amt,2),'currency':curr},
                        'regular':{'amount':round(reg,2),'currency':curr},
                        'cut':cut,
                        'url':url,
                        'sourceOrigin':'Direct Store Scraper (GOG)',
                    })
                    break
        except Exception:
            pass
        # 3. CheapShark Multi-Store Price Scraper (20+ PC digital & CD-key storefronts)
        try:
            c_url=f'https://www.cheapshark.com/api/1.0/games?title={quote(title.strip())}&limit=5'
            with urlopen(Request(c_url,headers=headers),timeout=3.5) as resp:
                c_games=json.loads(resp.read(262144).decode('utf-8','replace'))
            matched_gid=None
            for cg in (c_games if isinstance(c_games,list) else [])[:5]:
                if isinstance(cg,dict) and self._norm_title(cg.get('external',''))==nt and cg.get('gameID'):
                    matched_gid=cg['gameID']
                    break
            if matched_gid:
                d_url=f'https://www.cheapshark.com/api/1.0/games?id={quote(str(matched_gid))}'
                with urlopen(Request(d_url,headers=headers),timeout=3.5) as resp:
                    d_info=json.loads(resp.read(262144).decode('utf-8','replace'))
                for d in (d_info.get('deals') or [])[:15]:
                    if not isinstance(d,dict):continue
                    sid=str(d.get('storeID') or '')
                    if sid not in _CHEAPSHARK_STORES:continue
                    sname,sbase=_CHEAPSHARK_STORES[sid]
                    amt=round(float(d.get('price',0)),2)
                    reg=round(float(d.get('retailPrice',amt)),2)
                    cut=int(round(float(d.get('savings',0))))
                    did=d.get('dealID') or ''
                    url=f'https://www.cheapshark.com/redirect?dealID={quote(str(did))}' if did else sbase
                    deals.append({
                        'shop':{'name':sname},
                        'price':{'amount':amt,'currency':'USD'},
                        'regular':{'amount':reg,'currency':'USD'},
                        'cut':cut,
                        'url':url,
                        'sourceOrigin':'Multi-Store Scraper',
                    })
        except Exception:
            pass
        return deals
    def _lowest_offers(self):
        """Pick the lowest current deal independently per currency; retain source objects."""
        groups={}
        for row in self._source_rows:
            if not isinstance(row,dict):continue
            history=row.get('historyLow') if isinstance(row.get('historyLow'),dict) else {}
            for deal in row.get('deals',[]):
                if not isinstance(deal,dict):continue
                price=deal.get('price')
                if not isinstance(price,dict):continue
                currency=price.get('currency')
                try:amount=Decimal(str(price.get('amount')))
                except (InvalidOperation,ValueError):continue
                if not isinstance(currency,str) or not currency or not amount.is_finite() or amount<0:continue
                previous=groups.get(currency)
                if previous is None or amount<previous[0]:
                    groups[currency]=(amount,deal,history.get('all'))
        return [{'currency':currency,'deal':value[1],'history_low':value[2]} for currency,value in sorted(groups.items())]

    @Slot(str,result=bool)
    def saveApiKey(self,value):
        if self._store is None:return False
        if self._busy:
            self._message='Wait for the current price lookup to finish before replacing the key.'
            self.changed.emit();return False
        try:
            self._store.save('itad',{'kind':'itad_api_key','api_key':value})
            self._lookup_cache={}
            self._configured=True;self._message='ITAD API key stored in the desktop keyring.'
        except Exception:
            self._message='Could not save the ITAD API key. Unlock the desktop keyring and retry.'
            self.changed.emit();return False
        self.changed.emit();return True
    @Slot(result=bool)
    def clearApiKey(self):
        if self._store is None:return False
        if self._busy:
            self._message='Wait for the current price lookup to finish before removing the key.'
            self.changed.emit();return False
        try:self._store.clear('itad')
        except Exception:
            self._message='Could not remove the ITAD API key. Unlock the desktop keyring and retry.'
            self.changed.emit();return False
        self._configured=False;self._candidates=[];self._source_rows=[];self._scraped_offers=[];self._lookup_cache={}
        self._message='ITAD API key removed from the desktop keyring.';self.changed.emit();return True
    @Slot(str,bool,result=bool)
    def addScraperHost(self,host,termsConfirmed):
        if self._busy:
            self._message='Wait for the active page check to finish before editing scraper sources.';self.changed.emit();return False
        host=host.strip().lower().rstrip('.') if isinstance(host,str) else ''
        if not termsConfirmed:
            self._message='Review the retailer terms and robots.txt before enabling its host.'
            self.changed.emit();return False
        if not _HOST.fullmatch(host):
            self._message='Enter a hostname only, without scheme, port or path.'
            self.changed.emit();return False
        hosts=sorted(set(self._hosts)|{host});path=self._scraper_config_path
        try:new_scraper=self._scraper_factory(hosts)
        except Exception:
            self._message='Could not initialize the reviewed page source.';self.changed.emit();return False
        try:
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd,temp=tempfile.mkstemp(prefix='.store-scraper-',dir=path.parent)
            try:
                set_private_file_mode(fd, 0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as stream:
                    json.dump({'hosts':hosts},stream,separators=(',',':'));stream.flush();os.fsync(stream.fileno())
                os.replace(temp,path)
            except Exception:
                try:os.unlink(temp)
                except OSError:pass
                raise
        except Exception:
            self._message='Could not save retailer host allowlist.';self.changed.emit();return False
        self._hosts=hosts;self._scraper=new_scraper;self._message='Host enabled. Page checks still obey robots.txt and read only public HTML.'
        self.changed.emit();return True
    @Slot(str,result=bool)
    def removeScraperHost(self,host):
        if self._busy:
            self._message='Wait for the active page check to finish before editing scraper sources.'
            self.changed.emit();return False
        if host not in self._hosts:return False
        hosts=[item for item in self._hosts if item!=host];path=self._scraper_config_path
        try:
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd,temp=tempfile.mkstemp(prefix='.store-scraper-',dir=path.parent)
            try:
                set_private_file_mode(fd, 0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as stream:
                    json.dump({'hosts':hosts},stream,separators=(',',':'));stream.flush();os.fsync(stream.fileno())
                os.replace(temp,path)
            except Exception:
                try:os.unlink(temp)
                except OSError:pass
                raise
            self._scraper=self._scraper_factory(hosts) if hosts else None
        except Exception:
            self._message='Could not update retailer host allowlist.'
            self.changed.emit();return False
        self._hosts=hosts;self._message='Retailer host removed from Waystone scraper sources.'
        self.changed.emit();return True

    @Slot(str,str,result=bool)
    def scrapePublicPage(self,url,expectedTitle):
        if self._busy:return False
        if not self._hosts:
            self._message='Enable a retailer hostname in Store Accounts after reviewing its terms and robots.txt.'
            self.changed.emit();return False
        url=url.strip() if isinstance(url,str) else ''
        if not url:return False
        def lookup():
            host=(urlsplit(url).hostname or '').lower()
            # Reuse the configured scraper so rate limits and robots cache persist across lookups.
            rows=self._scraper.scrape(url,source_id=host,observed_at=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z'))
            title=rows[0]['title'] if rows else ''
            mismatch=bool(title and expectedTitle and title.strip().casefold()!=expectedTitle.strip().casefold())
            message=(f'Page title {title!r} differs from this game; do not treat it as a match. ' if mismatch else '')+f'Found {len(rows)} offer record(s); verify edition, region and checkout price.'
            return {'ok':True,'scraped_offers':rows,'candidates':[],'rows':[],'title':title,'scraped_title':title,'message':message}
        return self._start(lookup,'Checking robots.txt and public product page…')

    def _title_key(self,title,country=None,vouchers=None):
        country=country or self._country;vouchers=self._include_vouchers if vouchers is None else vouchers
        return "title:" + country + (":v:" if vouchers else ":n:") + " ".join(title.casefold().split())

    def _game_key(self,game_id,country=None,vouchers=None):
        country=country or self._country;vouchers=self._include_vouchers if vouchers is None else vouchers
        return "itad-game:" + country + (":v:" if vouchers else ":n:") + str(game_id)

    def _evict_game_cache(self, game_id,country=None,vouchers=None):
        """Invalidate every title/ID alias for one game within the active price dimensions."""
        game_id = str(game_id);country=country or self._country
        vouchers=self._include_vouchers if vouchers is None else vouchers
        for cache_key, cached in list(self._lookup_cache.items()):
            if cache_key == self._game_key(game_id,country,vouchers) or (cached.get("game_id") == game_id and cached.get("country",country)==country and cached.get("vouchers",vouchers)==vouchers):
                self._lookup_cache.pop(cache_key, None)

    def _show_cached(self,key):
        cached=self._lookup_cache.get(key)
        if not cached and self._use_disk_card_cache and key.startswith(f"title:{self._country}:"):
            nt=key.split(":",3)[-1]
            disk_entry=self._card_prices.get(nt)
            if isinstance(disk_entry,dict) and disk_entry.get("rows"):
                self._candidates=list(disk_entry.get("candidates",[]));self._source_rows=list(disk_entry["rows"])
                self._scraped_offers=[];self._scraped_title="";self._selected_title=disk_entry.get("title",nt)
                self._message=f"Showing cached ITAD offers for {self._country} pricing. Use Refresh for a live check."
                self.changed.emit()
                return True
        if not cached:return False
        if time.monotonic()-cached["stored_monotonic"]>900:
            self._lookup_cache.pop(key,None)
            return False
        self._candidates=list(cached["candidates"]);self._source_rows=list(cached["rows"])
        self._scraped_offers=[];self._scraped_title="";self._selected_title=cached["title"]
        self._message=f"Showing session-cached prices checked {cached['checked_at']} UTC. Refresh to update."
        self.changed.emit()
        return True

    @Slot(str,result=bool)
    def openOfferUrl(self,url):
        """Open only a well-formed HTTPS offer/page URL after an explicit user click."""
        if not isinstance(url,str) or not 1<=len(url)<=4096 or any(ord(c)<0x20 or c.isspace() for c in url):
            return False
        try:
            parts=urlsplit(url)
            if (parts.scheme.lower()!='https' or not parts.hostname or parts.username is not None
                    or parts.password is not None or parts.port not in (None,443)):
                return False
            parsed=QUrl(url,QUrl.ParsingMode.StrictMode)
            if not parsed.isValid() or parsed.scheme().lower()!='https' or not parsed.host():return False
            return bool(self._url_opener(parsed))
        except (ValueError,TypeError):
            return False
        except Exception:
            return False

    @Slot(str,result=bool)
    def compareTitle(self,title):
        return self._compare_title(title,force=False)

    @Slot(str,result=bool)
    def refreshPrices(self,title):
        return self._compare_title(title,force=True)

    def _compare_title(self,title,*,force):
        if self._busy:return False
        if not self._configured and not self._has_key():
            self._message='Add an ITAD API key in Store Accounts to compare prices.';self.changed.emit();return False
        self._configured=True
        if not isinstance(title,str) or not title.strip():return False
        country=self._country;vouchers=self._include_vouchers
        key=self._title_key(title,country,vouchers)
        if not force and self._show_cached(key):return True
        if force:
            old=self._lookup_cache.pop(key,None)
            if old and old.get("game_id"):self._evict_game_cache(old["game_id"],country,vouchers)
            # If the user is refreshing an ambiguous title, invalidate any selected
            # candidate caches from that result too; the next selection is fresh.
            for candidate in self._candidates:
                if isinstance(candidate,dict) and candidate.get("id"):
                    self._evict_game_cache(candidate["id"],country,vouchers)
        self._candidates=[]
        def lookup():
            record=self._store.load('itad');client=self._client_factory(record['api_key'])
            candidates=client.search(title.strip(),results=12)
            exact=[g for g in candidates if g.get('title','').strip().casefold()==title.strip().casefold()]
            if len(exact)==1:
                selected=exact[0]
                game_key=self._game_key(selected['id'],country,vouchers)
                rows=client.prices([selected['id']],country=country,capacity=30,vouchers=vouchers)
                auto_scraped=self._scrape_multi_store_offers(selected['title'],country)
                return {'ok':True,'cache_keys':[key,game_key],'game_id':selected['id'],'country':country,'vouchers':vouchers,'candidates':candidates,'rows':rows,'auto_scraped_deals':auto_scraped,'title':selected['title'],'message':f'Showing ITAD offers for {country} pricing.'}
            return {'ok':True,'candidates':candidates,'rows':[],'title':'','message':'Choose the matching edition below.' if candidates else 'ITAD did not find a matching title.'}
        return self._start(lookup,'Looking up store prices...')

    def _fetch_candidate(self,candidate,*,force):
        country=self._country;vouchers=self._include_vouchers
        game_key=self._game_key(candidate['id'],country,vouchers)
        if force:self._evict_game_cache(candidate['id'],country,vouchers)
        elif self._show_cached(game_key):return True
        selected=dict(candidate)
        def lookup():
            record=self._store.load('itad');client=self._client_factory(record['api_key'])
            rows=client.prices([selected['id']],country=country,capacity=30,vouchers=vouchers)
            auto_scraped=self._scrape_multi_store_offers(selected.get('title',''),country)
            return {'ok':True,'cache_keys':[game_key],'game_id':selected['id'],'country':country,'vouchers':vouchers,'candidates':self._candidates,'rows':rows,'auto_scraped_deals':auto_scraped,'title':selected['title'],'message':f'Showing ITAD offers for {country} pricing.'}
        return self._start(lookup,'Loading current store offers...')

    @Slot(str,result=bool)
    def selectCandidate(self,gameId):
        if self._busy or not self._configured:return False
        candidate=next((g for g in self._candidates if g.get('id')==gameId),None)
        return False if candidate is None else self._fetch_candidate(candidate,force=False)

    @Slot(str,result=bool)
    def refreshCandidate(self,gameId):
        if self._busy or not self._configured:return False
        candidate=next((g for g in self._candidates if g.get('id')==gameId),None)
        return False if candidate is None else self._fetch_candidate(candidate,force=True)
    def _start(self,factory,message):
        if self._worker is not None:
            if self._worker.isRunning():self._worker.wait()
            self._worker.deleteLater()
        # Never leave another game's prices visible while a new lookup is pending.
        self._source_rows=[];self._scraped_offers=[];self._scraped_title='';self._auto_scraped_deals=[]
        self._busy=True;self._message=message;self._worker=_PriceJob(factory,self)
        self._worker.result.connect(self._finish);self._worker.start();self.changed.emit();return True
    def _finish(self,result):
        self._busy=False;self._message=result.get('message','ITAD lookup failed.')
        self._source_rows=[];self._scraped_offers=[];self._scraped_title='';self._auto_scraped_deals=[]
        if result.get('ok'):
            self._candidates=result.get('candidates',[]);self._source_rows=result.get('rows',[]);self._scraped_offers=result.get('scraped_offers',[]);self._scraped_title=result.get('scraped_title','');self._auto_scraped_deals=result.get('auto_scraped_deals',[]);self._selected_title=result.get('title','')
            cache_keys=result.get('cache_keys',[])
            if cache_keys and result.get('rows'):
                checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
                cached={'stored_monotonic':time.monotonic(),'checked_at':checked_at,'candidates':list(self._candidates),'rows':list(self._source_rows),'title':self._selected_title,'game_id':result.get('game_id'),'country':result.get('country',self._country),'vouchers':result.get('vouchers',self._include_vouchers)}
                for cache_key in cache_keys:self._lookup_cache[cache_key]=dict(cached)
                if self._selected_title:
                    self._record_card_price(self._selected_title,self._source_rows,self._candidates,result.get('game_id'))
                    self._save_card_cache()
        self.changed.emit()
    def shutdown(self):
        pworker=self._preload_worker
        if pworker is not None and pworker.isRunning():pworker.wait(5000)
        if pworker is not None and not pworker.isRunning():pworker.deleteLater()
        self._preload_worker=None
        worker=self._worker
        if worker is not None and worker.isRunning():worker.wait(10000)
        if worker is not None and not worker.isRunning():worker.deleteLater()
        self._worker=None
