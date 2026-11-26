import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

// Devices tab: how physical controllers combine and which output slots they drive (saved in the profile).
ProfileSection {
    id: root
    objectName: "deviceOutputSection"
    title: "Controller inputs & output slots"
    saveObjectName: "saveDeviceOutputButton"
    readonly property var mergePolicies: ["first_active", "all_connected"]
    property string sourceMergePolicy: "first_active"
    property bool outputSlot1: true
    property bool outputSlot2: false
    property bool outputSlot3: false
    property bool outputSlot4: false
    property string deviceSlotsJson: "{}"

    onLoaded: function(data) {
        root.sourceMergePolicy = mergePolicies.indexOf(data.source_merge_policy) !== -1 ? data.source_merge_policy : "first_active"
        var slots = data.enabled_output_slots || [1]
        root.outputSlot1 = slots.indexOf(1) !== -1 || slots.indexOf("1") !== -1
        root.outputSlot2 = slots.indexOf(2) !== -1 || slots.indexOf("2") !== -1
        root.outputSlot3 = slots.indexOf(3) !== -1 || slots.indexOf("3") !== -1
        root.outputSlot4 = slots.indexOf(4) !== -1 || slots.indexOf("4") !== -1
        root.deviceSlotsJson = data.device_output_slots ? JSON.stringify(data.device_output_slots, null, 2) : "{}"
    }
    collect: function() {
        var parsed = {}
        if (root.deviceSlotsJson && root.deviceSlotsJson.trim().length > 0) {
            try {
                parsed = JSON.parse(root.deviceSlotsJson)
            } catch (err) {
                root.validationError = "Invalid JSON in per-device slot assignments: " + err.message
                return null
            }
            if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
                root.validationError = "Per-device slot assignments must be a valid JSON object."
                return null
            }
        }
        var slots = []
        if (root.outputSlot1) slots.push(1)
        if (root.outputSlot2) slots.push(2)
        if (root.outputSlot3) slots.push(3)
        if (root.outputSlot4) slots.push(4)
        return { source_merge_policy: root.sourceMergePolicy, enabled_output_slots: slots, device_output_slots: parsed }
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 12
        Text { text: "Combine controllers:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family; Layout.preferredWidth: 140 }
        CompactCombo {
            objectName: "mergePolicyCombo"
            implicitWidth: 180
            model: root.mergePolicies
            currentIndex: Math.max(0, root.mergePolicies.indexOf(root.sourceMergePolicy))
            onActivated: { root.sourceMergePolicy = currentText; root.markDirty() }
        }
        Text {
            text: "Merge every connected controller, or only use the first active one."
            color: Theme.muted; font.pixelSize: 11; font.family: Theme.family
            Layout.fillWidth: true; wrapMode: Text.WordWrap
        }
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 16
        Text { text: "Output slots:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
        ThemeCheckBox { text: "Slot 1"; checked: root.outputSlot1; onToggled: { root.outputSlot1 = checked; root.markDirty() } }
        ThemeCheckBox { text: "Slot 2"; checked: root.outputSlot2; onToggled: { root.outputSlot2 = checked; root.markDirty() } }
        ThemeCheckBox { text: "Slot 3"; checked: root.outputSlot3; onToggled: { root.outputSlot3 = checked; root.markDirty() } }
        ThemeCheckBox { text: "Slot 4"; checked: root.outputSlot4; onToggled: { root.outputSlot4 = checked; root.markDirty() } }
    }

    ColumnLayout {
        Layout.fillWidth: true; spacing: 4
        Text { text: "Per-device slot assignments (JSON):"; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }
        TextArea {
            objectName: "deviceSlotsArea"
            Layout.fillWidth: true
            Layout.preferredHeight: 65
            text: root.deviceSlotsJson
            color: Theme.text
            font.family: "Monospace"
            font.pixelSize: 11
            background: Rectangle {
                color: Theme.background
                border.color: parent.activeFocus ? Theme.text : Theme.line
                radius: Theme.cornerRadius / 2
            }
            onTextChanged: if (activeFocus) { root.deviceSlotsJson = text; root.markDirty() }
        }
    }
}
