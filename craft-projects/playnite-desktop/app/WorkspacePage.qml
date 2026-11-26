import QtQuick
import QtQuick.Controls

// A destination inside the main window, never a modal or a second window.
Pane {
    id: page
    anchors.fill: parent
    padding: 16
    property string title: ""
    property string pageId: ""
    property string currentPage: ""
    property bool routed: false
    property bool standaloneOpen: false
    property bool lifecycleReady: false
    visible: routed ? currentPage === pageId : standaloneOpen
    signal navigationRequested(string destination)
    signal backRequested()
    signal opened()
    signal closed()
    function open() {
        if (routed) navigationRequested(pageId)
        else standaloneOpen = true
    }
    function close() {
        if (routed) { if (visible) backRequested() }
        else standaloneOpen = false
    }
    onVisibleChanged: {
        if (lifecycleReady) {
            if (visible) opened()
            else closed()
        }
    }
    Component.onCompleted: { lifecycleReady = true; if (visible) opened() }
    background: Rectangle { color: Theme.background }
}
