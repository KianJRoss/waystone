"""Qt launch lifecycle integration with fixture tracker; zero host process scans."""
import os,tempfile,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtGui import QGuiApplication
from launch_view import LaunchView
from native_services.launch_profiles import LaunchProfile

class FixtureTracker:
    def __init__(self):self.sessions={};self.prelaunch_count=0
    def capture_prelaunch_pids(self):self.prelaunch_count+=1;return frozenset({1})
    def start_tracking(self,target_id,provider,**kwargs):
        assert kwargs['prelaunch_pids']==frozenset({1})
        session=SimpleNamespace(session_id='session-'+target_id,target_id=target_id,
                                 provider_or_platform=provider,launch_time=1,running_since=None,state='launching')
        self.sessions[session.session_id]=session;return session
    def get_session(self,sid):return self.sessions.get(sid)
    def poll_session(self,sid):return self.sessions[sid]

class LaunchLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name);exe=base/'steam';exe.write_text('fixture');exe.chmod(0o700)
        self.calls=[];self.tracker=FixtureTracker()
        def spawn(argv,**kw):self.calls.append(argv);return SimpleNamespace(pid=42)
        self.view=LaunchView(base/'profiles',spawn_fn=spawn,process_tracker=self.tracker,
                             launcher_status_root=base/'launch-status')
        self.addCleanup(self.view._tracking_timer.stop)
        self.view._profile_store.create_profile(LaunchProfile('test','Steam','native_game','steam',str(exe),('{game_id}',)))
        self.view._profile_store.default_associations['steam']='test'
        self.view._profile_store.save()
        self.view.setTarget('1276390',{'provider':'steam'})
    def test_handoff_not_running_then_process_closure_restores_play(self):
        self.assertEqual(self.view.play()['status'],'handed_off')
        self.assertEqual(self.view.gameState,'launching');self.assertEqual(self.view.playLabel,'Starting…')
        session=self.tracker.sessions['session-1276390'];session.state='running'
        self.view.pollGameState();self.assertEqual(self.view.playLabel,'Playing')
        session.state='closed';self.view.pollGameState()
        self.assertEqual(self.view.gameState,'closed');self.assertEqual(self.view.playLabel,'Play')
        self.assertFalse(self.view._tracking_timer.isActive())
        self.assertEqual(len(self.calls),1)
    def test_launcher_status_file_is_shown_while_playing(self):
        import json as _json
        status=Path(self.tmp.name)/'status.json'
        prof=self.view._profile_store.get_profile('test')
        from dataclasses import replace
        self.view._profile_store.update_profile(replace(prof,extra_metadata={'status_file':str(status)}))
        self.view.setTarget('1276390',{'provider':'steam'})
        self.assertEqual(self.view.launchDetail,'')
        self.view.play();status.write_text(_json.dumps({'stage':'loading','text':'Mod Organizer is loading the mod list'}))
        self.assertEqual(self.view.launchDetail,'Mod Organizer is loading the mod list')
        self.tracker.sessions['session-1276390'].state='closed';self.view.pollGameState()
        self.assertEqual(self.view.launchDetail,'')

    def test_owned_provider_status_drives_start_play_close_and_active_game_ui(self):
        import json as _json
        from dataclasses import replace
        self.view._launcher_owner_enabled=True
        profile=self.view._profile_store.get_profile('test')
        self.view._profile_store.update_profile(replace(profile,argv_template=('steam://rungameid/{game_id}',)))
        self.view.setTarget('1276390',{'provider':'steam','install_path':'/games/steam/1276390'})
        self.view.play()
        path=Path(self.view._active_status_file)
        self.assertTrue(any(str(a).endswith('launcher_owner_runtime.py') for a in self.calls[0]))
        self.assertFalse(path.exists())  # fake spawn must not run the monitored launcher
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(_json.dumps({'stage':'preparing','text':'Preparing in Steam'}))
        self.assertEqual(self.view.gameState,'launching')
        self.assertEqual(self.view.launchDetail,'Preparing in Steam')
        path.write_text(_json.dumps({'stage':'playing','text':'Playing','game_pids':[2]}))
        self.tracker.sessions['session-1276390'].state='running'
        self.assertEqual(self.view.gameState,'running')
        self.assertEqual(self.view.activeGame['state'],'running')
        self.assertEqual(self.view.playLabel,'Playing')
        path.write_text(_json.dumps({'stage':'done','text':'Game closed'}))
        self.view.pollGameState()
        self.assertEqual(self.view.gameState,'closed')
        self.assertEqual(self.view.playLabel,'Play')
        self.assertEqual(self.view.activeGame,{})
    def test_local_entries_are_tracked_through_their_launcher(self):
        exe=Path(self.tmp.name)/'mo2_launch';exe.write_text('fixture');exe.chmod(0o700)
        self.view._profile_store.create_profile(LaunchProfile('local-nolvus','Nolvus','native_game','local',str(exe),()))
        self.view._profile_store.set_game_override('local:nolvus','local-nolvus')
        self.view.setTarget('local:nolvus',{'provider':'Local','product_id':'local:nolvus'})
        self.view.play()
        self.assertIn('session-local:nolvus',self.tracker.sessions)
        self.tracker.sessions['session-local:nolvus'].state='running';self.view.pollGameState()
        self.assertEqual(self.view.playLabel,'Playing')
    def test_running_local_launcher_is_adopted_after_restart(self):
        import json as _json, os as _os
        exe=Path(self.tmp.name)/'mo2_launch';exe.write_text('fixture');exe.chmod(0o700)
        status=Path(self.tmp.name)/'status.json'
        status.write_text(_json.dumps({'stage':'playing','text':'Playing','launcher_pid':_os.getpid()}))
        self.view._profile_store.create_profile(LaunchProfile('local-x','X','native_game','local',str(exe),(),extra_metadata={'status_file':str(status)}))
        self.view._profile_store.set_game_override('local:x','local-x')
        self.view.setTarget('local:x',{'provider':'Local','product_id':'local:x'})
        self.assertIn('session-local:x',self.tracker.sessions)   # adopted without pressing Play
        self.assertEqual(self.calls,[])                          # nothing was launched
        status.write_text(_json.dumps({'stage':'done','text':'Game closed','launcher_pid':_os.getpid()}))
        self.view.setTarget('local:y',{'provider':'Local','product_id':'local:y'})
        self.assertNotIn('session-local:y',self.tracker.sessions)
    def test_duplicate_play_does_not_spawn_again_and_selection_preserves_session(self):
        self.view.play();self.assertEqual(self.view.play()['status'],'already_running')
        self.assertEqual(len(self.calls),1)
        self.view.setTarget('other',{'provider':'steam'});self.assertEqual(self.view.gameState,'idle')
        self.view.setTarget('1276390',{'provider':'steam'});self.assertEqual(self.view.gameState,'launching')
    def test_tracker_failure_never_falsely_shows_running(self):
        self.view.play()
        def failure(_):raise OSError('fixture')
        self.tracker.poll_session=failure
        self.view.pollGameState();self.assertEqual(self.view.gameState,'unknown')
        self.assertNotEqual(self.view.playLabel,'Playing')


class LaunchMonitorTests(unittest.TestCase):
    """Settings > Games monitor: focused before launch, the game window moved there, per-game override wins."""
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def setUp(self):
        import json as _json
        import sys as _sys
        _sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'tests'))
        from test_game_monitor import AOC,SAMSUNG,FakeRun,_backend
        from native_services.game_monitor import GameMonitorSettings
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name);exe=base/'steam';exe.write_text('fixture');exe.chmod(0o700)
        self.events=[];self.tracker=FixtureTracker()
        self.run=FakeRun(clients=[{'address':'0xgame','pid':77,'monitor':1,'workspace':{'name':'11'}}])
        recorder=self.run
        events=self.events
        class Recording:
            def __call__(s,argv,**kw):
                if argv[1]=='dispatch':events.append(('dispatch',argv[2]))
                return recorder(argv,**kw)
        def spawn(argv,**kw):self.events.append(('spawn',));return SimpleNamespace(pid=42)
        self.settings=GameMonitorSettings(base/'settings.json')
        self.settings.set_default(AOC['description'])
        self.view=LaunchView(base/'profiles',spawn_fn=spawn,process_tracker=self.tracker,
                             launcher_status_root=base/'launch-status',monitor_settings=self.settings,
                             monitor_backend=_backend(Recording()))
        self.addCleanup(self.view._tracking_timer.stop)
        self.view._profile_store.create_profile(LaunchProfile('test','Steam','native_game','steam',str(exe),('{game_id}',)))
        self.view._profile_store.default_associations['steam']='test'
        self.view._profile_store.save()
        self.samsung=SAMSUNG
    def test_default_monitor_is_focused_before_launch_and_game_window_moved(self):
        self.view.setTarget('1276390',{'provider':'steam','canonical_game_id':'game-btd'})
        self.assertEqual(self.view.play()['status'],'handed_off')
        self.assertEqual(self.events[0],('dispatch','hl.dsp.focus({ monitor = "DP-2" })'))
        self.assertEqual(self.events[1],('spawn',))
        session=self.tracker.sessions['session-1276390'];session.state='running';session.tracked_processes={77:None}
        self.view.pollGameState()
        self.assertIn(('dispatch','hl.dsp.window.move({ monitor = "DP-2", follow = true, window = "address:0xgame" })'),self.events)
        self.assertEqual(len(self.view.monitorChoices),2)
    def test_per_game_choice_overrides_default_and_anywhere_skips_focus(self):
        self.view.setGameMonitorFor('game-btd',self.samsung['description'])
        self.assertEqual(self.view.gameMonitorFor('game-btd'),self.samsung['description'])
        self.view.setTarget('1276390',{'provider':'steam','canonical_game_id':'game-btd'})
        self.view.play()
        self.assertEqual(self.events[0],('dispatch','hl.dsp.focus({ monitor = "DP-1" })'))
        self.tracker.sessions['session-1276390'].state='closed';self.view.pollGameState()
        self.events.clear()
        self.view.setGameMonitorFor('game-btd','any')
        self.view.setTarget('1276390',{'provider':'steam','canonical_game_id':'game-btd'})
        self.view.play()
        self.assertEqual(self.events,[('spawn',)])
