from PySide6.QtCore import QObject,Signal
from controller_ui_navigation import ControllerUiNavigation

class Live(QObject):
    changed=Signal()
    def __init__(self):super().__init__();self.available=True;self.frame={'input':{}};self.owners=set()
    def acquire(self,o):self.owners.add(o)
    def release(self,o):self.owners.discard(o)
    def push(self,buttons=0,x=0,y=0,extended=None,lt=0,rt=0):
        self.frame={'input':{'buttons':buttons,'left_x':x,'left_y':y,'left_trigger':lt,'right_trigger':rt},'extended_buttons':extended or []};self.changed.emit()

def setup():
    live=Live();now=[0.];nav=ControllerUiNavigation(live,clock=lambda:now[0]);events=[];nav.action.connect(events.append)
    return live,now,nav,events

def test_focus_context_owns_polling_and_never_injects_background_input():
    live,now,nav,events=setup();live.push(0x1000);assert not events
    nav.setContext('console');live.push(0x1000);assert not events
    live.push();live.push(0x1000);assert events==['accept']
    nav.setContext('');assert not live.owners;live.push();live.push(0x1000);assert events==['accept']

def test_confirm_and_back_are_edges_not_repeat_and_no_auto_play_on_drift():
    live,now,nav,events=setup();nav.setContext('console');live.push()
    live.push(0x1000);now[0]=10.;live.push(0x1000);assert events==['accept']
    live.push();live.push(0x2000);live.push(0x2000);assert events==['accept','back']
    live.push(x=20000);assert events[-1]=='right' and events.count('accept')==1

def test_axis_hysteresis_and_bounded_direction_repeat():
    live,now,nav,events=setup();nav.setContext('console');live.push()
    live.push(x=19000);assert events==['right']
    now[0]=.2;live.push(x=13000);assert events==['right']
    now[0]=.36;live.push(x=13000);assert events==['right','right']
    now[0]=.4;live.push(x=13000);assert len(events)==2
    live.push(x=0);live.push(y=22000);assert events[-1]=='up'

def test_share_modifier_is_not_repurposed_as_ui_navigation():
    live,now,nav,events=setup();nav.setContext('console');live.push();live.push(extended=['Share']);assert events==[]

def test_disconnect_and_context_switch_require_neutral_release():
    live,now,nav,events=setup();nav.setContext('console');live.push();live.push(0x1000)
    live.available=False;live.changed.emit();live.available=True;live.push(0x1000);assert events==['accept']
    nav.setContext('overlay');live.push(0x1000);assert events==['accept']
    live.push();live.push(0x1000);assert events==['accept','accept']

def test_shoulders_menu_and_dpad_actions():
    live,now,nav,events=setup();nav.setContext('console');live.push()
    for mask in [1,2,4,8,0x100,0x200,0x10]:live.push(mask);live.push()
    assert events==['up','down','left','right','previous_section','next_section','menu']

def test_triggers_emit_page_navigation():
    live,now,nav,events=setup();nav.setContext('console');live.push()
    live.push(lt=200);live.push(lt=200);assert events==['previous_page']
    live.push(lt=0);live.push(rt=220);assert events==['previous_page','next_page']

def test_guide_button_is_watched_without_a_context_and_only_when_enabled():
    live,now,nav,events=setup()
    live.push(0x400);live.push();assert events==[]            # not enabled: nothing listens
    nav.setGuideWatch(True);assert 'guide' in live.owners;live.push()   # released once after enabling
    live.push(0x400);live.push(0x400);live.push();assert events==['guide']   # one edge, no repeat
    live.push(0x1000);assert events==['guide']                 # no context: A is never forwarded
    nav.setContext('desktop');live.push();live.push(0x400);assert events==['guide','guide']
    nav.setGuideWatch(False);assert 'guide' not in live.owners

def test_desktop_context_and_face_buttons():
    live,now,nav,events=setup();nav.setContext('desktop');live.push()
    for mask in [0x1000,0x2000,0x4000,0x8000]:live.push(mask);live.push()
    assert events==['accept','back','options','search']
