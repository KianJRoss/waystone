import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    property var studio: null
    property var service: null
    property var commands: null
    property var profileData: null
    property var live: null

    // Presets
    readonly property var presetsList: ["native", "gyro_aim", "tilt_drive", "dual_motion", "inertia_aim", "custom"]
    property string preset: "native"

    // Capabilities gating on typed commands contract
    readonly property bool canRecenter: Boolean(commands && commands.available && !commands.busy && !commands.uncertain && commands.capabilities && commands.capabilities.indexOf("motion_recenter") !== -1)
    readonly property bool canCalibrate: Boolean(commands && commands.available && !commands.busy && !commands.uncertain && commands.capabilities && commands.capabilities.indexOf("motion_calibrate") !== -1)

    onVisibleChanged: {
        if (visible && commands && typeof commands.refreshCapabilities === "function") {
            commands.refreshCapabilities()
        }
    }

    // Left stick motion
    property bool leftEnabled: false
    property string leftMode: "rate"
    property string leftXAxis: "yaw"
    property string leftYAxis: "pitch"
    property string leftActivation: "always"
    property real leftFullScale: 120.0
    property real leftDeadzone: 2.0
    property real leftCurve: 1.0

    // Right stick motion
    property bool rightEnabled: false
    property string rightMode: "rate"
    property string rightXAxis: "yaw"
    property string rightYAxis: "pitch"
    property string rightActivation: "always"
    property real rightFullScale: 120.0
    property real rightDeadzone: 2.0
    property real rightCurve: 1.0

    // Live orientation & DSU state (observational, strictly gated to telemetry)
    readonly property bool liveActive: Boolean(root.live && root.live.available)
    readonly property var liveOrientation: (liveActive && root.live.frame && root.live.frame.orientation) ? root.live.frame.orientation : null
    readonly property var liveDsu: (liveActive && root.live.frame && root.live.frame.dsu) ? root.live.frame.dsu : null

    // Validation error text
    property string validationError: ""
    property string statusMessage: ""

    function isFiniteNum(val) {
        return typeof val === "number" && !isNaN(val) && isFinite(val)
    }

    function loadFromProfile(data) {
        if (!data) return
        root.profileData = data

        var m = data.motion || {}
        root.preset = (presetsList.indexOf(m.preset) !== -1) ? m.preset : "native"

        var l = m.left || {}
        root.leftEnabled = (l.enabled === true)
        root.leftMode = l.mode || "rate"
        root.leftXAxis = l.x_axis || "yaw"
        root.leftYAxis = l.y_axis || "pitch"
        root.leftActivation = l.activation || "always"
        root.leftFullScale = isFiniteNum(l.full_scale) ? l.full_scale : 120.0
        root.leftDeadzone = isFiniteNum(l.deadzone) ? l.deadzone : 2.0
        root.leftCurve = isFiniteNum(l.curve) ? l.curve : 1.0

        var r = m.right || {}
        root.rightEnabled = (r.enabled === true)
        root.rightMode = r.mode || "rate"
        root.rightXAxis = r.x_axis || "yaw"
        root.rightYAxis = r.y_axis || "pitch"
        root.rightActivation = r.activation || "always"
        root.rightFullScale = isFiniteNum(r.full_scale) ? r.full_scale : 120.0
        root.rightDeadzone = isFiniteNum(r.deadzone) ? r.deadzone : 2.0
        root.rightCurve = isFiniteNum(r.curve) ? r.curve : 1.0

        root.validationError = ""
        root.statusMessage = ""
    }

    function validateAndApply() {
        if (!isFiniteNum(leftFullScale) || !isFiniteNum(leftDeadzone) || !isFiniteNum(leftCurve) ||
            !isFiniteNum(rightFullScale) || !isFiniteNum(rightDeadzone) || !isFiniteNum(rightCurve)) {
            validationError = "Motion parameters must be finite numbers."
            return false
        }

        if (leftFullScale < 5.0 || leftFullScale > 360.0 || rightFullScale < 5.0 || rightFullScale > 360.0) {
            validationError = "Full scale must be between 5 and 360 dps."
            return false
        }

        if (leftDeadzone < 0.0 || leftDeadzone > 60.0 || rightDeadzone < 0.0 || rightDeadzone > 60.0) {
            validationError = "Deadzone must be between 0 and 60."
            return false
        }

        if (leftCurve < 0.5 || leftCurve > 3.0 || rightCurve < 0.5 || rightCurve > 3.0) {
            validationError = "Curve exponent must be between 0.5 and 3.0."
            return false
        }

        validationError = ""

        if (!profileData) profileData = {}
        if (!profileData.motion) profileData.motion = {}

        profileData.motion.preset = root.preset

        // Preserve unknown nested fields in left
        if (!profileData.motion.left) profileData.motion.left = {}
        profileData.motion.left.enabled = root.leftEnabled
        profileData.motion.left.mode = root.leftMode
        profileData.motion.left.x_axis = root.leftXAxis
        profileData.motion.left.y_axis = root.leftYAxis
        profileData.motion.left.x_enabled = true
        profileData.motion.left.y_enabled = true
        profileData.motion.left.full_scale = root.leftFullScale
        profileData.motion.left.deadzone = root.leftDeadzone
        profileData.motion.left.curve = root.leftCurve
        profileData.motion.left.activation = root.leftActivation

        // Preserve unknown nested fields in right
        if (!profileData.motion.right) profileData.motion.right = {}
        profileData.motion.right.enabled = root.rightEnabled
        profileData.motion.right.mode = root.rightMode
        profileData.motion.right.x_axis = root.rightXAxis
        profileData.motion.right.y_axis = root.rightYAxis
        profileData.motion.right.x_enabled = true
        profileData.motion.right.y_enabled = true
        profileData.motion.right.full_scale = root.rightFullScale
        profileData.motion.right.deadzone = root.rightDeadzone
        profileData.motion.right.curve = root.rightCurve
        profileData.motion.right.activation = root.rightActivation

        // Remove legacy motion_mapping key per original ProfileEditorService contract
        if (profileData.motion_mapping !== undefined) {
            delete profileData.motion_mapping
        }

        return true
    }

    Connections {
        target: root.service
        function onOperationFinished(success) {
            if (success) {
                root.statusMessage = "Motion settings saved."
                if (root.studio) root.studio.dirty = false
            } else {
                root.statusMessage = "Save failed. Draft preserved."
            }
        }
    }

    function saveMotion() {
        if (validateAndApply()) {
            if (root.studio) root.studio.dirty = true
            root.statusMessage = "Saving motion settings…"
            root.service.saveProfile(JSON.stringify({ motion: root.profileData.motion, motion_mapping: root.profileData.motion_mapping }))  // only this tab's fields
        }
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: 14

            // Quick Motion Guide
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 46
                radius: Theme.cornerRadius / 3
                color: "#182638"
                border.color: "#3a6ea5"
                border.width: 1

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    spacing: 8

                    Text {
                        text: "💡 Motion Guide:"
                        color: "#5bc0de"; font.bold: true; font.pixelSize: 11; font.family: Theme.family
                    }
                    Text {
                        text: "• 'Gyro Aim': Controller gyro aims the right stick (activation 'LT' = aim down sights when holding Left Trigger).\n• 'Native': Streams 500 Hz IMU over UDP 26760 to Switch/Wii U/3DS emulators (Citron, Eden, Dolphin, Ryujinx, Cemu)."
                        color: Theme.text; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    }
                }
            }

            // Header & Save / Reset Bar
            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Text {
                    text: "Motion Mapping & Gyro Axes"
                    font.pixelSize: 18; font.bold: true; color: Theme.text; font.family: Theme.family
                    Layout.fillWidth: true
                }

                RowLayout {
                    spacing: 6
                    Text { text: "Preset:"; color: Theme.muted; font.pixelSize: 13; font.family: Theme.family }
                    CompactCombo {
                        id: presetPicker
                        implicitWidth: 150
                        model: root.presetsList
                        currentIndex: Math.max(0, root.presetsList.indexOf(root.preset))
                        onActivated: {
                            root.preset = currentText
                            if (root.preset === "gyro_aim") {
                                root.rightEnabled = true; root.rightMode = "rate"; root.rightXAxis = "yaw"; root.rightYAxis = "pitch"; root.rightActivation = "LT"
                                root.leftEnabled = false
                            } else if (root.preset === "tilt_drive") {
                                root.leftEnabled = true; root.leftMode = "tilt"; root.leftXAxis = "roll"; root.leftFullScale = 30
                                root.rightEnabled = false
                            } else if (root.preset === "dual_motion") {
                                root.leftEnabled = true; root.leftMode = "tilt"; root.leftXAxis = "roll"; root.leftFullScale = 30
                                root.rightEnabled = true; root.rightMode = "rate"; root.rightXAxis = "yaw"; root.rightYAxis = "pitch"
                            } else if (root.preset === "native") {
                                root.leftEnabled = false; root.rightEnabled = false
                            }
                            if (root.studio) root.studio.dirty = true
                        }
                    }
                }

                SocialButton {
                    id: saveMotionButton
                    objectName: "saveMotionButton"
                    text: "Save Motion"
                    enabled: root.service && root.service.available && !root.service.busy && !root.service.uncertain
                    onClicked: root.saveMotion()
                }

                SocialButton {
                    text: "Reset Defaults"
                    onClicked: {
                        root.preset = "native"
                        root.leftEnabled = false; root.leftMode = "rate"; root.leftXAxis = "yaw"; root.leftYAxis = "pitch"; root.leftActivation = "always"; root.leftFullScale = 120.0; root.leftDeadzone = 2.0; root.leftCurve = 1.0
                        root.rightEnabled = false; root.rightMode = "rate"; root.rightXAxis = "yaw"; root.rightYAxis = "pitch"; root.rightActivation = "always"; root.rightFullScale = 120.0; root.rightDeadzone = 2.0; root.rightCurve = 1.0
                        root.validationError = ""
                        root.statusMessage = ""
                        if (root.studio) root.studio.dirty = true
                    }
                }
            }

            // Status feedback / Validation banner
            Text {
                Layout.fillWidth: true
                text: root.validationError
                color: "#e03a2f"; font.pixelSize: 12; font.family: Theme.family
                visible: root.validationError.length > 0
            }

            Text {
                Layout.fillWidth: true
                text: root.statusMessage
                color: "#2fbf71"; font.pixelSize: 12; font.family: Theme.family
                visible: root.statusMessage.length > 0 && root.validationError.length === 0
            }

            // Card 1: 3D Gyro / Live Orientation
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 180
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "3D Gyro · Live Orientation"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family; Layout.fillWidth: true }
                        Text {
                            text: root.liveOrientation ? "Tracking active" : "IMU sampling telemetry unavailable (staged)"
                            color: root.liveOrientation ? "#2fbf71" : Theme.muted
                            font.pixelSize: 11; font.family: Theme.family
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 20

                        // 3D Gyro visual orientation box
                        Rectangle {
                            width: 140; height: 90
                            radius: Theme.cornerRadius / 2
                            color: Theme.background
                            border.color: Theme.line

                            // Orientation visual indicator
                            Rectangle {
                                width: 50; height: 34
                                anchors.centerIn: parent
                                radius: 4
                                color: Theme.main
                                border.color: root.liveOrientation ? "#2fbf71" : Theme.glyph
                                border.width: 1.5
                                rotation: root.liveOrientation ? (root.liveOrientation.roll_deg || root.liveOrientation.roll || 0) : 0
                                scale: root.liveOrientation ? Math.max(0.5, Math.min(1.5, 1.0 + ((root.liveOrientation.pitch_deg || root.liveOrientation.pitch || 0) / 90.0) * 0.3)) : 1.0
                                Text {
                                    anchors.centerIn: parent
                                    text: "IMU"
                                    color: Theme.text
                                    font.pixelSize: 10
                                    font.bold: true
                                    font.family: Theme.family
                                }
                            }
                        }

                        // Readout Grid
                        GridLayout {
                            Layout.fillWidth: true
                            columns: 3
                            rowSpacing: 8; columnSpacing: 16

                            ColumnLayout {
                                Text { text: "Pitch"; color: Theme.muted; font.pixelSize: 10; font.family: Theme.family }
                                Text { text: (root.liveOrientation && (root.liveOrientation.pitch_deg !== undefined || root.liveOrientation.pitch !== undefined)) ? (root.liveOrientation.pitch_deg !== undefined ? root.liveOrientation.pitch_deg : root.liveOrientation.pitch).toFixed(1) + "°" : "Unavailable"; color: Theme.text; font.pixelSize: 14; font.bold: true; font.family: Theme.family }
                            }
                            ColumnLayout {
                                Text { text: "Roll"; color: Theme.muted; font.pixelSize: 10; font.family: Theme.family }
                                Text { text: (root.liveOrientation && (root.liveOrientation.roll_deg !== undefined || root.liveOrientation.roll !== undefined)) ? (root.liveOrientation.roll_deg !== undefined ? root.liveOrientation.roll_deg : root.liveOrientation.roll).toFixed(1) + "°" : "Unavailable"; color: Theme.text; font.pixelSize: 14; font.bold: true; font.family: Theme.family }
                            }
                            ColumnLayout {
                                Text { text: "Yaw"; color: Theme.muted; font.pixelSize: 10; font.family: Theme.family }
                                Text { text: (root.liveOrientation && (root.liveOrientation.yaw_deg !== undefined || root.liveOrientation.yaw !== undefined)) ? (root.liveOrientation.yaw_deg !== undefined ? root.liveOrientation.yaw_deg : root.liveOrientation.yaw).toFixed(1) + "°" : "Unavailable"; color: Theme.text; font.pixelSize: 14; font.bold: true; font.family: Theme.family }
                            }
                            ColumnLayout {
                                Text { text: "Angular Velocity"; color: Theme.muted; font.pixelSize: 10; font.family: Theme.family }
                                Text { text: (root.liveOrientation && root.liveOrientation.angular_velocity_dps !== undefined) ? root.liveOrientation.angular_velocity_dps.toFixed(1) + " dps" : "Unavailable"; color: Theme.text; font.pixelSize: 14; font.bold: true; font.family: Theme.family }
                            }
                            ColumnLayout {
                                Text { text: "Total Accel"; color: Theme.muted; font.pixelSize: 10; font.family: Theme.family }
                                Text { text: (root.liveOrientation && root.liveOrientation.total_accel_g !== undefined) ? root.liveOrientation.total_accel_g.toFixed(2) + " g" : "Unavailable"; color: Theme.text; font.pixelSize: 14; font.bold: true; font.family: Theme.family }
                            }
                            ColumnLayout {
                                Text { text: "Stillness"; color: Theme.muted; font.pixelSize: 10; font.family: Theme.family }
                                Text { text: root.liveOrientation ? (root.liveOrientation.still ? "Still" : "Moving") : "Unavailable"; color: Theme.text; font.pixelSize: 14; font.bold: true; font.family: Theme.family }
                            }
                        }
                    }

                    // Live calibration controls (honestly labeled)
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        SocialButton {
                            objectName: "motionRecenterButton"
                            text: "Recenter"
                            enabled: root.canRecenter
                            onClicked: if (root.commands && typeof root.commands.recenterMotion === "function") root.commands.recenterMotion()
                        }
                        SocialButton {
                            objectName: "motionCalibrateButton"
                            text: "Calibrate"
                            enabled: root.canCalibrate
                            onClicked: if (root.commands && typeof root.commands.calibrateMotion === "function") root.commands.calibrateMotion()
                        }
                        Text {
                            text: root.commands ? root.commands.status : "Native command controller unavailable."
                            color: Theme.muted; font.pixelSize: 11; font.family: Theme.family
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            // Card 2: DSU / Cemuhook
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 70
                radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                RowLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 12
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text { text: "DSU / Cemuhook Motion Server"; font.bold: true; font.pixelSize: 14; color: Theme.text; font.family: Theme.family }
                        Text { text: "Citron/Eden: Controls → Motion → cemuhook 127.0.0.1:26760"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
                    }
                    Rectangle {
                        implicitWidth: dsuText.implicitWidth + 16
                        implicitHeight: 26
                        radius: 13
                        color: root.liveDsu && root.liveDsu.running ? "#2fbf71" : Theme.main
                        Text {
                            id: dsuText
                            anchors.centerIn: parent
                            text: root.liveDsu && root.liveDsu.running ? ("Running · " + (root.liveDsu.clients || 0) + " clients") : "DSU unavailable (staged)"
                            color: Theme.text
                            font.pixelSize: 11
                            font.bold: true
                            font.family: Theme.family
                        }
                    }
                }
            }

            // Card 3: Left & Right Motion Mapping Cards
            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                // Left Stick Motion Card
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 330
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 8

                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Left Stick Motion"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family; Layout.fillWidth: true }
                            CheckBox {
                                text: "Enabled"
                                checked: root.leftEnabled
                                onToggled: { root.leftEnabled = checked; if (root.studio) root.studio.dirty = true }
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: 2
                            rowSpacing: 8; columnSpacing: 12

                            Text { text: "Mode:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
                            CompactCombo {
                                Layout.fillWidth: true
                                model: ["rate", "tilt", "inertia"]
                                currentIndex: Math.max(0, model.indexOf(root.leftMode))
                                onActivated: { root.leftMode = currentText; if (root.studio) root.studio.dirty = true }
                            }

                            Text { text: "Horizontal Source:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
                            CompactCombo {
                                Layout.fillWidth: true
                                model: ["yaw", "pitch", "roll"]
                                currentIndex: Math.max(0, model.indexOf(root.leftXAxis))
                                onActivated: { root.leftXAxis = currentText; if (root.studio) root.studio.dirty = true }
                            }

                            Text { text: "Vertical Source:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
                            CompactCombo {
                                Layout.fillWidth: true
                                model: ["pitch", "yaw", "roll"]
                                currentIndex: Math.max(0, model.indexOf(root.leftYAxis))
                                onActivated: { root.leftYAxis = currentText; if (root.studio) root.studio.dirty = true }
                            }

                            Text { text: "Activation:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
                            CompactCombo {
                                Layout.fillWidth: true
                                model: ["always", "LT", "RT", "LB", "RB"]
                                currentIndex: Math.max(0, model.indexOf(root.leftActivation))
                                onActivated: { root.leftActivation = currentText; if (root.studio) root.studio.dirty = true }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Full Scale: " + Math.round(root.leftFullScale) + " dps"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                from: 5; to: 360; stepSize: 5; value: root.leftFullScale
                                onMoved: { root.leftFullScale = value; if (root.studio) root.studio.dirty = true }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Deadzone: " + root.leftDeadzone.toFixed(1); color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                from: 0; to: 60; stepSize: 1; value: root.leftDeadzone
                                onMoved: { root.leftDeadzone = value; if (root.studio) root.studio.dirty = true }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Curve Exponent: " + root.leftCurve.toFixed(2); color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                from: 0.5; to: 3.0; stepSize: 0.05; value: root.leftCurve
                                onMoved: { root.leftCurve = value; if (root.studio) root.studio.dirty = true }
                            }
                        }
                    }
                }

                // Right Stick Motion Card
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 330
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 8

                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Right Stick Motion"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family; Layout.fillWidth: true }
                            CheckBox {
                                text: "Enabled"
                                checked: root.rightEnabled
                                onToggled: { root.rightEnabled = checked; if (root.studio) root.studio.dirty = true }
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: 2
                            rowSpacing: 8; columnSpacing: 12

                            Text { text: "Mode:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
                            CompactCombo {
                                Layout.fillWidth: true
                                model: ["rate", "tilt", "inertia"]
                                currentIndex: Math.max(0, model.indexOf(root.rightMode))
                                onActivated: { root.rightMode = currentText; if (root.studio) root.studio.dirty = true }
                            }

                            Text { text: "Horizontal Source:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
                            CompactCombo {
                                Layout.fillWidth: true
                                model: ["yaw", "pitch", "roll"]
                                currentIndex: Math.max(0, model.indexOf(root.rightXAxis))
                                onActivated: { root.rightXAxis = currentText; if (root.studio) root.studio.dirty = true }
                            }

                            Text { text: "Vertical Source:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
                            CompactCombo {
                                Layout.fillWidth: true
                                model: ["pitch", "yaw", "roll"]
                                currentIndex: Math.max(0, model.indexOf(root.rightYAxis))
                                onActivated: { root.rightYAxis = currentText; if (root.studio) root.studio.dirty = true }
                            }

                            Text { text: "Activation:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
                            CompactCombo {
                                Layout.fillWidth: true
                                model: ["always", "LT", "RT", "LB", "RB"]
                                currentIndex: Math.max(0, model.indexOf(root.rightActivation))
                                onActivated: { root.rightActivation = currentText; if (root.studio) root.studio.dirty = true }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Full Scale: " + Math.round(root.rightFullScale) + " dps"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                from: 5; to: 360; stepSize: 5; value: root.rightFullScale
                                onMoved: { root.rightFullScale = value; if (root.studio) root.studio.dirty = true }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Deadzone: " + root.rightDeadzone.toFixed(1); color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                from: 0; to: 60; stepSize: 1; value: root.rightDeadzone
                                onMoved: { root.rightDeadzone = value; if (root.studio) root.studio.dirty = true }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Curve Exponent: " + root.rightCurve.toFixed(2); color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                from: 0.5; to: 3.0; stepSize: 0.05; value: root.rightCurve
                                onMoved: { root.rightCurve = value; if (root.studio) root.studio.dirty = true }
                            }
                        }
                    }
                }
            }

            Text {
                text: "Unknown native motion and sensor fields are preserved across saves."
                color: Theme.muted; font.pixelSize: 11; font.family: Theme.family
            }
        }
    }
}
