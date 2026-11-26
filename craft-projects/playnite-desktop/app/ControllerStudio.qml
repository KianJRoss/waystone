import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Full original UI is a native child of THIS Waystone window, not a new launcher.
WorkspacePage {
    id: studio
    objectName: "controllerStudio"
    property var host: typeof controllerStudioHost !== "undefined" ? controllerStudioHost : null
    focus: true
    padding: 10
    background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: 10 }
    onOpened: if (host) host.open()
    ColumnLayout {
        anchors.fill: parent
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            Text { visible: !studio.routed; text: "Controllers"; color: Theme.text; font.pixelSize: 18; font.bold: true }
            Text { Layout.fillWidth: true; text: studio.host ? studio.host.status : "Controller studio unavailable"; color: Theme.muted; elide: Text.ElideRight; textFormat: Text.PlainText }
            Button { visible: !studio.routed; text: "Back to library"; onClicked: studio.close() }
        }
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            WindowContainer {
                anchors.fill: parent
                // Hiding only the view keeps controller forwarding/macros alive.
                window: studio.host ? studio.host.nativeWindow : null
                visible: studio.visible && window !== null
            }
            Text {
                anchors.centerIn: parent
                width: Math.min(parent.width - 32, 680)
                visible: !studio.host || !studio.host.nativeWindow
                text: studio.host ? studio.host.status : "The original controller studio is not included in this build."
                color: Theme.text
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                textFormat: Text.PlainText
            }
        }
    }
}
