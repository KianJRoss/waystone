import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: overlay
    objectName: "waystoneOverlay"
    title: "Waystone Overlay"
    transientParent: null
    visible: false
    width: 760; height: 620
    minimumWidth: 680; minimumHeight: 540
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: Theme.background
    signal libraryRequested()
    signal controllersRequested()
    signal connectionsRequested()
    function toggle() { if (visible) hide(); else { show(); raise(); requestActivate() } }
    Shortcut { enabled: overlay.visible; sequence: "Escape"; onActivated: overlay.hide() }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 18; spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Text { text: "WAYSTONE"; color: Theme.text; font.bold: true; font.pixelSize: 18; Layout.fillWidth: true }
            SocialButton { text: "Controllers"; onClicked: { overlay.hide(); overlay.controllersRequested() } }
            SocialButton { text: "Library"; onClicked: { overlay.hide(); overlay.libraryRequested() } }
            SocialButton { text: "Close"; onClicked: overlay.hide() }
        }
        SocialPanel { Layout.fillWidth: true; Layout.fillHeight: true; compact: true; onConnectionsRequested: { overlay.hide(); overlay.connectionsRequested() } }
    }
}
