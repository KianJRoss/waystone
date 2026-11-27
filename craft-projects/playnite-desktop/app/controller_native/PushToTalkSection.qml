import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

// Hotkeys tab: controller binding (Mod-key style) that drives Vesktop's Push-to-Talk over its local socket/CLI.
ProfileSection {
    id: root
    objectName: "pushToTalkSection"
    title: "Discord Push-to-Talk (Vesktop)"
    saveObjectName: "savePushToTalkButton"
    readonly property var pttBehaviors: ["hold", "toggle"]
    property string pttSource: (typeof discordView !== "undefined" && discordView && discordView.pttSource) ? discordView.pttSource : ""
    property string pttBehavior: (typeof discordView !== "undefined" && discordView && discordView.pttBehavior) ? discordView.pttBehavior : "hold"

    onLoaded: function(data) {
        var ptt = data.push_to_talk || {}
        if (ptt.source !== undefined) {
            root.pttSource = String(ptt.source)
        } else if (typeof discordView !== "undefined" && discordView) {
            root.pttSource = discordView.pttSource
        }
        root.pttBehavior = pttBehaviors.indexOf(ptt.behavior) !== -1 ? ptt.behavior : "hold"
    }
    collect: function() {
        var cleaned = (root.pttSource || "").trim()
        if (typeof discordView !== "undefined" && discordView) {
            discordView.setControllerPtt(cleaned, root.pttBehavior)
        }
        return { push_to_talk: { source: cleaned, behavior: root.pttBehavior } }
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 12
        Text { text: "PTT button:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family; Layout.preferredWidth: 90 }
        TextField {
            objectName: "pttSourceField"
            Layout.preferredWidth: 200
            text: root.pttSource
            color: "#e49b2f"
            font.family: Theme.family
            font.bold: true
            placeholderText: "e.g. Controller:Right Stick"
            background: Rectangle {
                color: Theme.background
                border.color: parent.activeFocus ? Theme.text : Theme.line
                radius: Theme.cornerRadius / 2
            }
            onTextEdited: { root.pttSource = text; root.markDirty() }
        }
        Text { text: "Behavior:"; color: Theme.muted; font.pixelSize: 12; font.family: Theme.family }
        CompactCombo {
            objectName: "pttBehaviorCombo"
            implicitWidth: 140
            model: root.pttBehaviors
            currentIndex: Math.max(0, root.pttBehaviors.indexOf(root.pttBehavior))
            onActivated: { root.pttBehavior = currentText; root.markDirty() }
        }
        SocialButton {
            objectName: "pttUseSelectedButton"
            text: "Use Map Selection"
            visible: Boolean(root.studio && root.studio.selectedSource)
            onClicked: {
                if (root.studio && root.studio.selectedSource) {
                    root.pttSource = root.studio.selectedSource
                    root.markDirty()
                }
            }
        }
    }
    Text {
        objectName: "pttClientStatusText"
        Layout.fillWidth: true
        text: (typeof discordView !== "undefined" && discordView)
              ? discordView.pttStatusMessage
              : "Ready · drives Vesktop Push-to-Talk via $XDG_RUNTIME_DIR/vesktop.sock (or vesktop --run-shortcut)."
        color: (typeof discordView !== "undefined" && discordView && !discordView.activeClientSupportsPtt) ? "#e49b2f" : Theme.muted
        font.pixelSize: 11; font.family: Theme.family; wrapMode: Text.WordWrap
    }
}
