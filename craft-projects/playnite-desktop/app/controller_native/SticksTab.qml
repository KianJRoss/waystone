import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root
    enabled: root.service && root.service.available && !root.service.busy && !root.service.uncertain

    property var studio: null
    property var service: null
    property var profileData: null
    property var live: null

    readonly property bool liveActive: Boolean(root.live && root.live.available && root.live.frame && root.live.frame.input)
    readonly property var liveInput: liveActive ? root.live.frame.input : null
    readonly property var liveOutput: liveActive && root.live.frame.output ? root.live.frame.output : null

    readonly property real normLeftX: liveInput ? ((liveInput.left_x < 0) ? (liveInput.left_x / 32768.0) : (liveInput.left_x / 32767.0)) : 0.0
    readonly property real normLeftY: liveInput ? ((liveInput.left_y < 0) ? (liveInput.left_y / 32768.0) : (liveInput.left_y / 32767.0)) : 0.0
    readonly property real normRightX: liveInput ? ((liveInput.right_x < 0) ? (liveInput.right_x / 32768.0) : (liveInput.right_x / 32767.0)) : 0.0
    readonly property real normRightY: liveInput ? ((liveInput.right_y < 0) ? (liveInput.right_y / 32768.0) : (liveInput.right_y / 32767.0)) : 0.0
    readonly property real processedLeftX: liveOutput ? ((liveOutput.left_x < 0) ? (liveOutput.left_x / 32768.0) : (liveOutput.left_x / 32767.0)) : 0.0
    readonly property real processedLeftY: liveOutput ? ((liveOutput.left_y < 0) ? (liveOutput.left_y / 32768.0) : (liveOutput.left_y / 32767.0)) : 0.0
    readonly property real processedRightX: liveOutput ? ((liveOutput.right_x < 0) ? (liveOutput.right_x / 32768.0) : (liveOutput.right_x / 32767.0)) : 0.0
    readonly property real processedRightY: liveOutput ? ((liveOutput.right_y < 0) ? (liveOutput.right_y / 32768.0) : (liveOutput.right_y / 32767.0)) : 0.0

    // Left stick state
    property bool leftEnabled: true
    property real leftInner: 0.08
    property real leftOuter: 1.0
    property real leftAnti: 0.0
    property real leftCurve: 1.0

    // Right stick state
    property bool rightEnabled: true
    property real rightInner: 0.08
    property real rightOuter: 1.0
    property real rightAnti: 0.0
    property real rightCurve: 1.0
    property bool sameForBoth: false


    // Validation error text
    property string validationError: ""

    function stageValue(leftName, rightName, value, isLeft) {
        root[isLeft ? leftName : rightName] = value
        if (root.sameForBoth) root[isLeft ? rightName : leftName] = value
        if (root.studio) root.studio.dirty = true
    }

    function discardTuning() {
        if (!root.service || !root.service.profileJson) return false
        try {
            root.loadFromProfile(JSON.parse(root.service.profileJson))
            if (root.studio) root.studio.dirty = false
            return true
        } catch (e) { return false }
    }

    function syncLiveLease() {
        if (!root.live) return
        if (root.visible) root.live.acquire("sticks")
        else root.live.release("sticks")
    }
    onVisibleChanged: syncLiveLease()
    Component.onDestruction: if (root.live) root.live.release("sticks")

    function isFiniteNum(val) {
        return typeof val === "number" && !isNaN(val) && isFinite(val)
    }

    function loadFromProfile(data) {
        if (!data) return
        root.profileData = data

        // Stick processing
        var sp = data.stick_processing || {}
        var l = sp.left || {}
        root.leftEnabled = (l.enabled !== false)
        root.leftInner = isFiniteNum(l.inner_deadzone) ? l.inner_deadzone : 0.08
        root.leftOuter = isFiniteNum(l.outer_deadzone) ? l.outer_deadzone : 1.0
        root.leftAnti = isFiniteNum(l.anti_deadzone) ? l.anti_deadzone : 0.0
        root.leftCurve = isFiniteNum(l.curve) ? l.curve : 1.0

        var r = sp.right || {}
        root.rightEnabled = (r.enabled !== false)
        root.rightInner = isFiniteNum(r.inner_deadzone) ? r.inner_deadzone : 0.08
        root.rightOuter = isFiniteNum(r.outer_deadzone) ? r.outer_deadzone : 1.0
        root.rightAnti = isFiniteNum(r.anti_deadzone) ? r.anti_deadzone : 0.0
        root.rightCurve = isFiniteNum(r.curve) ? r.curve : 1.0


        root.validationError = ""
    }

    function saveTuning() {
        if (validateAndApply()) {
            if (root.studio) root.studio.dirty = true
            // only the stick settings: other tabs' settings in this profile are left alone
            root.service.saveProfile(JSON.stringify({ stick_processing: root.profileData.stick_processing }))
        }
    }

    function validateAndApply() {
        // Enforce typed numerical constraints without silent clamping:
        // Reject NaN or Infinity
        if (!isFiniteNum(leftInner) || !isFiniteNum(leftOuter) || !isFiniteNum(leftAnti) || !isFiniteNum(leftCurve) ||
            !isFiniteNum(rightInner) || !isFiniteNum(rightOuter) || !isFiniteNum(rightAnti) || !isFiniteNum(rightCurve)) {
            validationError = "Values must be finite numbers."
            return false
        }

        // Deadzone inner/outer bounds
        if (leftInner < 0.0 || leftInner > 0.5 || leftOuter < 0.5 || leftOuter > 1.0 || leftInner >= leftOuter) {
            validationError = "Left stick requires inner 0–0.5 and outer 0.5–1.0, with inner below outer."
            return false
        }
        if (rightInner < 0.0 || rightInner > 0.5 || rightOuter < 0.5 || rightOuter > 1.0 || rightInner >= rightOuter) {
            validationError = "Right stick requires inner 0–0.5 and outer 0.5–1.0, with inner below outer."
            return false
        }

        if (leftAnti < 0.0 || leftAnti > 0.5 || rightAnti < 0.0 || rightAnti > 0.5) {
            validationError = "Anti-deadzone must be between 0 and 0.5."
            return false
        }

        // Curve exponent bounds
        if (leftCurve < 0.2 || leftCurve > 3.0 || rightCurve < 0.2 || rightCurve > 3.0) {
            validationError = "Curve exponent must be between 0.2 and 3.0."
            return false
        }

        validationError = ""

        // Preserve all existing unknown and nested fields by mutating in-place or merging
        if (!profileData) profileData = {}
        if (!profileData.stick_processing) profileData.stick_processing = {}
        if (!profileData.stick_processing.left) profileData.stick_processing.left = {}
        if (!profileData.stick_processing.right) profileData.stick_processing.right = {}

        // Mutate known keys on left stick, preserving any extra keys (e.g. future_option)
        profileData.stick_processing.left.enabled = leftEnabled
        profileData.stick_processing.left.inner_deadzone = leftInner
        profileData.stick_processing.left.outer_deadzone = leftOuter
        profileData.stick_processing.left.anti_deadzone = leftAnti
        profileData.stick_processing.left.curve = leftCurve

        // Mutate known keys on right stick
        profileData.stick_processing.right.enabled = rightEnabled
        profileData.stick_processing.right.inner_deadzone = rightInner
        profileData.stick_processing.right.outer_deadzone = rightOuter
        profileData.stick_processing.right.anti_deadzone = rightAnti
        profileData.stick_processing.right.curve = rightCurve


        return true
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: 14

            // Header & Save / Discard Bar
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: "Stick tuning · " + (root.service ? root.service.editingProfile : "profile")
                    font.pixelSize: 18; font.bold: true; color: Theme.text; font.family: Theme.family
                    Layout.fillWidth: true
                    visible: !root.studio || !root.studio.compact
                }

                SocialButton {
                    objectName: "openDeadbandSticksButton"
                    text: "Open Deadband"
                    visible: typeof controllerDeviceCommands !== "undefined" && controllerDeviceCommands && controllerDeviceCommands.deadbandAvailable
                    onClicked: controllerDeviceCommands.openDeadband()
                }

                SocialButton {
                    id: saveSticksButton
                    objectName: "saveSticksButton"
                    text: root.service && root.service.busy ? "Saving…" : "Save"
                    enabled: root.studio && root.studio.dirty && root.service && root.service.available && !root.service.busy && !root.service.uncertain
                    onClicked: root.saveTuning()
                }

                CheckBox {
                    id: sameForBothCheck
                    objectName: "sticksSameForBoth"
                    text: "Same for both"
                    checked: root.sameForBoth
                    contentItem: Text {
                        text: sameForBothCheck.text
                        color: Theme.text
                        verticalAlignment: Text.AlignVCenter
                        leftPadding: sameForBothCheck.indicator.width + sameForBothCheck.spacing
                    }
                    onToggled: {
                        root.sameForBoth = checked
                        if (checked) {
                            root.rightEnabled = root.leftEnabled; root.rightInner = root.leftInner
                            root.rightOuter = root.leftOuter; root.rightAnti = root.leftAnti; root.rightCurve = root.leftCurve
                            if (root.studio) root.studio.dirty = true
                        }
                    }
                }
                SocialButton {
                    objectName: "discardSticksButton"
                    text: "Discard"
                    enabled: root.studio && root.studio.dirty && root.service && !root.service.busy
                    onClicked: root.discardTuning()
                }

                SocialButton {
                    text: "Reset Defaults"
                    onClicked: {
                        root.leftEnabled = true; root.leftInner = 0.08; root.leftOuter = 1.0; root.leftAnti = 0.0; root.leftCurve = 1.0
                        root.rightEnabled = true; root.rightInner = 0.08; root.rightOuter = 1.0; root.rightAnti = 0.0; root.rightCurve = 1.0
                        if (root.sameForBoth) {
                            root.rightEnabled = root.leftEnabled; root.rightInner = root.leftInner
                            root.rightOuter = root.leftOuter; root.rightAnti = root.leftAnti; root.rightCurve = root.leftCurve
                        }
                        root.validationError = ""
                        if (root.studio) root.studio.dirty = true
                    }
                }
            }

            // Validation warning banner
            Text {
                Layout.fillWidth: true
                text: root.validationError || (root.service ? root.service.status : "Engine unavailable; tuning is disabled.")
                color: root.validationError ? "#e03a2f" : (root.service && root.service.available ? Theme.muted : "#e03a2f")
                font.pixelSize: 12; font.family: Theme.family
                visible: !!text
            }

            // Halves: Left Stick and Right Stick tuning cards
            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                // Left Stick Card
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 410
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Left Stick"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family; Layout.fillWidth: true }
                            CheckBox {
                                id: leftEnabledCheck
                                text: "Enabled"
                                checked: root.leftEnabled
                                contentItem: Text {
                                    text: leftEnabledCheck.text
                                    color: Theme.text
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: leftEnabledCheck.indicator.width + leftEnabledCheck.spacing
                                }
                                onToggled: { root.leftEnabled = checked; if (root.studio) root.studio.dirty = true }
                            }
                        }

                        StickPlot {
                            Layout.fillWidth: true; Layout.preferredHeight: 140
                            stickEnabled: root.leftEnabled
                            innerDeadzone: root.leftInner
                            outerDeadzone: root.leftOuter
                            antiDeadzone: root.leftAnti
                            curve: root.leftCurve
                            liveAvailable: root.liveActive
                            liveNormX: root.normLeftX
                            liveNormY: root.normLeftY
                            processedAvailable: root.liveActive && !!root.liveOutput
                            processedNormX: root.processedLeftX
                            processedNormY: root.processedLeftY
                        }

                        // Sliders
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Inner Deadzone: " + Math.round(root.leftInner * 100) + "%"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                from: 0; to: 0.5; stepSize: 0.01; value: root.leftInner
                                onMoved: root.stageValue("leftInner", "rightInner", value, true)
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Outer Range: " + Math.round(root.leftOuter * 100) + "%"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                from: 0.5; to: 1.0; stepSize: 0.01; value: root.leftOuter
                                onMoved: root.stageValue("leftOuter", "rightOuter", value, true)
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Anti-deadzone: " + Math.round(root.leftAnti * 100) + "%"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                objectName: "leftAntiDeadzoneSlider"
                                from: 0; to: 0.5; stepSize: 0.01; value: root.leftAnti
                                onMoved: root.stageValue("leftAnti", "rightAnti", value, true)
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Curve Exponent: " + root.leftCurve.toFixed(2); color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                objectName: "leftCurveSlider"
                                from: 0.2; to: 3.0; stepSize: 0.05; value: root.leftCurve
                                onMoved: root.stageValue("leftCurve", "rightCurve", value, true)
                            }
                        }
                    }
                }

                // Right Stick Card
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 410
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Right Stick"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family; Layout.fillWidth: true }
                            CheckBox {
                                id: rightEnabledCheck
                                text: "Enabled"
                                checked: root.rightEnabled
                                contentItem: Text {
                                    text: rightEnabledCheck.text
                                    color: Theme.text
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: rightEnabledCheck.indicator.width + rightEnabledCheck.spacing
                                }
                                onToggled: root.stageValue("rightEnabled", "leftEnabled", checked, false)
                            }
                        }

                        StickPlot {
                            Layout.fillWidth: true; Layout.preferredHeight: 140
                            stickEnabled: root.rightEnabled
                            innerDeadzone: root.rightInner
                            outerDeadzone: root.rightOuter
                            antiDeadzone: root.rightAnti
                            curve: root.rightCurve
                            liveAvailable: root.liveActive
                            liveNormX: root.normRightX
                            liveNormY: root.normRightY
                            processedAvailable: root.liveActive && !!root.liveOutput
                            processedNormX: root.processedRightX
                            processedNormY: root.processedRightY
                        }

                        // Sliders
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Inner Deadzone: " + Math.round(root.rightInner * 100) + "%"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                from: 0; to: 0.5; stepSize: 0.01; value: root.rightInner
                                onMoved: root.stageValue("rightInner", "leftInner", value, false)
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Outer Range: " + Math.round(root.rightOuter * 100) + "%"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                from: 0.5; to: 1.0; stepSize: 0.01; value: root.rightOuter
                                onMoved: root.stageValue("rightOuter", "leftOuter", value, false)
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Anti-deadzone: " + Math.round(root.rightAnti * 100) + "%"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                objectName: "rightAntiDeadzoneSlider"
                                from: 0; to: 0.5; stepSize: 0.01; value: root.rightAnti
                                onMoved: root.stageValue("rightAnti", "leftAnti", value, false)
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "Curve Exponent: " + root.rightCurve.toFixed(2); color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.fillWidth: true }
                            Slider {
                                objectName: "rightCurveSlider"
                                from: 0.2; to: 3.0; stepSize: 0.05; value: root.rightCurve
                                onMoved: root.stageValue("rightCurve", "leftCurve", value, false)
                            }
                        }
                    }
                }
            }

            // Controller mouse: its one home (saved on its own, so it never overwrites the stick settings)
            ControllerMouseSection {
                service: root.service
                studio: root.studio
            }
        }
    }
}
