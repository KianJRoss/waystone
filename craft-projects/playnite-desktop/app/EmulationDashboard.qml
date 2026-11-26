import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
WorkspacePage {
    id: dashboard
    objectName: "emulationDashboard"
    property var manager: typeof emulationManager !== "undefined" ? emulationManager : null
    title: "Emulation · systems & emulator profiles"
    onOpened: if (manager) manager.refresh()
    background: Rectangle { color: Theme.popup; border.color: Theme.line; radius: Theme.cornerRadius }
    ColumnLayout {
        anchors.fill: parent; spacing: 8
        Text {
            Layout.fillWidth: true; textFormat: Text.PlainText; color: Theme.text
            wrapMode: Text.WordWrap
            text: dashboard.manager ? dashboard.manager.profiles.length + " configured emulator profiles · " + dashboard.manager.systems.length + " catalog systems" : "Emulator management unavailable"
        }
        Text {
            Layout.fillWidth: true; textFormat: Text.PlainText; color: Theme.muted; wrapMode: Text.WordWrap
            text: "These are the profiles used by Play. Finding an executable does not verify game compatibility. No emulator is started by this screen."
        }
        Text { Layout.fillWidth: true; text: dashboard.manager ? dashboard.manager.error : ""; textFormat: Text.PlainText; color: Theme.text; visible: !!text; wrapMode: Text.WordWrap }
        ListView {
            id: profiles; objectName: "emulatorProfiles"
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4
            model: dashboard.manager ? dashboard.manager.profiles : []
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 12; radius: 2; color: Theme.muted } background: Rectangle { color: Theme.background } }
            delegate: ItemDelegate {
                required property var modelData
                width: profiles.width; height: 58
                background: Rectangle { color: parent.hovered ? Theme.main : Theme.panel; radius: Theme.cornerRadius }
                contentItem: Column {
                    Text { width: parent.width; text: modelData.name + (modelData.default ? " · Default" : ""); textFormat: Text.PlainText; color: Theme.text; elide: Text.ElideRight }
                    Text { width: parent.width; text: modelData.platforms.join(", ") + " · " + (modelData.installed ? "Executable found" : "Executable missing"); textFormat: Text.PlainText; color: Theme.muted; elide: Text.ElideRight }
                }
                onClicked: {
                    profileId.text = modelData.id; profileName.text = modelData.name
                    systemId.text = modelData.platform; executable.text = modelData.executable
                    profileArgs.text = modelData.argsJson; workingDir.text = modelData.cwd
                }
            }
        }
        GridLayout {
            columns: 2; Layout.fillWidth: true; enabled: dashboard.manager && dashboard.manager.enabled
            Text { text: "Profile ID / name"; color: Theme.muted }
            RowLayout { Layout.fillWidth: true
                TextField { id: profileId; objectName: "emulatorProfileId"; placeholderText: "emu-custom"; Layout.fillWidth: true; color: Theme.text }
                TextField { id: profileName; placeholderText: "My emulator"; Layout.fillWidth: true; color: Theme.text }
            }
            Text { text: "System ID"; color: Theme.muted }
            TextField { id: systemId; placeholderText: "switch, gamecube, ps1, ps2…"; Layout.fillWidth: true; color: Theme.text }
            Text { text: "Executable"; color: Theme.muted }
            TextField { id: executable; placeholderText: "Absolute emulator or wrapper path"; Layout.fillWidth: true; color: Theme.text }
            Text { text: "Arguments (JSON array)"; color: Theme.muted }
            TextField { id: profileArgs; placeholderText: '["-f", "{rom}"]'; Layout.fillWidth: true; color: Theme.text }
            Text { text: "Working directory"; color: Theme.muted }
            TextField { id: workingDir; placeholderText: "Optional"; Layout.fillWidth: true; color: Theme.text }
        }
        RowLayout {
            CompactButton { text: "New profile"; onClicked: { profileId.clear(); profileName.clear(); systemId.clear(); executable.clear(); profileArgs.text = '["{rom}"]'; workingDir.clear() } }
            CompactButton { objectName: "saveEmulatorProfile"; text: "Save profile"; enabled: dashboard.manager && dashboard.manager.enabled
                onClicked: dashboard.manager.saveProfile(profileId.text, profileName.text, systemId.text, executable.text, profileArgs.text, workingDir.text) }
            CompactButton { text: "Use as system default"; enabled: dashboard.manager && dashboard.manager.enabled && !!profileId.text
                onClicked: dashboard.manager.setDefault(systemId.text, profileId.text) }
            Item { Layout.fillWidth: true }
        }
    }
}
