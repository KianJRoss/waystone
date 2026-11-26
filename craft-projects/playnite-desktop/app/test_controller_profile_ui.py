"""Actual profile QML with synthetic local documents and inert editor objects.

No app session, native client, socket, engine, device, or game is constructed.
The harness is also used by the explicitly synthetic screenshot renderer.
"""
import json
import os
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'

import pytest
from PySide6.QtCore import QObject, Property, QMetaObject, QPointF, QUrl, Signal, Slot, Qt, Q_ARG
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from controller_profiles_view import ControllerProfilesView

APP = Path(__file__).resolve().parent


def synthetic_document():
    return {
        'version': 1, 'active_profile': 'General', 'default_profile': 'General',
        'profiles': {
            'General': {'routes': {'Controller:A': 'Controller:A'}, 'motion': {'passthrough': True}},
            'Racing': {'routes': {'Controller:RB': 'Controller:A'}, 'unknown': {'preserve': [1, 2]}},
            'Couch': {'routes': {'Controller:B': 'Controller:B'}},
        },
        'game_profiles': {}, 'synthetic_fixture': True,
        'opaque_extension': {'keep': 'not an engine or user profile'},
    }


class InertStudio(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._dirty = False

    @Property(bool, notify=changed)
    def dirty(self):
        return self._dirty

    @dirty.setter
    def dirty(self, value):
        self._dirty = bool(value)
        self.changed.emit()


class InertEditor(QObject):
    changed = Signal()
    operationFinished = Signal(bool)

    def __init__(self):
        super().__init__()
        self.calls = []

    @Property(bool, notify=changed)
    def busy(self): return False

    @Property(bool, notify=changed)
    def uncertain(self): return False

    @Property(bool, notify=changed)
    def available(self): return True

    @Property(str, notify=changed)
    def editingProfile(self): return 'General'

    @Property(str, notify=changed)
    def activeProfile(self): return 'General'

    @Property('QStringList', notify=changed)
    def profiles(self): return ['General', 'Racing', 'Couch']

    @Slot(str)
    def loadProfile(self, name): self.calls.append(('load', name))

    @Slot(str)
    def reloadProfile(self, name): self.calls.append(('reload', name))


class InertProfileCommands(QObject):
    changed = Signal()
    operationFinished = Signal(str, bool)

    @Property('QStringList', notify=changed)
    def capabilities(self): return ['profile_import', 'profile_export']

    @Property(bool, notify=changed)
    def busy(self): return False

    @Property(bool, notify=changed)
    def uncertain(self): return False

    @Property(str, notify=changed)
    def status(self): return 'Inert synthetic command capabilities'


def variant(value):
    return value.toVariant() if hasattr(value, 'toVariant') else value


class ProfileQmlHarness:
    def __init__(self, bridge, component='ControllerProfilePicker.qml', *, width=480, height=200,
                 game_id='', compact=False, editor=None, studio=None, profile_commands=None):
        self.warnings = []
        self.engine = QQmlEngine()
        self.engine.warnings.connect(lambda errors: self.warnings.extend(error.toString() for error in errors))
        self.engine.rootContext().setContextProperty('controllerProfiles', bridge)
        self.engine.rootContext().setContextProperty('headlessCheck', True)
        self.window = QQuickWindow()
        self.window.setColor(QColor('#000000'))
        self.window.resize(width, height)
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(APP / component)))
        assert not self.component.isError(), [error.toString() for error in self.component.errors()]
        props = {'parent': self.window.contentItem(), 'x': 16, 'y': 16,
                 'width': width - 32, 'height': height - 32}
        if component == 'ControllerProfilePicker.qml':
            props.update(profileView=bridge, gameId=game_id, compact=compact)
        else:
            props.update(assignments=bridge)
            # Omitted QML var properties keep their declared null value. Python
            # None as an explicit initial QVariant instead becomes undefined.
            if editor is not None: props['service'] = editor
            if studio is not None: props['studio'] = studio
            if profile_commands is not None: props['profileCommands'] = profile_commands
        self.root = self.component.createWithInitialProperties(props)
        assert self.root is not None, [error.toString() for error in self.component.errors()]
        self.window.show()
        QTest.qWait(60)
        if self.warnings:
            self.close()
        assert not self.warnings, self.warnings

    def child(self, name):
        result = self.root.findChild(QObject, name)
        assert result is not None, name
        return result

    def activate(self, index):
        assert QMetaObject.invokeMethod(self.child('controllerProfileChoice'), 'activated', Q_ARG(int, index))
        QTest.qWait(15)

    def click(self, name):
        button = self.child(name)
        assert button.property('enabled'), name
        center = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, center)
        QTest.qWait(20)

    def screenshot(self, path):
        QTest.qWait(70)
        image = self.window.grabWindow()
        assert not image.isNull()
        assert image.save(str(path))
        assert not self.warnings, self.warnings
        return image.width(), image.height()

    def close(self):
        self.window.close()
        self.root.setParentItem(None)
        self.root.deleteLater()
        self.window.deleteLater()
        self.engine.deleteLater()
        QTest.qWait(20)


@pytest.fixture(scope='module', autouse=True)
def application():
    return QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture
def fixture(tmp_path):
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps(synthetic_document()), encoding='utf-8')
    bridge = ControllerProfilesView(path)
    windows = []

    def create(**kwargs):
        window = ProfileQmlHarness(bridge, **kwargs)
        windows.append(window)
        return window

    yield path, bridge, create
    for window in reversed(windows):
        try:
            assert not window.warnings, window.warnings
        finally:
            window.close()


def test_real_picker_is_default_first_and_constructor_selection_are_inert(fixture):
    path, bridge, create = fixture
    before = path.read_bytes()
    commits = []
    bridge.documentChanged.connect(lambda: commits.append(True))
    ui = create(game_id='library:one')
    assert variant(ui.root.property('choices')) == ['Use default (General)', 'General', 'Racing', 'Couch']
    assert ui.child('controllerProfileChoice').property('currentIndex') == 0
    ui.root.setProperty('gameId', 'library:two')
    bridge.selectGame('library:two')
    bridge.setRuntimeGame('library:one')
    QTest.qWait(15)
    assert path.read_bytes() == before and commits == []


def test_real_picker_explicit_choice_and_reset_persist_per_game_only(fixture):
    path, bridge, create = fixture
    ui = create(game_id='library:one')
    ui.activate(2)
    assert bridge.gameProfiles == {'library:one': 'Racing'}
    assert ui.child('controllerProfileChoice').property('currentIndex') == 2
    saved = json.loads(path.read_text())
    assert saved['profiles'] == synthetic_document()['profiles']
    assert saved['opaque_extension'] == synthetic_document()['opaque_extension']
    ui.activate(0)
    assert bridge.gameProfiles == {}
    assert bridge.resolveProfile('library:one') == 'General'


def test_programmatic_combo_index_change_does_not_write(fixture):
    path, _, create = fixture
    ui = create(game_id='library:one')
    before = path.read_bytes()
    ui.child('controllerProfileChoice').setProperty('currentIndex', 3)
    QTest.qWait(15)
    assert path.read_bytes() == before


def test_general_picker_edits_default_not_a_game_assignment(fixture):
    _, bridge, create = fixture
    ui = create()
    assert variant(ui.root.property('choices')) == ['General', 'Racing', 'Couch']
    ui.activate(2)
    assert bridge.defaultProfile == 'Couch' and bridge.gameProfiles == {}


def test_same_title_distinct_ids_never_share_override(fixture):
    _, bridge, create = fixture
    first = create(game_id='same-title:one')
    second = create(game_id='same-title:two')
    first.activate(2)
    assert second.child('controllerProfileChoice').property('currentIndex') == 0
    second.activate(3)
    assert bridge.gameProfiles == {'same-title:one': 'Racing', 'same-title:two': 'Couch'}


def test_compact_overlay_picker_shows_tracked_game_and_runtime_status(fixture):
    path, bridge, create = fixture
    bridge.setRuntimeGame('tracked:canonical')
    bridge.setRuntimeStatus('Applying Racing · awaiting native readback')
    before = path.read_bytes()
    ui = create(game_id=bridge.runtimeGameId, compact=True, width=340)
    texts = [obj.property('text') for obj in ui.root.findChildren(QObject) if obj.metaObject().indexOfProperty('text') >= 0]
    assert bridge.runtimeStatus in texts
    assert path.read_bytes() == before


def test_unavailable_storage_disables_picker_and_shows_error(fixture):
    path, bridge, create = fixture
    path.write_text('invalid fixture JSON')
    assert not bridge.refresh()
    ui = create(game_id='library:one')
    assert not ui.child('controllerProfileChoice').property('enabled')
    texts = [obj.property('text') for obj in ui.root.findChildren(QObject) if obj.metaObject().indexOfProperty('text') >= 0]
    assert bridge.error in texts
    assert path.read_text() == 'invalid fixture JSON'


def test_pending_or_uncertain_engine_state_disables_picker_and_blocks_signal_writes(fixture):
    path, bridge, create = fixture
    ui = create(game_id='library:one')
    before = path.read_bytes()
    bridge.setEngineMutationBlocked(True)
    QTest.qWait(10)
    assert not ui.child('controllerProfileChoice').property('enabled')
    ui.activate(2)  # Even synthetic direct signal injection must not bypass bridge admission.
    assert path.read_bytes() == before and bridge.gameProfiles == {}
    bridge.setEngineMutationBlocked(False)
    QTest.qWait(10)
    assert ui.child('controllerProfileChoice').property('enabled')
    ui.activate(2)
    assert bridge.gameProfiles == {'library:one': 'Racing'}


def test_actual_profiles_tab_constructor_and_dialog_opening_do_not_write(fixture):
    path, bridge, create = fixture
    editor, studio = InertEditor(), InertStudio()
    before = path.read_bytes()
    ui = create(component='controller_native/ProfilesTab.qml', width=1240, height=860,
                editor=editor, studio=studio)
    assert variant(ui.root.property('currentProfileList')) == ['General', 'Racing', 'Couch']
    ui.root.setProperty('selectedProfileName', 'Racing')
    QTest.qWait(10)
    assert path.read_bytes() == before and editor.calls == []
    ui.click('createProfileButton')
    assert ui.root.property('activeDialog') == 'create'
    assert path.read_bytes() == before and editor.calls == []


def test_actual_profiles_tab_lifecycle_buttons_preserve_bodies_and_references(fixture):
    path, bridge, create = fixture
    editor, studio = InertEditor(), InertStudio()
    ui = create(component='controller_native/ProfilesTab.qml', width=1240, height=860,
                editor=editor, studio=studio)

    def confirm(button, name=None):
        ui.click(button)
        if name is not None:
            ui.root.setProperty('dialogInputName', name)
        ui.click('dialogConfirmButton')
        assert ui.root.property('activeDialog') == ''

    confirm('createProfileButton', 'New fixture')
    assert 'New fixture' in bridge.profiles
    assert json.loads(path.read_text())['profiles']['New fixture'] == synthetic_document()['profiles']['General']
    ui.root.setProperty('selectedProfileName', 'Racing')
    confirm('copyProfileButton', 'Racing copy')
    assert json.loads(path.read_text())['profiles']['Racing copy'] == synthetic_document()['profiles']['Racing']
    assert bridge.assignGame('library:one', 'Racing copy')
    ui.root.setProperty('selectedProfileName', 'Racing copy')
    confirm('renameProfileButton', 'Renamed fixture')
    assert bridge.gameProfiles['library:one'] == 'Renamed fixture'
    ui.root.setProperty('selectedProfileName', 'Renamed fixture')
    ui.click('setDefaultProfileButton')
    assert bridge.defaultProfile == 'Renamed fixture'
    confirm('deleteProfileButton')
    assert 'Renamed fixture' not in bridge.profiles and bridge.gameProfiles == {}
    assert json.loads(path.read_text())['opaque_extension'] == synthetic_document()['opaque_extension']


def test_dirty_editor_and_blocked_engine_disable_local_lifecycle(fixture):
    path, bridge, create = fixture
    editor, studio = InertEditor(), InertStudio()
    ui = create(component='controller_native/ProfilesTab.qml', width=1240, height=860,
                editor=editor, studio=studio)
    studio.dirty = True
    QTest.qWait(10)
    for name in ('createProfileButton', 'copyProfileButton', 'renameProfileButton', 'deleteProfileButton'):
        assert not ui.child(name).property('enabled')
    studio.dirty = False
    bridge.setEngineMutationBlocked(True)
    QTest.qWait(10)
    for name in ('createProfileButton', 'copyProfileButton', 'renameProfileButton', 'deleteProfileButton'):
        assert not ui.child(name).property('enabled')
    assert json.loads(path.read_text()) == synthetic_document()


def test_native_import_is_disabled_for_dirty_draft_or_pending_engine(fixture):
    path, bridge, create = fixture
    editor, studio, commands = InertEditor(), InertStudio(), InertProfileCommands()
    ui = create(component='controller_native/ProfilesTab.qml', width=1240, height=860,
                editor=editor, studio=studio, profile_commands=commands)
    assert ui.child('importProfileButton').property('enabled')
    studio.dirty = True
    QTest.qWait(10)
    assert not ui.child('importProfileButton').property('enabled')
    studio.dirty = False
    bridge.setEngineMutationBlocked(True)
    QTest.qWait(10)
    assert not ui.child('importProfileButton').property('enabled')
    assert editor.calls == [] and json.loads(path.read_text()) == synthetic_document()
