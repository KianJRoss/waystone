import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Dialog {
    id: prompt
    objectName: "gameInstallPrompt"
    property var controller: typeof gameInstalls !== "undefined" ? gameInstalls : null
    property string scopeKey: ""
    readonly property var request: controller ? controller.prompt : ({})
    modal: true
    anchors.centerIn: parent
    width: Math.min(540, parent ? parent.width - 32 : 540)
    title: request.title || "Install game"
    closePolicy: Popup.CloseOnEscape
    background: Rectangle { color: Theme.background; border.color: Theme.line; radius: Theme.cornerRadius }
    header: Label { padding: 18; color: Theme.text; text: prompt.title; font.pixelSize: 20; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
    onRejected: if (controller) controller.dismissPrompt()
    function updateRequest() {
        if (!controller || !request.open) { close(); scopeKey = ""; return }
        var key = request.game_id + ":" + request.kind
        if (scopeKey !== key) {
            scopeKey = key
            storeChoice.currentIndex = request.choices && request.choices.length === 1 ? 0 : -1
            packageChoice.currentIndex = -1
            destination.text = request.root || ""
        }
        if (!visible) open()
    }
    Connections { target: prompt.controller; function onChanged() { prompt.updateRequest() } }
    FolderDialog {
        id: folderPicker
        title: "Choose installation folder"
        onAccepted: { if (typeof providerJobs !== "undefined" && providerJobs) destination.text = providerJobs.localFolder(selectedFolder.toString()) }
    }
    contentItem: ColumnLayout {
        spacing: 14
        Text { Layout.fillWidth: true; text: prompt.request.message || ""; color: Theme.text; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
        CompactCombo {
            id: storeChoice; objectName: "gameInstallStoreChoice"; Layout.fillWidth: true
            visible: prompt.request.kind === "target"
            model: prompt.request.choices || []; textRole: "label"
            displayText: currentIndex < 0 ? "Choose your store copy…" : currentText
        }
        CompactCombo {
            id: packageChoice; objectName: "gameInstallPackageChoice"; Layout.fillWidth: true
            visible: prompt.request.kind === "package"
            model: prompt.request.packages || []; textRole: "title"
            displayText: currentIndex < 0 ? "Choose the PC package…" : currentText
        }
        RowLayout {
            Layout.fillWidth: true
            visible: prompt.request.kind === "target" && (storeChoice.currentIndex < 0 || prompt.request.choices[storeChoice.currentIndex].provider !== "steam")
            TextField { id: destination; objectName: "gameInstallFolder"; Layout.fillWidth: true; color: Theme.text; placeholderText: "Installation folder"; background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: Theme.cornerRadius } }
            CompactButton { text: "Browse…"; implicitWidth: 95; onClicked: folderPicker.open() }
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            CompactButton { objectName: "cancelGameInstallOptions"; text: prompt.request.kind === "error" ? "Close" : "Cancel"; implicitWidth: 95; onClicked: prompt.controller.dismissPrompt() }
            CompactButton {
                objectName: "confirmGameInstall"; text: "Install"; implicitWidth: 100
                visible: prompt.request.kind !== "error"
                enabled: prompt.request.kind === "package" ? packageChoice.currentIndex >= 0 : storeChoice.currentIndex >= 0
                onClicked: {
                    if (prompt.request.kind === "package") prompt.controller.confirmPackage(prompt.request.packages[packageChoice.currentIndex].content_id)
                    else prompt.controller.confirm(prompt.request.choices[storeChoice.currentIndex].key, destination.text)
                }
            }
        }
    }
}
