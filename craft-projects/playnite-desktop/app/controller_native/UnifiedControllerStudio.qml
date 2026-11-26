import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: controllerStudioRoot
    implicitWidth: 1440
    implicitHeight: 860

    property var service: null
    property var commands: (typeof controllerCommands !== "undefined") ? controllerCommands : null
    property var profileCommands: (typeof controllerProfileCommands !== "undefined") ? controllerProfileCommands : null
    property var deviceCommands: (typeof controllerDeviceCommands !== "undefined") ? controllerDeviceCommands : null
    property var live: null
    property string activeTab: "Map"
    property string selectedSource: ""
    property string selectedDestination: ""
    property var routes: ({})
    property var draftProfile: null
    property bool dirty: false
    property bool compact: false
    property bool capturePhysicalSelection: false
    property var previousPhysicalPressed: ({})

    function capturePressedControl() {
        if (!capturePhysicalSelection || !live || !live.available || !ctrlSurface) return
        var regions = ctrlSurface.hitRegions
        for (var i = 0; i < regions.length; ++i) {
            var code = regions[i].code
            var pressed = ctrlSurface.isControlPressed(code)
            if (pressed && !previousPhysicalPressed[code]) {
                selectedSource = code
                capturePhysicalSelection = false
                break
            }
            previousPhysicalPressed[code] = pressed
        }
    }
    function beginPhysicalSelectionCapture() {
        previousPhysicalPressed = ({})
        if (live && live.available && ctrlSurface) {
            var regions = ctrlSurface.hitRegions
            for (var i = 0; i < regions.length; ++i)
                previousPhysicalPressed[regions[i].code] = ctrlSurface.isControlPressed(regions[i].code)
        }
        capturePhysicalSelection = true
    }

    onServiceChanged: {
        if (!dirty) refreshRoutes()
    }

    property string activeLayer: "Base"

    function saveRemap() {
        if (!service || !draftProfile || !selectedSource || !selectedDestination) return
        dirty = true
        if (!draftProfile.routes) draftProfile.routes = {}
        draftProfile.routes[selectedSource] = selectedDestination
        if (!draftProfile.route_conditions) draftProfile.route_conditions = {}
        if (activeLayer === "Mod") {
            draftProfile.route_conditions[selectedSource] = { "layer": "Mod", "chord": [] }
        } else if (draftProfile.route_conditions[selectedSource] && draftProfile.route_conditions[selectedSource].layer === "Mod") {
            delete draftProfile.route_conditions[selectedSource]
        }
        service.saveProfile(JSON.stringify({ routes: draftProfile.routes, route_conditions: draftProfile.route_conditions }))  // only the mapping fields
    }

    function refreshRoutes() {
        if (!service) return
        var jsonStr = service.profileJson
        if (!jsonStr) return
        try {
            var data = JSON.parse(jsonStr)
            controllerStudioRoot.draftProfile = data
            if (data && data.routes) {
                controllerStudioRoot.routes = data.routes
            } else {
                controllerStudioRoot.routes = ({})
            }
            if (sticksTabLoader.item) {
                sticksTabLoader.item.loadFromProfile(data)
            }
            if (motionTabLoader.item) {
                motionTabLoader.item.loadFromProfile(data)
            }
            if (vibrationTabLoader.item) {
                vibrationTabLoader.item.loadFromProfile(data)
            }
            if (macrosTabLoader.item) {
                macrosTabLoader.item.loadFromProfile(data)
            }
            if (hotkeysTabLoader.item) {
                hotkeysTabLoader.item.loadFromProfile(data)
            }
            if (modulesTabLoader.item) {
                modulesTabLoader.item.loadFromProfile(data)
            }
            if (profilesTabLoader.item) {
                profilesTabLoader.item.loadFromProfile(data)
            }
            if (devicesTabLoader.item) {
                devicesTabLoader.item.loadFromProfile(data)
            }
            controllerStudioRoot.dirty = false
        } catch (e) {
            console.log("JSON parse error:", e)
        }
    }

    Connections {
        target: controllerStudioRoot.service
        function onProfileJsonChanged() {
            if (!controllerStudioRoot.dirty) controllerStudioRoot.refreshRoutes()
        }
        function onChanged() {
            if (!controllerStudioRoot.dirty && !controllerStudioRoot.service.busy) controllerStudioRoot.refreshRoutes()
        }
        function onOperationFinished(success) {
            if (success) {
                controllerStudioRoot.dirty = false
                controllerStudioRoot.refreshRoutes()
            } else if (controllerStudioRoot.service
                       && controllerStudioRoot.service.status.indexOf("Reloaded the latest profile") >= 0) {
                controllerStudioRoot.dirty = false
                controllerStudioRoot.refreshRoutes()
            }
        }
    }

    Component.onCompleted: refreshRoutes()

    Timer {
        interval: 40
        repeat: true
        running: controllerStudioRoot.capturePhysicalSelection
        onTriggered: controllerStudioRoot.capturePressedControl()
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.background
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: controllerStudioRoot.compact ? 7 : 14
        spacing: controllerStudioRoot.compact ? 6 : 10

        // 1. Top Navigation Bar (Deadband fidelity with Waystone Theme)
        Rectangle {
            visible: !controllerStudioRoot.compact
            Layout.fillWidth: true
            implicitHeight: 52
            radius: Theme.cornerRadius
            color: Theme.panel
            border.color: Theme.line
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 12

                // Brand
                RowLayout {
                    spacing: 9
                    Rectangle {
                        width: 28; height: 28; radius: Theme.cornerRadius / 2; color: Theme.main
                        Text { anchors.centerIn: parent; text: "UC"; font.bold: true; color: Theme.text; font.family: Theme.family }
                    }
                    Text {
                        text: "Unified Controller"
                        font.pixelSize: 18
                        font.bold: true
                        color: Theme.text
                        font.family: Theme.family
                        elide: Text.ElideRight
                        Layout.maximumWidth: controllerStudioRoot.width > 800 ? 240 : 130
                    }
                }

                Item { Layout.fillWidth: true }

                // Top Controls & Profile Picker
                RowLayout {
                    spacing: 8
                    CompactCombo {
                        id: topProfilePicker
                        implicitWidth: 180
                        enabled: controllerStudioRoot.service && !controllerStudioRoot.service.busy && !controllerStudioRoot.dirty
                        model: controllerStudioRoot.service ? controllerStudioRoot.service.profiles : ["Default"]
                        currentIndex: controllerStudioRoot.service ? Math.max(0, model.indexOf(controllerStudioRoot.service.editingProfile)) : 0
                        onActivated: if (controllerStudioRoot.service) controllerStudioRoot.service.loadProfile(currentText)
                    }

                    SocialButton {
                        text: "Use profile"
                        enabled: controllerStudioRoot.service && controllerStudioRoot.service.available && !controllerStudioRoot.service.busy && !controllerStudioRoot.service.uncertain && !controllerStudioRoot.dirty && (typeof controllerProfiles === "undefined" || !controllerProfiles.engineMutationBlocked) && controllerStudioRoot.service.editingProfile !== controllerStudioRoot.service.activeProfile
                        onClicked: controllerStudioRoot.service.useProfile()
                    }
                    Rectangle {
                        implicitWidth: 160; implicitHeight: 32; radius: Theme.cornerRadius / 2
                        color: Theme.background; border.color: Theme.line; border.width: 1
                        RowLayout {
                            anchors.centerIn: parent; spacing: 6
                            Rectangle {
                                width: 8; height: 8; radius: 4
                                color: controllerStudioRoot.service && controllerStudioRoot.service.available && !controllerStudioRoot.service.uncertain ? "#2fbf71" : "#e03a2f"
                            }
                            Text {
                                text: controllerStudioRoot.service ? (controllerStudioRoot.service.uncertain ? "State Uncertain" : (controllerStudioRoot.service.available ? "Engine Active" : "Disconnected")) : "Unavailable"
                                color: Theme.text; font.pixelSize: 11; font.family: Theme.family
                            }
                        }
                    }

                    SocialButton {
                        text: "Refresh"
                        enabled: controllerStudioRoot.service && !controllerStudioRoot.service.busy && !controllerStudioRoot.dirty
                        onClicked: {
                            if (controllerStudioRoot.service) controllerStudioRoot.service.refresh()
                            if (typeof controllerProfileIntegration !== "undefined") controllerProfileIntegration.refresh()
                        }
                    }
                }
            }
        }

        // Quick Guide / Intuitive Hints Banner
        Rectangle {
            visible: !controllerStudioRoot.compact
            Layout.fillWidth: true
            implicitHeight: 32
            radius: Theme.cornerRadius / 2
            color: Theme.panel
            border.color: Theme.line
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 16

                Text {
                    text: "🎮 Share = Mod Key (tap or hold for secondary shortcuts)"
                    color: "#e49b2f"; font.pixelSize: 11; font.family: Theme.family; font.bold: true
                }
                Text { text: "·"; color: Theme.muted; font.pixelSize: 11 }
                Text {
                    text: "🖱️ Mouse Mode: Share + R3 (Right Stick click) to toggle (RB=Left Click, LB=Right Click)"
                    color: Theme.text; font.pixelSize: 11; font.family: Theme.family
                }
                Text { text: "·"; color: Theme.muted; font.pixelSize: 11 }
                Text {
                    text: "🧭 Motion: 500 Hz IMU Gyro Aim & DSU Cemuhook active"
                    color: "#2fbf71"; font.pixelSize: 11; font.family: Theme.family
                }
                Item { Layout.fillWidth: true }
            }
        }

        // 2. Tab Bar Navigation (10 Primary Tabs matching upstream index)
        Flickable {
            Layout.fillWidth: true
            implicitHeight: controllerStudioRoot.compact ? 32 : 40
            contentWidth: tabRow.implicitWidth
            contentHeight: 40
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            RowLayout {
                id: tabRow
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4
                readonly property var tabs: controllerStudioRoot.compact
                    ? ["Map", "Sticks", "Motion", "Vibration", "Profiles", "More"]
                    : ["Map", "Sticks", "Motion", "Vibration", "Macros", "Hotkeys", "Modules", "Profiles", "Devices", "Live"]

                Repeater {
                    model: parent.tabs
                    delegate: Rectangle {
                        implicitWidth: tabText.implicitWidth + (controllerStudioRoot.compact ? 16 : 24)
                        implicitHeight: controllerStudioRoot.compact ? 28 : 34
                        radius: Theme.cornerRadius / 2
                        color: controllerStudioRoot.activeTab === modelData ? Theme.main : (tabMouse.containsMouse ? Theme.panel : "transparent")
                        border.color: controllerStudioRoot.activeTab === modelData ? Theme.text : "transparent"
                        border.width: 1

                        Text {
                            id: tabText
                            anchors.centerIn: parent
                            text: modelData
                            color: controllerStudioRoot.activeTab === modelData ? Theme.text : Theme.muted
                            font.family: Theme.family
                            font.pixelSize: controllerStudioRoot.compact ? 11 : 13
                            font.bold: controllerStudioRoot.activeTab === modelData
                        }

                        MouseArea {
                            id: tabMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: controllerStudioRoot.activeTab = modelData
                        }
                    }
                }
            }
        }

        // 3. Tab Body Views
        StackLayout {
            visible: !(controllerStudioRoot.compact && controllerStudioRoot.activeTab === "More")
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: Math.max(0, ["Map", "Sticks", "Motion", "Vibration", "Macros", "Hotkeys", "Modules", "Profiles", "Devices", "Live"].indexOf(controllerStudioRoot.activeTab))

            // Tab 0: Map
            Item {
                id: mapTab
                RowLayout {
                    anchors.fill: parent
                    spacing: 8

                    // Left Column: Interactive Visual Surfaces
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 8

                        // Upper row: Mapping Chips + Controller Surface + Mouse Surface
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            spacing: 8

                            // Button Mapping Chips
                            Rectangle {
                                objectName: "chipCard"
                                Layout.preferredWidth: Math.max(160, Math.min(200, controllerStudioRoot.width * 0.18))
                                Layout.minimumWidth: 140
                                Layout.fillHeight: true
                                clip: true
                                radius: Theme.cornerRadius
                                color: Theme.panel
                                border.color: Theme.line

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 10
                                    spacing: 8

                                    RowLayout {
                                        Layout.fillWidth: true
                                        Text { text: "Button Mapping"; visible: !controllerStudioRoot.compact; color: Theme.text; font.bold: true; font.pixelSize: 13; font.family: Theme.family; Layout.fillWidth: true; elide: Text.ElideRight }
                                        SocialButton {
                                            objectName: "capturePhysicalSelectionButton"
                                            text: controllerStudioRoot.capturePhysicalSelection ? "Listening…" : (controllerStudioRoot.compact ? "Listen" : "Press a button")
                                            implicitWidth: controllerStudioRoot.compact ? 72 : 106
                                            enabled: controllerStudioRoot.live && controllerStudioRoot.live.available
                                            hint: enabled ? "Press a physical controller button to select it on the map" : "Live controller input is unavailable"
                                            onClicked: controllerStudioRoot.beginPhysicalSelectionCapture()
                                        }
                                    }

                                    // Layer Toggle
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 4
                                        Rectangle {
                                            Layout.fillWidth: true
                                            height: 24
                                            radius: Theme.cornerRadius / 3
                                            color: controllerStudioRoot.activeLayer === "Base" ? Theme.main : Theme.background
                                            border.color: controllerStudioRoot.activeLayer === "Base" ? Theme.text : Theme.line
                                            Text {
                                                anchors.centerIn: parent
                                                text: "Base Layer"
                                                color: controllerStudioRoot.activeLayer === "Base" ? Theme.text : Theme.muted
                                                font.pixelSize: 10; font.bold: controllerStudioRoot.activeLayer === "Base"
                                                font.family: Theme.family
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                onClicked: controllerStudioRoot.activeLayer = "Base"
                                            }
                                        }
                                        Rectangle {
                                            Layout.fillWidth: true
                                            height: 24
                                            radius: Theme.cornerRadius / 3
                                            color: controllerStudioRoot.activeLayer === "Mod" ? "#e49b2f" : Theme.background
                                            border.color: controllerStudioRoot.activeLayer === "Mod" ? Theme.text : Theme.line
                                            Text {
                                                anchors.centerIn: parent
                                                text: "Mod Layer"
                                                color: controllerStudioRoot.activeLayer === "Mod" ? "#000000" : Theme.muted
                                                font.pixelSize: 10; font.bold: controllerStudioRoot.activeLayer === "Mod"
                                                font.family: Theme.family
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                onClicked: controllerStudioRoot.activeLayer = "Mod"
                                            }
                                        }
                                    }

                                    ListView {
                                        id: chipListView
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        clip: true
                                        model: {
                                            var keys = Object.keys(controllerStudioRoot.routes)
                                            var conds = (controllerStudioRoot.draftProfile && controllerStudioRoot.draftProfile.route_conditions) ? controllerStudioRoot.draftProfile.route_conditions : {}
                                            if (controllerStudioRoot.activeLayer === "Mod") {
                                                return keys.filter(function(k) { return conds[k] && conds[k].layer === "Mod" })
                                            } else {
                                                return keys.filter(function(k) { return !conds[k] || conds[k].layer !== "Mod" })
                                            }
                                        }
                                        delegate: Rectangle {
                                            width: chipListView.width
                                            height: 28
                                            radius: Theme.cornerRadius / 3
                                            color: controllerStudioRoot.selectedSource === modelData ? Theme.main : Theme.background
                                            border.color: Theme.line

                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.margins: 6
                                                Text { text: modelData; color: Theme.text; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true; Layout.preferredWidth: 60; Layout.maximumWidth: 120; elide: Text.ElideRight }
                                                Text { text: "→"; color: Theme.muted; font.pixelSize: 11 }
                                                Text { text: controllerStudioRoot.routes[modelData] || ""; color: "#2fbf71"; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true; Layout.preferredWidth: 80; Layout.maximumWidth: 150; elide: Text.ElideRight }
                                            }

                                            MouseArea {
                                                anchors.fill: parent
                                                onClicked: {
                                                    controllerStudioRoot.selectedSource = modelData
                                                    controllerStudioRoot.selectedDestination = controllerStudioRoot.routes[modelData] || ""
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // Controller Vector/Bitmap Surface
                            Rectangle {
                                objectName: "ctrlCard"
                                Layout.fillWidth: true
                                Layout.minimumWidth: 260
                                Layout.fillHeight: true
                                clip: true
                                radius: Theme.cornerRadius
                                color: Theme.panel
                                border.color: Theme.line

                                ControllerSurface {
                                    id: ctrlSurface
                                    objectName: "ctrlSurface"
                                    anchors.fill: parent
                                    live: controllerStudioRoot.live
                                    selectedSource: controllerStudioRoot.selectedSource
                                    selectedDestination: controllerStudioRoot.selectedDestination
                                    routes: controllerStudioRoot.routes
                                    onControlClicked: function(code, kind) {
                                        if (kind === "OpenStickTuning") {
                                            controllerStudioRoot.activeTab = "Sticks"
                                        } else {
                                            controllerStudioRoot.selectedSource = code
                                        }
                                    }
                                }
                            }

                            // Mouse Vector Surface
                            Rectangle {
                                objectName: "mouseCard"
                                Layout.preferredWidth: Math.max(140, Math.min(180, controllerStudioRoot.width * 0.15))
                                Layout.minimumWidth: 120
                                Layout.fillHeight: true
                                clip: true
                                radius: Theme.cornerRadius
                                color: Theme.panel
                                border.color: Theme.line

                                MouseSurface {
                                    id: mouseSurface
                                    objectName: "mouseSurface"
                                    anchors.fill: parent
                                    selectedSource: controllerStudioRoot.selectedSource
                                    selectedDestination: controllerStudioRoot.selectedDestination
                                    routes: controllerStudioRoot.routes
                                    onControlClicked: function(code) {
                                        controllerStudioRoot.selectedSource = code
                                    }
                                }
                            }
                        }

                        // Lower Row: Full Keyboard Surface
                        Rectangle {
                            objectName: "keyboardCard"
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.max(140, Math.min(200, controllerStudioRoot.height * 0.22))
                            clip: true
                            radius: Theme.cornerRadius
                            color: Theme.panel
                            border.color: Theme.line

                            KeyboardSurface {
                                id: keyboardSurface
                                objectName: "keyboardSurface"
                                anchors.fill: parent
                                selectedSource: controllerStudioRoot.selectedSource
                                selectedDestination: controllerStudioRoot.selectedDestination
                                routes: controllerStudioRoot.routes
                                onControlClicked: function(code) {
                                    controllerStudioRoot.selectedSource = code
                                }
                            }
                        }
                    }

                    // Right Column: Remap Assignment Panel
                    Rectangle {
                        objectName: "remapCard"
                        Layout.preferredWidth: Math.max(260, Math.min(320, controllerStudioRoot.width * 0.26))
                        Layout.minimumWidth: 240
                        Layout.fillHeight: true
                        radius: Theme.cornerRadius
                        color: Theme.panel
                        border.color: Theme.line

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 12

                            Text { text: "Assign Remap"; color: Theme.text; font.bold: true; font.pixelSize: 16; font.family: Theme.family }
                            Text {
                                text: "1. Select input control.\n2. Choose output target.\n3. Save remap."
                                color: Theme.muted; font.pixelSize: 12; font.family: Theme.family
                            }

                            Rectangle {
                                Layout.fillWidth: true; implicitHeight: 28
                                radius: Theme.cornerRadius / 3
                                color: controllerStudioRoot.activeLayer === "Mod" ? "#332a10" : "#102e1c"
                                border.color: controllerStudioRoot.activeLayer === "Mod" ? "#e49b2f" : "#2fbf71"
                                Text {
                                    anchors.centerIn: parent
                                    text: "Target: " + (controllerStudioRoot.activeLayer === "Mod" ? "MOD LAYER (Active with Share)" : "BASE LAYER (Standard)")
                                    color: controllerStudioRoot.activeLayer === "Mod" ? "#e49b2f" : "#2fbf71"
                                    font.pixelSize: 11; font.bold: true; font.family: Theme.family
                                }
                            }

                            // 1 - Input card
                            Rectangle {
                                Layout.fillWidth: true; implicitHeight: 60
                                radius: Theme.cornerRadius / 2; color: Theme.background; border.color: Theme.line
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 8; spacing: 4
                                    Text { text: "1 - INPUT SOURCE"; color: Theme.muted; font.pixelSize: 10; font.bold: true; font.family: Theme.family }
                                    Text { text: controllerStudioRoot.selectedSource || "Nothing selected"; color: controllerStudioRoot.selectedSource ? "#e49b2f" : Theme.glyph; font.bold: true; font.pixelSize: 13; font.family: Theme.family }
                                }
                            }

                            // 2 - Output card
                            Rectangle {
                                Layout.fillWidth: true; implicitHeight: 60
                                radius: Theme.cornerRadius / 2; color: Theme.background; border.color: Theme.line
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 8; spacing: 4
                                    Text { text: "2 - OUTPUT TARGET"; color: Theme.muted; font.pixelSize: 10; font.bold: true; font.family: Theme.family }
                                    Text { text: controllerStudioRoot.selectedDestination || "Nothing selected"; color: controllerStudioRoot.selectedDestination ? "#2fbf71" : Theme.glyph; font.bold: true; font.pixelSize: 13; font.family: Theme.family }
                                }
                            }

                            // Remap Actions: strictly gated on available, !busy, !uncertain, valid destination
                            RowLayout {
                                Layout.fillWidth: true
                                SocialButton {
                                    id: saveRemapButton
                                    objectName: "saveRemapButton"
                                    text: "Save Remap"
                                    enabled: controllerStudioRoot.service && controllerStudioRoot.service.available && !controllerStudioRoot.service.busy && !controllerStudioRoot.service.uncertain && controllerStudioRoot.selectedSource.length > 0 && controllerStudioRoot.selectedDestination.length > 0
                                    onClicked: controllerStudioRoot.saveRemap()
                                }

                                SocialButton {
                                    text: "Clear"
                                    enabled: controllerStudioRoot.service && controllerStudioRoot.service.available && !controllerStudioRoot.service.busy && controllerStudioRoot.selectedSource.length > 0
                                    onClicked: {
                                        if (controllerStudioRoot.routes[controllerStudioRoot.selectedSource]) {
                                            controllerStudioRoot.dirty = true
                                            delete controllerStudioRoot.draftProfile.routes[controllerStudioRoot.selectedSource]
                                            controllerStudioRoot.service.saveProfile(JSON.stringify({ routes: controllerStudioRoot.draftProfile.routes, route_conditions: controllerStudioRoot.draftProfile.route_conditions }))
                                        }
                                        controllerStudioRoot.selectedSource = ""
                                        controllerStudioRoot.selectedDestination = ""
                                    }
                                }
                            }

                            // Explanatory status notice for gated state
                            Text {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: !controllerStudioRoot.service || !controllerStudioRoot.service.available ? "Engine stopped or unavailable. Mapping changes cannot be saved." : (controllerStudioRoot.service.uncertain ? "Backend state uncertain. Reconcile or refresh before saving." : "")
                                color: "#e03a2f"; font.pixelSize: 11; font.family: Theme.family
                                visible: text.length > 0
                            }

                            Item { Layout.fillHeight: true }
                        }
                    }
                }
            }

            // Tab 1: Sticks
            // Build on first use, then keep the page alive so staged state survives tab changes.
            Loader {
                id: sticksTabLoader
                active: controllerStudioRoot.activeTab === "Sticks" || item !== null
                sourceComponent: Component {
                    SticksTab {
                        id: sticksTab

                        objectName: "sticksTab" 
                        studio: controllerStudioRoot
                        service: controllerStudioRoot.service
                        live: controllerStudioRoot.live
                        Component.onCompleted: if (controllerStudioRoot.draftProfile) loadFromProfile(controllerStudioRoot.draftProfile)
                    }
                }
            }

            // Tab 2: Motion
            // Build on first use, then keep the page alive so staged state survives tab changes.
            Loader {
                id: motionTabLoader
                active: controllerStudioRoot.activeTab === "Motion" || item !== null
                sourceComponent: Component {
                    MotionTab {
                        id: motionTab

                        objectName: "motionTab"
                        studio: controllerStudioRoot
                        service: controllerStudioRoot.service
                        commands: controllerStudioRoot.commands
                        live: controllerStudioRoot.live
                        Component.onCompleted: if (controllerStudioRoot.draftProfile) loadFromProfile(controllerStudioRoot.draftProfile)
                    }
                }
            }

            // Tab 3: Vibration
            // Build on first use, then keep the page alive so staged state survives tab changes.
            Loader {
                id: vibrationTabLoader
                active: controllerStudioRoot.activeTab === "Vibration" || item !== null
                sourceComponent: Component {
                    VibrationTab {
                        id: vibrationTab

                        objectName: "vibrationTab"
                        studio: controllerStudioRoot
                        service: controllerStudioRoot.service
                        commands: controllerStudioRoot.commands
                        live: controllerStudioRoot.live
                        Component.onCompleted: if (controllerStudioRoot.draftProfile) loadFromProfile(controllerStudioRoot.draftProfile)
                    }
                }
            }

            // Tab 4: Macros
            // Build on first use, then keep the page alive so staged state survives tab changes.
            Loader {
                id: macrosTabLoader
                active: controllerStudioRoot.activeTab === "Macros" || item !== null
                sourceComponent: Component {
                    MacrosTab {
                        id: macrosTab

                        objectName: "macrosTab"
                        studio: controllerStudioRoot
                        service: controllerStudioRoot.service
                        Component.onCompleted: if (controllerStudioRoot.draftProfile) loadFromProfile(controllerStudioRoot.draftProfile)
                    }
                }
            }

            // Tab 5: Hotkeys
            // Build on first use, then keep the page alive so staged state survives tab changes.
            Loader {
                id: hotkeysTabLoader
                active: controllerStudioRoot.activeTab === "Hotkeys" || item !== null
                sourceComponent: Component {
                    HotkeysTab {
                        id: hotkeysTab

                        objectName: "hotkeysTab"
                        studio: controllerStudioRoot
                        service: controllerStudioRoot.service
                        Component.onCompleted: if (controllerStudioRoot.draftProfile) loadFromProfile(controllerStudioRoot.draftProfile)
                    }
                }
            }

            // Tab 6: Modules
            // Build on first use, then keep the page alive so staged state survives tab changes.
            Loader {
                id: modulesTabLoader
                active: controllerStudioRoot.activeTab === "Modules" || item !== null
                sourceComponent: Component {
                    ModulesTab {
                        id: modulesTab

                        objectName: "modulesTab"
                        studio: controllerStudioRoot
                        service: controllerStudioRoot.service
                        Component.onCompleted: if (controllerStudioRoot.draftProfile) loadFromProfile(controllerStudioRoot.draftProfile)
                    }
                }
            }

            // Tab 7: Profiles
            // Build on first use, then keep the page alive so staged state survives tab changes.
            Loader {
                id: profilesTabLoader
                active: controllerStudioRoot.activeTab === "Profiles" || item !== null
                sourceComponent: Component {
                    ProfilesTab {
                        id: profilesTab

                        objectName: "profilesTab"
                        studio: controllerStudioRoot
                        service: controllerStudioRoot.service
                        profileCommands: controllerStudioRoot.profileCommands
                        Component.onCompleted: if (controllerStudioRoot.draftProfile) loadFromProfile(controllerStudioRoot.draftProfile)
                    }
                }
            }

            // Tab 8: Devices
            // Build on first use, then keep the page alive so staged state survives tab changes.
            Loader {
                id: devicesTabLoader
                active: controllerStudioRoot.activeTab === "Devices" || item !== null
                sourceComponent: Component {
                    DevicesTab {
                        id: devicesTab

                        objectName: "devicesTab"
                        studio: controllerStudioRoot
                        service: controllerStudioRoot.service
                        deviceCommands: controllerStudioRoot.deviceCommands
                        Component.onCompleted: if (controllerStudioRoot.draftProfile) loadFromProfile(controllerStudioRoot.draftProfile)
                    }
                }
            }

            // Tab 9: Live
            // Build on first use, then keep the page alive so staged state survives tab changes.
            Loader {
                id: liveTabLoader
                active: controllerStudioRoot.activeTab === "Live" || item !== null
                sourceComponent: Component {
                    LiveTab {
                        id: liveTab

                        objectName: "liveTab"
                        studio: controllerStudioRoot
                        service: controllerStudioRoot.service
                        live: controllerStudioRoot.live
                    }
                }
            }
        }

        Item {
            objectName: "controllerStudioMore"
            visible: controllerStudioRoot.compact && controllerStudioRoot.activeTab === "More"
            Layout.fillWidth: true
            Layout.fillHeight: true
            GridLayout {
                anchors.fill: parent
                columns: width > 520 ? 3 : 2
                rowSpacing: 8; columnSpacing: 8
                Repeater {
                    model: ["Macros", "Hotkeys", "Modules", "Devices", "Live"]
                    delegate: SocialButton {
                        objectName: "controllerMoreTab_" + modelData
                        text: modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44
                        onClicked: controllerStudioRoot.activeTab = modelData
                    }
                }
            }
        }
    }
}
