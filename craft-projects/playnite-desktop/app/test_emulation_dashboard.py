import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile,unittest
from pathlib import Path
from PySide6.QtCore import QObject,QMetaObject,Qt,QCoreApplication,QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session
from native_services.launch_profiles import LaunchProfileStore

class EmulationDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def test_editable_overview_not_bound_to_selected_game(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);exe=base/'emulator';exe.write_text('fixture');exe.chmod(0o700)
            engine,model,root,warnings=create_session(self.app,headless=True,launch_profile_root=base/'profiles')
            try:
                manager=engine.emulation_manager
                self.assertTrue(manager.saveProfile('test-gc','Dolphin','gamecube',str(exe),'["-b","{rom}"]',''))
                dialog=root.findChild(QObject,'emulationDashboard');self.assertIsNotNone(dialog)
                QMetaObject.invokeMethod(dialog,'open',Qt.DirectConnection);QTest.qWait(30)
                self.assertTrue(dialog.property('visible'))
                rows=root.findChild(QObject,'emulatorProfiles');self.assertEqual(rows.property('count'),len(manager.profiles))
                self.assertIn('test-gc',{p['id'] for p in manager.profiles})
                self.assertTrue(manager.setDefault('gamecube','test-gc'))
                external=LaunchProfileStore(base/'profiles');chosen,_=external.resolve_profiles('rom-id','gamecube','rom')
                self.assertEqual(chosen.executable,str(exe));self.assertEqual(chosen.argv_template,('-b','{rom}'))
                self.assertEqual(warnings,[])
            finally:
                engine.live_updates.stop();engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents()
