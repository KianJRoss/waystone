"""Real picker QML, inert QObject only; no client, socket, engine, or devices."""
import os
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
import pytest
from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl, QMetaObject, Q_ARG
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

APP = Path(__file__).parent
TYPES = [('xbox360', 'Xbox 360'), ('xboxone', 'Xbox One'), ('xboxseries', 'Xbox Series'),
         ('dualshock4', 'DualShock 4'), ('dualsense', 'DualSense')]

class FakePad(QObject):
    """Settings side: types, default, and which profile/type a game resolves to."""
    changed = Signal()
    def __init__(self):
        super().__init__()
        self.game_profiles = {'native:one': 'Racing'}
        self.profile_types = {'Racing': 'dualsense', 'General': ''}
    @Property('QVariantList', notify=changed)
    def choices(self): return [dict(value=v, label=l) for v, l in TYPES]
    @Property(str, notify=changed)
    def defaultType(self): return 'xboxone'
    @Property(str, notify=changed)
    def currentType(self): return 'dualshock4'
    @Property(str, notify=changed)
    def error(self): return ''
    @Property(str, notify=changed)
    def status(self): return ''
    @Slot(str, result=str)
    def profileForGame(self, game): return self.game_profiles.get(game, 'General')
    @Slot(str, result=str)
    def resolveType(self, game): return self.resolveTypeFor(game, '')
    @Slot(str, str, result=str)
    def resolveTypeFor(self, game, title):
        chosen = self.profile_types.get(self.profileForGame(game))
        return chosen or ('passthrough' if title == 'Rocket League' else 'xboxone')
    @Slot(str, result=str)
    def profileType(self, name): return self.profile_types.get(name, '')
    @Slot(str, result=str)
    def anticheatFor(self, title): return 'Easy Anti-Cheat' if title == 'Rocket League' else ''
    @Slot()
    def reloadSettings(self): self.changed.emit()


class FakeEditor(QObject):
    """controllerView: the profile being edited in the controller menu."""
    changed = Signal()
    operationFinished = Signal(bool)
    def __init__(self, pad_type='xboxseries'):
        super().__init__()
        self.pad_type, self.calls = pad_type, []
        self.ready, self.pending, self.unsure = True, False, False
    @Property(str, notify=changed)
    def editingProfile(self): return 'Racing'
    @Property(str, notify=changed)
    def padType(self): return self.pad_type
    @Property(bool, notify=changed)
    def available(self): return self.ready
    @Property(bool, notify=changed)
    def busy(self): return self.pending
    @Property(bool, notify=changed)
    def uncertain(self): return self.unsure
    @Slot(str)
    def setPadType(self, value):
        self.calls.append(value)
        self.pad_type = value
        self.changed.emit()

class Harness:
    def __init__(self, pad, game='', editor=None, title=''):
        self.engine = QQmlEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda errors: self.warnings.extend(e.toString() for e in errors))
        self.window = QQuickWindow()
        self.window.resize(420, 240)
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(APP / 'VirtualPadPicker.qml')))
        assert not self.component.isError(), [e.toString() for e in self.component.errors()]
        self.root = self.component.createWithInitialProperties(dict(padView=pad, gameId=game, editor=editor, gameTitle=title,
                                parent=self.window.contentItem(), width=400))
        assert self.root is not None
        self.combo = self.root.findChild(QObject, 'virtualPadTypeChoice')
        assert self.combo is not None
        self.window.show()
        QTest.qWait(20)
        assert not self.warnings
    def activate(self, index):
        assert self.combo.property('enabled')
        assert QMetaObject.invokeMethod(self.combo, 'activated', Q_ARG(int, index))
        QTest.qWait(10)
    def close(self):
        self.window.close()
        self.root.setParentItem(None)
        self.root.deleteLater()
        self.engine.deleteLater()
        QTest.qWait(10)

@pytest.fixture(scope='module', autouse=True)
def app():
    instance = QGuiApplication.instance() or QGuiApplication([])
    yield instance

@pytest.fixture
def picker():
    items = []
    def create(pad, game='', editor=None, title=''):
        item = Harness(pad, game, editor, title)
        items.append(item)
        return item
    yield create
    for item in reversed(items): item.close()

def variant(value): return value.toVariant() if hasattr(value, 'toVariant') else value

def variant(value): return value.toVariant() if hasattr(value, 'toVariant') else value

def test_profile_pad_type_default_first_and_saved_through_the_editor(picker):
    pad, editor = FakePad(), FakeEditor('xboxseries')
    ui = picker(pad, editor=editor)
    assert ui.combo.property('visible')
    assert variant(ui.root.property('labels')) == ['Use default (Xbox One)'] + [l for _, l in TYPES]
    assert ui.combo.property('currentIndex') == 3
    assert editor.calls == []
    ui.activate(5)
    assert editor.calls == ['dualsense'] and ui.combo.property('currentIndex') == 5
    ui.activate(0)
    assert editor.calls == ['dualsense', ''] and ui.combo.property('currentIndex') == 0

@pytest.mark.parametrize('busy,available,uncertain', [(True, True, False), (False, False, False), (False, True, True)])
def test_unavailable_busy_or_uncertain_editor_cannot_change_the_profile(picker, busy, available, uncertain):
    editor = FakeEditor(); editor.pending, editor.ready, editor.unsure = busy, available, uncertain
    ui = picker(FakePad(), editor=editor)
    assert not ui.combo.property('enabled') and editor.calls == []

def test_unknown_stored_profile_type_is_not_shown_as_default(picker):
    editor = FakeEditor('future-pad')
    ui = picker(FakePad(), editor=editor)
    assert ui.combo.property('currentIndex') == -1
    assert ui.combo.property('displayText') == 'Unrecognized: future-pad'
    ui.activate(0)
    assert editor.calls == ['']

def test_game_shows_which_pad_it_gets_from_its_profile_read_only(picker):
    pad = FakePad()
    one = picker(pad, 'native:one'); other = picker(pad, 'native:other')
    summary = one.root.findChild(QObject, 'virtualPadGameSummary')
    assert not one.combo.property('visible')
    assert summary.property('text') == 'Virtual pad: DualSense · from profile Racing'
    assert other.root.findChild(QObject, 'virtualPadGameSummary').property('text') == \
        'Virtual pad: Xbox One · from profile General'
    pad.profile_types['Racing'] = 'xbox360'; pad.reloadSettings(); QTest.qWait(10)
    assert summary.property('text') == 'Virtual pad: Xbox 360 · from profile Racing'

def test_anticheat_game_shows_the_real_controller_unless_its_profile_picks_a_pad(picker):
    pad = FakePad()
    rl = picker(pad, 'native:rl', title='Rocket League')
    summary = rl.root.findChild(QObject, 'virtualPadGameSummary')
    assert summary.property('text') == \
        'Controller: your real controller (no virtual pad) · anti-cheat game (Easy Anti-Cheat)'
    pad.game_profiles['native:rl'] = 'Racing'; pad.reloadSettings(); QTest.qWait(10)
    assert summary.property('text') == 'Virtual pad: DualSense · from profile Racing'
    pad.profile_types['Racing'] = 'passthrough'; pad.reloadSettings(); QTest.qWait(10)
    assert summary.property('text') == 'Controller: your real controller (no virtual pad) · from profile Racing'
