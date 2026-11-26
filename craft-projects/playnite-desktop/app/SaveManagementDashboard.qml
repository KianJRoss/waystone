import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

WorkspacePage {
    id: dashboard
    objectName: "saveManagementDashboard"
    property var manager: typeof saveManagement !== "undefined" ? saveManagement : null
    property string selectedGameId: ""
    property string selectedGameTitle: ""
    property string selectedGameSource: ""

    title: "Save Management · profiles, backups & restores"

    onOpened: {
        if (manager) manager.cancelRestore()
        if (manager && manager.ensureToolChecked) manager.ensureToolChecked()
        if (manager && selectedGameId) {
            manifestBindingInput.text = manager.getManifestBinding(selectedGameId)
            if (manifestBindingInput.text) manager.listBackupsAsync(selectedGameId, selectedGameTitle, selectedGameSource, manifestBindingInput.text)
        }
    }
    onClosed: if (manager && !manager.isJobRunning) manager.cancelRestore()
    Connections {
        target: dashboard.manager
        function onRestorePlanChanged() { acknowledgeConflictsCheck.checked=false }
    }


    background: Rectangle {
        color: Theme.popup
        border.color: Theme.line
        radius: Theme.cornerRadius
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        // 1. Tool Status & Capability Bar
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Rectangle {
                width: 12; height: 12; radius: 6
                color: (dashboard.manager && dashboard.manager.toolAvailable) ? "#4caf50" : "#f44336"
            }
            Text { textFormat: Text.PlainText;
                id: statusText
                objectName: "toolStatusText"
                text: dashboard.manager ? dashboard.manager.toolStatus : "Save manager unavailable"
                color: Theme.text
                font.family: Theme.family
                font.pixelSize: 13
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            CompactButton {
                id: checkToolButton
                objectName: "checkToolButton"
                text: "Check Ludusavi"
                enabled: dashboard.manager && !dashboard.manager.isJobRunning
                onClicked: {
                    if (dashboard.manager) {
                        dashboard.manager.checkCapabilitiesAsync()
                    }
                }
            }
        }

        // Active operation / warning message banner
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: warningText.implicitHeight + 10
            visible: !!warningText.text || (dashboard.manager && dashboard.manager.isJobRunning)
            color: (dashboard.manager && dashboard.manager.isJobRunning) ? Theme.panel : "#4a2020"
            radius: Theme.cornerRadius

            Text { textFormat: Text.PlainText;
                id: warningText
                anchors.centerIn: parent
                width: parent.width - 16
                text: (dashboard.manager && dashboard.manager.isJobRunning) ? "Operation in progress... Do not close window." : ""
                color: "#ffffff"
                font.family: Theme.family
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }
        }

        GridLayout {
            columns: 3; Layout.fillWidth: true
            enabled: dashboard.manager && !dashboard.manager.isJobRunning
            Text { textFormat: Text.PlainText; text: "Ludusavi executable"; color: Theme.muted }
            TextField { id: toolPathInput; objectName: "saveToolPath"; Layout.fillWidth: true; text: dashboard.manager ? dashboard.manager.toolPath : ""; color: Theme.text }
            CompactButton { text: "Use tool path"; onClicked: if (dashboard.manager.setToolPath(toolPathInput.text, false)) dashboard.manager.checkCapabilitiesAsync() }
            Text { textFormat: Text.PlainText; text: "Default backup folder"; color: Theme.muted }
            TextField { id: backupPathInput; objectName: "saveBackupPath"; Layout.fillWidth: true; text: dashboard.manager ? dashboard.manager.defaultBackupDirectory : ""; color: Theme.text }
            CompactButton { text: "Save location"; onClicked: dashboard.manager.setDefaultBackupDirectory(backupPathInput.text) }
        }
        RowLayout {
            Layout.fillWidth: true
            enabled: dashboard.manager && !dashboard.manager.isJobRunning
            CompactButton { objectName: "findSaveDefinitions"; text: "Find save definitions"; enabled: dashboard.manager && dashboard.manager.toolAvailable && !!dashboard.selectedGameTitle; onClicked: dashboard.manager.findManifestAsync(dashboard.selectedGameTitle) }
            CompactCombo { objectName: "saveDefinitionCandidates"; Layout.fillWidth: true; model: dashboard.manager ? dashboard.manager.manifestCandidates : []; textRole: "title"; onActivated: manifestBindingInput.text=currentText }
            CompactButton { text: "Update definitions"; enabled: dashboard.manager && dashboard.manager.toolAvailable; onClicked: dashboard.manager.updateDefinitionsAsync() }
        }

        // 2. Profiles Management Section
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text { textFormat: Text.PlainText;
                text: "Profile:"
                color: Theme.text
                font.family: Theme.family
                font.pixelSize: 12
            }

            CompactCombo {
                id: profileComboBox
                objectName: "profileComboBox"
                Layout.preferredWidth: 200
                model: [{id: "", name: "Default backup location"}].concat(dashboard.manager ? dashboard.manager.profiles : [])
                textRole: "name"
                valueRole: "id"
                currentIndex: Math.max(0, model.findIndex(function(profile) { return dashboard.manager && profile.id === dashboard.manager.selectedProfileId }))
                enabled: dashboard.manager && !dashboard.manager.isJobRunning
                onActivated: if (dashboard.manager) dashboard.manager.selectProfile(currentValue)
            }

            TextField {
                id: newProfileNameInput
                objectName: "newProfileNameInput"
                Layout.preferredWidth: 160
                placeholderText: "New profile name"
                color: Theme.text
                background: Rectangle {
                    color: Theme.background
                    border.color: Theme.line
                    radius: 4
                }
            }
            TextField { id: profileBackupPath; objectName: "profileBackupPath"; Layout.fillWidth: true; placeholderText: "Profile backup folder (optional)"; color: Theme.text }
            TextField { id: profilePrefix; objectName: "profileWinePrefix"; Layout.fillWidth: true; placeholderText: "Wine/Proton pfx (optional)"; color: Theme.text }

            CompactButton {
                id: addProfileButton
                objectName: "addProfileButton"
                text: "Add Profile"
                enabled: dashboard.manager && !dashboard.manager.isJobRunning && !!newProfileNameInput.text.trim()
                onClicked: {
                    if (dashboard.manager && newProfileNameInput.text.trim()) {
                        var pId = "profile-" + Date.now();
                        dashboard.manager.createProfile(pId, newProfileNameInput.text.trim(), profileBackupPath.text, profilePrefix.text);
                        dashboard.manager.selectProfile(pId);
                        newProfileNameInput.text = "";
                    }
                }
            }

            Item { Layout.fillWidth: true }
        }

        // 3. Main Content Split: Left (Game & Controls), Right (Backups List)
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            // Left Panel: Selected Game, Explicit Binding, Action Buttons
            ColumnLayout {
                Layout.preferredWidth: 440
                Layout.fillHeight: true
                spacing: 8

                Text { textFormat: Text.PlainText;
                    text: "Selected Game & Manifest Binding"
                    color: Theme.text
                    font.family: Theme.family
                    font.pixelSize: 14
                    font.bold: true
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 64
                    color: Theme.background
                    radius: Theme.cornerRadius
                    border.color: Theme.line

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        Text { textFormat: Text.PlainText;
                            text: dashboard.selectedGameTitle ? dashboard.selectedGameTitle : "No game selected"
                            color: Theme.text
                            font.bold: true
                            font.pixelSize: 13
                            elide: Text.ElideRight
                        }
                        Text { textFormat: Text.PlainText;
                            text: (dashboard.selectedGameId ? ("ID: " + dashboard.selectedGameId) : "") +
                                  (dashboard.selectedGameSource ? (" · Source: " + dashboard.selectedGameSource) : "")
                            color: Theme.muted
                            font.pixelSize: 11
                        }
                    }
                }

                // Explicit Manifest Binding Form
                Text { textFormat: Text.PlainText;
                    text: "Ludusavi Manifest Title (Required):"
                    color: Theme.text
                    font.pixelSize: 12
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    TextField {
                        id: manifestBindingInput
                        objectName: "manifestBindingInput"
                        Layout.fillWidth: true
                        placeholderText: "Exact manifest title (e.g. Hades)"
                        color: Theme.text
                        onTextEdited: if (dashboard.manager) dashboard.manager.cancelRestore()
                        background: Rectangle {
                            color: Theme.background
                            border.color: Theme.line
                            radius: 4
                        }
                    }

                    CompactButton {
                        id: saveBindingButton
                        objectName: "saveBindingButton"
                        text: "Save Binding"
                        enabled: dashboard.manager && !!dashboard.selectedGameId && !!manifestBindingInput.text.trim()
                        onClicked: {
                            if (dashboard.manager && dashboard.selectedGameId) {
                                dashboard.manager.cancelRestore()
                                dashboard.manager.setManifestBinding(dashboard.selectedGameId, manifestBindingInput.text.trim())
                            }
                        }
                    }
                }

                Text { textFormat: Text.PlainText;
                    Layout.fillWidth: true
                    text: "Ludusavi matches saves by manifest title, not game library title. Explicit binding is required."
                    color: Theme.muted
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                }

                Item { implicitHeight: 6 }

                // Actions: Preview Backup / Backup Now / Preview Restore
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    CompactButton {
                        id: previewBackupButton
                        objectName: "previewBackupButton"
                        text: "Preview Backup"
                        enabled: dashboard.manager && dashboard.manager.toolAvailable && !dashboard.manager.isJobRunning && !!manifestBindingInput.text.trim()
                        onClicked: {
                            dashboard.manager.backupGameAsync(
                                dashboard.selectedGameId,
                                dashboard.selectedGameTitle,
                                dashboard.selectedGameSource,
                                true,
                                manifestBindingInput.text.trim()
                            )
                        }
                    }

                    CompactButton {
                        id: backupNowButton
                        objectName: "backupNowButton"
                        text: "Backup Now"
                        enabled: dashboard.manager && dashboard.manager.toolAvailable && !dashboard.manager.isJobRunning && !!manifestBindingInput.text.trim()
                        onClicked: {
                            dashboard.manager.backupGameAsync(
                                dashboard.selectedGameId,
                                dashboard.selectedGameTitle,
                                dashboard.selectedGameSource,
                                false,
                                manifestBindingInput.text.trim()
                            )
                        }
                    }

                    CompactButton {
                        id: previewRestoreButton
                        objectName: "previewRestoreButton"
                        text: "Preview Restore"
                        enabled: dashboard.manager && dashboard.manager.toolAvailable && !dashboard.manager.isJobRunning && !!manifestBindingInput.text.trim()
                        onClicked: {
                            dashboard.manager.previewRestoreAsync(
                                dashboard.selectedGameId,
                                dashboard.selectedGameTitle,
                                dashboard.selectedGameSource,
                                manifestBindingInput.text.trim()
                            )
                        }
                    }

                    Item { Layout.fillWidth: true }
                }

                // Restore confirmation / conflict panel
                Rectangle {
                    id: restorePlanBox
                    objectName: "restorePlanBox"
                    Layout.fillWidth: true
                    implicitHeight: planLayout.implicitHeight + 12
                    visible: dashboard.manager && !!dashboard.manager.pendingRestorePlan.request_id
                    color: Theme.background
                    radius: Theme.cornerRadius
                    border.color: dashboard.manager && dashboard.manager.hasConflicts ? "#f44336" : Theme.line

                    ColumnLayout {
                        id: planLayout
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 6

                        Text { textFormat: Text.PlainText;
                            text: (dashboard.manager && dashboard.manager.hasConflicts) ?
                                  ("⚠️ Restore Conflict: " + dashboard.manager.conflictCount + " existing saves will be overwritten") :
                                  "Restore Plan Ready"
                            color: (dashboard.manager && dashboard.manager.hasConflicts) ? "#f44336" : Theme.text
                            font.bold: true
                            font.pixelSize: 12
                        }

                        CheckBox {
                            id: acknowledgeConflictsCheck
                            objectName: "acknowledgeConflictsCheck"
                            visible: dashboard.manager && dashboard.manager.hasConflicts
                            text: "I understand existing destination files will be overwritten"
                            contentItem: Text { textFormat: Text.PlainText;
                                text: parent.text
                                font.pixelSize: 11
                                color: Theme.text
                                leftPadding: parent.indicator.width + 4
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            CompactButton {
                                id: applyRestoreButton
                                objectName: "applyRestoreButton"
                                text: "Confirm & Restore"
                                enabled: dashboard.manager && !dashboard.manager.isJobRunning &&
                                         (!dashboard.manager.hasConflicts || acknowledgeConflictsCheck.checked)
                                onClicked: {
                                    if (dashboard.manager && dashboard.manager.pendingRestorePlan.request_id) {
                                        dashboard.manager.applyRestoreAsync(
                                            dashboard.manager.pendingRestorePlan.request_id,
                                            acknowledgeConflictsCheck.checked
                                        )
                                    }
                                }
                            }

                            CompactButton {
                                id: cancelRestoreButton
                                objectName: "cancelRestoreButton"
                                text: "Cancel"
                                onClicked: {
                                    if (dashboard.manager) {
                                        dashboard.manager.cancelRestore()
                                    }
                                }
                            }
                        }
                    }
                }

                Item { Layout.fillHeight: true }
            }

            // Vertical divider
            Rectangle {
                Layout.fillHeight: true
                width: 1
                color: Theme.line
            }

            // Right Panel: Available Backups List
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8

                RowLayout {
                    Layout.fillWidth: true
                    Text { textFormat: Text.PlainText;
                        text: "Backups Inventory"
                        color: Theme.text
                        font.family: Theme.family
                        font.pixelSize: 14
                        font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    CompactButton {
                        text: "Refresh List"
                        enabled: dashboard.manager && !dashboard.manager.isJobRunning
                        onClicked: {
                            if (dashboard.manager) {
                                dashboard.manager.listBackupsAsync(
                                    dashboard.selectedGameId,
                                    dashboard.selectedGameTitle,
                                    dashboard.selectedGameSource,
                                    manifestBindingInput.text.trim()
                                )
                            }
                        }
                    }
                }

                ListView {
                    id: backupsListView
                    objectName: "backupsListView"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 6
                    model: dashboard.manager ? dashboard.manager.currentBackups : []

                    delegate: Rectangle {
                        width: backupsListView.width
                        implicitHeight: 48
                        color: Theme.background
                        radius: Theme.cornerRadius
                        border.color: Theme.line

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 8

                            ColumnLayout {
                                Layout.fillWidth: true
                                Text { textFormat: Text.PlainText;
                                    text: modelData.title || "Untitled"
                                    color: Theme.text
                                    font.bold: true
                                    font.pixelSize: 12
                                }
                                Text { textFormat: Text.PlainText;
                                    text: "Backups: " + (modelData.backup_count || 0)
                                    color: Theme.muted
                                    font.pixelSize: 11
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
