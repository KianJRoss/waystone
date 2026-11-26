"""Exercise real app/QML selection wiring, with only a temporary plugin archive."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from PySide6.QtCore import QObject,QCoreApplication,QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session,HERE
from library_model import load_fixture
from native_services.plugin_metadata import APPLICATION_ID

class PluginPanelIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def test_exact_selection_renders_imported_history(self):
        games=load_fixture(HERE/'fixture-library.json');ident=games[0]['id'];other=games[1]['id']
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'plugins.sqlite'
            with closing(sqlite3.connect(path)) as db,db:
                db.execute(f'PRAGMA application_id={APPLICATION_ID}')
                db.execute('CREATE TABLE records(canonical_id TEXT,dataset TEXT,source_path TEXT,raw BLOB)')
                db.execute('INSERT INTO records VALUES(?,?,?,?)',(ident,'GameActivity','fixture.json',json.dumps({'Items':[{'ElapsedSeconds':3600}]}).encode()))
            engine,model,root,warnings=create_session(self.app,headless=True,plugin_metadata_db=path)
            try:
                root.setProperty('selectedId',ident);QTest.qWait(30)
                panel=root.findChild(QObject,'pluginHistoryPanel');self.assertIsNotNone(panel)
                self.assertEqual(engine.plugin_features.selectedGame,ident)
                self.assertEqual(panel.property('canonicalId'),ident)
                self.assertTrue(panel.property('visible'))
                summaries=engine.plugin_features.summaries
                self.assertEqual(next(r for r in summaries if r['dataset']=='GameActivity')['durationLabel'],'1.0 hours recorded')
                root.setProperty('selectedId',other);QTest.qWait(30)
                self.assertEqual(engine.plugin_features.selectedGame,other)
                self.assertTrue(all(r['status']=='absent' for r in engine.plugin_features.summaries))
                self.assertEqual(warnings,[])
            finally:
                engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents()

    def test_optional_archive_keeps_fixture_app_working(self):
        engine,model,root,warnings=create_session(self.app,headless=True)
        try:
            self.assertFalse(engine.plugin_features.available)
            self.assertEqual(warnings,[])
        finally:
            engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents()
