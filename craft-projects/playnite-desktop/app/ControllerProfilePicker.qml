import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "controllerProfilePicker"
    property var profileView: typeof controllerProfiles !== "undefined" ? controllerProfiles : null
    property string gameId: ""
    property bool compact: false
    readonly property var names: profileView ? profileView.profiles : []
    readonly property string defaultName: profileView ? profileView.defaultProfile : ""
    readonly property string assignment: gameId && profileView ? (profileView.gameProfiles[gameId] || "") : ""
    readonly property var choices: gameId ? ["Use default (" + (defaultName || "not configured") + ")"].concat(names) : names
    readonly property int selectedIndex: gameId ? (assignment ? names.indexOf(assignment) + 1 : 0) : names.indexOf(defaultName)
    implicitHeight: content.implicitHeight
    implicitWidth: 260
    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right
        spacing: root.compact ? 4 : 6
        Text {
            Layout.fillWidth: true
            text: "Controller profile"
            textFormat: Text.PlainText
            color: Theme.text; font.family: Theme.family; font.pixelSize: root.compact ? 12 : 14
        }
        CompactCombo {
            objectName: "controllerProfileChoice"
            Layout.fillWidth: true
            implicitHeight: root.compact ? 30 : 34
            model: root.choices
            currentIndex: root.selectedIndex
            enabled: root.profileView && root.profileView.available && !root.profileView.engineMutationBlocked && root.names.length > 0
            Accessible.name: root.gameId ? "Controller profile for this game" : "General default controller profile"
            onActivated: function(index) {
                if (!root.profileView) return
                if (root.gameId) root.profileView.assignGame(root.gameId, index === 0 ? "" : root.names[index - 1])
                else if (index >= 0 && index < root.names.length) root.profileView.setDefaultProfile(root.names[index])
            }
        }
        Text {
            Layout.fillWidth: true
            text: root.gameId ? "Per-game override · manage profiles in Controllers" : "No tracked game · general default"
            color: Theme.muted; font.family: Theme.family; font.pixelSize: 10
            textFormat: Text.PlainText; wrapMode: Text.WordWrap
        }
        Text {
            Layout.fillWidth: true
            visible: !!text.length
            text: root.profileView ? (root.profileView.error || ((root.compact || root.gameId === root.profileView.runtimeGameId) ? root.profileView.runtimeStatus : "")) : "Controller profile storage unavailable"
            color: Theme.muted; font.family: Theme.family; font.pixelSize: 10
            textFormat: Text.PlainText; wrapMode: Text.WordWrap
        }
    }
}
