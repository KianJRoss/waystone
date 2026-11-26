import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json,tempfile,unittest
from pathlib import Path
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session,HERE
from live_library_updates import LiveLibraryUpdates
from native_services.organizer_store import OrganizerStore
from native_services.catalog_link_store import CatalogLinkStore


class LiveLibraryUpdatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.data=json.loads((HERE/'fixture-library.json').read_bytes())
        self.data['catalog_kind']='playnite-display-selected-copy-v1'
        self.catalog=self.base/'catalog.json';self.catalog.write_text(json.dumps(self.data))
        self.ids=[g['id'] for g in self.data['games']]
    def session(self,**kw):
        engine,model,root,warnings=create_session(self.app,headless=True,catalog=self.catalog,**kw)
        def cleanup():
            engine.live_updates.stop();engine.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents()
        self.addCleanup(cleanup)
        return engine,model,root,warnings
    def waitFor(self,check):
        for _ in range(80):
            if check():return
            QTest.qWait(40)
        self.fail('Committed external update did not reach the same running app')
    def test_external_metadata_commit_updates_same_open_session(self):
        state=self.base/'organizer.sqlite'
        engine,model,root,warnings=self.session(organizer_state=state)
        root.setProperty('selectedId',self.ids[0]);before=id(engine)
        external=OrganizerStore(state)
        external.apply(external.preview([{'game_id':self.ids[0],'changes':{'tags':['Live edit'],'title':'Corrected title','platform':'Nintendo GameCube'}}]),'external-edit')
        self.waitFor(lambda:model._organizer_revision==1)
        self.assertIn('Live edit',model.overview(self.ids[0])['tags'])
        self.assertEqual(model.overview(self.ids[0])['title'],'Corrected title')
        self.assertEqual(model.overview(self.ids[0])['platforms'],'Nintendo GameCube')
        self.assertIn('Nintendo GameCube',engine.rootContext().contextProperty('platformOptions'))
        self.assertEqual(id(engine),before);self.assertEqual(root.property('selectedId'),self.ids[0])
        self.assertEqual(warnings,[])
    def test_external_link_file_creation_and_commit_updates_open_ui(self):
        path=self.base/'links.sqlite';engine,model,root,warnings=self.session(link_state=path)
        root.setProperty('selectedId',self.ids[1])
        external=CatalogLinkStore.create(path,self.ids)
        external.apply(external.preview([{'canonical_ids':self.ids[:2],'representative_id':self.ids[0]}]),'external-link')
        self.waitFor(lambda:len(model.games)==len(self.ids)-1)
        self.assertEqual(root.property('selectedId'),self.ids[0])
        self.assertEqual(sum(len(g['variants']) for g in model.games),sum(len(g['variants']) for g in self.data['games']))
        self.assertEqual(warnings,[])
    def test_invalid_replacement_retries_then_recovers_and_stops(self):
        path=self.base/'state.json';path.write_text('{}');seen=[]
        watcher=LiveLibraryUpdates(interval_ms=30);self.addCleanup(watcher.stop)
        def changed():seen.append(json.loads(path.read_text()));return True
        watcher.watch(path,changed);watcher.start()
        path.write_text('bad json');self.waitFor(lambda:bool(watcher.error))
        self.assertEqual(seen,[])
        replacement=self.base/'replacement';replacement.write_text('{"ok":true}');replacement.replace(path)
        self.waitFor(lambda:bool(seen))
        self.assertEqual(seen[-1],{'ok':True});self.assertEqual(watcher.error,'')
        watcher.stop();self.assertFalse(watcher._timer.isActive())
