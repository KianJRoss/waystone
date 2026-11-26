import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Virtual pad type lives in a controller profile (Kian 2026-11-25: "set the virtual pad from the controller menu
// under a certain profile and then pick the profile for that certain game").
// - editor set (controller menu): choose the pad type of the profile being edited; saved through the engine.
// - gameId set (game page, overlay): read-only line saying which pad the game gets and from which profile.
Item {
    id: root
    objectName: "virtualPadPicker"
    property var padView: typeof virtualPad !== "undefined" ? virtualPad : null
    property var editor: null                 // controllerView: the profile being edited
    property string gameId: ""
    property string gameTitle: ""              // lets anti-cheat games default to the real controller
    property bool compact: false
    property string controlObjectName: "virtualPadTypeChoice"
    readonly property var padChoices: padView ? padView.choices : []
    readonly property var values: padChoices.map(function(choice) { return choice.value })
    readonly property var typeLabels: padChoices.map(function(choice) { return choice.label })
    readonly property string defaultType: padView ? padView.defaultType : ""
    readonly property string defaultLabel: labelFor(defaultType || "xbox360")
    readonly property bool editing: !!editor && !gameId
    readonly property string profileName: editing ? (editor.editingProfile || "") : ""
    readonly property string profileType: editing ? (editor.padType || "") : ""
    readonly property var labels: ["Use default (" + defaultLabel + ")"].concat(typeLabels)
    readonly property int selectedIndex: !profileType ? 0 : (values.indexOf(profileType) >= 0 ? values.indexOf(profileType) + 1 : -1)
    // read-only game summary; re-evaluated when either view changes
    readonly property string gameProfile: {
        if (!gameId || !padView) return ""
        var rev = padView.status + padView.defaultType
        return padView.profileForGame(gameId)
    }
    readonly property string gameType: {
        if (!gameId || !padView) return ""
        var rev = padView.status + padView.defaultType + gameProfile
        return padView.resolveTypeFor(gameId, gameTitle)
    }
    readonly property bool gameTypeFromProfile: {
        if (!gameId || !padView || !gameProfile) return false
        var rev = padView.status + gameType
        return padView.profileType(gameProfile).length > 0
    }
    readonly property string gameAnticheat: gameId && padView && gameTitle ? padView.anticheatFor(gameTitle) : ""
    implicitWidth: 260
    implicitHeight: content.implicitHeight
    function labelFor(value) {
        var index = values.indexOf(value)
        return index >= 0 ? typeLabels[index] : (value || "not verified")
    }
    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right
        spacing: root.compact ? 4 : 6
        Text {
            Layout.fillWidth: true
            visible: root.editing
            text: "Virtual pad type"
            textFormat: Text.PlainText
            color: Theme.text; font.family: Theme.family; font.pixelSize: root.compact ? 12 : 14
        }
        CompactCombo {
            objectName: root.controlObjectName
            Layout.fillWidth: true
            visible: root.editing
            implicitHeight: root.compact ? 30 : 34
            model: root.labels
            currentIndex: root.selectedIndex
            displayText: currentIndex >= 0 ? currentText : "Unrecognized: " + root.profileType
            enabled: !!(root.editing && root.editor.available && !root.editor.busy && !root.editor.uncertain
                        && root.profileName.length && root.values.length)
            Accessible.name: "Virtual pad type for profile " + root.profileName
            onActivated: function(index) {
                if (!root.editing) return
                root.editor.setPadType(index === 0 ? "" : root.values[index - 1])
            }
        }
        Text {
            Layout.fillWidth: true
            visible: root.editing
            text: "Games using " + (root.profileName || "this profile") + " get this · applied before the next launch. "
                  + "\"Use default\" gives anti-cheat games your real controller and other games the default pad."
            textFormat: Text.PlainText; wrapMode: Text.WordWrap
            color: Theme.muted; font.family: Theme.family; font.pixelSize: 10
        }
        Text {
            objectName: "virtualPadGameSummary"
            Layout.fillWidth: true
            visible: !!root.gameId
            text: root.gameType === "passthrough"
                  ? "Controller: your real controller (no virtual pad)"
                    + (root.gameTypeFromProfile ? " · from profile " + root.gameProfile
                       : (root.gameAnticheat ? " · anti-cheat game (" + root.gameAnticheat + ")" : ""))
                  : "Virtual pad: " + root.labelFor(root.gameType || root.defaultType || "xbox360")
                    + (root.gameProfile ? " · from profile " + root.gameProfile : " · default")
            textFormat: Text.PlainText; wrapMode: Text.WordWrap
            color: Theme.muted; font.family: Theme.family; font.pixelSize: root.compact ? 11 : 12
        }
        Text {
            Layout.fillWidth: true
            visible: root.editing
            text: root.padView && root.padView.currentType ? "Native output now: " + root.labelFor(root.padView.currentType) : "Native output type not verified"
            textFormat: Text.PlainText; wrapMode: Text.WordWrap
            color: Theme.muted; font.family: Theme.family; font.pixelSize: 10
        }
        Text {
            Layout.fillWidth: true
            visible: root.editing && !!text.length
            text: root.padView ? (root.padView.error || "") : "Virtual pad service unavailable"
            textFormat: Text.PlainText; wrapMode: Text.WordWrap
            color: Theme.muted; font.family: Theme.family; font.pixelSize: 10
        }
    }
}
