import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

// A card of profile settings that lives in the tab it belongs to (Kian 2026-11-25: Profiles is only for choosing
// and saving profiles). It loads from the profile being edited and saves ONLY its own fields: the engine merges
// top-level fields, so sections never overwrite each other's settings.
Rectangle {
    id: section
    property var service: null
    property var studio: null
    property string title: ""
    property string saveObjectName: ""
    property bool dirty: false
    property bool saving: false
    property string validationError: ""
    property string statusFeedback: ""
    // set by the user of this card: collect() returns {field: value, ...} or null after setting validationError
    property var collect: null
    signal loaded(var data)
    default property alias content: body.data

    Layout.fillWidth: true
    implicitHeight: column.implicitHeight + 28
    radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line

    function load() {
        if (section.dirty || section.saving || !section.service || !section.service.profileJson) return
        var data
        try { data = JSON.parse(section.service.profileJson) } catch (e) { return }
        section.loaded(data || {})
    }
    function markDirty() {
        section.dirty = true
        section.statusFeedback = ""
        if (section.studio) section.studio.dirty = true
    }
    function save() {
        if (!section.collect || !section.service) return false
        var partial = section.collect()
        if (!partial) return false
        section.validationError = ""
        section.saving = true
        section.statusFeedback = "Saving to " + section.service.editingProfile + "…"
        section.service.saveProfile(JSON.stringify(partial))
        return true
    }

    Connections {
        target: section.service
        ignoreUnknownSignals: true
        function onChanged() { section.load() }
        function onOperationFinished(ok) {
            if (!section.saving) return
            section.saving = false
            if (ok) {
                section.dirty = false
                if (section.studio) section.studio.dirty = false
                section.statusFeedback = "Saved to " + section.service.editingProfile + "."
                section.load()
            } else {
                section.statusFeedback = "Save failed. Your changes are kept."
            }
        }
    }
    Component.onCompleted: load()

    ColumnLayout {
        id: column
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 14
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Text {
                text: section.title
                font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family
                Layout.fillWidth: true
            }
            SocialButton {
                objectName: section.saveObjectName
                text: "Save"
                enabled: section.dirty && !section.saving && !!section.service && section.service.available
                         && !section.service.busy && !section.service.uncertain && !!section.service.editingProfile
                onClicked: section.save()
            }
        }
        Text {
            Layout.fillWidth: true
            visible: text.length > 0
            text: section.validationError
            color: "#e03a2f"; font.pixelSize: 12; font.family: Theme.family; wrapMode: Text.WordWrap
        }
        Text {
            Layout.fillWidth: true
            visible: text.length > 0 && section.validationError.length === 0
            text: section.statusFeedback
            color: Theme.muted; font.pixelSize: 12; font.family: Theme.family; wrapMode: Text.WordWrap
        }
        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: 10
        }
    }
}
