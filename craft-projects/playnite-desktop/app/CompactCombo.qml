import QtQuick
import QtQuick.Controls
ComboBox {
    id: control
    implicitHeight: 30
    implicitWidth: 170
    leftPadding: 8
    rightPadding: 22
    font.family: Theme.family
    font.pixelSize: 13
    Accessible.name: "Selection"
    contentItem: Text {
        text: control.displayText
        textFormat: Text.PlainText
        color: Theme.text
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Text {
        x: control.width - 18
        anchors.verticalCenter: parent.verticalCenter
        text: "▾"; color: Theme.glyph; font.pixelSize: 13
    }
    background: Rectangle {
        radius: Theme.cornerRadius; color: Theme.background
        border.color: control.activeFocus ? Theme.text : Theme.line
        border.width: 1
    }
    delegate: ItemDelegate {
        width: control.width
        implicitHeight: 30
        contentItem: Text { text: control.textRole && typeof modelData === "object" ? (modelData[control.textRole] || "") : modelData; textFormat: Text.PlainText; color: Theme.text; font: control.font; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: Theme.cornerRadius; color: highlighted ? Theme.main : Theme.popup }
        highlighted: control.highlightedIndex === index
    }
    popup: Popup {
        y: control.height; width: control.width
        implicitHeight: Math.min(320, contentItem.implicitHeight + 2)
        padding: 1
        contentItem: ListView {
            clip: true; implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle { radius: Theme.cornerRadius; color: Theme.popup; border.color: Theme.line; border.width: 2 }
    }
}
