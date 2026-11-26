import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dashboard
    objectName: "controllerDashboard"
    property var service: typeof unifiedController !== "undefined" ? unifiedController : null
    property bool dirty: false
    property bool loadingEditor: false
    property int editorPage: 0
    function loadEditor() { loadingEditor = true; editor.text = service.profileJson; mappings.loadProfile(service.profileJson); loadingEditor = false }
    title: "Controllers · UnifiedController"
    width: Math.min(parent.width - 40, 1020); height: Math.min(parent.height - 40, 760)
    anchors.centerIn: parent; modal: true; standardButtons: Dialog.Close
    background: Rectangle { color: Theme.popup; border.color: Theme.line }
    onOpened: if (service && !dirty) service.refresh()
    Connections {
        target: dashboard.service
        function onOperationFinished(success) { if (success) { dashboard.dirty = false; dashboard.loadEditor() } }
        function onChanged() {
            if (!dashboard.service.busy && !dashboard.dirty) dashboard.loadEditor()
        }
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Text { text: "UnifiedController"; font.pixelSize: 22; font.bold: true; color: Theme.text; Layout.fillWidth: true }
            SocialButton { objectName: "controllerRefresh"; text: "Refresh"; enabled: dashboard.service && !dashboard.service.busy && !dashboard.dirty; onClicked: dashboard.service.refresh() }
            SocialButton { objectName: "controllerStopMacros"; text: "Stop all macros"; enabled: dashboard.service && dashboard.service.available && !dashboard.service.busy; onClicked: dashboard.service.cancelMacros() }
        }
        Text { objectName: "controllerStatus"; Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText; color: Theme.muted; text: dashboard.service ? dashboard.service.status : "Controller integration unavailable." }
        Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: "Uses your existing native controller engine. Opening this page never grabs a device or starts another runtime."; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12 }
        Text {
            Layout.fillWidth: true; color: Theme.text; textFormat: Text.PlainText; wrapMode: Text.WordWrap
            text: dashboard.service && dashboard.service.devices.length
                ? dashboard.service.devices.map(function(d) { return d.name + (d.virtual ? " · virtual" : "") + (d.connected ? " · connected" : " · disconnected") }).join("   |   ")
                : "No controller devices reported. Refresh reads the existing engine; it does not scan or grab devices."
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: "Profile"; color: Theme.text }
            CompactCombo {
                id: chooser; objectName: "controllerProfileChooser"; Layout.fillWidth: true
                enabled: dashboard.service && !dashboard.service.busy && !dashboard.dirty
                model: dashboard.service ? dashboard.service.profiles : []
                currentIndex: dashboard.service ? Math.max(0, model.indexOf(dashboard.service.editingProfile)) : 0
                onActivated: dashboard.service.loadProfile(currentText)
            }
            SocialButton {
                objectName: "controllerUseProfile"; text: "Use profile"
                enabled: dashboard.service && dashboard.service.available && !dashboard.service.busy && !dashboard.service.uncertain && !dashboard.dirty
                onClicked: dashboard.service.useProfile()
            }
        }
        Text { text: "Mappings & profile settings"; color: Theme.text; font.bold: true }
        Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12; textFormat: Text.PlainText; text: "Native profile editor: routing, layers, sticks, motion and macros remain in the original profile schema. Save validates with UnifiedController's own parser and preserves other profiles. Use Mappings for bindings, or Advanced profile for layers, motion and macros." }
        RowLayout {
            SocialButton { text: "Mappings"; selected: dashboard.editorPage === 0; onClicked: dashboard.editorPage = 0 }
            SocialButton { text: "Advanced profile"; selected: dashboard.editorPage === 1; onClicked: dashboard.editorPage = 1 }
        }
        StackLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; currentIndex: dashboard.editorPage
            ControllerMappingEditor {
                id: mappings
                enabled: dashboard.service && dashboard.service.available && !dashboard.service.busy
                onEdited: {
                    if (!dashboard.loadingEditor) {
                        dashboard.loadingEditor = true
                        editor.text = serializedProfile()
                        dashboard.loadingEditor = false
                        dashboard.dirty = true
                    }
                }
            }
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                TextArea {
                    id: editor; objectName: "controllerProfileEditor"
                    color: Theme.text; font.family: "monospace"; font.pixelSize: 13
                    textFormat: TextEdit.PlainText; wrapMode: TextEdit.NoWrap; selectByMouse: true
                    readOnly: !dashboard.service || !dashboard.service.available || dashboard.service.busy
                    onTextChanged: if (!dashboard.loadingEditor && activeFocus && !readOnly) {
                        dashboard.dirty = true
                        mappings.loadProfile(text)
                    }
                    background: Rectangle { color: Theme.panel; border.color: Theme.line }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { Layout.fillWidth: true; text: dashboard.dirty ? "Unsaved local edits" : "No unsaved edits"; color: Theme.muted }
            SocialButton { text: "Discard edits"; enabled: dashboard.dirty && dashboard.service && !dashboard.service.busy; onClicked: { dashboard.dirty = false; dashboard.loadEditor() } }
            SocialButton {
                objectName: "controllerSaveProfile"; text: "Save profile"
                enabled: dashboard.dirty && dashboard.service && dashboard.service.available && !dashboard.service.busy && !dashboard.service.uncertain
                onClicked: { dashboard.service.saveProfile(editor.text) }
            }
        }
    }
}
