import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Rectangle {
    id: root
    objectName: "modManagementPage"
    color: Theme.background

    property var viewModel: (typeof modManagement !== "undefined") ? modManagement : null
    property string installDirectoryInput: (viewModel && viewModel.selectedProfile && viewModel.selectedProfile.install_directory) ? String(viewModel.selectedProfile.install_directory) : ""

    property var gameChoices: typeof modGameChoices !== "undefined" ? modGameChoices : []
    property var filteredGames: gameChoices.filter(function(g) {
        if (!gameSearch.text) return true
        return (g.title || "").toLowerCase().indexOf(gameSearch.text.toLowerCase()) !== -1
    })

    property int activeTab: 3  // Browse: pick a game on the left, find mods, install
    property string selectedLogPath: ""

    FolderDialog {
        id: profileFolder
        title: "Choose cached r2modman profile folder"
        onAccepted: if (viewModel) importProfileDirField.text = viewModel.localFolder(selectedFolder.toString())
    }
    FileDialog {
        id: exportPackDialog
        objectName: "exportPackDialog"
        title: "Save mod pack"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Waystone mod pack (*.wsmodpack.json)"]
        onAccepted: if (viewModel && viewModel.selectedProfileId)
            viewModel.exportModpack(viewModel.selectedProfileId, viewModel.localFolder(selectedFile.toString()))
    }
    FolderDialog {
        id: installFolder
        title: "Choose this game's installation folder"
        onAccepted: if (viewModel) importInstallDirField.text = viewModel.localFolder(selectedFolder.toString())
    }

    Connections {
        target: (typeof modBrowser !== "undefined") ? modBrowser : null
        function onNexusLinkReceived() { root.activeTab = 3 }
    }

    Component.onCompleted: {
        if (!root.selectedGameId && root.gameChoices.length > 0 && !(typeof modBrowser !== "undefined" && modBrowser && modBrowser.filesFor && modBrowser.filesFor.platform === "nexus"))
            root.chooseGame(root.gameChoices[0])
        // Opened because a Nexus 'Mod manager download' arrived: go straight to the download.
        if (typeof modBrowser !== "undefined" && modBrowser && modBrowser.filesFor && modBrowser.filesFor.platform === "nexus"
                && (modBrowser.busy || modBrowser.downloadedPath.length > 0))
            root.activeTab = 3
        if (viewModel && viewModel.refreshProfiles)
            viewModel.refreshProfiles()
        if (viewModel && viewModel.discoverLogs)
            viewModel.discoverLogs()
    }

    component TabNavButton: Button {
        id: tabBtn
        property bool selected: false
        implicitHeight: 34
        leftPadding: 14; rightPadding: 14
        contentItem: Text {
            text: tabBtn.text
            color: tabBtn.selected ? "#ffffff" : Theme.muted
            font.pixelSize: 13
            font.bold: tabBtn.selected
            font.family: Theme.family
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            color: tabBtn.selected ? (Theme.main || "#2a52be") : (tabBtn.hovered ? Theme.hover : "transparent")
            radius: 6
            border.color: tabBtn.selected ? "transparent" : (tabBtn.hovered ? Theme.line : "transparent")
        }
    }

    component Badge: Rectangle {
        property alias text: badgeLabel.text
        property color textColor: "#ffffff"
        implicitHeight: 20
        implicitWidth: badgeLabel.implicitWidth + 12
        radius: 10
        Text {
            id: badgeLabel
            anchors.centerIn: parent
            font.pixelSize: 10
            font.bold: true
            color: parent.textColor
        }
    }

    property string selectedGameId: ""
    function chooseGame(g) {
        if (!g) return
        root.selectedGameId = g.game_id
        if (typeof modBrowser !== "undefined" && modBrowser) modBrowser.selectGame(g.game_id)
        if (viewModel && viewModel.selectGame) viewModel.selectGame(g.game_id)
        if (importGameIdField) importGameIdField.text = g.game_id
        if (importGameTitleField) importGameTitleField.text = g.title
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 12

    // Games: PC first, then the ones you play most (installed, favourite, playtime, recent). One place to pick.
    Rectangle {
        objectName: "modGamePanel"
        Layout.preferredWidth: 260
        Layout.fillHeight: true
        color: Theme.panel; radius: Theme.cornerRadius; border.color: Theme.line
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 10; spacing: 8
            TextField {
                id: modGameFilter
                objectName: "modGameFilter"
                Layout.fillWidth: true
                placeholderText: "Search games"
                placeholderTextColor: Theme.muted
                color: Theme.text; font.family: Theme.family
                background: Rectangle { color: Theme.hover; radius: 6; border.color: modGameFilter.activeFocus ? Theme.main : Theme.line }
            }
            ListView {
                id: modGameList
                objectName: "modGameList"
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; spacing: 2
                ScrollBar.vertical: ScrollBar {}
                property string needle: modGameFilter.text.toLowerCase()
                model: root.gameChoices.filter(function(g) { return !needle || (g.title || "").toLowerCase().indexOf(needle) !== -1 }).slice(0, 400)
                section.property: "pc"
                section.delegate: Text {
                    required property bool section
                    width: modGameList.width; topPadding: 6; bottomPadding: 2
                    text: section ? "PC games" : "Emulated & other"
                    color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; font.bold: true
                }
                delegate: Rectangle {
                    required property var modelData
                    width: modGameList.width; height: 34; radius: 6
                    color: root.selectedGameId === modelData.game_id ? Theme.main : (gameMouse.containsMouse ? Theme.hover : "transparent")
                    MouseArea { id: gameMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.chooseGame(modelData) }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 6
                        Text { text: modelData.title; color: Theme.text; font.family: Theme.family; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true; textFormat: Text.PlainText }
                        Text {
                            visible: modelData.playtime >= 3600
                            text: Math.round(modelData.playtime / 3600) + " h"
                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 11
                        }
                    }
                }
            }
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: 10

        // ==========================================
        // TOP HERO & INSTANCE BAR (PRISM / MO2 STYLE)
        // ==========================================
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 56
            color: Theme.panel
            radius: Theme.cornerRadius
            border.color: Theme.line

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 10

                Text {
                    visible: false
                    text: "🎮 Game:"
                    color: Theme.text
                    font.bold: true
                    font.pixelSize: 13
                }

                ComboBox {
                    id: heroGameCombo
                    visible: false
                    Layout.preferredWidth: 200
                    model: root.filteredGames
                    textRole: "title"
                    currentIndex: -1
                    displayText: currentIndex < 0 ? (root.filteredGames.length > 0 ? "Select a game" : "All Games") : currentText
                    palette.text: Theme.text; palette.buttonText: Theme.text
                    palette.button: Theme.panel; palette.base: Theme.panel
                    onActivated: {
                        var g = root.filteredGames[currentIndex]
                        if (g && typeof modBrowser !== "undefined" && modBrowser)
                            modBrowser.selectGame(g.game_id)
                        if (g && viewModel && viewModel.selectGame) {
                            viewModel.selectGame(g.game_id)
                            if (importGameIdField) importGameIdField.text = g.game_id
                            if (importGameTitleField) importGameTitleField.text = g.title
                        }
                    }
                }


                Text {
                    text: "🧩 Instance:"
                    color: Theme.text
                    font.bold: true
                    font.pixelSize: 13
                }

                ComboBox {
                    id: heroInstanceCombo
                    Layout.preferredWidth: 220
                    model: viewModel ? (viewModel.instances || viewModel.profiles || []) : []
                    textRole: "display_name"
                    currentIndex: {
                        if (!viewModel) return -1
                        var list = viewModel.instances || viewModel.profiles || []
                        for (var i = 0; i < list.length; i++) {
                            if (list[i].profile_id === viewModel.selectedProfileId) return i
                        }
                        return list.length > 0 ? 0 : -1
                    }
                    displayText: currentIndex >= 0 && model[currentIndex] ? (model[currentIndex].display_name || model[currentIndex].name || "Instance") : "No instance"
                    palette.text: Theme.text; palette.buttonText: Theme.text
                    palette.button: Theme.panel; palette.base: Theme.panel
                    onActivated: {
                        if (model[currentIndex] && viewModel && viewModel.selectInstance) {
                            viewModel.selectInstance(model[currentIndex].profile_id)
                        }
                    }
                }

                Badge {
                    color: (viewModel && viewModel.selectedProfile && viewModel.selectedProfile.isolate_saves) ? "#2e7d32" : "#424242"
                    text: (viewModel && viewModel.selectedProfile && viewModel.selectedProfile.isolate_saves) ? "🗄️ Isolated Saves" : "Shared Saves"
                    textColor: "#ffffff"
                }

                Item { Layout.fillWidth: true }

                SocialButton {
                    text: "+ New Instance"
                    onClicked: newInstanceDialog.open()
                }

                SocialButton {
                    text: "Clone"
                    enabled: Boolean(viewModel && viewModel.selectedProfileId)
                    onClicked: {
                        if (viewModel && viewModel.selectedProfileId) {
                            var name = (viewModel.selectedProfile.display_name || "Instance") + " Copy"
                            viewModel.cloneInstance(viewModel.selectedProfileId, name)
                        }
                    }
                }

                SocialButton {
                    text: "Export Pack"
                    enabled: Boolean(viewModel && viewModel.selectedProfileId)
                    onClicked: if (viewModel && viewModel.selectedProfileId) exportPackDialog.open()
                }
            }
        }

        // ==========================================
        // WORKSPACE TAB NAVIGATION
        // ==========================================
        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            TabNavButton {
                text: "📦 Installed Mods & Load Order"
                selected: root.activeTab === 0
                onClicked: root.activeTab = 0
            }
            TabNavButton {
                text: "🧩 Instances & Modpacks (Prism)"
                selected: root.activeTab === 1
                onClicked: root.activeTab = 1
            }
            TabNavButton {
                text: "⚡ AI Log Review & Crash Diagnostics"
                selected: root.activeTab === 2
                onClicked: {
                    root.activeTab = 2
                    if (viewModel && viewModel.discoverLogs) viewModel.discoverLogs()
                }
            }
            TabNavButton {
                objectName: "modBrowseTabButton"
                text: "🌐 Browse Mods"
                selected: root.activeTab === 3
                onClicked: {
                    root.activeTab = 3
                    if (typeof modBrowser !== "undefined" && modBrowser && modBrowser.items.length === 0 && !modBrowser.busy)
                        modBrowser.search("")
                }
            }

            Item { Layout.fillWidth: true }

            Label {
                id: modsStatusLabel
                objectName: "modsStatusLabel"
                color: Theme.muted
                font.pixelSize: 12
                text: viewModel ? (viewModel.status || "Ready.") : "No mod view model."
                elide: Text.ElideRight
                Layout.maximumWidth: 320
            }
        }

        // ==========================================
        // TAB 0: INSTALLED MODS & LOAD ORDER (VORTEX / MO2)
        // ==========================================
        Rectangle {
            visible: root.activeTab === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.panel
            radius: Theme.cornerRadius
            border.color: Theme.line

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8

                // Header toolbar
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    TextField {
                        id: modSearchInput
                        Layout.preferredWidth: 240
                        placeholderText: "Filter installed mods..."
                        color: Theme.text
                        background: Rectangle { color: Theme.main; radius: 5; border.color: Theme.line }
                    }

                    Text {
                        text: {
                            var mods = (viewModel && viewModel.selectedProfile && viewModel.selectedProfile.mods) ? viewModel.selectedProfile.mods : []
                            var enabledCount = 0
                            for (var i = 0; i < mods.length; i++) if (mods[i].enabled) enabledCount++
                            return enabledCount + " of " + mods.length + " mods active"
                        }
                        color: Theme.muted
                        font.pixelSize: 12
                    }

                    Item { Layout.fillWidth: true }

                    SocialButton {
                        text: "Enable All"
                        onClicked: {
                            var mods = (viewModel && viewModel.selectedProfile && viewModel.selectedProfile.mods) ? viewModel.selectedProfile.mods : []
                            for (var i = 0; i < mods.length; i++) {
                                if (!mods[i].enabled && viewModel) viewModel.toggleMod(viewModel.selectedProfileId, mods[i].mod_id)
                            }
                        }
                    }
                    SocialButton {
                        text: "Disable All"
                        onClicked: {
                            var mods = (viewModel && viewModel.selectedProfile && viewModel.selectedProfile.mods) ? viewModel.selectedProfile.mods : []
                            for (var i = 0; i < mods.length; i++) {
                                if (mods[i].enabled && viewModel) viewModel.toggleMod(viewModel.selectedProfileId, mods[i].mod_id)
                            }
                        }
                    }
                }

                // Table Header
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 28
                    color: Theme.hover
                    radius: 4

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8; anchors.rightMargin: 8
                        spacing: 8
                        Text { text: "Active"; color: Theme.muted; font.pixelSize: 11; font.bold: true; Layout.preferredWidth: 50 }
                        Text { text: "Priority"; color: Theme.muted; font.pixelSize: 11; font.bold: true; Layout.preferredWidth: 50 }
                        Text { text: "Mod Name"; color: Theme.muted; font.pixelSize: 11; font.bold: true; Layout.fillWidth: true }
                        Text { text: "Category"; color: Theme.muted; font.pixelSize: 11; font.bold: true; Layout.preferredWidth: 100 }
                        Text { text: "Conflict (+/-)"; color: Theme.muted; font.pixelSize: 11; font.bold: true; Layout.preferredWidth: 90 }
                        Text { text: "Actions"; color: Theme.muted; font.pixelSize: 11; font.bold: true; Layout.preferredWidth: 100 }
                    }
                }

                // Mod List
                ListView {
                    id: modsListView
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: {
                        var mods = (viewModel && viewModel.selectedProfile && viewModel.selectedProfile.mods) ? viewModel.selectedProfile.mods : []
                        if (!modSearchInput.text) return mods
                        return mods.filter(function(m) {
                            return (m.name || "").toLowerCase().indexOf(modSearchInput.text.toLowerCase()) !== -1
                        })
                    }
                    delegate: Rectangle {
                        width: modsListView.width
                        implicitHeight: 38
                        color: index % 2 === 0 ? "transparent" : Theme.hover
                        radius: 4

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8; anchors.rightMargin: 8
                            spacing: 8

                            CheckBox {
                                Layout.preferredWidth: 50
                                checked: Boolean(modelData.enabled)
                                onToggled: {
                                    if (viewModel && viewModel.toggleMod)
                                        viewModel.toggleMod(viewModel.selectedProfileId, modelData.mod_id)
                                }
                            }

                            Text {
                                Layout.preferredWidth: 50
                                text: "#" + (modelData.priority !== undefined ? modelData.priority : index)
                                color: Theme.muted
                                font.pixelSize: 12
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                Text {
                                    text: modelData.name || modelData.mod_id || "Mod"
                                    color: modelData.enabled ? Theme.text : Theme.muted
                                    font.bold: Boolean(modelData.enabled)
                                    font.pixelSize: 13
                                }
                                Text {
                                    text: "v" + (modelData.version || "1.0.0") + " by " + (modelData.author || "Community")
                                    color: Theme.muted
                                    font.pixelSize: 10
                                }
                            }

                            Badge {
                                Layout.preferredWidth: 100
                                color: "#37474f"
                                text: modelData.category || "General"
                                textColor: "#eceff1"
                            }

                            // Conflict Badge
                            Badge {
                                Layout.preferredWidth: 90
                                property var conflicts: (viewModel && viewModel.conflictMatrix && viewModel.conflictMatrix.conflicts) ? viewModel.conflictMatrix.conflicts : []
                                property bool hasConflict: {
                                    for (var c = 0; c < conflicts.length; c++) {
                                        if (conflicts[c].winning_mod_id === modelData.mod_id || conflicts[c].losing_mod_id === modelData.mod_id)
                                            return true
                                    }
                                    return false
                                }
                                color: hasConflict ? "#c62828" : "#2e7d32"
                                text: hasConflict ? "⚠️ Overrides" : "✓ Clean"
                                textColor: "#ffffff"
                            }

                            RowLayout {
                                Layout.preferredWidth: 100
                                spacing: 4
                                Button {
                                    text: "▲"
                                    implicitWidth: 26; implicitHeight: 26
                                    enabled: index > 0
                                    onClicked: {
                                        var mods = (viewModel && viewModel.selectedProfile && viewModel.selectedProfile.mods) ? viewModel.selectedProfile.mods : []
                                        if (index > 0) {
                                            var ids = []
                                            for (var k = 0; k < mods.length; k++) ids.push(mods[k].mod_id)
                                            var tmp = ids[index]; ids[index] = ids[index - 1]; ids[index - 1] = tmp
                                            if (viewModel) viewModel.reorderMods(viewModel.selectedProfileId, ids)
                                        }
                                    }
                                }
                                Button {
                                    text: "▼"
                                    implicitWidth: 26; implicitHeight: 26
                                    enabled: index < modsListView.count - 1
                                    onClicked: {
                                        var mods = (viewModel && viewModel.selectedProfile && viewModel.selectedProfile.mods) ? viewModel.selectedProfile.mods : []
                                        if (index < mods.length - 1) {
                                            var ids = []
                                            for (var k = 0; k < mods.length; k++) ids.push(mods[k].mod_id)
                                            var tmp = ids[index]; ids[index] = ids[index + 1]; ids[index + 1] = tmp
                                            if (viewModel) viewModel.reorderMods(viewModel.selectedProfileId, ids)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // ==========================================
                // DEPLOYMENT & ROLLBACK ACTION DOCK
                // ==========================================
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 90
                    color: Theme.hover
                    radius: 6
                    border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            TextField {
                                id: installDirField
                                objectName: "installDirField"
                                Layout.fillWidth: true
                                placeholderText: "Target install directory (profile default if empty)"
                                text: root.installDirectoryInput
                                onTextChanged: root.installDirectoryInput = text
                            }

                            SocialButton {
                                id: previewButton
                                objectName: "previewButton"
                                text: (viewModel && viewModel.busy) ? "Working..." : "Preview Deploy"
                                enabled: Boolean(viewModel && !viewModel.busy && viewModel.selectedProfileId !== "")
                                onClicked: if (viewModel) viewModel.previewSelected(installDirField.text)
                            }
                            SocialButton {
                                id: applyButton
                                objectName: "applyButton"
                                text: "Apply Changes"
                                enabled: Boolean(viewModel && viewModel.canApply)
                                onClicked: if (viewModel) viewModel.applyReviewedPlan()
                            }
                            SocialButton {
                                id: undoButton
                                objectName: "undoButton"
                                text: "Undo Deployment"
                                enabled: Boolean(viewModel && viewModel.canUndo)
                                onClicked: if (viewModel) viewModel.undoSelectedOperation()
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 12

                            Label {
                                id: planLabel
                                objectName: "planLabel"
                                Layout.fillWidth: true
                                color: Theme.text
                                font.pixelSize: 11
                                text: {
                                    if (!viewModel) return ""
                                    var plan = viewModel.lastPlan || {}
                                    if (!plan || !plan.plan_id) return "No reviewed plan."
                                    var conflicts = (plan.conflicts || []).length
                                    return "Plan: " + (plan.plan_id || "")
                                        + "  Can apply: " + (plan.can_apply ? "yes" : "no")
                                        + "  Conflicts: " + conflicts
                                        + "  Actions: " + ((plan.actions || []).length)
                                }
                            }

                            Label {
                                id: resultLabel
                                objectName: "resultLabel"
                                Layout.fillWidth: true
                                color: Theme.muted
                                font.pixelSize: 11
                                text: {
                                    if (!viewModel) return ""
                                    var a = viewModel.lastApplyResult || {}
                                    var u = viewModel.lastUndoResult || {}
                                    var parts = []
                                    if (a && (a.operation_id || a.plan_id)) parts.push("Apply status: " + (a.status || "unknown"))
                                    if (u && (u.operation_id || u.plan_id)) parts.push("Undo status: " + (u.status || "unknown"))
                                    return parts.join(" | ")
                                }
                            }
                        }
                    }
                }
            }
        }

        // ==========================================
        // TAB 1: INSTANCES / MODPACKS (PRISM LAUNCHER)
        // ==========================================
        ScrollView {
            visible: root.activeTab === 1
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ColumnLayout {
                width: parent.width
                spacing: 12

                Text {
                    text: "Available Instances & Modpacks for Selected Game"
                    font.bold: true
                    font.pixelSize: 15
                    color: Theme.text
                }

                // Grid of Instances
                Flow {
                    Layout.fillWidth: true
                    spacing: 12

                    Repeater {
                        model: viewModel ? (viewModel.instances || viewModel.profiles || []) : []
                        delegate: Rectangle {
                            width: 280
                            height: 160
                            color: (viewModel && viewModel.selectedProfileId === modelData.profile_id) ? Theme.hover : Theme.panel
                            radius: Theme.cornerRadius
                            border.color: (viewModel && viewModel.selectedProfileId === modelData.profile_id) ? (Theme.main || "#2a52be") : Theme.line
                            border.width: (viewModel && viewModel.selectedProfileId === modelData.profile_id) ? 2 : 1

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 6

                                RowLayout {
                                    Layout.fillWidth: true
                                    Text {
                                        text: modelData.display_name || modelData.name || "Instance"
                                        font.bold: true
                                        font.pixelSize: 14
                                        color: Theme.text
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    Badge {
                                        color: modelData.isolate_saves ? "#2e7d32" : "#555"
                                        text: modelData.isolate_saves ? "Isolated" : "Shared"
                                    }
                                }

                                Text {
                                    text: (modelData.mods_count !== undefined ? modelData.mods_count : ((modelData.mods || []).length)) + " mods installed"
                                    color: Theme.muted
                                    font.pixelSize: 12
                                }

                                Text {
                                    text: modelData.notes || "Custom player modpack instance"
                                    color: Theme.muted
                                    font.pixelSize: 11
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }

                                Item { Layout.fillHeight: true }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6

                                    SocialButton {
                                        text: (viewModel && viewModel.selectedProfileId === modelData.profile_id) ? "Active" : "Select"
                                        enabled: Boolean(viewModel && viewModel.selectedProfileId !== modelData.profile_id)
                                        onClicked: if (viewModel) viewModel.selectInstance(modelData.profile_id)
                                    }

                                    SocialButton {
                                        text: "Clone"
                                        onClicked: if (viewModel) viewModel.cloneInstance(modelData.profile_id, (modelData.display_name || "Instance") + " Copy")
                                    }

                                    SocialButton {
                                        text: "Delete"
                                        onClicked: if (viewModel) viewModel.deleteInstance(modelData.profile_id)
                                    }
                                }
                            }
                        }
                    }
                }

                // ==========================================
                // LEGACY PROFILES & IMPORT SECTION (CI PRESERVED)
                // ==========================================
                GroupBox {
                    title: "Import Cached r2modman / BepInEx Profile"
                    Layout.fillWidth: true
                    label: Label { textFormat: Text.PlainText; text: parent.title; color: Theme.text; font.bold: true }
                    background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: Theme.cornerRadius }

                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 6

                        Label {
                            objectName: "selectedProfileLabel"
                            Layout.fillWidth: true
                            color: Theme.text
                            wrapMode: Text.WordWrap
                            text: {
                                if (!viewModel || !viewModel.selectedProfileId) return "No profile selected."
                                var p = viewModel.selectedProfile || {}
                                return "Selected: " + (p.display_name || p.name || p.profile_id || viewModel.selectedProfileId)
                                    + "  Manager: " + (p.manager_type || p.manager || "?")
                                    + "  Install: " + (p.install_directory || "")
                            }
                        }

                        TextField {
                            id: importProfileDirField
                            objectName: "importProfileDirField"
                            Layout.fillWidth: true
                            placeholderText: "Cached r2modman profile directory (contains mods.yml)"
                            color: Theme.text; background: Rectangle { color: Theme.main; radius: 5; border.color: Theme.line }
                        }
                        TextField {
                            id: importInstallDirField
                            objectName: "importInstallDirField"
                            Layout.fillWidth: true
                            placeholderText: "Target game install directory"
                            color: Theme.text; background: Rectangle { color: Theme.main; radius: 5; border.color: Theme.line }
                        }
                        TextField {
                            id: gameSearch
                            objectName: "modGameSearch"
                            Layout.fillWidth: true
                            placeholderText: "Find a game in your library"
                            color: Theme.text
                            background: Rectangle { color: Theme.main; radius: 5; border.color: Theme.line }
                        }
                        ComboBox {
                            id: libraryGameChoice
                            objectName: "modLibraryGameChoice"
                            Layout.fillWidth: true
                            model: root.filteredGames
                            textRole: "title"
                            currentIndex: -1
                            displayText: currentIndex < 0 ? "Choose library game" : currentText
                            palette.text: Theme.text; palette.buttonText: Theme.text
                            palette.button: Theme.main; palette.base: Theme.panel
                            onActivated: {
                                var game = root.filteredGames[currentIndex]
                                if (game) {
                                    if (importGameIdField) importGameIdField.text = game.game_id
                                    if (importGameTitleField) importGameTitleField.text = game.title
                                }
                            }
                        }

                        RowLayout {
                            visible: false
                            TextField { id: importGameIdField; objectName: "importGameIdField" }
                            TextField { id: importGameTitleField; objectName: "importGameTitleField" }
                        }

                        SocialButton {
                            id: importProfileButton
                            objectName: "importProfileButton"
                            text: "Import r2modman Profile"
                            onClicked: {
                                if (viewModel && viewModel.importCachedR2Profile) {
                                    viewModel.importCachedR2Profile(
                                        importProfileDirField.text,
                                        importInstallDirField.text,
                                        importGameIdField.text,
                                        importGameTitleField.text
                                    )
                                }
                            }
                        }

                        // Preserved profilesListView for CI tests
                        ListView {
                            id: profilesListView
                            objectName: "profilesListView"
                            Layout.fillWidth: true
                            implicitHeight: Math.min(120, count * 36)
                            clip: true
                            model: viewModel ? viewModel.profiles : []
                            delegate: Rectangle {
                                width: profilesListView.width
                                implicitHeight: 32
                                color: (viewModel && viewModel.selectedProfileId === modelData.profile_id) ? Theme.hover : "transparent"
                                Text {
                                    anchors.centerIn: parent
                                    color: Theme.text
                                    text: (modelData.display_name || modelData.name || modelData.profile_id || "profile") + " [" + (modelData.manager_type || modelData.manager || "") + "]"
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: if (viewModel) viewModel.selectProfile(modelData.profile_id)
                                }
                            }
                        }
                    }
                }
            }
        }

        // ==========================================
        // TAB 2: AI LOG REVIEW & RAPID CRASH TRIAGING
        // ==========================================
        ScrollView {
            visible: root.activeTab === 2
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ColumnLayout {
                width: parent.width
                spacing: 12

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 64
                    color: Theme.panel
                    radius: Theme.cornerRadius
                    border.color: Theme.line

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10

                        Text {
                            text: "⚡ Modpack Log Discovery:"
                            color: Theme.text
                            font.bold: true
                            font.pixelSize: 13
                        }

                        ComboBox {
                            id: logFilesCombo
                            Layout.fillWidth: true
                            model: viewModel ? viewModel.discoveredLogs : []
                            textRole: "name"
                            displayText: currentIndex >= 0 && model[currentIndex] ? (model[currentIndex].name + " (" + model[currentIndex].modified + ")") : "No logs selected"
                            palette.text: Theme.text; palette.buttonText: Theme.text
                            palette.button: Theme.hover; palette.base: Theme.panel
                            onActivated: {
                                if (model[currentIndex]) root.selectedLogPath = model[currentIndex].path
                            }
                        }

                        SocialButton {
                            text: "🔍 Scan Logs"
                            onClicked: if (viewModel) viewModel.discoverLogs()
                        }

                        SocialButton {
                            text: "⚡ Analyze with AI"
                            enabled: Boolean(root.selectedLogPath || (logFilesCombo.model && logFilesCombo.model.length > 0))
                            onClicked: {
                                var p = root.selectedLogPath
                                if (!p && logFilesCombo.model && logFilesCombo.model.length > 0)
                                    p = logFilesCombo.model[0].path
                                if (p && viewModel) viewModel.analyzeLogFile(p)
                            }
                        }
                    }
                }

                // AI Diagnostic Findings Report
                GroupBox {
                    title: "Diagnostic Triage Summary"
                    Layout.fillWidth: true
                    label: Label { textFormat: Text.PlainText; text: parent.title; color: Theme.text; font.bold: true }
                    background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: Theme.cornerRadius }

                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 8

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 12

                            Badge {
                                property var rep: viewModel ? viewModel.latestLogReport : {}
                                color: (rep && rep.healthy) ? "#2e7d32" : "#c62828"
                                text: (rep && rep.healthy) ? "🟢 Engine Healthy" : "🔴 Crashes / Errors Detected"
                            }

                            Text {
                                text: {
                                    var rep = (viewModel && viewModel.latestLogReport) ? viewModel.latestLogReport : {}
                                    if (!rep || !rep.log_path) return "Select a log and click 'Analyze with AI'."
                                    return (rep.filtered_line_count || 0) + " lines of debug noise pre-filtered. " + (rep.raw_error_count || 0) + " errors caught."
                                }
                                color: Theme.muted
                                font.pixelSize: 12
                            }
                        }

                        Text {
                            text: (viewModel && viewModel.latestLogReport && viewModel.latestLogReport.summary) ? viewModel.latestLogReport.summary : "No log analyzed yet."
                            color: Theme.text
                            font.bold: true
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }

                        // Findings List
                        Repeater {
                            model: (viewModel && viewModel.latestLogReport && viewModel.latestLogReport.findings) ? viewModel.latestLogReport.findings : []
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: findingCol.implicitHeight + 16
                                color: Theme.hover
                                radius: 6
                                border.color: modelData.severity === "critical" ? "#e53935" : Theme.line

                                ColumnLayout {
                                    id: findingCol
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    spacing: 4

                                    RowLayout {
                                        Layout.fillWidth: true
                                        Badge {
                                            color: modelData.severity === "critical" ? "#c62828" : "#f57c00"
                                            text: modelData.error_type || "Exception"
                                        }
                                        Text {
                                            text: modelData.message || ""
                                            color: Theme.text
                                            font.bold: true
                                            font.pixelSize: 12
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                        Badge {
                                            visible: Boolean(modelData.culprit_mod_id)
                                            color: "#d32f2f"
                                            text: "Culprit: " + (modelData.culprit_mod_name || modelData.culprit_mod_id || "Mod")
                                        }
                                    }

                                    // Recommended Fix Trigger
                                    RowLayout {
                                        visible: Boolean(modelData.recommended_fix)
                                        Layout.fillWidth: true
                                        spacing: 8
                                        Text {
                                            text: "💡 Agent Fix: " + (modelData.recommended_fix ? modelData.recommended_fix.description : "")
                                            color: "#81c784"
                                            font.pixelSize: 11
                                            Layout.fillWidth: true
                                        }
                                        SocialButton {
                                            text: "🔧 1-Click Fix"
                                            onClicked: {
                                                if (viewModel && modelData.recommended_fix)
                                                    viewModel.applyDiagnosticFix(modelData.recommended_fix)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // ==========================================
        // TAB 3: BROWSE (Nexus Mods / GameBanana / Modrinth), W7 phase 1
        // ==========================================
        Rectangle {
            id: browseTab
            objectName: "modBrowseTab"
            visible: root.activeTab === 3
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.panel
            radius: Theme.cornerRadius
            border.color: Theme.line
            property var browser: (typeof modBrowser !== "undefined") ? modBrowser : null

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Repeater {
                        model: browseTab.browser ? browseTab.browser.platforms : []
                        delegate: TabNavButton {
                            required property var modelData
                            objectName: "modPlatform_" + modelData.id
                            text: modelData.label
                            selected: browseTab.browser && browseTab.browser.platform === modelData.id
                            onClicked: browseTab.browser.selectPlatform(modelData.id)
                        }
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        objectName: "modMatchedGame"
                        visible: browseTab.browser && browseTab.browser.matchedGame.length > 0
                        text: browseTab.browser ? browseTab.browser.matchedGame : ""
                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 12
                    }
                    CompactButton {
                        objectName: "modChangeMatch"
                        visible: browseTab.browser && browseTab.browser.platform !== "modrinth" && browseTab.browser.matchedGame.length > 0
                        implicitWidth: 70; text: "Change"
                        hint: "Pick a different game on this site"
                        onClicked: browseTab.browser.changeGameMatch()
                    }
                }

                // What this game needs (shipped per-game knowledge): manager, frameworks, Proton launch option.
                Rectangle {
                    objectName: "modRecipe"
                    Layout.fillWidth: true
                    visible: browseTab.browser && browseTab.browser.recipe && browseTab.browser.recipe.id !== undefined
                    implicitHeight: recipeCol.implicitHeight + 16
                    radius: Theme.cornerRadius; color: Theme.background; border.color: Theme.line
                    property var r: browseTab.browser ? browseTab.browser.recipe : ({})
                    ColumnLayout {
                        id: recipeCol
                        anchors.fill: parent; anchors.margins: 8; spacing: 4
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText
                            color: Theme.text; font.family: Theme.family; font.pixelSize: 12
                            text: {
                                var r = parent.parent.r || {}
                                if (r.manager === "mo2") return "Best with Mod Organizer 2 (load order, FOMOD installers): " + (r.manager_tool || "")
                                if (r.manager === "prism") return "Use Prism Launcher instances: " + (r.manager_tool || "")
                                return "Waystone installs these mods into the game folder with preview and undo."
                            }
                        }
                        Flow {
                            Layout.fillWidth: true; spacing: 6
                            visible: (parent.parent.r.frameworks || []).length > 0
                            Text { text: "Needs first:"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                            Repeater {
                                model: parent.parent.parent.r.frameworks || []
                                delegate: CompactButton {
                                    required property var modelData
                                    implicitWidth: Math.min(420, modelData.name.length * 8 + 30)
                                    text: modelData.name
                                    hint: modelData.why || "Open its download page"
                                    onClicked: Qt.openUrlExternally(modelData.nexus_id
                                        ? "https://www.nexusmods.com/" + browseTab.browser.recipe.nexus_domain + "/mods/" + modelData.nexus_id + "?tab=files"
                                        : modelData.url)
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 6
                            visible: !!parent.parent.r.launch_options
                            Text { text: "Steam launch option:"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                            Text { text: parent.parent.parent.r.launch_options || ""; color: Theme.text; font.family: "monospace"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true; textFormat: Text.PlainText }
                            CompactButton { implicitWidth: 60; text: "Copy"; onClicked: browseTab.browser.copyText(browseTab.browser.recipe.launch_options) }
                        }
                    }
                }

                TextField {
                    id: modSearchField
                    objectName: "modSearchField"
                    Layout.fillWidth: true
                    placeholderText: browseTab.browser && browseTab.browser.gameTitle.length > 0
                                     ? "Search mods for " + browseTab.browser.gameTitle + " (Enter)"
                                     : "Search mods (Enter)"
                    color: Theme.text
                    font.family: Theme.family
                    background: Rectangle { color: Theme.hover; radius: 6; border.color: modSearchField.activeFocus ? Theme.main : Theme.line }
                    placeholderTextColor: Theme.muted
                    onAccepted: if (browseTab.browser) browseTab.browser.search(text)
                }

                Text {
                    objectName: "modBrowseStatus"
                    Layout.fillWidth: true
                    text: browseTab.browser ? browseTab.browser.status : "Mod browsing is unavailable."
                    color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; elide: Text.ElideRight
                }

                // Which game on the site is this? Shown only when the name did not match exactly.
                Flow {
                    objectName: "modGameCandidates"
                    Layout.fillWidth: true
                    spacing: 6
                    visible: browseTab.browser && browseTab.browser.candidates.length > 0
                    Repeater {
                        model: browseTab.browser ? browseTab.browser.candidates : []
                        delegate: CompactButton {
                            required property var modelData
                            required property int index
                            implicitWidth: Math.min(320, Math.max(120, label.implicitWidth + 24))
                            text: modelData.name
                            Text { id: label; visible: false; text: modelData.name; font.pixelSize: 13 }
                            onClicked: browseTab.browser.chooseCandidate(index)
                        }
                    }
                }

                ListView {
                    id: modResults
                    objectName: "modResults"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 6
                    model: browseTab.browser ? browseTab.browser.items : []
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        width: modResults.width - 12
                        height: 84
                        radius: Theme.cornerRadius
                        color: rowMouse.containsMouse ? Theme.hover : Theme.background
                        border.color: Theme.line
                        MouseArea { id: rowMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 8; spacing: 12
                            Rectangle {
                                Layout.preferredWidth: 112; Layout.preferredHeight: 68
                                radius: 6; color: Theme.panel; clip: true
                                Image {
                                    anchors.fill: parent
                                    source: modelData.thumbnail_url || ""
                                    fillMode: Image.PreserveAspectCrop
                                    asynchronous: true; cache: true
                                    sourceSize.width: 224; sourceSize.height: 136
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { text: modelData.title; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true; textFormat: Text.PlainText }
                                Text {
                                    text: [modelData.author ? "by " + modelData.author : "", modelData.game || "",
                                           modelData.downloads ? Number(modelData.downloads).toLocaleString(Qt.locale(), "f", 0) + (modelData.platform === "gamebanana" ? " views" : " downloads") : "",
                                           modelData.likes ? Number(modelData.likes).toLocaleString(Qt.locale(), "f", 0) + " likes" : "",
                                           modelData.updated_at ? "updated " + String(modelData.updated_at).slice(0, 10) : ""].filter(function(x) { return x.length > 0 }).join("  ·  ")
                                    color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true; textFormat: Text.PlainText
                                }
                                Text { text: modelData.summary || ""; visible: text.length > 0; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; elide: Text.ElideRight; maximumLineCount: 1; Layout.fillWidth: true; textFormat: Text.PlainText }
                            }
                            CompactButton {
                                implicitWidth: 80; text: "Files"
                                hint: "List downloadable files to install"
                                onClicked: browseTab.browser.showFiles(index)
                            }
                            CompactButton {
                                implicitWidth: 96; text: "Open page"
                                hint: "Open this mod on " + modelData.platform + " in your browser"
                                onClicked: Qt.openUrlExternally(modelData.url)
                            }
                        }
                    }
                    footer: Item {
                        width: modResults.width; height: moreBtn.visible ? 48 : 0
                        CompactButton {
                            id: moreBtn
                            objectName: "modLoadMore"
                            anchors.centerIn: parent
                            visible: browseTab.browser && browseTab.browser.canLoadMore
                            implicitWidth: 140; text: "Load more"
                            onClicked: browseTab.browser.loadMore()
                        }
                    }
                }

                // Files -> download -> preview -> install (with undo). Shown once a mod's files are listed.
                Rectangle {
                    objectName: "modInstallPanel"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(260, installCol.implicitHeight + 20)
                    visible: browseTab.browser && browseTab.browser.filesFor && browseTab.browser.filesFor.title !== undefined
                    radius: Theme.cornerRadius; color: Theme.background; border.color: Theme.line
                    ColumnLayout {
                        id: installCol
                        anchors.fill: parent; anchors.margins: 10; spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: browseTab.browser && browseTab.browser.filesFor ? (browseTab.browser.filesFor.title || "") : ""; color: Theme.text; font.family: Theme.family; font.bold: true; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true; textFormat: Text.PlainText }
                            ProgressBar { visible: browseTab.browser && browseTab.browser.busy && browseTab.browser.progress > 0 && browseTab.browser.progress < 1; value: browseTab.browser ? browseTab.browser.progress : 0; Layout.preferredWidth: 160 }
                        }
                        ListView {
                            objectName: "modFiles"
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.min(110, count * 32)
                            clip: true; spacing: 2
                            model: browseTab.browser ? browseTab.browser.files : []
                            delegate: RowLayout {
                                required property var modelData
                                required property int index
                                width: ListView.view.width
                                Text { text: modelData.name + (modelData.size ? "  ·  " + (modelData.size >= 1048576 ? (modelData.size / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(modelData.size / 1024)) + " KB") : "") + (modelData.date ? "  ·  " + String(modelData.date).slice(0, 10) : ""); color: Theme.text; font.family: Theme.family; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true; textFormat: Text.PlainText }
                                CompactButton {
                                    implicitWidth: (browseTab.browser && browseTab.browser.filesFor ? browseTab.browser.filesFor.platform : "") === "nexus" ? 150 : 90
                                    text: (browseTab.browser && browseTab.browser.filesFor ? browseTab.browser.filesFor.platform : "") === "nexus" ? "Download (Nexus)" : "Download"
                                    hint: (browseTab.browser && browseTab.browser.filesFor ? browseTab.browser.filesFor.platform : "") === "nexus" ? "Premium: downloads directly. Free account: opens the file on nexusmods.com, press 'Mod manager download' there" : "Download and verify this file"
                                    enabled: browseTab.browser && !browseTab.browser.busy
                                    onClicked: (browseTab.browser && browseTab.browser.filesFor ? browseTab.browser.filesFor.platform : "") === "nexus" ? browseTab.browser.openNexusDownload(index) : browseTab.browser.download(index)
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            visible: browseTab.browser && browseTab.browser.downloadedPath.length > 0
                            Text { text: "Install into"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                            // PC game (Nexus): the game's folder, remembered per game.
                            CompactButton {
                                objectName: "modGameFolder"
                                visible: (browseTab.browser && browseTab.browser.filesFor ? browseTab.browser.filesFor.platform : "") === "nexus"
                                implicitWidth: 260
                                text: browseTab.browser && browseTab.browser.gameFolder.length > 0 ? browseTab.browser.gameFolder : "Choose game folder…"
                                hint: "The game's install folder (where its .exe or Data folder is)"
                                onClicked: gameFolderDialog.open()
                            }
                            FolderDialog {
                                id: gameFolderDialog
                                title: "Choose the game's install folder"
                                onAccepted: browseTab.browser.setGameFolder(selectedFolder.toString())
                            }
                            ComboBox {
                                id: emulatorChoice
                                visible: (browseTab.browser && browseTab.browser.filesFor ? browseTab.browser.filesFor.platform : "") !== "nexus"
                                objectName: "modEmulatorChoice"
                                Layout.preferredWidth: 140
                                model: browseTab.browser ? browseTab.browser.emulators : []
                                textRole: "name"
                                palette.text: Theme.text; palette.buttonText: Theme.text; palette.button: Theme.panel; palette.base: Theme.panel
                            }
                            TextField {
                                id: titleIdField
                                objectName: "modTitleId"
                                visible: (browseTab.browser && browseTab.browser.filesFor ? browseTab.browser.filesFor.platform : "") !== "nexus"
                                Layout.preferredWidth: 190
                                placeholderText: "Title ID (if not in the zip)"
                                placeholderTextColor: Theme.muted
                                color: Theme.text; font.family: "monospace"
                                background: Rectangle { color: Theme.hover; radius: 6; border.color: Theme.line }
                            }
                            CompactButton {
                                objectName: "modPreviewInstall"
                                implicitWidth: 80; text: "Preview"
                                enabled: (browseTab.browser && browseTab.browser.filesFor ? browseTab.browser.filesFor.platform : "") === "nexus" ? browseTab.browser.gameFolder.length > 0 : emulatorChoice.currentIndex >= 0
                                onClicked: (browseTab.browser && browseTab.browser.filesFor ? browseTab.browser.filesFor.platform : "") === "nexus"
                                           ? browseTab.browser.previewGameInstall(browseTab.browser.gameFolder)
                                           : browseTab.browser.previewInstall(browseTab.browser.emulators[emulatorChoice.currentIndex].id, titleIdField.text)
                            }
                            CompactButton {
                                objectName: "modApplyInstall"
                                implicitWidth: 80; text: "Install"
                                enabled: browseTab.browser && browseTab.browser.installPlan.files !== undefined && (browseTab.browser.installPlan.conflicts || []).length === 0
                                onClicked: browseTab.browser.applyInstall()
                            }
                            CompactButton {
                                objectName: "modUndoInstall"
                                implicitWidth: 70; text: "Undo"
                                enabled: browseTab.browser && browseTab.browser.canUndo
                                onClicked: browseTab.browser.undoInstall()
                            }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }
            }
        }
    }

    }
    // Modal Dialog: New Instance
    Dialog {
        id: newInstanceDialog
        title: "Create New Mod Instance (Prism Style)"
        modal: true
        standardButtons: Dialog.Ok | Dialog.Cancel
        anchors.centerIn: parent
        width: 380

        ColumnLayout {
            anchors.fill: parent
            spacing: 8

            TextField {
                id: newInstanceNameField
                Layout.fillWidth: true
                placeholderText: "Instance Name (e.g. Hardcore Plus)"
            }

            CheckBox {
                id: newInstanceIsolateSavesCheck
                text: "Isolate save games for this instance"
                checked: true
            }
        }

        onAccepted: {
            if (viewModel && viewModel.createInstance && newInstanceNameField.text) {
                var gid = root.selectedGameId || importGameIdField.text || "game"
                viewModel.createInstance(gid, newInstanceNameField.text, newInstanceIsolateSavesCheck.checked, "")
                newInstanceNameField.text = ""
            }
        }
    }
}
