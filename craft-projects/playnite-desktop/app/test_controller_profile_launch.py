from types import SimpleNamespace
from pathlib import Path
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication
from launch_view import LaunchView
from native_services.launch_profiles import LaunchProfile

_APP=None

class Tracker:
    def __init__(self):self.sessions={};self.polled=[];self.raise_for=set()
    def capture_prelaunch_pids(self):return frozenset()
    def start_tracking(self,target,provider,**kwargs):
        sid='session-'+str(len(self.sessions)+1);s=SimpleNamespace(session_id=sid,state='launching');self.sessions[sid]=s;return s
    def get_session(self,sid):return self.sessions.get(sid)
    def poll_session(self,sid):
        self.polled.append(sid)
        if sid in self.raise_for:raise OSError('fixture uncertainty')
        return self.sessions[sid]

@pytest.fixture
def launch(tmp_path):
    global _APP
    _APP=QCoreApplication.instance() or QGuiApplication([])
    exe=tmp_path/'steam';exe.write_text('fixture');exe.chmod(0o700)
    tracker=Tracker();view=LaunchView(tmp_path/'profiles',spawn_fn=lambda *a,**k:SimpleNamespace(pid=42),process_tracker=tracker)
    view._profile_store.create_profile(LaunchProfile('test','Steam','native_game','steam',str(exe),('{game_id}',)))
    view._profile_store.default_associations['steam']='test'
    view._profile_store.save()
    events=[];view.controllerSessionObserved.connect(lambda *event:events.append(event))
    yield view,tracker,events
    view._tracking_timer.stop()


def play(view,product,canonical):
    view.setTarget('variant-'+product,{'provider':'steam','product_id':product,'canonical_game_id':canonical,'title':'same title'})
    result=view.play();assert result['status']=='handed_off',result


def test_identity_snapshotted_without_playtime_and_selection_change(launch):
    view,tracker,events=launch
    play(view,'111','canonical-A');assert events==[('session-1','canonical-A','launching')]
    view.setTarget('other',{'provider':'steam','canonical_game_id':'different-selected'})
    tracker.sessions['session-1'].state='running';view.pollGameState()
    assert events[-1]==('session-1','canonical-A','running')
    tracker.sessions['session-1'].state='closed';view.pollGameState()
    assert events[-1]==('session-1','canonical-A','closed')
    assert not view._tracking_timer.isActive()


def test_overlap_distinct_targets_and_duplicate_launch_guard(launch):
    view,tracker,events=launch
    play(view,'111','canonical-A')
    assert view.play()['status']=='already_running'
    play(view,'222','canonical-B')
    tracker.sessions['session-1'].state='running';tracker.sessions['session-2'].state='running'
    view.pollGameState();assert set(tracker.polled)=={'session-1','session-2'}
    tracker.sessions['session-2'].state='closed';view.pollGameState()
    assert ('session-2','canonical-B','closed') in events and view._tracking_timer.isActive()
    tracker.sessions['session-1'].state='closed';view.pollGameState()
    assert ('session-1','canonical-A','closed') in events and not view._tracking_timer.isActive()


def test_unknown_observation_not_exit_and_running_failed_state_retains_monitor(launch):
    view,tracker,events=launch
    play(view,'111','canonical-A');tracker.sessions['session-1'].state='running';view.pollGameState()
    tracker.raise_for.add('session-1');view.pollGameState();assert events[-1]==('session-1','canonical-A','unknown')
    tracker.raise_for.clear();tracker.sessions['session-1'].state='failed';view.pollGameState()
    assert 'session-1' in view._controller_profile_sessions and view._tracking_timer.isActive()
    tracker.sessions['session-1'].state='closed';view.pollGameState();assert events[-1][-1]=='closed'
