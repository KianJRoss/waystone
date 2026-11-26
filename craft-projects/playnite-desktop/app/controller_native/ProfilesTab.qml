import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    property var studio: null
    property var service: null
    property var profileCommands: null
    property var profileData: null
    property var assignments: typeof controllerProfiles !== "undefined" ? controllerProfiles : null
    readonly property bool localLifecycle: !!assignments
    readonly property bool localCanEdit: !!(assignments && assignments.available && !assignments.engineMutationBlocked && (!studio || !studio.dirty) && (!service || (!service.busy && !service.uncertain)) && (!profileCommands || (!profileCommands.busy && !profileCommands.uncertain)))

    // Selected profile for lifecycle actions
    property string selectedProfileName: (service && (service.editingProfile || service.activeProfile)) || (assignments ? assignments.defaultProfile : "")

    // Dialog & input states
    property string activeDialog: "" // "create", "copy", "rename", "delete", "import", "export"
    property string dialogInputName: ""
    property string dialogDocumentText: ""
    property string dialogErrorMessage: ""
    property string statusFeedback: ""

    // Native profile settings

    readonly property var currentProfileList: assignments ? assignments.profiles : (profileCommands && profileCommands.profiles && profileCommands.profiles.length > 0)
        ? profileCommands.profiles
        : ((service && service.profiles && service.profiles.length > 0) ? service.profiles : ["Default"])

    readonly property bool canCreate: localLifecycle ? (localCanEdit) : Boolean(
        profileCommands &&
        profileCommands.capabilities.indexOf("profile_create") !== -1 &&
        !profileCommands.busy &&
        !profileCommands.uncertain
    )

    readonly property bool canCopy: localLifecycle ? (localCanEdit && selectedProfileName.length > 0) : Boolean(
        profileCommands &&
        profileCommands.capabilities.indexOf("profile_copy") !== -1 &&
        !profileCommands.busy &&
        !profileCommands.uncertain &&
        selectedProfileName.length > 0
    )

    readonly property bool canRename: localLifecycle ? (localCanEdit && selectedProfileName.length > 0) : Boolean(
        profileCommands &&
        profileCommands.capabilities.indexOf("profile_rename") !== -1 &&
        !profileCommands.busy &&
        !profileCommands.uncertain &&
        selectedProfileName.length > 0
    )

    readonly property bool canDelete: localLifecycle ? (localCanEdit && selectedProfileName.length > 0 && currentProfileList.length > 1) : Boolean(
        profileCommands &&
        profileCommands.capabilities.indexOf("profile_delete") !== -1 &&
        !profileCommands.busy &&
        !profileCommands.uncertain &&
        currentProfileList.length > 1 &&
        selectedProfileName.length > 0
    )

    readonly property bool canImport: Boolean(
        (!studio || !studio.dirty) && (!service || !service.busy) && (!assignments || !assignments.engineMutationBlocked) &&
        profileCommands &&
        profileCommands.capabilities.indexOf("profile_import") !== -1 &&
        !profileCommands.busy &&
        !profileCommands.uncertain
    )

    readonly property bool canExport: Boolean(
        profileCommands &&
        profileCommands.capabilities.indexOf("profile_export") !== -1 &&
        !profileCommands.busy &&
        selectedProfileName.length > 0
    )

    Connections {
        target: root.profileCommands
        function onOperationFinished(op, success) {
            if (op === "profile_command_capabilities") {
                // Capability read is not a mutation; must NEVER close dialogs, announce commit, or trigger refresh
                if (!success) {
                    root.statusFeedback = "Failed to refresh profile capabilities."
                }
                return
            }

            if (success) {
                if (op === "profile_export") {
                    root.dialogDocumentText = root.profileCommands.exportDocument || ""
                    root.statusFeedback = "Profile exported as document."
                } else if (["profile_create", "profile_copy", "profile_rename", "profile_delete", "profile_import", "profile_set_field"].indexOf(op) !== -1) {
                    root.statusFeedback = "Profile operation '" + op + "' committed."
                    root.activeDialog = ""
                    if (root.assignments) root.assignments.refresh()
                    if (op === "profile_import" && root.dialogInputName.trim().length) root.selectedProfileName = root.dialogInputName.trim()
                    if (root.service && (!root.studio || !root.studio.dirty)) {
                        if (typeof root.service.reloadProfile === "function") root.service.reloadProfile(root.selectedProfileName)
                        else root.service.loadProfile(root.selectedProfileName)
                    }
                }
            } else {
                root.dialogErrorMessage = "Profile operation '" + op + "' failed or outcome uncertain."
                root.statusFeedback = root.dialogErrorMessage
            }
        }
    }

    Connections {
        target: root.assignments
        function onChanged() {
            if (root.currentProfileList.indexOf(root.selectedProfileName) < 0)
                root.selectedProfileName = root.assignments.defaultProfile || root.currentProfileList[0] || ""
        }
        function onOperationFinished(op, success) {
            if (op === "metadata") return
            if (success) {
                root.activeDialog = ""
                root.statusFeedback = "Controller profile changes saved."
                Qt.callLater(function() {
                    if (root.service && root.service.available && !root.service.busy && (!root.studio || !root.studio.dirty) && root.selectedProfileName) {
                        if (typeof root.service.reloadProfile === "function") root.service.reloadProfile(root.selectedProfileName)
                        else root.service.loadProfile(root.selectedProfileName)
                    }
                })
            } else {
                root.dialogErrorMessage = root.assignments.error
                root.statusFeedback = root.assignments.error
            }
        }
    }

    function isFiniteNum(val) {
        return typeof val === "number" && !isNaN(val) && isFinite(val)
    }

    function openCreateDialog() {
        dialogInputName = ""
        dialogErrorMessage = ""
        activeDialog = "create"
    }

    function openCopyDialog() {
        dialogInputName = selectedProfileName + " Copy"
        dialogErrorMessage = ""
        activeDialog = "copy"
    }

    function openRenameDialog() {
        dialogInputName = selectedProfileName
        dialogErrorMessage = ""
        activeDialog = "rename"
    }

    function openDeleteDialog() {
        dialogErrorMessage = ""
        activeDialog = "delete"
    }

    function openImportDialog() {
        dialogInputName = "Imported Profile"
        dialogDocumentText = ""
        dialogErrorMessage = ""
        activeDialog = "import"
    }

    function openExportDialog() {
        dialogErrorMessage = ""
        activeDialog = "export"
        if (profileCommands && typeof profileCommands.exportProfile === "function") {
            profileCommands.exportProfile(selectedProfileName)
        }
    }

    function confirmCreate() {
        var name = dialogInputName.trim()
        if (name.length === 0) {
            dialogErrorMessage = "Profile name cannot be empty."
            return
        }
        if (currentProfileList.indexOf(name) !== -1) {
            dialogErrorMessage = "A profile named '" + name + "' already exists."
            return
        }
        if (assignments) {
            if (assignments.createProfile(name)) selectedProfileName = name
        } else if (profileCommands && typeof profileCommands.createProfile === "function") {
            profileCommands.createProfile(name)
        }
    }

    function confirmCopy() {
        var name = dialogInputName.trim()
        if (name.length === 0) {
            dialogErrorMessage = "New profile name cannot be empty."
            return
        }
        if (currentProfileList.indexOf(name) !== -1) {
            dialogErrorMessage = "A profile named '" + name + "' already exists."
            return
        }
        if (assignments) {
            if (assignments.duplicateProfile(selectedProfileName, name)) selectedProfileName = name
        } else if (profileCommands && typeof profileCommands.copyProfile === "function") {
            profileCommands.copyProfile(selectedProfileName, name)
        }
    }

    function confirmRename() {
        var name = dialogInputName.trim()
        if (name.length === 0) {
            dialogErrorMessage = "Profile name cannot be empty."
            return
        }
        if (name === selectedProfileName) {
            dialogErrorMessage = "New name must be different from current name."
            return
        }
        if (currentProfileList.indexOf(name) !== -1) {
            dialogErrorMessage = "A profile named '" + name + "' already exists."
            return
        }
        if (assignments) {
            if (assignments.renameProfile(selectedProfileName, name)) selectedProfileName = name
        } else if (profileCommands && typeof profileCommands.renameProfile === "function") {
            profileCommands.renameProfile(selectedProfileName, name)
        }
    }

    function confirmDelete() {
        if (currentProfileList.length <= 1) {
            dialogErrorMessage = "Cannot delete the last remaining profile."
            return
        }
        if (assignments) assignments.deleteProfile(selectedProfileName)
        else if (profileCommands && typeof profileCommands.deleteProfile === "function") {
            profileCommands.deleteProfile(selectedProfileName)
        }
    }

    function confirmImport() {
        var name = dialogInputName.trim()
        if (name.length === 0) {
            dialogErrorMessage = "Import profile name cannot be empty."
            return
        }
        if (dialogDocumentText.trim().length === 0) {
            dialogErrorMessage = "Paste a valid profile JSON document to import."
            return
        }
        try {
            JSON.parse(dialogDocumentText)
        } catch (e) {
            dialogErrorMessage = "Invalid JSON syntax: " + e.message
            return
        }
        if (profileCommands && typeof profileCommands.importProfile === "function") {
            profileCommands.importProfile(name, dialogDocumentText)
        }
    }

    function loadFromProfile(data) {
        // Settings moved to their own tabs; Profiles keeps only the profile list.
    }

    RowLayout {
        anchors.fill: parent
        spacing: 14

        // Left Column: Profile Selector Card
        Rectangle {
            Layout.preferredWidth: 280
            Layout.fillHeight: true
            radius: Theme.cornerRadius
            color: Theme.panel
            border.color: Theme.line

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                Text { text: "Profiles"; font.bold: true; font.pixelSize: 16; color: Theme.text; font.family: Theme.family }
                Text { text: "Manage local and game-specific profiles."; color: Theme.muted; font.pixelSize: 11; font.family: Theme.family }

                ListView {
                    id: profileListView
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: root.currentProfileList
                    delegate: Rectangle {
                        width: profileListView.width
                        height: 36
                        radius: Theme.cornerRadius / 3
                        color: (root.selectedProfileName === modelData || (root.service && (root.service.editingProfile === modelData || root.service.activeProfile === modelData))) ? Theme.main : Theme.background
                        border.color: Theme.line

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            Text {
                                text: modelData
                                color: Theme.text
                                font.pixelSize: 12
                                font.bold: (root.selectedProfileName === modelData || (root.service && root.service.activeProfile === modelData))
                                font.family: Theme.family
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Text {
                                text: (root.assignments && root.assignments.defaultProfile === modelData) ? "Default" : ((root.service && root.service.activeProfile === modelData) ? "Active" : "")
                                color: "#2fbf71"
                                font.pixelSize: 11
                                font.bold: true
                                font.family: Theme.family
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            enabled: (!root.service || !root.service.busy) && (!root.profileCommands || !root.profileCommands.busy)
                            onClicked: {
                                root.selectedProfileName = modelData
                                if (root.service && (!root.studio || !root.studio.dirty) && typeof root.service.loadProfile === "function") {
                                    root.service.loadProfile(modelData)
                                }
                            }
                        }
                    }
                }

                // Profile action buttons
                GridLayout {
                    Layout.fillWidth: true
                    columns: 3
                    rowSpacing: 6
                    columnSpacing: 6

                    SocialButton {
                        objectName: "createProfileButton"
                        text: "New"
                        enabled: root.canCreate
                        onClicked: root.openCreateDialog()
                    }
                    SocialButton {
                        objectName: "copyProfileButton"
                        text: "Duplicate"
                        enabled: root.canCopy
                        onClicked: root.openCopyDialog()
                    }
                    SocialButton {
                        objectName: "renameProfileButton"
                        text: "Rename"
                        enabled: root.canRename
                        onClicked: root.openRenameDialog()
                    }
                    SocialButton {
                        objectName: "deleteProfileButton"
                        text: "Delete"
                        enabled: root.canDelete
                        onClicked: root.openDeleteDialog()
                    }
                    SocialButton {
                        objectName: "importProfileButton"
                        text: "Import"
                        enabled: root.canImport
                        onClicked: root.openImportDialog()
                    }
                    SocialButton {
                        objectName: "exportProfileButton"
                        text: "Export"
                        enabled: root.canExport
                        onClicked: root.openExportDialog()
                    }
                }

                SocialButton {
                    objectName: "setDefaultProfileButton"
                    text: "Set as general default"
                    Layout.fillWidth: true
                    visible: root.localLifecycle
                    enabled: root.localCanEdit && root.currentProfileList.indexOf(root.selectedProfileName) >= 0 && root.selectedProfileName !== root.assignments.defaultProfile
                    onClicked: root.assignments.setDefaultProfile(root.selectedProfileName)
                }

                // Command status text
                Text {
                    text: root.assignments ? root.assignments.status : (root.profileCommands ? root.profileCommands.status : "Native profile lifecycle unavailable.")
                    color: Theme.muted; font.pixelSize: 10; font.family: Theme.family
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
            }
        }

        // Right column: where each setting lives. Profiles only picks, creates and saves profiles (Kian 2026-11-25).
        Rectangle {
            objectName: "profileSettingsGuide"
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            implicitHeight: guideColumn.implicitHeight + 28
            radius: Theme.cornerRadius; color: Theme.panel; border.color: Theme.line
            ColumnLayout {
                id: guideColumn
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 14; spacing: 8
                Text {
                    text: "Editing: " + (root.service && root.service.editingProfile ? root.service.editingProfile : "no profile loaded")
                    font.bold: true; font.pixelSize: 15; color: Theme.text; font.family: Theme.family
                }
                Text {
                    objectName: "profilesStatusFeedback"
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: root.statusFeedback
                    color: "#2fbf71"; font.pixelSize: 12; font.family: Theme.family; wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    text: "Each tab saves its settings into this profile:"
                    color: Theme.muted; font.pixelSize: 12; font.family: Theme.family; wrapMode: Text.WordWrap
                }
                Repeater {
                    model: [
                        "Map: button and key mappings",
                        "Sticks: deadzones, response curves, controller mouse",
                        "Motion, Vibration, Macros: gyro, rumble, macros",
                        "Hotkeys: the Mod key and desktop shortcuts",
                        "Devices: virtual pad type, how controllers combine, output slots"
                    ]
                    delegate: Text {
                        Layout.fillWidth: true
                        text: "• " + modelData
                        color: Theme.text; font.pixelSize: 12; font.family: Theme.family; wrapMode: Text.WordWrap
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: "Give a game its profile on the game's page in the Library. Unknown profile fields and route layers are preserved across saves."
                    color: Theme.muted; font.pixelSize: 11; font.family: Theme.family; wrapMode: Text.WordWrap
                }
            }
        }
    }

    // Modal Confirmation / Input Overlay Dialog
    Rectangle {
        id: dialogOverlay
        anchors.fill: parent
        color: "#b0000000"
        visible: root.activeDialog.length > 0
        z: 999

        MouseArea {
            anchors.fill: parent
            // Block clicks to background
            onClicked: {}
        }

        Rectangle {
            anchors.centerIn: parent
            width: root.activeDialog === "export" || root.activeDialog === "import" ? 560 : 420
            height: root.activeDialog === "export" || root.activeDialog === "import" ? 440 : 220
            radius: Theme.cornerRadius
            color: Theme.panel
            border.color: Theme.line

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 12

                // Dialog Title
                Text {
                    text: root.activeDialog === "create" ? "Create New Profile" :
                          root.activeDialog === "copy" ? "Copy Profile (" + root.selectedProfileName + ")" :
                          root.activeDialog === "rename" ? "Rename Profile (" + root.selectedProfileName + ")" :
                          root.activeDialog === "delete" ? "Delete Profile Confirmation" :
                          root.activeDialog === "import" ? "Import Profile Document" :
                          root.activeDialog === "export" ? "Export Profile Document" : ""
                    font.bold: true
                    font.pixelSize: 16
                    color: Theme.text
                    font.family: Theme.family
                }

                // Dialog Error message if any
                Text {
                    Layout.fillWidth: true
                    text: root.dialogErrorMessage
                    color: "#e03a2f"
                    font.pixelSize: 11
                    font.family: Theme.family
                    visible: root.dialogErrorMessage.length > 0
                }

                // Dialog body depending on activeDialog
                // 1. Text input for create, copy, rename, import name
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    visible: root.activeDialog === "create" || root.activeDialog === "copy" || root.activeDialog === "rename" || root.activeDialog === "import"

                    Text {
                        text: root.activeDialog === "rename" ? "New Name:" : "Profile Name:"
                        color: Theme.muted
                        font.pixelSize: 12
                        font.family: Theme.family
                    }
                    TextField {
                        id: dialogNameField
                        objectName: "dialogNameField"
                        Layout.fillWidth: true
                        text: root.dialogInputName
                        color: Theme.text
                        font.family: Theme.family
                        background: Rectangle {
                            color: Theme.background
                            border.color: parent.activeFocus ? Theme.text : Theme.line
                            radius: Theme.cornerRadius / 2
                        }
                        onTextEdited: root.dialogInputName = text
                    }
                }

                // 2. Delete confirmation warning
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    visible: root.activeDialog === "delete"

                    Text {
                        text: "Are you sure you want to permanently delete profile '" + root.selectedProfileName + "'?"
                        color: Theme.text
                        font.pixelSize: 13
                        font.family: Theme.family
                        wrapMode: Text.Wrap
                    }
                    Text {
                        text: "This action cannot be undone. Any routes or configurations unique to this profile will be removed."
                        color: Theme.muted
                        font.pixelSize: 11
                        font.family: Theme.family
                        wrapMode: Text.Wrap
                    }
                }

                // 3. Document JSON text area for import / export
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 6
                    visible: root.activeDialog === "import" || root.activeDialog === "export"

                    Text {
                        text: root.activeDialog === "import" ? "Profile Document JSON (paste here):" : "Exported Profile JSON (read-only document):"
                        color: Theme.muted
                        font.pixelSize: 11
                        font.family: Theme.family
                    }
                    TextArea {
                        id: dialogDocArea
                        objectName: "dialogDocArea"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: root.dialogDocumentText
                        readOnly: root.activeDialog === "export"
                        color: Theme.text
                        font.family: "Monospace"
                        font.pixelSize: 10
                        background: Rectangle {
                            color: Theme.background
                            border.color: parent.activeFocus ? Theme.text : Theme.line
                            radius: Theme.cornerRadius / 2
                        }
                        onTextChanged: {
                            if (activeFocus && root.activeDialog === "import") {
                                root.dialogDocumentText = text
                            }
                        }
                    }
                }

                // Dialog action buttons
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Item { Layout.fillWidth: true }

                    SocialButton {
                        text: "Cancel"
                        onClicked: {
                            root.activeDialog = ""
                            root.dialogErrorMessage = ""
                        }
                    }

                    SocialButton {
                        objectName: "dialogConfirmButton"
                        text: root.activeDialog === "delete" ? "Delete Permanently" :
                              root.activeDialog === "create" ? "Create Profile" :
                              root.activeDialog === "copy" ? "Copy Profile" :
                              root.activeDialog === "rename" ? "Rename Profile" :
                              root.activeDialog === "import" ? "Import Profile" : "Close"
                        onClicked: {
                            if (root.activeDialog === "create") root.confirmCreate()
                            else if (root.activeDialog === "copy") root.confirmCopy()
                            else if (root.activeDialog === "rename") root.confirmRename()
                            else if (root.activeDialog === "delete") root.confirmDelete()
                            else if (root.activeDialog === "import") root.confirmImport()
                            else root.activeDialog = ""
                        }
                    }
                }
            }
        }
    }
}
