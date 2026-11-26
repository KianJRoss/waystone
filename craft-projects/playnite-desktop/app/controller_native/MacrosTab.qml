import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    property var studio: null
    property var service: null
    property var profileData: null

    // Macro selection and editing state
    property var macroNames: []
    property string selectedMacro: ""
    property string macroName: ""
    property int repeatCount: 1
    property string triggerSource: ""
    property bool cancelOnRelease: false
    property var steps: []

    // Step input builder state
    readonly property var stepTypes: ["press", "release", "axis", "delay", "action"]
    property string currentStepType: "press"
    property string currentStepTarget: "Controller:A"
    property int currentStepDuration: 100

    // Validation & feedback
    property string validationError: ""
    property string statusMessage: ""

    readonly property var nativeMacroAxes: [
        "Controller:Left Stick X", "Controller:Left Stick Y",
        "Controller:Right Stick X", "Controller:Right Stick Y",
        "Controller:LT", "Controller:RT"
    ]

    function isFiniteNum(val) {
        return typeof val === "number" && !isNaN(val) && isFinite(val)
    }

    function loadFromProfile(data) {
        if (!data) return
        root.profileData = data

        var macros = data.macros || {}
        var names = Object.keys(macros)
        root.macroNames = names

        if (names.length > 0) {
            if (!root.selectedMacro || names.indexOf(root.selectedMacro) === -1) {
                selectMacro(names[0])
            } else {
                selectMacro(root.selectedMacro)
            }
        } else {
            newMacro()
        }

        root.validationError = ""
        root.statusMessage = ""
    }

    function selectMacro(name) {
        root.selectedMacro = name
        var macros = (root.profileData && root.profileData.macros) ? root.profileData.macros : {}
        var m = macros[name]
        if (m) {
            root.macroName = name
            root.repeatCount = isFiniteNum(m.repeat_count) ? m.repeat_count : 1
            root.cancelOnRelease = (m.cancel_on_release === true)
            root.steps = m.steps ? JSON.parse(JSON.stringify(m.steps)) : []
            root.triggerSource = findTriggerForMacro(name)
        } else {
            newMacro()
        }
        root.validationError = ""
        root.statusMessage = ""
    }

    function newMacro() {
        root.selectedMacro = ""
        root.macroName = "New Macro"
        root.repeatCount = 1
        root.cancelOnRelease = false
        root.steps = []
        root.triggerSource = ""
        root.validationError = ""
        root.statusMessage = ""
    }

    function findTriggerForMacro(name) {
        if (!root.profileData || !root.profileData.routes) return ""
        var targetDest = "Macro:" + name
        var routes = root.profileData.routes
        for (var src in routes) {
            if (routes[src] === targetDest) return src
        }
        return ""
    }

    function addStep() {
        var type = root.currentStepType.trim().toLowerCase()
        var target = root.currentStepTarget.trim()
        var duration = root.currentStepDuration

        var newStep = null
        if (type === "press" || type === "release") {
            if (!target || target.length === 0) {
                root.validationError = "Target required for " + type + " step (e.g. Controller:A)."
                return
            }
            newStep = { "type": type, "target": target }
        } else if (type === "delay") {
            if (duration < 0 || duration > 10000) {
                root.validationError = "Delay duration must be between 0 and 10000 ms."
                return
            }
            newStep = { "type": "delay", "duration_ms": duration }
        } else if (type === "axis") {
            var parts = target.split("=")
            if (parts.length !== 2) {
                root.validationError = "Axis format must be 'Controller:Left Stick X=0.5'."
                return
            }
            var axisName = parts[0].trim()
            var axisVal = parseFloat(parts[1].trim())
            if (root.nativeMacroAxes.indexOf(axisName) === -1) {
                root.validationError = "Axis target must be one of: " + root.nativeMacroAxes.join(", ")
                return
            }
            if (isNaN(axisVal) || axisVal < -1.0 || axisVal > 1.0) {
                root.validationError = "Axis value must be between -1.0 and 1.0."
                return
            }
            newStep = { "type": "axis", "target": axisName, "value": axisVal }
        } else if (type === "action") {
            if (!target || target.length === 0) {
                root.validationError = "Action name required."
                return
            }
            newStep = { "type": "action", "action": target }
        }

        if (newStep) {
            var copy = root.steps.slice()
            copy.push(newStep)
            root.steps = copy
            root.validationError = ""
            root.statusMessage = "Step added."
            if (root.studio) root.studio.dirty = true
        }
    }

    function removeStep(index) {
        if (index >= 0 && index < root.steps.length) {
            var copy = root.steps.slice()
            copy.splice(index, 1)
            root.steps = copy
            if (root.studio) root.studio.dirty = true
        }
    }

    function moveStep(index, delta) {
        var targetIndex = index + delta
        if (index >= 0 && index < root.steps.length && targetIndex >= 0 && targetIndex < root.steps.length) {
            var copy = root.steps.slice()
            var item = copy.splice(index, 1)[0]
            copy.splice(targetIndex, 0, item)
            root.steps = copy
            if (root.studio) root.studio.dirty = true
        }
    }

    function validateStep(step, idx) {
        if (!step || typeof step !== "object") return "Step " + (idx + 1) + " is not a valid object."
        var type = step.type
        if (["press", "release", "axis", "delay", "action"].indexOf(type) === -1) {
            return "Step " + (idx + 1) + " has unsupported type '" + type + "'."
        }
        if (type === "press" || type === "release") {
            if (typeof step.target !== "string" || !step.target.startsWith("Controller:")) {
                return "Step " + (idx + 1) + " (" + type + ") target must start with 'Controller:'."
            }
        } else if (type === "axis") {
            if (root.nativeMacroAxes.indexOf(step.target) === -1) {
                return "Step " + (idx + 1) + " (axis) target '" + step.target + "' is not supported by NativeMacroScheduler."
            }
            if (!isFiniteNum(step.value) || step.value < -1.0 || step.value > 1.0) {
                return "Step " + (idx + 1) + " (axis) value must be between -1.0 and 1.0."
            }
        } else if (type === "delay") {
            if (!isFiniteNum(step.duration_ms) || Math.floor(step.duration_ms) !== step.duration_ms || step.duration_ms < 0 || step.duration_ms > 10000) {
                return "Step " + (idx + 1) + " (delay) duration_ms must be an integer between 0 and 10000."
            }
        } else if (type === "action") {
            var validActions = [
                "Fishing Helper Toggle", "Keyboard Controller Mode", "Mouse Aim Mode",
                "Open Control Center", "Desktop Mode", "Cancel Macros"
            ]
            if (typeof step.action !== "string" || validActions.indexOf(step.action) === -1) {
                return "Step " + (idx + 1) + " action '" + step.action + "' is not supported by NativeMacroScheduler."
            }
        }
        return ""
    }

    function validateAndApply() {
        var name = root.macroName.trim()
        if (name.length === 0) {
            root.validationError = "Macro name cannot be empty."
            return false
        }
        if (name.length > 64) {
            root.validationError = "Macro name exceeds maximum length of 64 characters."
            return false
        }

        if (!isFiniteNum(root.repeatCount) || Math.floor(root.repeatCount) !== root.repeatCount || root.repeatCount < 1 || root.repeatCount > 100) {
            root.validationError = "Repeat count must be an integer between 1 and 100."
            return false
        }

        if (!root.steps || !Array.isArray(root.steps)) {
            root.validationError = "Macro steps must be an array."
            return false
        }
        if (root.steps.length > 256) {
            root.validationError = "Macro exceeds maximum limit of 256 steps."
            return false
        }

        for (var i = 0; i < root.steps.length; i++) {
            var err = validateStep(root.steps[i], i)
            if (err.length > 0) {
                root.validationError = err
                return false
            }
        }

        if (!profileData) profileData = {}
        if (!profileData.macros) profileData.macros = {}

        // Collision check on rename: if renaming from selectedMacro to name, and name already exists and is not selectedMacro
        if (root.selectedMacro && root.selectedMacro !== name && profileData.macros[name]) {
            root.validationError = "A macro named '" + name + "' already exists. Choose a unique name."
            return false
        }

        root.validationError = ""

        var oldName = root.selectedMacro
        var isRename = Boolean(oldName && oldName !== name && profileData.macros[oldName])
        var macroObj = {}

        if (isRename) {
            // Preserve ALL original properties of the macro (including unknown / future properties)
            var originalMacro = profileData.macros[oldName]
            if (typeof originalMacro === "object" && originalMacro !== null) {
                for (var prop in originalMacro) {
                    if (originalMacro.hasOwnProperty(prop)) {
                        macroObj[prop] = originalMacro[prop]
                    }
                }
            }
            delete profileData.macros[oldName]

            // Retarget ALL routes pointing to oldName to newName (supports scalar string or sequence/array of routes)
            if (profileData.routes) {
                var oldTarget = "Macro:" + oldName
                var newTarget = "Macro:" + name
                for (var rKey in profileData.routes) {
                    var routeVal = profileData.routes[rKey]
                    if (routeVal === oldTarget) {
                        profileData.routes[rKey] = newTarget
                    } else if (typeof routeVal === "object" && routeVal !== null && typeof routeVal.length === "number") {
                        var updatedList = []
                        for (var li = 0; li < routeVal.length; li++) {
                            updatedList.push(routeVal[li] === oldTarget ? newTarget : routeVal[li])
                        }
                        profileData.routes[rKey] = updatedList
                    }
                }
            }
        } else if (profileData.macros[name]) {
            // Existing macro without rename: preserve unknown / future properties
            var existing = profileData.macros[name]
            if (typeof existing === "object" && existing !== null) {
                for (var p in existing) {
                    if (existing.hasOwnProperty(p)) {
                        macroObj[p] = existing[p]
                    }
                }
            }
        }

        // Apply updated standard fields
        macroObj.repeat_count = root.repeatCount
        macroObj.cancel_on_release = root.cancelOnRelease
        macroObj.steps = JSON.parse(JSON.stringify(root.steps))
        profileData.macros[name] = macroObj

        // Update trigger route if specified
        if (root.triggerSource && root.triggerSource.trim().length > 0) {
            if (!profileData.routes) profileData.routes = {}
            profileData.routes[root.triggerSource.trim()] = "Macro:" + name
        }

        root.selectedMacro = name
        var names = Object.keys(profileData.macros)
        root.macroNames = names

        return true
    }

    Connections {
        target: root.service
        function onOperationFinished(success) {
            if (success) {
                root.statusMessage = "Macro '" + root.macroName + "' saved."
                if (root.studio) root.studio.dirty = false
            } else {
                root.statusMessage = "Save failed. Draft preserved."
            }
        }
    }

    function saveMacro() {
        if (validateAndApply()) {
            if (root.studio) root.studio.dirty = true
            root.statusMessage = "Saving macro…"
            root.service.saveProfile(JSON.stringify({ macros: root.profileData.macros, routes: root.profileData.routes }))  // only this tab's fields
        }
    }

    function deleteMacro() {
        if (!root.selectedMacro || !root.profileData || !root.profileData.macros) return
        var name = root.selectedMacro
        delete root.profileData.macros[name]

        if (root.profileData.routes) {
            for (var r in root.profileData.routes) {
                if (root.profileData.routes[r] === "Macro:" + name) {
                    delete root.profileData.routes[r]
                }
            }
        }

        if (root.studio) root.studio.dirty = true
        root.service.saveProfile(JSON.stringify({ macros: root.profileData.macros, routes: root.profileData.routes }))  // only this tab's fields
        newMacro()
        root.macroNames = Object.keys(root.profileData.macros)
        root.statusMessage = "Macro '" + name + "' deleted."
    }

    RowLayout {
        anchors.fill: parent
        spacing: 14

        // Left Column: Macros List
        Rectangle {
            Layout.preferredWidth: 260
            Layout.fillHeight: true
            radius: Theme.cornerRadius
            color: Theme.panel
            border.color: Theme.line

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                Text { text: "Macros"; font.bold: true; font.pixelSize: 16; color: Theme.text; font.family: Theme.family }
                Text { text: "Select a macro to edit ordered steps."; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }

                ListView {
                    id: macroListView
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: root.macroNames
                    delegate: Rectangle {
                        width: macroListView.width
                        height: 34
                        radius: Theme.cornerRadius / 3
                        color: root.selectedMacro === modelData ? Theme.main : Theme.background
                        border.color: Theme.line

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            Text {
                                text: modelData
                                color: Theme.text
                                font.pixelSize: 12
                                font.bold: (root.selectedMacro === modelData)
                                font.family: Theme.family
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Text {
                                text: (root.profileData && root.profileData.macros && root.profileData.macros[modelData] && root.profileData.macros[modelData].steps) ? (root.profileData.macros[modelData].steps.length + " steps") : ""
                                color: Theme.muted
                                font.pixelSize: 10
                                font.family: Theme.family
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            onClicked: root.selectMacro(modelData)
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    SocialButton {
                        text: "New Macro"
                        onClicked: root.newMacro()
                    }
                    SocialButton {
                        text: "Stop All"
                        enabled: Boolean(root.service && typeof root.service.stopAllMacros === "function")
                        onClicked: if (root.service && root.service.stopAllMacros) root.service.stopAllMacros()
                    }
                }
            }
        }

        // Right Column: Sequence & Playback Controls
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true

            ColumnLayout {
                width: parent.width
                spacing: 14

                // Header & Action Bar
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    Text {
                        text: root.selectedMacro ? ("Macro: " + root.selectedMacro) : "Macro Editor"
                        font.pixelSize: 18; font.bold: true; color: Theme.text; font.family: Theme.family
                        Layout.fillWidth: true
                    }

                    SocialButton {
                        id: saveMacroButton
                        objectName: "saveMacroButton"
                        text: "Save Macro"
                        enabled: root.service && root.service.available && !root.service.busy && !root.service.uncertain
                        onClicked: root.saveMacro()
                    }

                    SocialButton {
                        text: "Delete Macro"
                        enabled: root.selectedMacro.length > 0 && root.service && root.service.available && !root.service.busy
                        onClicked: root.deleteMacro()
                    }
                }

                // Validation / Feedback banner
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

                // Card 1: Sequence Steps List
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: Math.max(220, stepsColumn.implicitHeight + 28)
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        id: stepsColumn
                        anchors.fill: parent; anchors.margins: 14; spacing: 10

                        Text { text: "Sequence Steps"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family }

                        // Steps Repeater
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Repeater {
                                model: root.steps
                                delegate: Rectangle {
                                    Layout.fillWidth: true
                                    height: 38
                                    radius: Theme.cornerRadius / 3
                                    color: Theme.background
                                    border.color: Theme.line

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.margins: 6
                                        spacing: 8

                                        Text { text: String(index + 1); color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; Layout.preferredWidth: 20 }

                                        // Step type pill
                                        Rectangle {
                                            implicitWidth: typeLabel.implicitWidth + 12
                                            implicitHeight: 22
                                            radius: 4
                                            color: modelData.type === "delay" ? Theme.main : (modelData.type === "press" ? "#2fbf71" : (modelData.type === "release" ? "#e03a2f" : "#e49b2f"))
                                            Text {
                                                id: typeLabel
                                                anchors.centerIn: parent
                                                text: modelData.type || "step"
                                                color: Theme.text
                                                font.pixelSize: 10
                                                font.bold: true
                                                font.family: Theme.family
                                            }
                                        }

                                        // Target / Action
                                        Text {
                                            text: modelData.target || modelData.action || "—"
                                            color: Theme.text
                                            font.pixelSize: 12
                                            font.family: Theme.family
                                            font.bold: true
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }

                                        // Value / Duration
                                        Text {
                                            text: modelData.type === "delay" ? (modelData.duration_ms + " ms") : (modelData.value !== undefined ? ("val=" + modelData.value) : "")
                                            color: Theme.muted
                                            font.pixelSize: 11
                                            font.family: Theme.family
                                        }

                                        // Step order buttons
                                        SocialButton {
                                            text: "↑"
                                            enabled: index > 0
                                            onClicked: root.moveStep(index, -1)
                                        }
                                        SocialButton {
                                            text: "↓"
                                            enabled: index < root.steps.length - 1
                                            onClicked: root.moveStep(index, 1)
                                        }
                                        SocialButton {
                                            text: "×"
                                            onClicked: root.removeStep(index)
                                        }
                                    }
                                }
                            }

                            Text {
                                text: root.steps.length === 0 ? "No steps yet. Add the first step below." : ""
                                color: Theme.muted; font.pixelSize: 12; font.family: Theme.family
                                visible: root.steps.length === 0
                            }
                        }

                        // Add step builder row
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            CompactCombo {
                                Layout.preferredWidth: 110
                                model: root.stepTypes
                                currentIndex: Math.max(0, model.indexOf(root.currentStepType))
                                onActivated: root.currentStepType = currentText
                            }

                            TextField {
                                Layout.fillWidth: true
                                text: root.currentStepTarget
                                color: Theme.text
                                font.family: Theme.family
                                placeholderText: root.currentStepType === "delay" ? "Ignored for delay" : (root.currentStepType === "axis" ? "Controller:Left Stick X=0.5" : "Target (e.g. Controller:A)")
                                background: Rectangle { color: Theme.background; border.color: parent.activeFocus ? Theme.text : Theme.line; radius: Theme.cornerRadius / 2 }
                                onTextEdited: root.currentStepTarget = text
                            }

                            TextField {
                                Layout.preferredWidth: 90
                                text: String(root.currentStepDuration)
                                color: Theme.text
                                font.family: Theme.family
                                placeholderText: "ms"
                                background: Rectangle { color: Theme.background; border.color: parent.activeFocus ? Theme.text : Theme.line; radius: Theme.cornerRadius / 2 }
                                onTextEdited: {
                                    var val = parseInt(text)
                                    if (!isNaN(val)) root.currentStepDuration = val
                                }
                            }

                            SocialButton {
                                text: "Add Step"
                                onClicked: root.addStep()
                            }
                        }

                        Text {
                            text: "Duration specifies hold time for inputs or delay time in milliseconds."
                            color: Theme.muted; font.pixelSize: 10; font.family: Theme.family
                        }
                    }
                }

                // Card 2: Playback & Trigger Configuration
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 180
                    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 10

                        Text { text: "Playback & Trigger Rules"; font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family }

                        RowLayout {
                            Layout.fillWidth: true; spacing: 14

                            ColumnLayout {
                                Layout.fillWidth: true
                                Text { text: "Macro Name:"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
                                TextField {
                                    Layout.fillWidth: true
                                    text: root.macroName
                                    color: Theme.text
                                    font.family: Theme.family
                                    background: Rectangle { color: Theme.background; border.color: parent.activeFocus ? Theme.text : Theme.line; radius: Theme.cornerRadius / 2 }
                                    onTextEdited: {
                                        root.macroName = text
                                        if (root.studio) root.studio.dirty = true
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.preferredWidth: 120
                                Text { text: "Repeat Count (1–100):"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
                                TextField {
                                    Layout.fillWidth: true
                                    text: String(root.repeatCount)
                                    color: Theme.text
                                    font.family: Theme.family
                                    background: Rectangle { color: Theme.background; border.color: parent.activeFocus ? Theme.text : Theme.line; radius: Theme.cornerRadius / 2 }
                                    onTextEdited: {
                                        var val = parseInt(text)
                                        if (!isNaN(val)) {
                                            root.repeatCount = val
                                            if (root.studio) root.studio.dirty = true
                                        }
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.preferredWidth: 180
                                Text { text: "Trigger Route:"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
                                TextField {
                                    Layout.fillWidth: true
                                    text: root.triggerSource
                                    color: "#e49b2f"
                                    font.family: Theme.family
                                    placeholderText: "e.g. Controller:A"
                                    background: Rectangle { color: Theme.background; border.color: parent.activeFocus ? Theme.text : Theme.line; radius: Theme.cornerRadius / 2 }
                                    onTextEdited: {
                                        root.triggerSource = text
                                        if (root.studio) root.studio.dirty = true
                                    }
                                }
                            }
                        }

                        CheckBox {
                            text: "Cancel on trigger release"
                            checked: root.cancelOnRelease
                            onToggled: {
                                root.cancelOnRelease = checked
                                if (root.studio) root.studio.dirty = true
                            }
                        }
                    }
                }
            }
        }
    }
}
