import QtQuick
import QtQuick.Controls
ToolTip {
    id: tip
    padding: 8
    font.family: Theme.family
    font.pixelSize: 14
    contentItem: Text {
        text: tip.text
        textFormat: Text.PlainText
        font: tip.font
        color: Theme.text
        wrapMode: Text.Wrap
    }
    background: Rectangle {
        color: Qt.rgba(0, 0, 0, 0.9)
        border.color: Theme.line
        border.width: 2
        radius: Theme.cornerRadius
    }
}
