"""Short isolated native-window smoke: no account, voice, client or game services."""
from pathlib import Path
import ctypes, json, os, subprocess, sys
ROOT = Path(__file__).resolve().parents[1]
from PySide6.QtCore import QObject, QUrl, Qt, QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow  # Register Window wrappers before loading QML.
from PySide6.QtQml import QQmlEngine, QQmlComponent
from PySide6.QtTest import QTest

def override_redirect(window_id):
    # xcb_get_window_attributes_reply_t layout from /usr/include/xcb/xproto.h.
    class Cookie(ctypes.Structure): _fields_ = [('sequence',ctypes.c_uint)]
    lib=ctypes.CDLL('libxcb.so.1')
    lib.xcb_connect.argtypes=[ctypes.c_char_p,ctypes.POINTER(ctypes.c_int)];lib.xcb_connect.restype=ctypes.c_void_p
    lib.xcb_disconnect.argtypes=[ctypes.c_void_p]
    lib.xcb_get_window_attributes.argtypes=[ctypes.c_void_p,ctypes.c_uint32];lib.xcb_get_window_attributes.restype=Cookie
    lib.xcb_get_window_attributes_reply.argtypes=[ctypes.c_void_p,Cookie,ctypes.c_void_p];lib.xcb_get_window_attributes_reply.restype=ctypes.c_void_p
    libc=ctypes.CDLL(None);libc.free.argtypes=[ctypes.c_void_p]
    connection=lib.xcb_connect(None,None)
    try:
        cookie=lib.xcb_get_window_attributes(connection,window_id)
        reply=lib.xcb_get_window_attributes_reply(connection,cookie,None)
        assert reply, 'X server returned no window attributes'
        try:
            fields=ctypes.string_at(reply,28)
            assert fields[0]==1
            return bool(fields[27])
        finally: libc.free(reply)
    finally: lib.xcb_disconnect(connection)

app = QGuiApplication([])
app.setApplicationName('Waystone overlay probe')
engine = QQmlEngine()
warnings = []
engine.warnings.connect(lambda items: warnings.extend(x.toString() for x in items))
component = QQmlComponent(engine, QUrl.fromLocalFile(str(ROOT/'craft-projects/playnite-desktop/app/DesktopOverlay.qml')))
assert not component.isError(), component.errors()
manager = component.create()
assert manager is not None
manager.setParent(engine)
try:
    manager.open(); QTest.qWait(300)
    party = manager.findChild(QQuickWindow, 'overlayPartyWindow')
    friends = manager.findChild(QQuickWindow, 'overlayFriendsWindow')
    tools = manager.findChild(QQuickWindow, 'overlayToolsWindow')
    toolbar = manager.findChild(QQuickWindow, 'overlayToolbarWindow')
    windows = [party, friends, tools, toolbar]
    assert all(w and w.isVisible() for w in windows)
    ids = [int(w.winId()) for w in windows]
    assert len(set(ids)) == 4
    old_friend = (friends.x(), friends.y(), friends.width(), friends.height())
    party.setPosition(party.x()+33, party.y()+21)
    party.resize(party.width()+20, party.height()+10)
    QTest.qWait(120)
    assert old_friend == (friends.x(), friends.y(), friends.width(), friends.height())
    party.setProperty('pinned', True)
    manager.closeOverlay(); QTest.qWait(180)
    assert party.isVisible() and not any(w.isVisible() for w in [friends,tools,toolbar])
    assert party.flags() & Qt.WindowStaysOnTopHint
    assert party.flags() & Qt.WindowDoesNotAcceptFocus
    assert not manager.property('visible')
    hints = subprocess.check_output(['xprop','-id',hex(int(party.winId())),'_NET_WM_STATE','WM_HINTS'],text=True)
    bypass=override_redirect(int(party.winId()))
    assert '_NET_WM_STATE_ABOVE' in hints or bypass, hints
    assert 'Client accepts input or input focus: False' in hints, hints
    assert not warnings, warnings
    print(json.dumps({'native_platform':os.environ.get('QT_QPA_PLATFORM'), 'distinct_windows':4,
        'independent_geometry':True,'pin_survives_editor_close':True,
        'wm_above_or_override_redirect_verified':True,'wm_nonfocusable_verified':True,'qml_warnings':warnings,
        'no_services_attached':True,'no_game_launched':True},indent=2))
finally:
    for name in ['overlayPartyWindow','overlayFriendsWindow','overlayToolsWindow','overlayToolbarWindow']:
        win = manager.findChild(QQuickWindow,name)
        if win: win.setProperty('shown',False);win.hide()
    manager.deleteLater(); engine.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()
