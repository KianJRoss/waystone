"""Actual QML editor -> preview -> shared SQLite -> model, without live data."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json,tempfile,unittest
from pathlib import Path
from PySide6.QtCore import QObject,QMetaObject,Qt,QCoreApplication,QEvent
from PySide6.QtGui import QGuiApplication
from app import create_session,HERE
from native_services.organizer_store import OrganizerStore

class MetadataEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        data=json.loads((HERE/'fixture-library.json').read_text());data['catalog_kind']='playnite-display-selected-copy-v1'
        self.ident=data['games'][0]['id'];self.catalog=self.base/'catalog.json';self.catalog.write_text(json.dumps(data))
        self.state=self.base/'organizer.sqlite'
        self.engine,self.model,self.root,self.warnings=create_session(self.app,headless=True,catalog=self.catalog,organizer_state=self.state)
        self.addCleanup(self.close)
        self.root.setProperty('selectedId',self.ident);self.app.processEvents()
    def close(self):
        self.engine.live_updates.stop();self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents()
    def test_qml_title_platform_categories_edit_and_undo(self):
        title=self.root.findChild(QObject,'organizerTitle');self.assertIsNotNone(title)
        title.setProperty('text','Animal Crossing')
        self.root.findChild(QObject,'organizerPlatform').setProperty('text','Nintendo GameCube')
        self.root.findChild(QObject,'organizerCategories').setProperty('text','["Nintendo", "Backlog"]')
        preview=self.root.findChild(QObject,'metadataPreviewButton')
        self.assertTrue(QMetaObject.invokeMethod(preview,'clicked',Qt.DirectConnection))
        self.assertEqual(self.model.organizerPendingGame,self.ident)
        before=OrganizerStore(self.state).snapshot();self.assertEqual(before['revision'],0)
        self.model.applyOrganizer(self.ident)
        values=self.model.organizerEditValues(self.ident)
        self.assertEqual(values['title'],'Animal Crossing');self.assertEqual(values['platform'],'Nintendo GameCube')
        self.assertEqual(json.loads(values['categories']),['Nintendo','Backlog'])
        self.assertEqual(OrganizerStore(self.state).snapshot()['revision'],1)
        self.model.undoOrganizer();self.assertNotEqual(self.model.organizerEditValues(self.ident)['title'],'Animal Crossing')
        self.assertEqual(self.warnings,[])
    def test_invalid_metadata_never_writes(self):
        for changes in ({'categories':'not a list'},{'title':''},{'source':'steam'}):
            self.model.previewMetadata(self.ident,json.dumps(changes))
            self.assertFalse(self.model.organizerPendingGame)
        self.assertEqual(OrganizerStore(self.state).snapshot()['revision'],0)
    def test_no_edit_is_not_silently_committed(self):
        self.model.previewMetadata(self.ident,'{}')
        self.assertFalse(self.model.organizerPendingGame)
        self.assertEqual(OrganizerStore(self.state).snapshot()['revision'],0)
    def test_sort_tracks_edited_display_title_not_immutable_import(self):
        original=next(g['title'] for g in self.model._source_games if g['id']==self.ident)
        self.model.previewMetadata(self.ident,json.dumps({'title':'ZZZ corrected title'}))
        self.model.applyOrganizer(self.ident);self.model.setSort('title',False)
        labels=[self.model._display_title(g).casefold() for g in self.model.visible]
        self.assertEqual(labels,sorted(labels))
        self.assertEqual(next(g['title'] for g in self.model._source_games if g['id']==self.ident),original)
    def test_custom_libraries_are_live_filterable_and_do_not_change_provider(self):
        original_source=self.model._source_games[0]['variants'][0]['source']
        self.model.previewMetadata(self.ident,json.dumps({'libraries':['Nintendo collection'],'categories':['Co-op']}))
        self.model.applyOrganizer(self.ident)
        self.assertIn('Nintendo collection',self.model.collectionValues('libraries'))
        self.model.setCollectionFilters({'libraries':['Nintendo collection'],'categories':['Co-op']})
        self.assertEqual([g['id'] for g in self.model.visible],[self.ident])
        self.assertEqual(self.model._source_games[0]['variants'][0]['source'],original_source)
