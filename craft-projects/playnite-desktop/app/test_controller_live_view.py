import copy,time,pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from controller_live_view import ControllerLiveView,validated_frame

def state():
    neutral=dict(buttons=0,left_x=0,left_y=0,right_x=0,right_y=0,left_trigger=0,right_trigger=0)
    return {'running':True,'live':{'schema_version':1,'input':dict(neutral),'output':dict(neutral),'selected_source_id':'fixture'}}

@pytest.mark.parametrize('key,value',[('buttons',True),('left_x',-32769),('right_y',32768),('left_trigger',256),('right_trigger',-1),('left_x',float('nan'))])
def test_rejects_invalid_frame(key,value):
    s=state();s['live']['input'][key]=value
    with pytest.raises(ValueError):validated_frame(s)

def test_raw_extremes_preserved_without_clamping():
    s=state();s['live']['input'].update(buttons=0x9001,left_x=-32768,right_y=32767,left_trigger=128)
    assert validated_frame(s)['input']==s['live']['input']

_app=None
def test_live_disconnect_neutral_release_and_stop():
    global _app
    _app=QGuiApplication.instance() or QGuiApplication([])
    class Client:
        value=state();calls=[]
        def request(self,op):
            self.calls.append(op)
            if self.value is None:raise ConnectionError()
            return copy.deepcopy(self.value)
    c=Client();view=ControllerLiveView(c)
    def wait(predicate):
        until=time.monotonic()+3
        while time.monotonic()<until:
            QTest.qWait(10)
            if predicate():return
        assert predicate()
    try:
        assert not view.available and not c.calls
        c.value['live']['input']['buttons']=0x1000
        view.start();wait(lambda:view.available)
        assert view.frame['input']['buttons']==0x1000
        c.value=state();wait(lambda:view.frame.get('input',{}).get('buttons')==0)
        c.value=None;wait(lambda:not view.available)
        assert view.frame=={}
        c.value=state();wait(lambda:view.available)
        view.stop();assert not view.available and not view.frame
        count=len(c.calls);QTest.qWait(100);assert len(c.calls)<=count+1
        assert set(c.calls)=={'status'}
    finally:view.shutdown()


def test_extended_share_mod_state_and_release():
    s=state();s['live'].update(extended_buttons=['Share','Touchpad'],mod_active=True,controller_mouse_mode=True)
    frame=validated_frame(s)
    assert frame['extended_buttons']==['Share','Touchpad']
    assert frame['mod_active'] is True and frame['controller_mouse_mode'] is True
    s['live'].update(extended_buttons=[],mod_active=False)
    assert validated_frame(s)['extended_buttons']==[]
    assert validated_frame(s)['mod_active'] is False
    assert validated_frame(s)['controller_mouse_mode'] is True

@pytest.mark.parametrize('key,value',[('extended_buttons','Share'),('extended_buttons',[True]),('mod_active',1),('controller_mouse_mode','true')])
def test_invalid_extended_metadata_not_trusted(key,value):
    s=state();s['live'][key]=value
    with pytest.raises(ValueError):validated_frame(s)
