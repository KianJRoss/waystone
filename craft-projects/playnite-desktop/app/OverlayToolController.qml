import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "overlay_components"
import "controller_native" as Native

Rectangle {
    id: root
    objectName: "overlayToolController"
    color: "transparent"
    implicitWidth: 920
    implicitHeight: 650

    property var toolsBridge: null
    property Component controllerProfilePicker: null
    property var service: typeof unifiedController !== "undefined" ? unifiedController : null
    property var live: typeof controllerLiveView !== "undefined" ? controllerLiveView : null
    property var commands: typeof controllerCommands !== "undefined" ? controllerCommands : null
    property var profileCommands: typeof controllerProfileCommands !== "undefined" ? controllerProfileCommands : null
    property var deviceCommands: typeof controllerDeviceCommands !== "undefined" ? controllerDeviceCommands : null
    property var profileView: typeof controllerProfiles !== "undefined" ? controllerProfiles : null
    property string gameId: profileView ? profileView.runtimeGameId : ""
    property bool binding: false
    property string trackedProfileName: ""
    property string baselineProfileJson: ""
    property string undoProfileJson: ""
    property string pendingProfileName: ""
    property string pendingProfileJson: ""
    signal openFullRequested()
    signal backRequested()

    function navigate(action) {
        if (binding) return focusNavigation.action(action)
        if (action === "previous_section" || action === "next_section") return focusNavigation.action(action)
        return focusNavigation.action(action)
    }
    function switchStudioSection(delta) {
        if (binding) return
        var tabs = ["Map", "Sticks", "Motion", "Vibration", "Profiles", "More"]
        var index = tabs.indexOf(controllerStudio.activeTab)
        if (index < 0) index = 0
        controllerStudio.activeTab = tabs[(index + delta + tabs.length) % tabs.length]
    }

    function assignedProfile() {
        if (!profileView) return ""
        return (gameId && profileView.gameProfiles[gameId]) || profileView.defaultProfile || ""
    }
    function syncAssignedProfile() {
        if (!visible || binding || !service || service.busy || !profileView || !profileView.available) return
        var name = assignedProfile()
        if (!name || service.editingProfile === name) return
        if (service.profiles.indexOf(name) >= 0) service.loadProfile(name)
    }
    function saveAsGeneral() {
        if (!profileView || !service || !service.editingProfile || profileView.engineMutationBlocked) return
        profileView.setDefaultProfile(service.editingProfile)
    }
    function undoLastChange() {
        if (!service || !undoProfileJson || service.busy || service.uncertain) return
        var current = service.profileJson
        service.saveProfile(undoProfileJson)
        undoProfileJson = current
    }
    function revertSession() {
        if (!service || !baselineProfileJson || service.busy || service.uncertain) return
        undoProfileJson = service.profileJson
        service.saveProfile(baselineProfileJson)
    }
    function requestBack() {
        if (controllerStudio.dirty) discardSticksConfirm.open()
        else root.backRequested()
    }

    Connections {
        target: root.service
        function onBusyChanged() {
            if (root.service && root.service.busy) {
                root.pendingProfileName = root.service.editingProfile
                root.pendingProfileJson = root.service.profileJson
            }
        }
        function onChanged() {
            if (!root.service || root.service.busy || !root.service.profileJson) return
            if (root.trackedProfileName !== root.service.editingProfile) {
                root.trackedProfileName = root.service.editingProfile
                root.baselineProfileJson = root.service.profileJson
                root.undoProfileJson = ""
            }
            Qt.callLater(root.syncAssignedProfile)
        }
        function onOperationFinished(success) {
            if (success && root.service && root.service.status.indexOf("change saved") >= 0
                    && root.pendingProfileName === root.service.editingProfile) {
                root.undoProfileJson = root.pendingProfileJson
            } else if (!success && root.service
                       && root.service.status.indexOf("Reloaded the latest profile") >= 0) {
                controllerStudio.dirty = false
                controllerStudio.refreshRoutes()
            }
        }
    }
    Connections {
        target: root.profileView
        function onChanged() { root.syncAssignedProfile() }
    }
    onVisibleChanged: {
        if (!visible) return
        if (commands) commands.refreshCapabilities()
        if (profileCommands) profileCommands.refreshProfiles()
        if (deviceCommands) deviceCommands.refreshDevices()
        if (service) service.refresh()
        Qt.callLater(syncAssignedProfile)
    }
    Component.onCompleted: Qt.callLater(syncAssignedProfile)

    ColumnLayout {
        anchors.fill: parent
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            OverlayControl {
                objectName: "overlayControllerBackBtn"
                visible: !root.binding
                text: "Tools"; compact: false; Layout.minimumWidth: 66
                onClicked: root.backRequested()
            }
            Text {
                text: root.binding ? "Bind controller" : (root.toolsBridge && root.toolsBridge.currentRunningTitle
                    ? "Controller studio · " + root.toolsBridge.currentRunningTitle : "Controller studio")
                color: Theme.text; font.family: Theme.family; font.bold: true; font.pixelSize: 16
                Layout.fillWidth: true; elide: Text.ElideRight
            }
            OverlayControl {
                objectName: "overlayControllerBindAll"
                visible: !root.binding
                text: "Bind controller"; compact: false; Layout.minimumWidth: 112
                enabled: root.service && root.service.available && !root.service.busy && !root.service.uncertain && !root.profileBlocked
                hint: "Guided mapping for the running game's selected controller profile"
                onClicked: root.binding = true
            }
            OverlayControl {
                objectName: "overlayControllerCloseBinding"
                visible: root.binding
                text: "Cancel binding"; compact: false; Layout.minimumWidth: 112
                onClicked: root.binding = false
            }
        }

        RowLayout {
            Layout.fillWidth: true; spacing: 8; visible: !root.binding
            OverlayControl {
                objectName: "overlayControllerUndo"
                text: "Undo"; compact: false; Layout.minimumWidth: 68
                enabled: root.service && root.undoProfileJson.length > 0 && !root.service.busy && !root.service.uncertain
                onClicked: root.undoLastChange()
            }
            OverlayControl {
                objectName: "overlayControllerRevert"
                text: "Revert session"; compact: false; Layout.minimumWidth: 112
                enabled: root.service && root.baselineProfileJson.length > 0 && root.service.profileJson !== root.baselineProfileJson && !root.service.busy && !root.service.uncertain
                onClicked: revertConfirm.open()
            }
            Item { Layout.fillWidth: true }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: !root.binding
            spacing: 4
            Loader {
                objectName: "overlayControllerProfilePicker"
                Layout.fillWidth: true
                active: root.controllerProfilePicker !== null
                sourceComponent: root.controllerProfilePicker
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                OverlayControl {
                    objectName: "overlayControllerSetGeneral"
                    text: "Set as general"; compact: false; Layout.minimumWidth: 120
                    enabled: root.profileView && root.profileView.available && root.service && root.service.editingProfile
                        && !root.profileView.engineMutationBlocked
                    hint: "Use this edited profile as the default for games without an override"
                    onClicked: root.saveAsGeneral()
                }
                OverlayControl {
                    objectName: "overlayControllerOpenFull"
                    text: "Full page"; compact: false; Layout.minimumWidth: 88
                    onClicked: root.openFullRequested()
                }
                OverlayControl {
                    objectName: "overlayControllerDeadband"
                    visible: typeof controllerDeviceCommands !== "undefined" && controllerDeviceCommands
                             && controllerDeviceCommands.deadbandAvailable
                    text: "Deadband"; compact: false; Layout.minimumWidth: 88
                    hint: "Controller hardware: onboard profiles, deadzones, curves, lighting"
                    onClicked: controllerDeviceCommands.openDeadband()
                }
                Item { Layout.fillWidth: true }
            }
        }

        Text {
            objectName: "overlayControllerProfileStatus"
            Layout.fillWidth: true
            visible: !root.binding && !!text
            text: !root.service ? "Controller service unavailable" : root.service.uncertain
                ? "Controller profile state is uncertain. Refresh in the full studio before editing."
                : "Editing: " + (root.service.editingProfile || "loading…")
                  + (root.gameId ? " · assigned to this running game" : " · general profile")
            color: root.service && root.service.uncertain ? "#e03a2f" : Theme.muted
            font.family: Theme.family; font.pixelSize: 10; elide: Text.ElideRight
        }

        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            visible: !root.binding
            Native.UnifiedControllerStudio {
                id: controllerStudio
                objectName: "overlayUnifiedControllerStudio"
                anchors.fill: parent
                compact: true
                service: root.service
                commands: root.commands
                profileCommands: root.profileCommands
                deviceCommands: root.deviceCommands
                live: root.live
            }
        }

        OverlayControllerBindWizard {
            id: bindWizard
            objectName: "overlayControllerBindWizard"
            Layout.fillWidth: true; Layout.fillHeight: true
            visible: root.binding
            studio: controllerStudio
            service: root.service
            onCancelRequested: root.binding = false
            onApplied: root.binding = false
        }
    }

    Native.ControllerFocusNavigation {
        id: focusNavigation
        objectName: "overlayControllerFocusNavigation"
        anchors.fill: parent
        scope: root.binding ? bindWizard : controllerStudio
        active: root.visible
        sectionSwitcher: function(direction) { root.switchStudioSection(direction) }
        onBackRequested: {
            if (root.binding) root.binding = false
            else root.requestBack()
        }
        Component.onCompleted: Qt.callLater(focusFirst)
    }

    readonly property bool profileBlocked: profileView && profileView.engineMutationBlocked
    Dialog {
        id: revertConfirm
        title: "Revert controller edits?"
        width: 380
        height: 150
        modal: true
        standardButtons: Dialog.Yes | Dialog.Cancel
        onAccepted: root.revertSession()
        Label { text: "Restore the profile exactly as it was when this game-profile editor was opened?"; wrapMode: Text.WordWrap }
    }
    Dialog {
        id: discardSticksConfirm
        objectName: "overlayDiscardSticksConfirm"
        title: "Discard stick tuning changes?"
        width: 380
        height: 150
        modal: true
        standardButtons: Dialog.Yes | Dialog.Cancel
        onAccepted: {
            controllerStudio.refreshRoutes()
            root.backRequested()
        }
        Label { text: "Your staged controller profile changes have not been saved."; wrapMode: Text.WordWrap }
    }
}
