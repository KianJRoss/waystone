"""W5 slice 2 presentation/route contracts, synthetic offscreen data only."""
from pathlib import Path
import pytest
from PySide6.QtCore import QObject, QUrl, QMetaObject
from PySide6.QtQml import QQmlComponent
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickWindow
from test_desktop_overlay import qapp, create_engine_with_context, FakeSocialView, FakeVoiceView
from test_overlay_layout import create, destroy

APP = Path(__file__).resolve().parent


def test_arrange_hint_only_in_selected_arrange_widget_and_compact_translucent_cards(qapp):
    engine, manager, windows, social, voice = create(qapp)
    try:
        manager.open(); QTest.qWait(30)
        for window in windows:
            assert not window.findChild(QObject, 'widgetArrangeHint').property('visible')
            overlay_surface = manager.findChild(QQuickWindow, 'overlaySurfaceWindow')
            assert overlay_surface.color().alpha() == 0
            image = overlay_surface.grabWindow()
            assert not image.isNull()
            x, y = int(window.x()), int(window.y())
            # In editing mode the shared surface supplies the dim backdrop beneath
            # each transparent item corner. The card must remain translucent too.
            backdrop_alpha = image.pixelColor(x, y).alpha()
            assert 0 < backdrop_alpha < 255
            assert backdrop_alpha < image.pixelColor(x+10, y+int(window.height())-16).alpha() < 255
            surface = window.findChild(QObject, 'widgetCardSurface')
            assert 0 < surface.property('color').alpha() < 255
            assert window.findChild(QObject, 'widgetHeaderControls')
            for name in ('widgetMoveButton','widgetSizeButton','widgetPinButton','widgetCloseButton'):
                button = window.findChild(QObject, name)
                assert button.property('compact')
                assert button.property('height') == 24
        manager.beginArrange(1, 'move'); QTest.qWait(20)
        assert [w.findChild(QObject,'widgetArrangeHint').property('visible') for w in windows] == [False,True,False,False]
        assert windows[1].findChild(QObject,'widgetArrangeHint').property('text').startswith('MOVE')
        manager.navigate('accept'); QTest.qWait(20)
        assert windows[1].findChild(QObject,'widgetArrangeHint').property('text').startswith('RESIZE')
        manager.navigate('next_section'); QTest.qWait(20)
        assert [w.findChild(QObject,'widgetArrangeHint').property('visible') for w in windows] == [False,False,True,False]
        manager.finishArrange(); QTest.qWait(20)
        assert not any(w.findChild(QObject,'widgetArrangeHint').property('visible') for w in windows)
        assert not social.calls and not voice.calls and not engine._warnings
    finally: destroy(engine, manager, windows)


def test_tools_match_rail_no_connections_and_reserved_picker_is_inert(qapp):
    engine, manager, windows, social, voice = create(qapp)
    events=[]
    for label, signal in [('Library',manager.libraryRequested),('Downloads',manager.downloadsRequested),
                          ('Controllers',manager.controllersRequested),('Settings',manager.settingsRequested),
                          ('Connections',manager.connectionsRequested)]:
        signal.connect(lambda label=label: events.append(label))
    try:
        manager.open(); manager.selectWidget(3); QTest.qWait(30)
        tools=windows[3]
        buttons=[tools.findChild(QObject,'overlayAction'+name) for name in ('Library','Downloads','Controllers','Settings')]
        assert [b.property('text') for b in buttons] == ['Library','Downloads','Controllers','Settings']
        assert [b.property('y') for b in buttons] == sorted(b.property('y') for b in buttons)
        assert tools.findChild(QObject,'overlayActionConnections') is None
        assert 'Settings > Accounts' in buttons[-1].property('hint')
        slot=tools.findChild(QObject,'overlayControllerProfileSlot')
        assert not slot.property('active') and slot.property('item') is None
        assert tools.findChild(QObject,'overlayControllerProfilePlaceholder').property('visible')
        # Controller selection wraps over tool actions only, never the unimplemented slot.
        tool_count = tools.findChild(QObject, 'overlayToolsColumn').property('toolCount')
        manager.navigate('up'); assert manager.property('toolIndex') == tool_count - 1
        manager.navigate('down'); assert manager.property('toolIndex') == 0

        # In slice 3, Library and Settings open full pages and close overlay.
        manager.open(); manager.selectWidget(3); manager.setProperty('toolIndex', 0)
        assert manager.navigate('accept') is True
        assert not manager.property('editing')
        assert events == ['Library']

        manager.open(); manager.selectWidget(3); manager.setProperty('toolIndex', 3)
        assert manager.navigate('accept') is True
        assert not manager.property('editing')
        assert events == ['Library', 'Settings']

        # In slice 3, Downloads opens compact overlay panel inside overlay (editing stays true).
        manager.open(); manager.selectWidget(3); manager.setProperty('toolIndex', 1)
        assert manager.navigate('accept') is True
        assert manager.property('editing') is True
        assert tools.property('activeTool') == 'downloads'
        # Secondary link triggers full page request
        full_btn = tools.findChild(QObject, 'overlayDownloadsOpenFull')
        assert full_btn is not None
        assert QMetaObject.invokeMethod(full_btn, 'clicked')
        assert not manager.property('editing')
        assert 'Downloads' in events

        # In slice 3, Controllers opens compact overlay panel inside overlay.
        manager.open(); manager.selectWidget(3); manager.setProperty('toolIndex', 2)
        assert manager.navigate('accept') is True
        assert manager.property('editing') is True
        assert tools.property('activeTool') == 'controllers'
        full_ctrl = tools.findChild(QObject, 'overlayControllerOpenFull')
        assert full_ctrl is not None
        assert QMetaObject.invokeMethod(full_ctrl, 'clicked')
        assert not manager.property('editing')
        assert 'Controllers' in events
        assert not social.calls and not voice.calls and not engine._warnings
    finally: destroy(engine, manager, windows)


@pytest.mark.parametrize('record,known,online,status,initials', [
    ({'gamertag':'Ada Lovelace','online':True,'presence':'Playing a game'},True,True,'Playing a game','AL'),
    ({'gamertag':'One','online':True,'presence':' '},True,True,'Online','ON'),
    ({'gamertag':'Two','online':False,'presence':'Stale game text'},True,False,'Offline','TW'),
    ({'gamertag':'Unknown','inParty':True},False,False,'Presence unavailable','UN'),
    ({'gamertag':'Invalid','online':'true'},False,False,'Presence unavailable','IN'),
    ({'xuid':'123456','online':None},False,False,'Presence unavailable','U3'),
])
def test_friend_avatar_and_status_require_explicit_presence(qapp, record, known, online, status, initials):
    social=FakeSocialView(parent=qapp); voice=FakeVoiceView(parent=qapp)
    engine=create_engine_with_context(social,voice)
    component=QQmlComponent(engine,QUrl.fromLocalFile(str(APP/'overlay_components/FriendOverlayRow.qml')))
    assert not component.isError(), component.errors()
    row=component.createWithInitialProperties({'friend':record,'width':240})
    assert row is not None, component.errors()
    try:
        assert row.property('presenceKnown') == known
        assert row.property('online') == online
        assert row.property('presenceText') == status
        assert row.property('initials') == initials
        assert row.findChild(QObject,'overlayFriendStatusDot') is not None
        # Even a supplied remote avatar is data, never a network Image source.
        row.setProperty('friend',{'gamertag':'Changed','online':False,'avatarUrl':'https://not-contacted.invalid/avatar'})
        assert row.property('presenceText') == 'Offline'
        assert not social.calls and not voice.calls and not engine._warnings
    finally:
        row.deleteLater(); engine.deleteLater(); QTest.qWait(20)
