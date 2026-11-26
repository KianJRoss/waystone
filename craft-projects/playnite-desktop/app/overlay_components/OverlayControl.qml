import QtQuick
import QtQuick.Controls
import ".."

ToolButton {
    id: control
    property string hint: text
    property bool compact: false
    hoverEnabled: true
    Accessible.name: hint
    PlainToolTip {
        visible: control.hovered && control.hint.length > 0 && (typeof headlessCheck === "undefined" || !headlessCheck)
        text: control.hint
        delay: 500
    }
    // Padding matches the implicit width below, so labels are never elided at their natural size.
    leftPadding: compact ? 5 : 9
    rightPadding: compact ? 5 : 9
    implicitHeight: compact ? 24 : 30
    implicitWidth: Math.max(compact ? 24 : 44, label.implicitWidth + (compact ? 10 : 18))
    font.family: Theme.family
    font.pixelSize: compact ? 11 : 12
    contentItem: Text {
        id: label
        text: control.text
        color: control.enabled ? Theme.text : Theme.muted
        font: control.font
        textFormat: Text.PlainText
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        color: control.down || control.highlighted || control.hovered
            ? Qt.rgba(Theme.main.r, Theme.main.g, Theme.main.b, 0.86)
            : Qt.rgba(Theme.panel.r, Theme.panel.g, Theme.panel.b, control.compact ? 0.35 : 0.72)
        radius: Math.max(2, Theme.cornerRadius / 2)
        border.width: control.highlighted || control.visualFocus ? 2 : 1
        border.color: control.highlighted || control.visualFocus ? Theme.text
            : Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, control.compact ? 0.06 : 0.13)
    }
}
