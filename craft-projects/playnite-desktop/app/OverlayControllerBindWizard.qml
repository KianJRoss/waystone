import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "overlay_components"
import "controller_native" as Native

Rectangle {
    id: root
    objectName: "overlayControllerBindWizard"
    color: Theme.background
    radius: Theme.cornerRadius
    border.color: Theme.line

    property var studio: null
    property var service: null
    readonly property var live: studio ? studio.live : null
    property int step: 0
    property string detectedSource: ""
    property string destination: "Controller:A"
    property string capturedOutput: ""
    property string notice: ""
    property bool outputCapture: false
    property var pendingRoutes: ({})
    property var originalRoutes: ({})
    property var pendingProfile: null
    readonly property string currentSource: step < sources.length ? sources[step] : ""
    readonly property string previousBinding: currentSource ? (pendingRoutes[currentSource] || "(unbound)") : ""
    readonly property var changedSources: sources.filter(function(source) {
        return (pendingRoutes[source] || "") !== (originalRoutes[source] || "")
    })
    readonly property var destinationChoices: capturedOutput && destinations.indexOf(capturedOutput) < 0
        ? destinations.concat([capturedOutput]) : destinations
    readonly property var sources: [
        "Controller:A", "Controller:B", "Controller:X", "Controller:Y",
        "Controller:D-pad Up", "Controller:D-pad Down", "Controller:D-pad Left", "Controller:D-pad Right",
        "Controller:LB", "Controller:RB", "Controller:LT", "Controller:RT",
        "Controller:Menu", "Controller:View", "Controller:Home", "Controller:Share",
        "Controller:Left Stick", "Controller:Right Stick"
    ]
    readonly property var destinations: [
        "Controller:A", "Controller:B", "Controller:X", "Controller:Y", "Controller:LB", "Controller:RB",
        "Controller:LT", "Controller:RT", "Controller:D-pad Up", "Controller:D-pad Down",
        "Controller:D-pad Left", "Controller:D-pad Right", "Controller:Left Stick", "Controller:Right Stick",
        "Desktop:Key:Space", "Desktop:Key:Return", "Desktop:Key:Escape", "Desktop:Key:Tab",
        "Desktop:Key:W", "Desktop:Key:A", "Desktop:Key:S", "Desktop:Key:D",
        "Desktop:Mouse:Left", "Desktop:Mouse:Right", "Desktop:Mouse:Middle",
        "Desktop:Wheel:Up", "Desktop:Wheel:Down"
    ]
    signal cancelRequested()
    signal applied()

    function initialize() {
        step = 0; detectedSource = ""; capturedOutput = ""; outputCapture = false; notice = ""
        if (!service || !service.profileJson) { pendingProfile = null; pendingRoutes = ({}); return }
        try {
            pendingProfile = JSON.parse(service.profileJson)
            if (!pendingProfile || typeof pendingProfile !== "object" || Array.isArray(pendingProfile)) throw new Error("invalid profile")
            pendingRoutes = Object.assign({}, pendingProfile.routes || {})
            originalRoutes = Object.assign({}, pendingRoutes)
        } catch (e) { pendingProfile = null; pendingRoutes = ({}); notice = "Could not read the current profile. No edits will be applied." }
        syncDestination()
    }
    function syncDestination() {
        var current = step < sources.length ? pendingRoutes[sources[step]] : ""
        destination = current && destinations.indexOf(current) >= 0 ? current : "Controller:A"
    }
    function next() {
        if (!pendingProfile || !service || service.busy || service.uncertain || step >= sources.length) return
        if (!destination || destinationChoices.indexOf(destination) < 0) return
        var staged = Object.assign({}, pendingRoutes)
        staged[sources[step]] = destination
        pendingRoutes = staged
        step += 1; notice = ""
        capturedOutput = ""
        syncDestination()
    }
    function back() {
        if (step > 0) { step -= 1; syncDestination() }
        else cancelRequested()
    }
    function applyAll() {
        if (!pendingProfile || !service || service.busy || service.uncertain || step < sources.length) return
        pendingProfile.routes = pendingRoutes
        service.saveProfile(JSON.stringify(pendingProfile))
        notice = "Sending the completed map to the active profile…"
        applied()
    }
    function cancelOutputCapture(message) {
        if (!outputCapture) return
        outputCapture = false
        notice = message || "Output capture cancelled; choose an output from the list."
        forceActiveFocus()
    }
    function captureOutput(value) {
        if (!outputCapture || !value) return
        capturedOutput = value
        destination = value
        outputCapture = false
        notice = ""
        forceActiveFocus()
    }
    function captureKey(event) {
        if (!outputCapture) return false
        if (event.key === Qt.Key_Escape) {
            cancelOutputCapture("Capture cancelled with Escape.")
            event.accepted = true
            return true
        }
        // Ignore modifier-only events. Keep the saved notation compatible with
        // the existing ControllerMappingEditor (`Keyboard:<Qt key name>`).
        if ([Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta].indexOf(event.key) >= 0) {
            event.accepted = true
            return true
        }
        var keyName = ""
        if (event.key >= Qt.Key_A && event.key <= Qt.Key_Z) keyName = String.fromCharCode(event.key)
        else if (event.key >= Qt.Key_0 && event.key <= Qt.Key_9) keyName = String.fromCharCode(event.key)
        else {
            var names = {}
            names[Qt.Key_Space] = "Space"; names[Qt.Key_Return] = "Return"; names[Qt.Key_Enter] = "Enter"
            names[Qt.Key_Tab] = "Tab"; names[Qt.Key_Backspace] = "Backspace"; names[Qt.Key_Delete] = "Delete"
            names[Qt.Key_Left] = "Left"; names[Qt.Key_Right] = "Right"; names[Qt.Key_Up] = "Up"; names[Qt.Key_Down] = "Down"
            names[Qt.Key_Home] = "Home"; names[Qt.Key_End] = "End"; names[Qt.Key_PageUp] = "PageUp"; names[Qt.Key_PageDown] = "PageDown"
            names[Qt.Key_F1] = "F1"; names[Qt.Key_F2] = "F2"; names[Qt.Key_F3] = "F3"; names[Qt.Key_F4] = "F4"
            names[Qt.Key_F5] = "F5"; names[Qt.Key_F6] = "F6"; names[Qt.Key_F7] = "F7"; names[Qt.Key_F8] = "F8"
            names[Qt.Key_F9] = "F9"; names[Qt.Key_F10] = "F10"; names[Qt.Key_F11] = "F11"; names[Qt.Key_F12] = "F12"
            keyName = names[event.key] || ""
        }
        if (!keyName) return false
        captureOutput("Desktop:Key:" + keyName)
        event.accepted = true
        return true
    }

    onVisibleChanged: if (visible) initialize()
    Component.onCompleted: if (visible) initialize()
    Keys.onPressed: function(event) { root.captureKey(event) }
    Connections {
        target: root.studio
        function onSelectedSourceChanged() {
            if (!root.visible || !root.studio) return
            root.detectedSource = root.studio.selectedSource
            if (root.step < root.sources.length && root.detectedSource === root.sources[root.step]) {
                root.notice = "Detected " + root.detectedSource + " — choose its output below."
            } else if (root.detectedSource) {
                root.notice = "Detected " + root.detectedSource + "; this step expects " + root.sources[root.step] + "."
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 12; spacing: 10
        Text {
            objectName: "bindWizardProgress"
            Layout.fillWidth: true
            text: root.step < root.sources.length
                ? "Control " + (root.step + 1) + " of " + root.sources.length
                : "Review and apply · " + root.sources.length + " controls"
            color: Theme.muted; font.family: Theme.family; font.pixelSize: 12
        }

        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 16
            visible: root.step < root.sources.length
            Rectangle {
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 270
                radius: Theme.cornerRadius / 2; color: Theme.panel; border.color: Theme.line; clip: true
                Native.ControllerSurface {
                    objectName: "bindWizardControllerDiagram"
                    anchors.centerIn: parent
                    width: Math.min(520, parent.width - 12)
                    height: Math.min(360, parent.height - 12)
                    service: root.service; live: root.live; selectedSource: root.currentSource
                    routes: root.pendingRoutes
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 280; spacing: 12
                Text {
                    objectName: "bindWizardCurrentControl"
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    text: root.currentSource
                    color: Theme.text; font.family: Theme.family; font.bold: true
                    font.pixelSize: Math.min(28, Math.max(20, root.width * 0.027))
                }
                Text {
                    objectName: "bindWizardPreviousBinding"
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    text: "Currently: " + root.previousBinding
                    color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                }
                Text {
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    text: root.detectedSource === root.currentSource
                        ? "Physical control detected ✓" : "Press the highlighted control to verify it (optional)."
                    color: root.detectedSource === root.currentSource ? "#2fbf71" : Theme.text
                    font.family: Theme.family; font.pixelSize: 12
                }
                OverlayControl {
                    objectName: "bindWizardListen"
                    text: root.studio && root.studio.capturePhysicalSelection ? "Listening for controller input…" : "Listen for controller input"
                    compact: false; Layout.fillWidth: true; Layout.minimumHeight: 34
                    enabled: Boolean(root.live && root.live.available)
                    hint: enabled ? "Press the highlighted physical control" : "Controller telemetry is unavailable; choose the named input manually"
                    onClicked: { root.detectedSource = ""; root.studio.beginPhysicalSelectionCapture() }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
                Text { text: "Map output"; color: Theme.text; font.family: Theme.family; font.bold: true; font.pixelSize: 13 }
                CompactCombo {
                    objectName: "bindWizardDestination"
                    Layout.fillWidth: true
                    model: root.destinationChoices
                    currentIndex: Math.max(0, root.destinationChoices.indexOf(root.destination))
                    onActivated: function(index) {
                        if (index >= 0 && index < root.destinationChoices.length) {
                            root.destination = root.destinationChoices[index]
                            root.capturedOutput = ""
                        }
                    }
                }
                OverlayControl {
                    objectName: "bindWizardCaptureOutput"
                    Layout.fillWidth: true; Layout.minimumHeight: 34
                    text: root.outputCapture ? "Press a key or mouse button · Esc cancels" : "Capture keyboard or mouse output"
                    compact: false; enabled: !root.outputCapture
                    onClicked: { root.outputCapture = true; root.notice = "Press the output key or mouse button. Escape cancels capture."; root.forceActiveFocus() }
                }
                Text {
                    objectName: "bindWizardCapturedOutput"
                    Layout.fillWidth: true; visible: !!root.capturedOutput
                    text: "Captured: " + root.capturedOutput
                    color: Theme.glyph; font.family: Theme.family; font.bold: true; font.pixelSize: 13
                    wrapMode: Text.WordWrap
                }
                Item { Layout.fillHeight: true }
            }
        }

        ListView {
            objectName: "bindWizardReviewList"
            visible: root.step >= root.sources.length
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; model: root.sources
            delegate: Rectangle {
                objectName: "bindWizardReviewRow_" + index
                width: ListView.view.width; height: 32
                readonly property string staged: root.pendingRoutes[modelData] || ""
                readonly property bool changed: staged !== (root.originalRoutes[modelData] || "")
                color: changed ? Qt.rgba(Theme.main.r, Theme.main.g, Theme.main.b, 0.16) : (index % 2 ? Theme.panel : "transparent")
                Row {
                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
                    Text { x: 0; width: parent.width * 0.48; anchors.verticalCenter: parent.verticalCenter; text: modelData; color: Theme.text; font.family: Theme.family; font.pixelSize: 11; elide: Text.ElideRight }
                    Text { x: parent.width * 0.5; width: parent.width * 0.48; anchors.verticalCenter: parent.verticalCenter; text: parent.parent.staged || "(unchanged)"; color: parent.parent.changed ? Theme.glyph : Theme.muted; font.family: Theme.family; font.bold: parent.parent.changed; font.pixelSize: 11; elide: Text.ElideRight }
                }
            }
        }
        Item { Layout.fillHeight: true; visible: root.step < root.sources.length }
        Text {
            Layout.fillWidth: true; visible: !!root.notice; text: root.notice
            color: root.notice.indexOf("Could not") === 0 ? "#e03a2f" : Theme.muted
            font.family: Theme.family; font.pixelSize: 10; wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            OverlayControl { objectName: "bindWizardBack"; text: "Back"; compact: false; Layout.minimumWidth: 72; Layout.preferredWidth: 80; enabled: root.step > 0; onClicked: root.back() }
            Item { Layout.fillWidth: true }
            OverlayControl {
                objectName: "bindWizardNext"
                visible: root.step < root.sources.length
                text: "Skip control"; compact: false; Layout.minimumWidth: 110; Layout.preferredWidth: 120; enabled: root.step < root.sources.length
                onClicked: { root.step += 1; root.notice = "Input skipped; existing route retained."; root.syncDestination() }
            }
            OverlayControl {
                objectName: "bindWizardAdvance"
                visible: root.step < root.sources.length
                text: "Next control"; compact: false; Layout.minimumWidth: 110; Layout.preferredWidth: 120; enabled: !!root.pendingProfile && root.service && !root.service.busy && !root.service.uncertain
                onClicked: root.next()
            }
            OverlayControl {
                objectName: "bindWizardApply"
                visible: root.step >= root.sources.length
                text: "Apply map"; compact: false; Layout.minimumWidth: 110; Layout.preferredWidth: 120
                enabled: !!root.pendingProfile && root.service && root.service.available && !root.service.busy && !root.service.uncertain
                onClicked: root.applyAll()
            }
        }
        MouseArea {
            objectName: "bindWizardMouseCapture"
            parent: root
            anchors.fill: parent
            z: 20
            visible: root.outputCapture
            acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
            hoverEnabled: false
            onPressed: function(mouse) {
                var name = mouse.button === Qt.LeftButton ? "Left" : mouse.button === Qt.RightButton ? "Right" : "Middle"
                root.captureOutput("Desktop:Mouse:" + name)
                mouse.accepted = true
            }
        }
        Text {
            Layout.fillWidth: true
            visible: !root.live || !root.live.available
            text: "This device does not currently expose physical input telemetry. No controller is grabbed by this flow; the map remains manually selectable. Vendor back/paddle inputs are omitted unless the engine exposes them."
            color: Theme.muted; font.family: Theme.family; font.pixelSize: 10; wrapMode: Text.WordWrap
        }
    }
}
