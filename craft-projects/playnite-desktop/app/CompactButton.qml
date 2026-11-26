import QtQuick
import QtQuick.Controls
Button {
    id: control
    property string hint: text
    property bool selected: false
    implicitWidth: Math.max(34, contentItem.implicitWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(30, contentItem.implicitHeight + topPadding + bottomPadding)
    leftPadding: 10; rightPadding: 10
    topPadding: 5; bottomPadding: 5
    hoverEnabled: true
    Accessible.name: hint
    PlainToolTip {
        objectName: "compactButtonToolTip"
        visible: control.hovered && control.hint.length > 0 && !headlessCheck
        text: control.hint
        delay: 500
    }
    contentItem: Text {
        text: control.text
        textFormat: Text.PlainText
        font.family: Theme.family
        font.pixelSize: 14
        color: Theme.text
        opacity: control.enabled ? (control.selected || control.hovered || control.activeFocus ? 1 : 0.8) : 0.4
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        clip: true
    }
    background: Rectangle {
        radius: Theme.cornerRadius
        color: control.down ? Theme.hover : control.selected ? Theme.main : control.hovered ? Theme.panel : "transparent"
        border.width: control.activeFocus ? 1 : 0
        border.color: Theme.text
    }
}
