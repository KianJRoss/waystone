import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "controller_native" as Native

WorkspacePage {
    id: studio
    objectName: "controllerStudio"
    focus: true
    padding: 10
    property var host: typeof controllerStudioHost !== "undefined" ? controllerStudioHost : null
    onClosed: if (typeof controllerLiveView !== "undefined") controllerLiveView.release("studio")
    onOpened: {
        if (typeof controllerLiveView !== "undefined") controllerLiveView.acquire("studio")
        if (host) host.start()
        if (typeof controllerCommands !== "undefined" && (!host || host.available)) controllerCommands.refreshCapabilities()
        if (typeof controllerProfileCommands !== "undefined" && (!host || host.available)) controllerProfileCommands.refreshProfiles()
        if (typeof controllerDeviceCommands !== "undefined" && (!host || host.available)) controllerDeviceCommands.refreshDevices()
        if (typeof unifiedController !== "undefined" && unifiedController && (!host || host.available)) unifiedController.refresh()
    }
    Connections {
        target: studio.host
        function onReady() {
            if (studio.visible) {
                if (typeof controllerCommands !== "undefined") controllerCommands.refreshCapabilities()
                if (typeof controllerProfileCommands !== "undefined") controllerProfileCommands.refreshProfiles()
                if (typeof controllerDeviceCommands !== "undefined") controllerDeviceCommands.refreshDevices()
                if (typeof unifiedController !== "undefined" && unifiedController) unifiedController.refresh()
            }
        }
    }
    background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: Theme.cornerRadius }
    ColumnLayout {
        anchors.fill: parent
        RowLayout {
            Layout.fillWidth: true
            Text { visible: !studio.routed; text: "Controllers"; color: Theme.text; font.bold: true; font.family: Theme.family; font.pixelSize: 18 }
            Text { Layout.fillWidth: true; text: typeof controllerLiveView !== "undefined" && controllerLiveView.available ? controllerLiveView.status : (studio.host ? studio.host.status : "Controller engine unavailable"); color: Theme.muted; elide: Text.ElideRight; textFormat: Text.PlainText }
            Button { visible: !studio.routed; text: "Back to library"; onClicked: studio.close() }
        }
        Text {
            Layout.fillWidth: true
            text: "Native controller settings · Commands depend on connected-device capabilities. Unsupported features are marked unavailable."
            color: Theme.muted; font.family: Theme.family; wrapMode: Text.WordWrap
        }
        Native.UnifiedControllerStudio {
            objectName: "nativeControllerStudio"
            Layout.fillWidth: true
            Layout.fillHeight: true
            live: typeof controllerLiveView !== "undefined" ? controllerLiveView : null
            service: typeof unifiedController !== "undefined" ? unifiedController : null
        }
    }
}
