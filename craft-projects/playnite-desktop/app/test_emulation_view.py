import json,tempfile,unittest
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from emulation_view import EmulationView
from native_services.launch_profiles import LaunchProfileStore,LaunchProfile

class EmulationViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.exe=self.root/'emulator';self.exe.write_text('fixture');self.exe.chmod(0o700)
        self.view=EmulationView(self.root/'profiles')
    def test_open_does_not_create_state_or_launch(self):
        self.assertFalse((self.root/'profiles').exists());self.assertEqual(self.view.profiles,[])
    def test_save_and_default_use_actual_play_profile_store(self):
        self.assertTrue(self.view.saveProfile('custom-eden','Eden','switch',str(self.exe),'["-f","{rom}"]',''))
        self.assertTrue(self.view.setDefault('switch','custom-eden'))
        external=LaunchProfileStore(self.root/'profiles')
        selected,others=external.resolve_profiles('rom-id','switch','rom')
        self.assertEqual(selected.profile_id,'custom-eden');self.assertEqual(selected.argv_template,('-f','{rom}'))
        self.assertTrue(self.view.profiles[0]['installed'])
    def test_invalid_settings_do_not_write(self):
        self.assertFalse(self.view.saveProfile('bad','Bad','ps2','/missing','["{rom}"]',''))
        self.assertFalse((self.root/'profiles').exists())
        self.assertFalse(self.view.saveProfile('bad','Bad','ps2',str(self.exe),'not json',''))
    def test_incompatible_default_refused(self):
        self.view.saveProfile('gc','Dolphin','gamecube',str(self.exe),'["{rom}"]','')
        self.assertFalse(self.view.setDefault('ps5','gc'))
    def test_external_change_refreshes_without_starting_emulator(self):
        self.view.saveProfile('gc','Dolphin','gamecube',str(self.exe),'["{rom}"]','')
        other=EmulationView(self.root/'profiles');self.assertEqual(other.profiles[0]['name'],'Dolphin')
        self.view.saveProfile('gc','My Dolphin','gamecube',str(self.exe),'["-b","{rom}"]','')
        other.refresh();self.assertEqual(other.profiles[0]['name'],'My Dolphin')
    def test_edit_preserves_advanced_profile_fields_and_associations(self):
        store=LaunchProfileStore(self.root/'profiles')
        original=LaunchProfile('gc','Dolphin','rom','gamecube',str(self.exe),('{rom}',),
            env={'CUSTOM':'keep'},target_host='fixture-host',is_default=True,is_app_owned=False,
            supported_platforms=('gamecube','wii'),supported_extensions=('.iso','.rvz'),
            extra_metadata={'integration':{'version':2}})
        store.create_profile(original)
        self.view.refresh()
        self.assertTrue(self.view.setDefault('gamecube','gc'))
        self.assertTrue(self.view.saveProfile('gc','Updated Dolphin','gamecube',str(self.exe),'["-b","{rom}"]',''))
        updated=LaunchProfileStore(self.root/'profiles').get_profile('gc')
        for field in ('env','target_host','is_default','is_app_owned','supported_platforms','supported_extensions','extra_metadata'):
            self.assertEqual(getattr(updated,field),getattr(original,field),field)
        self.assertEqual(updated.display_name,'Updated Dolphin')
        self.assertEqual(LaunchProfileStore(self.root/'profiles').default_associations['gamecube'],'gc')
    def test_changing_system_does_not_leave_stale_associations(self):
        self.assertTrue(self.view.saveProfile('gc','Dolphin','gamecube',str(self.exe),'["{rom}"]',''))
        self.assertTrue(self.view.setDefault('gamecube','gc'))
        path=self.root/'profiles'/'launch_profiles.json';before=path.read_bytes()
        self.assertFalse(self.view.saveProfile('gc','PCSX2','ps2',str(self.exe),'["{rom}"]',''))
        self.assertEqual(path.read_bytes(),before)
    def test_native_launcher_cannot_be_overwritten_from_emulation(self):
        store=LaunchProfileStore(self.root/'profiles')
        store.create_profile(LaunchProfile('steam','Steam','native_game','steam',str(self.exe),('{game_id}',)))
        self.view.refresh();path=self.root/'profiles'/'launch_profiles.json';before=path.read_bytes()
        self.assertFalse(self.view.saveProfile('steam','Emulator','gamecube',str(self.exe),'["{rom}"]',''))
        self.assertEqual(path.read_bytes(),before)
    def test_external_addition_survives_edit_before_watcher_refresh(self):
        self.assertTrue(self.view.saveProfile('gc','Dolphin','gamecube',str(self.exe),'["{rom}"]',''))
        external=EmulationView(self.root/'profiles')
        self.assertTrue(external.saveProfile('ps2','PCSX2','ps2',str(self.exe),'["{rom}"]',''))
        self.assertTrue(self.view.saveProfile('gc','Updated Dolphin','gamecube',str(self.exe),'["{rom}"]',''))
        store=LaunchProfileStore(self.root/'profiles')
        self.assertIsNotNone(store.get_profile('ps2'))
        self.assertEqual(store.get_profile('gc').display_name,'Updated Dolphin')
