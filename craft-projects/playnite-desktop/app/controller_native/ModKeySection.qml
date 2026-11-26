import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

// Hotkeys tab: the Mod key that arms secondary shortcuts (saved in the profile).
ProfileSection {
    id: root
    objectName: "modKeySection"
    title: "Mod key"
    saveObjectName: "saveModKeyButton"
    readonly property var modBehaviors: ["toggle", "one-shot", "hold"]
    property string modSource: "Controller:Share"
    property string modBehavior: "toggle"

    onLoaded: function(data) {
        var mod = data.mod_key || {}
        root.modSource = mod.source !== undefined ? String(mod.source) : "Controller:Share"
        root.modBehavior = modBehaviors.indexOf(mod.behavior) !== -1 ? mod.behavior : "toggle"
    }
    collect: function() {
        if (!root.modSource || !root.modSource.trim().length) {
            root.validationError = "Choose a Mod key source, e.g. Controller:Share."
            return null
        }
        return { mod_key: { source: root.modSource.trim(), behavior: root.modBehavior } }
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 12
        Text { text: "Mod key:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family; Layout.preferredWidth: 90 }
        TextField {
            objectName: "modSourceField"
            Layout.preferredWidth: 200
            text: root.modSource
            color: "#e49b2f"
            font.family: Theme.family
            font.bold: true
            placeholderText: "e.g. Controller:Share"
            background: Rectangle {
                color: Theme.background
                border.color: parent.activeFocus ? Theme.text : Theme.line
                radius: Theme.cornerRadius / 2
            }
            onTextEdited: { root.modSource = text; root.markDirty() }
        }
        Text { text: "Behavior:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
        CompactCombo {
            objectName: "modBehaviorCombo"
            implicitWidth: 140
            model: root.modBehaviors
            currentIndex: Math.max(0, root.modBehaviors.indexOf(root.modBehavior))
            onActivated: { root.modBehavior = currentText; root.markDirty() }
        }
    }
    Text {
        Layout.fillWidth: true
        text: "Share is the Mod key by default. 'one-shot': tap it once to arm a secondary shortcut on the next button. "
              + "'hold': hold it down. 'toggle': press once to switch shortcuts on, again to switch them off."
        color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; wrapMode: Text.WordWrap
    }
}
