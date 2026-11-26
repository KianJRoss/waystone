"""Focus-scoped controller navigation using the existing engine, never OS key injection."""
import time
from PySide6.QtCore import QObject,Signal,Slot,Property

class ControllerUiNavigation(QObject):
    action=Signal(str)
    changed=Signal()
    def __init__(self,live,*,clock=time.monotonic,parent=None):
        super().__init__(parent);self.live=live;self.clock=clock;self._context=''
        self._neutral_required=True;self._previous=set();self._direction=None;self._next_repeat=0
        self._analog_x=0;self._analog_y=0
        # Guide/Xbox button (Kian 2026-11-24): watched even with no focus context, to open Big Screen or the overlay.
        self._guide_watch=False;self._guide_prev=False
        live.changed.connect(self._frame)
    @Slot(bool)
    def setGuideWatch(self,on):
        on=bool(on)
        if on==self._guide_watch:return
        self._guide_watch=on;self._guide_prev=True  # a Guide held while enabling is not a press
        if on:self.live.acquire('guide')
        else:self.live.release('guide')
    @Property(str,notify=changed)
    def context(self):return self._context
    @Slot(str)
    def setContext(self,context):
        context=context if context in ('console','overlay','desktop') else ''
        if context==self._context:return
        self._context=context;self._neutral_required=True;self._previous=set();self._direction=None
        self._analog_x=self._analog_y=0
        if context:self.live.acquire('navigation')
        else:self.live.release('navigation')
        self.changed.emit()
    @staticmethod
    def _axis(value,old):
        if old and abs(value)<.25:return 0
        if value>=.55:return 1
        if value<=-.55:return -1
        return old
    def _frame(self):
        if not (self._context or self._guide_watch):return
        if not self.live.available:
            self._neutral_required=True;self._previous=set();self._direction=None;self._guide_prev=True;return
        frame=self.live.frame.get('input',{})
        b=frame.get('buttons',0)
        guide=bool(b&0x400)
        if guide and not self._guide_prev:self.action.emit('guide')
        self._guide_prev=guide
        if not self._context:return
        x=frame.get('left_x',0)/32768.;y=frame.get('left_y',0)/32768.
        if self._neutral_required:
            # Never treat the button held while focus changed as a new confirm.
            if (b & 0xF31F)==0 and abs(x)<.25 and abs(y)<.25:self._neutral_required=False
            return
        self._analog_x=self._axis(x,self._analog_x);self._analog_y=self._axis(y,self._analog_y)
        direction=('up' if b&1 else 'down' if b&2 else 'left' if b&4 else 'right' if b&8 else
                   ('right' if self._analog_x>0 else 'left' if self._analog_x<0 else
                    'up' if self._analog_y>0 else 'down' if self._analog_y<0 else None))
        now=self.clock()
        if direction!=self._direction:
            self._direction=direction;self._next_repeat=now+.35
            if direction:self.action.emit(direction)
        elif direction and now>=self._next_repeat:
            self._next_repeat=now+.12;self.action.emit(direction)
        lt=frame.get('left_trigger',0)>128
        rt=frame.get('right_trigger',0)>128
        current={name for mask,name in [(0x1000,'accept'),(0x2000,'back'),(0x4000,'options'),(0x8000,'search'),(0x10,'menu'),(0x100,'previous_section'),(0x200,'next_section')] if b&mask}
        if lt:current.add('previous_page')
        if rt:current.add('next_page')
        for name in sorted(current-self._previous):self.action.emit(name)
        self._previous=current
