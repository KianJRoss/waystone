"""Actual bottom buttons -> aggregate cards -> scoped records -> back; no launches/scans."""
import hashlib,json,os,tempfile
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QObject,QMetaObject,Qt,QCoreApplication,QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session
from native_services.rom_inventory import RomRecord

def fixture_files(root):
    games=[{'id':'steam-game','title':'A game','variants':[{'source':'Steam','source_id':'steam-guid','provider_game_id':'42','platform':'PC','status':'unknown'}]},
           {'id':'gc-game','title':'GC catalog game','variants':[{'source':'Emulation','source_id':'gc-guid','platform':'Nintendo GameCube','status':'unknown','source_context':{'provider':'Emulation'}}]},
           {'id':'switch-game','title':'Switch catalog game','variants':[{'source':'Emulation','source_id':'sw-guid','platform':'Nintendo Switch','status':'unknown','source_context':{'provider':'Emulation'}}]}]
    catalog=root/'catalog.json';catalog.write_text(json.dumps({'schema_version':2,'catalog_kind':'playnite-display-selected-copy-v1','games':games}))
    rows=[]
    for provider,product in [('steam','42'),('steam','99'),('heroic-epic','epic-fixture')]:
        variant=hashlib.sha256((provider+product).encode()).hexdigest();matched=product=='42'
        rows.append(dict(provider=provider,product_id=product,record_key=hashlib.sha256(('r'+variant).encode()).hexdigest(),variant_key=variant,
            state='installed',title='Private individual '+product,platform='PC',evidence=['fixture'],
            source_context={'host':'FixtureHost','instance':'fixture','installation_id':'fixture'},
            canonical_game_ids=['steam-game'] if matched else [],join_state='matched' if matched else 'unmatched',
            join_evidence=[{'source':'Steam' if provider=='steam' else 'Epic','provider_game_id':product}],
            current_claims={'installation':'installed','client_only':False,'gameplay_tested':False,'verification_scope':'fixture'},
            launch_intent={'provider':provider,'variant_key':variant,'dry_run':True,'execution_supported':False}))
    inventory=root/'inventory.json';inventory.write_text(json.dumps(dict(schema_version=1,inventory_kind='native-connections-readonly',host='FixtureHost',observed_at='2026-11-15T00:00:00Z',
        catalog_sha256=hashlib.sha256(catalog.read_bytes()).hexdigest(),execution_supported=False,connections=rows,
        canonical_links={'steam-game':[rows[0]['variant_key']]},unmatched_native_variant_keys=[r['variant_key'] for r in rows[1:]],ambiguous_native_variant_keys=[])))
    store=root/'store.json';store.write_text(json.dumps([{'Name':'Private store game','Folder':'package','RemotePath':'PC/package'}]))
    return catalog,inventory,store

def card(overview,key):
    grid=overview.findChild(QObject,'overviewCards')
    item=grid.property('contentItem')
    def walk(node):
        if node.objectName()=='overviewCard_'+key:return node
        for child in node.childItems():
            found=walk(child)
            if found is not None:return found
    found=walk(item)
    assert found is not None, (key,grid.property('count'),grid.property('width'),grid.property('height'))
    return found

def click(obj):
    assert obj is not None
    assert QMetaObject.invokeMethod(obj,'clicked',Qt.DirectConnection)
    QTest.qWait(60)

def test_bottom_menus_open_aggregates_and_drill_only_after_group_choice():
    app=QGuiApplication.instance() or QGuiApplication([])
    with tempfile.TemporaryDirectory() as folder:
        catalog,inventory,store=fixture_files(Path(folder));spawns=[]
        engine,model,root,warnings=create_session(app,headless=True,catalog=catalog,connections=inventory,store_catalog=store,spawn_fn=lambda *a,**kw:spawns.append(a))
        try:
            root.setVisible(True)
            engine.launch_view._rom_records=[RomRecord('gc-rom','Nintendo GameCube','/fixture/gc.iso','Private GC.iso',1,1,'.iso',emulator_name='Dolphin'),
                RomRecord('switch-rom','Nintendo Switch','/fixture/sw.nsp','Private SW.nsp',1,1,'.nsp',emulator_name='Eden')]
            engine.launch_view.romsChanged.emit();QTest.qWait(80)
            overview=root.findChild(QObject,'menuOverviewDashboard')
            con_detail=root.findChild(QObject,'nativeConnectionsDialog');rom_detail=root.findChild(QObject,'romInventoryDialog');cat_detail=root.findChild(QObject,'nativeProvidersDialog')
            before=model.totalCount;selected=root.property('selectedId')
            click(root.findChild(QObject,'settingsLinuxConnections'))
            assert overview.property('visible') and not con_detail.property('visible')
            assert overview.property('section')=='connections'
            assert len(engine.menu_overviews.connections)==2
            assert sum(c['count'] for c in engine.menu_overviews.connections)==3
            assert '2 installed locally' in next(c['summary'] for c in engine.menu_overviews.connections if c['key']=='steam')
            assert 'Private individual' not in json.dumps(engine.menu_overviews.connections)
            click(card(overview,'steam'))
            assert con_detail.property('visible') and not overview.property('visible')
            assert len(engine.connection_view.rows)==2
            click(con_detail.findChild(QObject,'connectionOverviewBack'))
            assert overview.property('visible') and len(engine.menu_overviews.connections)==2
            overview.close()
            click(root.findChild(QObject,'tabRoms'))
            assert overview.property('visible') and not rom_detail.property('visible')
            assert len(engine.menu_overviews.roms)==2
            scan=overview.findChild(QObject,'overviewPrimaryAction');settings=overview.findChild(QObject,'overviewEmulatorSettings')
            assert scan.property('width')>150
            assert settings.property('x')>=scan.property('x')+scan.property('width')
            overview.findChild(QObject,'overviewGroupSearch').setProperty('text','GameCube');app.processEvents()
            assert overview.findChild(QObject,'overviewCards').property('count')==1
            assert len(engine.menu_overviews.roms)==2
            click(card(overview,'Nintendo GameCube'))
            assert rom_detail.property('visible')
            assert rom_detail.findChild(QObject,'romListView').property('count')==1
            engine.launch_view._selected_rom_id='switch-rom';engine.launch_view.romsChanged.emit();app.processEvents()
            assert not rom_detail.findChild(QObject,'romPlayButton').property('enabled')
            click(rom_detail.findChild(QObject,'romOverviewBack'));overview.close()
            click(root.findChild(QObject,'settingsNativeCatalogs'))
            assert overview.property('visible') and not cat_detail.property('visible')
            if engine.native_providers.addonsAvailable:
                assert any(c['key']==engine.native_providers.storeCatalogKey for c in engine.menu_overviews.catalogs)
                assert overview.findChild(QObject,'overviewCards').property('count')==2
            else:
                assert overview.findChild(QObject,'overviewCards').property('count')==1
            click(card(overview,'emulation'))
            assert overview.property('section')=='catalog-systems' and not cat_detail.property('visible')
            click(card(overview,'emulation:Nintendo GameCube'))
            assert cat_detail.property('visible')
            assert engine.native_providers.retroRows and all(r['platform']=='Nintendo GameCube' for r in engine.native_providers.retroRows)
            click(cat_detail.findChild(QObject,'catalogOverviewBack'))
            assert overview.property('visible')
            assert overview.property('section')=='catalog-systems'
            click(overview.findChild(QObject,'catalogSystemsBack'))
            assert overview.property('section')=='catalogs'
            assert model.totalCount==before and root.property('selectedId')==selected and not spawns
            assert warnings==[]
        finally:
            root.setVisible(False);engine.provider_jobs.shutdown();engine.social_view.shutdown();engine.live_updates.stop()
            engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()
