import time
from PySide6.QtGui import QGuiApplication
from price_comparison_view import PriceComparisonView

class Store:
    def __init__(self):self.value=None
    def load(self,name):assert name=='itad';return self.value
    def save(self,name,value):assert name=='itad';self.value=dict(value)
    def clear(self,name):assert name=='itad';self.value=None

class Client:
    def __init__(self,key):assert key=='fixture-secret-key'
    def search(self,title,results=10):
        return [{'id':'12345678-1234-1234-1234-123456789abc','title':'Sea of Thieves'}]
    def prices(self,ids,country='US',capacity=30,vouchers=None):
        return [{'id':ids[0],'deals':[{'shop':{'name':'Steam'},'price':{'amount':12.5,'currency':'USD'},'url':'https://itad.link/fixture'}]}]


class FailingClient(Client):
    def prices(self,ids,country='US',capacity=30,vouchers=None):
        raise RuntimeError('fixture failure')

def wait(view):
    app=QGuiApplication.instance() or QGuiApplication([])
    deadline=time.monotonic()+2
    while view.busy and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    app.processEvents()
    assert not view.busy

def test_offer_url_opener_allows_only_https_without_credentials_or_custom_ports():
    opened=[]
    view=PriceComparisonView(Store(),url_opener=lambda url: opened.append(url.toString()) or True)
    assert view.openOfferUrl('https://store.example/game?ref=waystone')
    assert opened==['https://store.example/game?ref=waystone']
    for url in ('file:///etc/passwd','javascript:alert(1)','http://store.example/','https://user:pass@store.example/','https://store.example:444/','https://store.example/\n'):
        assert not view.openOfferUrl(url),url
    assert len(opened)==1
    view.shutdown()


def test_personal_itad_flow_keeps_key_out_of_qml_and_returns_source_offers():
    store=Store();view=PriceComparisonView(store,client_factory=Client)
    assert not view.keyConfigured
    assert view.saveApiKey('fixture-secret-key')
    assert view.keyConfigured
    assert 'fixture-secret-key' not in view.message
    assert view.compareTitle('Sea of Thieves')
    wait(view)
    assert view.message=='Showing ITAD offers for US pricing.'
    assert view.offers==[{'shop':{'name':'Steam'},'price':{'amount':12.5,'currency':'USD'},'url':'https://itad.link/fixture'}]
    assert 'fixture-secret-key' not in repr(view.offers)
    view.clearApiKey()
    assert not view.keyConfigured and not view.offers
    view.shutdown()



def test_new_or_failed_lookup_never_leaves_old_game_prices_visible():
    store=Store();view=PriceComparisonView(store,client_factory=Client)
    view.saveApiKey('fixture-secret-key')
    assert view.compareTitle('Sea of Thieves')
    wait(view)
    assert view.offers
    view._client_factory=FailingClient
    assert view.refreshPrices('Sea of Thieves')
    assert view.offers==[]
    wait(view)
    assert view.offers==[]
    assert view.compareTitle('Sea of Thieves')
    wait(view)
    assert view.offers==[]
    view.shutdown()


def test_failed_candidate_refresh_evicts_title_alias_before_next_compare():
    class ToggleClient(Client):
        fail_prices=False
        searches=0
        def search(self,title,results=10):
            self.searches+=1
            return super().search(title,results)
        def prices(self,ids,country='US',capacity=30,vouchers=None):
            if self.fail_prices:raise RuntimeError('fixture failure')
            return super().prices(ids,country,capacity,vouchers)
    client=ToggleClient('fixture-secret-key')
    view=PriceComparisonView(Store(),client_factory=lambda key:client)
    view.saveApiKey('fixture-secret-key')
    assert view.compareTitle('Sea of Thieves');wait(view)
    game_id=view.candidates[0]['id']
    assert view.offers and len(view._lookup_cache)==2
    client.fail_prices=True
    assert view.refreshCandidate(game_id);wait(view)
    assert not view.offers
    # A later title query must not revive the title alias from before the failed ID refresh.
    assert view.compareTitle('Sea of Thieves');wait(view)
    assert client.searches==2
    assert not view.offers
    assert not view._lookup_cache
    view.shutdown()


def test_preexisting_keyring_key_is_discovered_on_explicit_lookup_only():
    store=Store();store.value={'kind':'itad_api_key','api_key':'fixture-secret-key'}
    view=PriceComparisonView(store,client_factory=Client)
    assert store.value is not None
    assert not view.keyConfigured
    # App construction itself must not read the keyring.
    assert view.compareTitle('Sea of Thieves')
    wait(view)
    assert view.keyConfigured and view.offers
    view.shutdown()


def test_forget_is_refused_while_lookup_is_active():
    import threading
    entered=threading.Event();release=threading.Event()
    class SlowClient(Client):
        def search(self,title,results=10):
            entered.set();release.wait(1)
            return super().search(title,results)
    store=Store();view=PriceComparisonView(store,client_factory=SlowClient)
    view.saveApiKey('fixture-secret-key')
    assert view.compareTitle('Sea of Thieves')
    assert entered.wait(.5)
    assert not view.clearApiKey()
    assert view.keyConfigured
    release.set();wait(view);view.shutdown()


def test_scraper_host_requires_explicit_terms_confirmation_and_is_private(tmp_path):
    view=PriceComparisonView(Store(),scraper_config_path=tmp_path/'allowlist.json')
    assert not view.addScraperHost('shop.example',False)
    assert not view.scrapeAvailable
    assert not view.addScraperHost('https://shop.example/path',True)
    assert view.addScraperHost('Shop.Example',True)
    assert view.scrapeAvailable
    assert view.scraperHosts==['shop.example']
    assert (tmp_path/'allowlist.json').stat().st_mode & 0o777 == 0o600
    view.shutdown()


def test_scraper_public_page_uses_reviewed_host_and_reports_title_mismatch(tmp_path):
    class FakeScraper:
        def __init__(self,hosts):assert hosts==['shop.example']
        def scrape(self,url,source_id,observed_at):
            assert url=='https://shop.example/game'
            assert source_id=='shop.example'
            assert observed_at.endswith('Z')
            return [{'title':'Other Game','source_id':source_id,'seller':'Seller','price':'3.00','currency':'USD','product_page_url':url,'verification':'unverified'}]
    cfg=tmp_path/'allowlist.json';cfg.write_text('{"hosts":["shop.example"]}')
    view=PriceComparisonView(Store(),scraper_factory=FakeScraper,scraper_config_path=cfg)
    assert view.scrapePublicPage('https://shop.example/game','Expected Game')
    wait(view)
    assert view.scrapedTitle=='Other Game'
    assert 'differs from this game' in view.message
    assert view.scrapedOffers[0]['price']=='3.00'
    view.shutdown()


def test_public_scraper_instance_is_reused_and_host_can_be_removed(tmp_path):
    instances=[]
    class FakeScraper:
        def __init__(self,hosts):
            self.hosts=list(hosts);self.calls=0;instances.append(self)
        def scrape(self,url,source_id,observed_at):
            self.calls+=1
            return [{'title':'Fixture','source_id':source_id,'seller':None,'price':'1','currency':'USD','product_page_url':url,'verification':'unverified'}]
    cfg=tmp_path/'allowlist.json'
    view=PriceComparisonView(Store(),scraper_factory=FakeScraper,scraper_config_path=cfg)
    assert view.addScraperHost('shop.example',True)
    configured=view._scraper
    assert configured is instances[-1]
    assert view.scrapePublicPage('https://shop.example/game','Fixture')
    wait(view)
    assert configured.calls==1
    assert view.removeScraperHost('shop.example')
    assert view.scraperHosts==[]
    assert not view.scrapeAvailable
    assert view._scraper is None
    import json
    assert json.loads(cfg.read_text())=={'hosts':[]}
    view.shutdown()


def test_itad_results_reuse_short_session_cache_with_explicit_force_refresh():
    class CountingClient(Client):
        def __init__(self,key):
            super().__init__(key);self.search_calls=0;self.price_calls=0
        def search(self,title,results=10):
            self.search_calls+=1
            return super().search(title,results)
        def prices(self,ids,country='US',capacity=30,vouchers=None):
            self.price_calls+=1
            return super().prices(ids,country,capacity,vouchers)
    client=CountingClient('fixture-secret-key')
    store=Store();view=PriceComparisonView(store,client_factory=lambda key:client)
    view.saveApiKey('fixture-secret-key')
    assert view.compareTitle('Sea of Thieves')
    wait(view)
    assert (client.search_calls,client.price_calls)==(1,1)
    assert view.compareTitle('  sea   of thieves  ')
    assert (client.search_calls,client.price_calls)==(1,1)
    assert 'session-cached' in view.message
    assert view.refreshPrices('Sea of Thieves')
    wait(view)
    assert (client.search_calls,client.price_calls)==(2,2)
    assert view.offers
    view.shutdown()


def test_price_market_persists_and_cache_isolated_by_country(tmp_path):
    countries=[]
    class MarketClient(Client):
        def search(self,title,results=10):
            return super().search(title,results)
        def prices(self,ids,country='US',capacity=30,vouchers=None):
            countries.append(country)
            return [{'id':ids[0],'deals':[{'shop':{'name':country},'price':{'amount':12,'currency':country},'url':'https://fixture.test/'+country}]}]
    market_path=tmp_path/'store-market.json'
    view=PriceComparisonView(Store(),client_factory=MarketClient,market_config_path=market_path)
    view.saveApiKey('fixture-secret-key')
    assert view.marketCountry=='US'
    assert view.compareTitle('Sea of Thieves');wait(view)
    assert countries==['US']
    assert view._title_key('Sea of Thieves')=='title:US:n:sea of thieves'
    assert view.setMarketCountry('CA')
    assert not view.offers
    assert market_path.stat().st_mode & 0o777==0o600
    assert view.compareTitle('Sea of Thieves');wait(view)
    assert countries==['US','CA']
    assert view.offers[0]['price']['currency']=='CA'
    restored=PriceComparisonView(Store(),market_config_path=market_path)
    assert restored.marketCountry=='CA'
    assert restored.includeVouchers is False
    restored.shutdown()
    assert view.setMarketCountry('US')
    assert view.compareTitle('Sea of Thieves')
    assert 'session-cached' in view.message
    assert countries==['US','CA']
    assert not view.setMarketCountry('ZZ')
    view.shutdown()


def test_voucher_preference_persists_and_isolated_in_cache(tmp_path):
    requests=[]
    class VoucherClient(Client):
        def prices(self,ids,country='US',capacity=30,vouchers=None):
            requests.append((country,vouchers))
            return [{'id':ids[0],'deals':[{'shop':{'name':'Fixture'},'price':{'amount':1,'currency':'USD'},'url':'https://fixture.test/offer'}]}]
    prefs=tmp_path/'market.json'
    view=PriceComparisonView(Store(),client_factory=VoucherClient,market_config_path=prefs)
    view.saveApiKey('fixture-secret-key')
    assert view.compareTitle('Sea of Thieves');wait(view)
    assert requests==[('US',False)]
    assert view.setIncludeVouchers(True)
    assert not view.offers
    assert view.compareTitle('Sea of Thieves');wait(view)
    assert requests==[('US',False),('US',True)]
    assert view._title_key('Sea of Thieves')=='title:US:v:sea of thieves'
    restored=PriceComparisonView(Store(),market_config_path=prefs)
    assert restored.includeVouchers is True
    assert restored.marketCountry=='US'
    restored.shutdown()
    assert view.setIncludeVouchers(False)
    assert view.compareTitle('Sea of Thieves') and 'session-cached' in view.message
    assert prefs.stat().st_mode & 0o777==0o600
    view.shutdown()


def test_invalid_or_unavailable_market_preference_falls_back_to_us(tmp_path):
    import json
    path=tmp_path/'market.json'
    path.write_text(json.dumps({'country':'ZZ'}))
    view=PriceComparisonView(Store(),market_config_path=path)
    assert view.marketCountry=='US'
    view._busy=True
    assert not view.setMarketCountry('CA')
    assert view.marketCountry=='US'
    view._busy=False;view.shutdown()


def test_price_cache_expires_after_fifteen_minutes():
    class CountingClient(Client):
        calls=0
        def search(self,*args,**kwargs):
            self.calls+=1
            return super().search(*args,**kwargs)
    client=CountingClient('fixture-secret-key')
    store=Store();view=PriceComparisonView(store,client_factory=lambda key:client)
    view.saveApiKey('fixture-secret-key')
    assert view.compareTitle('Sea of Thieves');wait(view)
    assert client.calls==1
    entry=view._lookup_cache['title:US:n:sea of thieves']
    entry['stored_monotonic']-=901
    assert view.compareTitle('Sea of Thieves');wait(view)
    assert client.calls==2
    view.shutdown()


def test_key_replacement_is_rejected_during_lookup_and_does_not_poison_cache():
    import threading
    entered=threading.Event();release=threading.Event()
    class SlowClient(Client):
        def search(self,title,results=10):
            entered.set();release.wait(1)
            return super().search(title,results)
    store=Store();view=PriceComparisonView(store,client_factory=SlowClient)
    view.saveApiKey('fixture-secret-key')
    assert view.compareTitle('Sea of Thieves')
    assert entered.wait(.5)
    assert not view.saveApiKey('replacement-key')
    assert store.value['api_key']=='fixture-secret-key'
    release.set();wait(view)
    assert view.offers
    assert view._lookup_cache['title:US:n:sea of thieves']
    view.shutdown()


def test_lowest_offer_summary_compares_only_within_currency_and_keeps_source_fields():
    store=Store();view=PriceComparisonView(store,client_factory=Client)
    view._source_rows=[{
        'id':'game-id',
        'historyLow':{'all':{'amount':2.50,'currency':'USD'}},
        'deals':[
            {'shop':{'name':'Store A'},'price':{'amount':19.99,'currency':'USD'},'url':'https://example.test/a'},
            {'shop':{'name':'Store B'},'price':{'amount':9.99,'currency':'USD'},'url':'https://example.test/b','cut':50},
            {'shop':{'name':'Store C'},'price':{'amount':500,'currency':'JPY'},'url':'https://example.test/c'},
            {'shop':{'name':'Invalid'},'price':{'amount':'NaN','currency':'USD'},'url':'https://example.test/nan'},
        ],
    }]
    summary=view.lowestOffers
    assert [row['currency'] for row in summary]==['JPY','USD']
    usd=summary[1]
    assert usd['deal'] is view._source_rows[0]['deals'][1]
    assert usd['deal']['url']=='https://example.test/b'
    assert usd['deal']['cut']==50
    assert usd['history_low']=={'amount':2.50,'currency':'USD'}
    view.shutdown()


def test_ambiguous_same_title_candidates_have_independent_id_caches_and_refresh():
    calls=[]
    class AmbiguousClient:
        def __init__(self,key):assert key=='fixture-secret-key'
        def search(self,title,results=10):
            return [
                {'id':'11111111-1111-1111-1111-111111111111','title':'Same Game'},
                {'id':'22222222-2222-2222-2222-222222222222','title':'Same Game'},
            ]
        def prices(self,ids,country='US',capacity=30,vouchers=None):
            calls.append(ids[0])
            amount={'11111111-1111-1111-1111-111111111111':10,'22222222-2222-2222-2222-222222222222':3}[ids[0]]
            return [{'id':ids[0],'deals':[{'shop':{'name':ids[0][:3]},'price':{'amount':amount,'currency':'USD'},'url':'https://fixture.test/'+ids[0]}]}]
    view=PriceComparisonView(Store(),client_factory=AmbiguousClient)
    view.saveApiKey('fixture-secret-key')
    assert view.compareTitle('Same Game');wait(view)
    assert len(view.candidates)==2 and view.offers==[]
    id1='11111111-1111-1111-1111-111111111111';id2='22222222-2222-2222-2222-222222222222'
    assert view.selectCandidate(id1);wait(view)
    assert view.offers[0]['price']['amount']==10
    assert view.selectCandidate(id2);wait(view)
    assert view.offers[0]['price']['amount']==3
    assert view.selectCandidate(id1)
    assert view.offers[0]['price']['amount']==10
    assert calls==[id1,id2]
    assert view.refreshCandidate(id1);wait(view)
    assert calls==[id1,id2,id1]
    view.shutdown()


def test_title_refresh_invalidates_exact_game_id_cache_before_request():
    calls=[]
    class CountingClient(Client):
        def search(self,title,results=10):
            calls.append('search')
            return super().search(title,results)
        def prices(self,ids,country='US',capacity=30,vouchers=None):
            calls.append('prices')
            return super().prices(ids,country,capacity,vouchers)
    view=PriceComparisonView(Store(),client_factory=CountingClient)
    view.saveApiKey('fixture-secret-key')
    assert view.compareTitle('Sea of Thieves');wait(view)
    assert calls==['search','prices']
    assert view.refreshPrices('Sea of Thieves');wait(view)
    assert calls==['search','prices','search','prices']
    assert len(view._lookup_cache)==2
    view.shutdown()


def test_price_comparison_qml_loads_with_cache_and_offer_controls():
    from pathlib import Path
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent,QQmlEngine
    app=QGuiApplication.instance() or QGuiApplication([])
    app.processEvents()
    view=PriceComparisonView(Store(),client_factory=Client)
    engine=QQmlEngine()
    app_dir=Path(__file__).parent
    engine.addImportPath(str(app_dir))
    engine.rootContext().setContextProperty('priceComparison',view)
    component=QQmlComponent(engine,QUrl.fromLocalFile(str(app_dir/'PriceComparisonPanel.qml')))
    if component.isError():
        raise AssertionError('PriceComparisonPanel.qml failed to load: '+ '\n'.join(e.toString() for e in component.errors()))
    item=component.create()
    assert item is not None, '\n'.join(e.toString() for e in component.errors())
    item.setProperty('gameTitle','Fixture Game')
    app.processEvents()
    # Validate named Controls through QObject traversal without assuming their concrete QML classes.
    all_names=set();controls={}
    stack=[item]
    while stack:
        current=stack.pop()
        name=current.objectName()
        if name:
            all_names.add(name);controls[name]=current
        stack.extend(current.children())
    assert 'compareStorePrices' in all_names
    assert 'refreshStorePrices' in all_names
    assert 'priceMarketSelector' in all_names
    assert 'includeVoucherPrices' in all_names
    # A saved key is loaded only after the user asks; buttons must remain clickable
    # while keyConfigured is still false so that saved-key discovery is reachable.
    assert not view.keyConfigured
    assert controls['compareStorePrices'].property('enabled')
    assert controls['refreshStorePrices'].property('enabled')
    view.shutdown()
    engine.deleteLater()
    item.deleteLater()


def test_store_catalog_hides_bought_and_secondary_by_default():
    view=PriceComparisonView(Store(),client_factory=Client)
    view._store_feeds={
        '-trending':[
            {'gameId':'g1','title':'Owned On Steam','priceBadge':'$9.99','shopName':'Steam'},
            {'gameId':'g2','title':'Secondary Catalog Title','priceBadge':'$14.99 (-50%)','shopName':'Fanatical'},
            {'gameId':'g3','title':'Brand New Unowned Game','priceBadge':'$19.99 (-20%)','shopName':'GOG'},
        ]
    }
    view.setLibraryOwnership(['Owned On Steam'],['Secondary Catalog Title v12345'])
    assert view.excludeOwned is True
    assert view.excludeSecondary is True
    assert view.ownedLibraryCount==1
    assert view.secondaryLibraryCount==1
    visible=view.storeCatalogItems
    assert [x['gameId'] for x in visible]==['g3']
    view.setExcludeSecondary(False)
    assert [x['gameId'] for x in view.storeCatalogItems]==['g2','g3']
    view.shutdown()


def test_comparison_offers_combines_itad_and_scraper_and_attaches_cdkey_disclaimers():
    view=PriceComparisonView(Store(),client_factory=Client)
    view._source_rows=[{
        'id':'game-1',
        'deals':[
            {'shop':{'name':'Steam'},'price':{'amount':29.99,'currency':'USD'},'url':'https://store.steampowered.com/app/10'},
            {'shop':{'name':'Fanatical'},'price':{'amount':19.99,'currency':'USD'},'cut':33,'url':'https://www.fanatical.com/en/game/test'},
        ]
    }]
    view._auto_scraped_deals=[
        {'shop':{'name':'Steam'},'price':{'amount':29.99,'currency':'USD'},'url':'https://store.steampowered.com/app/10','sourceOrigin':'Direct Store Scraper (Steam)'},
        {'shop':{'name':'CDKeys'},'price':{'amount':14.49,'currency':'USD'},'cut':52,'url':'https://www.cdkeys.com/pc/test','sourceOrigin':'Multi-Store Scraper'},
    ]
    comp=view.comparisonOffers
    assert [c['shopName'] for c in comp]==['CDKeys','Fanatical','Steam']
    assert comp[0]['storeCategory']=='cdkey_marketplace'
    assert comp[0]['isCdKeySite'] is True
    assert 'CD-Key Marketplace Disclaimer' in comp[0]['cdKeyDisclaimer']
    assert comp[1]['storeCategory']=='cdkey_retailer'
    assert comp[1]['isCdKeySite'] is True
    assert 'CD-Key Site Disclaimer' in comp[1]['cdKeyDisclaimer']
    assert comp[2]['storeCategory']=='official_store'
    assert comp[2]['isCdKeySite'] is False
    assert comp[2]['sourceOrigin']=='ITAD API + Scraper Verified'
    assert view.hasCdKeyOffers is True
    assert 'CD-Key & Third-Party Key Site Disclaimer' in view.cdKeyDisclaimer
    view.shutdown()


def test_exclude_cd_keys_filter(tmp_path):
    view = PriceComparisonView(Store())
    view._source_rows = [{
        'id': 'game-1',
        'deals': [
            {'shop': {'name': 'Steam'}, 'price': {'amount': 29.99, 'currency': 'USD'}, 'url': 'https://store.steampowered.com/app/10'},
            {'shop': {'name': 'Fanatical'}, 'price': {'amount': 19.99, 'currency': 'USD'}, 'cut': 33, 'url': 'https://www.fanatical.com/en/game/test'},
        ]
    }]
    view._auto_scraped_deals = [
        {'shop': {'name': 'CDKeys'}, 'price': {'amount': 14.49, 'currency': 'USD'}, 'cut': 52, 'url': 'https://www.cdkeys.com/pc/test'},
    ]
    assert view.excludeCdKeys is False
    assert len(view.comparisonOffers) == 3
    view.setExcludeCdKeys(True)
    assert view.excludeCdKeys is True
    assert [c['shopName'] for c in view.comparisonOffers] == ['Steam']
    view.setExcludeCdKeys(False)
    assert len(view.comparisonOffers) == 3
    view.shutdown()


def test_store_cover_only_accepts_https_urls():
    from price_comparison_view import _https_image
    assert _https_image("https://assets.isthereanydeal.com/x/boxart.jpg") == "https://assets.isthereanydeal.com/x/boxart.jpg"
    for bad in ("http://cdn.example/x.jpg", "file:///etc/passwd", "data:image/png;base64,AAAA", "/tmp/x.png",
                "https://user:pw@cdn.example/x.jpg", "", None, 42):
        assert _https_image(bad) == ""
