"""Main Waystone window -> native dashboards -> services, no live credentials/saves."""
import os,sys,tempfile,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PySide6.QtCore import QObject,QMetaObject,Qt,QCoreApplication,QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session
from test_save_management_dashboard import make_dashboard_runner
from test_xodus_social_view import AsyncMockSocialTransport,wait_until

class ManagementIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def test_same_main_window_save_and_social_flows_with_clean_shutdown(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);save=base/'fixture-save';save.write_text('fixture')
            runner=make_dashboard_runner(destination_file=save);transport=AsyncMockSocialTransport()
            engine,model,root,warnings=create_session(self.app,headless=True,
                save_config=base/'saves/config.json',save_runner=runner,social_transport=transport)
            try:
                root.setVisible(True)  # Offscreen platform, exercise a real window close event.
                self.assertEqual(runner.executed,[])
                self.assertIsNotNone(engine.provider_jobs)
                self.assertIsNone(engine.provider_jobs._worker)
                self.assertFalse(engine.social_view.connected)
                root.setProperty('selectedId',model.idAt(0));self.app.processEvents()
                dialog=root.findChild(QObject,'saveManagementDashboard')
                self.assertIsNotNone(dialog)
                QMetaObject.invokeMethod(root,'openSaveManager',Qt.DirectConnection)
                manager=engine.save_management
                self.assertTrue(manager.setToolPath(sys.executable,False))
                button=dialog.findChild(QObject,'checkToolButton')
                QMetaObject.invokeMethod(button,'clicked',Qt.DirectConnection)
                self.assertTrue(wait_until(lambda: manager.toolAvailable and not manager.isJobRunning,3000))
                target=root.property('selectedId')
                dialog.findChild(QObject,'manifestBindingInput').setProperty('text','Fixture save title')
                QMetaObject.invokeMethod(dialog.findChild(QObject,'saveBindingButton'),'clicked',Qt.DirectConnection)
                self.assertEqual(manager.getManifestBinding(target),'Fixture save title')
                QMetaObject.invokeMethod(dialog.findChild(QObject,'previewBackupButton'),'clicked',Qt.DirectConnection)
                self.assertTrue(wait_until(lambda: bool(manager.lastJobResult.get('preview')) and not manager.isJobRunning,3000))
                self.assertTrue(manager.lastJobResult['success'])
                self.assertEqual(manager.lastJobResult['targets'][0]['id'],target)
                self.assertTrue(str(base) in manager.lastJobResult['argv'][2])
                manager.createProfile('portable','Portable',str(base/'portable-backups'))
                self.assertTrue(manager.selectProfile('portable'))
                self.assertTrue(manager.backupGameAsync(target,'Fixture display','Steam',True,'Fixture save title'))
                self.assertTrue(wait_until(lambda: not manager.isJobRunning,3000))
                self.assertIn(str(base/'portable-backups'),manager.lastJobResult['argv'])
                dialog.close()
                social=root.findChild(QObject,'socialDashboard');QMetaObject.invokeMethod(social,'open',Qt.DirectConnection)
                QMetaObject.invokeMethod(social.findChild(QObject,'connectButton'),'clicked',Qt.DirectConnection)
                self.assertTrue(wait_until(lambda: engine.social_view.connected,3000))
                self.assertGreater(len(engine.social_view.friends),0)
                self.assertEqual(warnings,[])
                root.close();self.app.processEvents()
                self.assertFalse(engine.social_view.connected)
                self.assertIsNone(engine.social_view._worker)
                self.assertFalse(manager.isJobRunning)
            finally:
                engine.social_view.shutdown();engine.save_management.shutdown();engine.live_updates.stop()
                engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents()
