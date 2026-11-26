import QtQuick
import QtQuick.Controls
import ".."
ScrollBar {
    id: bar
    policy: ScrollBar.AsNeeded
    visible: size < 1
    padding: 2
    contentItem: Rectangle {
        implicitWidth: 6; implicitHeight: 24; radius: 3
        color: bar.pressed || bar.hovered ? Theme.text : Theme.glyph
    }
    background: Rectangle { color: Theme.background; opacity: 0.5; radius: 3 }
}
