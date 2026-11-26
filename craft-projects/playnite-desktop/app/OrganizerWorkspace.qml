import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: workspace
    objectName: "organizerWorkspace"
    property var service: typeof organizerWorkspace !== "undefined" ? organizerWorkspace : null
    property var agentService: typeof organizerAgents !== "undefined" ? organizerAgents : null
    property string initialGameId: ""
    property string baselineReport: ""
    property bool dirty: false
    property var editBaseline: ({})
    property string pendingSelection: ""
    property int section: 0
    property bool agentDetails: false
    property bool showDiagnostics: false
    function prepareAssistantTask(kind) {
        allowTitle.checked = kind === "metadata"
        allowPlatform.checked = kind === "metadata"
        allowDescription.checked = kind === "metadata" || kind === "description"
        allowTags.checked = kind === "organize"
        allowCategories.checked = kind === "organize"
        allowLibraries.checked = kind === "organize"
        if (kind === "metadata") agentTask.text = "Review this exact game's title, platform and description. Correct factual errors using evidence. Keep different platforms and editions distinct. Return only justified changes."
        else if (kind === "organize") agentTask.text = "Suggest useful tags, categories and library collections for this exact game. Keep existing useful labels; avoid redundant or speculative labels. Return reviewable metadata changes."
        else agentTask.text = "Write a concise factual description for this exact game. Do not invent release, edition or platform facts. Return only the description change."
    }
    component MetadataField: TextField {
        implicitHeight: 36; color: Theme.text; placeholderTextColor: Theme.muted
        font.family: Theme.family; selectByMouse: true
        background: Rectangle { radius: 6; color: Theme.panel; border.color: parent.activeFocus ? Theme.text : Theme.line }
    }
    component WorkspaceTab: TabButton {
        implicitHeight: 36
        contentItem: Text { text: parent.text; color: Theme.text; textFormat: Text.PlainText; font.family: Theme.family; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { color: parent.checked ? Theme.main : Theme.panel; radius: 5 }
    }
    function reloadForm() {
        editBaseline = service ? service.selectedGame : ({})
        titleEdit.text = editBaseline.title || ""
        platformEdit.text = editBaseline.platform || ""
        tagsEdit.text = (editBaseline.tags || []).join(", ")
        categoriesEdit.text = (editBaseline.categories || []).join(", ")
        librariesEdit.text = (editBaseline.libraries || []).join(", ")
        descriptionEdit.text = editBaseline.description || ""
        favoriteEdit.checked = editBaseline.favorite === true
        hiddenEdit.checked = editBaseline.hidden === true
        dirty = false
    }
    function chooseGame(key) {
        if (!service || !key || key === service.selectedGameId) return
        if (dirty) { pendingSelection = key; discardDialog.open() }
        else service.selectGame(key)
    }
    function load() { if (visible && service && service.available && !service.busy && !dirty) { if (initialGameId && initialGameId !== service.selectedGameId) service.selectGame(initialGameId); else service.refresh() } }
    onVisibleChanged: load()
    Component.onCompleted: load()
    Connections { target: workspace.service; function onSelectedChanged() { workspace.reloadForm() } }
    Timer { interval: 1000; running: workspace.visible && workspace.section === 1 && workspace.service && workspace.service.agents.length > 0; repeat: true; onTriggered: if (!workspace.service.busy) workspace.service.refreshAgentActivity() }
    // Reconnect observation only: never submit another inference POST.
    Timer { interval: 3000; running: workspace.visible && workspace.section === 1 && workspace.agentService && workspace.agentService.unsettled; repeat: true; onTriggered: if (!workspace.agentService.busy) workspace.agentService.inspectRun() }
    Dialog {
        id: discardDialog; objectName: "organizerDiscardDialog"; title: "Discard unsaved metadata edits?"
        parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: { workspace.dirty = false; workspace.service.selectGame(workspace.pendingSelection) }
    }
    function listValue(text) { return text.split(",").map(function(s) { return s.trim() }).filter(function(s) { return s.length > 0 }) }
    function save() {
        var changes = {}
        if (titleEdit.text !== (editBaseline.title || "")) changes.title = titleEdit.text
        if (platformEdit.text !== (editBaseline.platform || "")) changes.platform = platformEdit.text || null
        if (tagsEdit.text !== (editBaseline.tags || []).join(", ")) changes.tags = listValue(tagsEdit.text)
        if (categoriesEdit.text !== (editBaseline.categories || []).join(", ")) changes.categories = listValue(categoriesEdit.text)
        if (librariesEdit.text !== (editBaseline.libraries || []).join(", ")) changes.libraries = listValue(librariesEdit.text)
        if (descriptionEdit.text !== (editBaseline.description || "")) changes.description = descriptionEdit.text
        if (favoriteEdit.checked !== (editBaseline.favorite === true)) changes.favorite = favoriteEdit.checked
        if (hiddenEdit.checked !== (editBaseline.hidden === true)) changes.hidden = hiddenEdit.checked
        service.saveEdits(JSON.stringify(changes))
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 8
        RowLayout {
            Layout.fillWidth: true
            Text { text: "Library Organizer"; color: Theme.text; font.family: Theme.family; font.pixelSize: 20; font.bold: true; Layout.fillWidth: true }
            SocialButton { text: workspace.showDiagnostics ? "Hide diagnostics" : "Diagnostics"; onClicked: { workspace.showDiagnostics = !workspace.showDiagnostics; if (!workspace.showDiagnostics && workspace.section === 3) workspace.section = 0 } }
            SocialButton { objectName: "organizerDeduplicateButton"; text: workspace.service && workspace.service.deduplicateButtonLabel ? workspace.service.deduplicateButtonLabel : "Merge duplicates (across stores)"; enabled: !!workspace.service && workspace.service.available && !workspace.service.busy && !workspace.dirty; onClicked: workspace.service.deduplicateLibrary() }
            SocialButton { objectName: "organizerReload"; text: "Reload"; enabled: !!workspace.service && workspace.service.available && !workspace.service.busy && !workspace.dirty; onClicked: workspace.service.refresh() }
            SocialButton { objectName: "organizerUndo"; text: "Undo last edit"; enabled: !!workspace.service && workspace.service.canUndo && !workspace.dirty; onClicked: workspace.service.undo() }
        }
        Text { Layout.fillWidth: true; text: workspace.service ? workspace.service.status : "Organizer unavailable"; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: Theme.muted }
        TabBar {
            Layout.fillWidth: true
            currentIndex: workspace.section; onCurrentIndexChanged: workspace.section = currentIndex
            WorkspaceTab { text: "Edit library" }
            WorkspaceTab { text: "Assistant"; onClicked: if (workspace.agentService && workspace.agentService.available && !workspace.agentService.connected && !workspace.agentService.busy) workspace.agentService.connectGateway() }
            WorkspaceTab { text: "Edit history" }
            WorkspaceTab { visible: workspace.showDiagnostics; text: "Import diagnostics" }
        }
        RowLayout {
            visible: workspace.section === 0; Layout.fillWidth: true; Layout.fillHeight: true; spacing: 14
            ColumnLayout {
                Layout.preferredWidth: Math.min(290, workspace.width * 0.35); Layout.maximumWidth: 290; Layout.fillHeight: true
                MetadataField { objectName: "organizerSearch"; Layout.fillWidth: true; placeholderText: "Search all library games"; onTextEdited: if (workspace.service && !workspace.dirty) workspace.service.search(text); onAccepted: if (workspace.service && !workspace.dirty) workspace.service.search(text) }
                Text { text: "Exact game records · first 100 matches"; color: Theme.muted; font.pixelSize: 11; textFormat: Text.PlainText }
                ListView {
                    objectName: "organizerGameList"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    model: workspace.service ? workspace.service.rows : []
                    ScrollBar.vertical: ScrollBar {}
                    delegate: ItemDelegate {
                        required property var modelData
                        width: ListView.view.width
                        text: modelData.title || modelData.canonical_id
                        highlighted: workspace.service && workspace.service.selectedGameId === modelData.canonical_id
                        onClicked: workspace.chooseGame(modelData.canonical_id)
                        contentItem: Text { text: parent.text; color: Theme.text; elide: Text.ElideRight; textFormat: Text.PlainText }
                    }
                }
            }
            ScrollView {
                id: editorScroll; contentWidth: availableWidth
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                ColumnLayout {
                    width: editorScroll.availableWidth; spacing: 9
                    enabled: !!workspace.service && workspace.service.available && !workspace.service.busy && !!workspace.service.selectedGameId
                    Text { Layout.fillWidth: true; text: workspace.service ? workspace.service.selectedGameId : "Select a game"; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere; font.pixelSize: 11 }
                    Text { text: "Title"; color: Theme.text }
                    MetadataField { id: titleEdit; objectName: "organizerTitleEdit"; Layout.fillWidth: true; onTextEdited: workspace.dirty = true }
                    Text { text: "Platform"; color: Theme.text }
                    MetadataField { id: platformEdit; objectName: "organizerPlatformEdit"; Layout.fillWidth: true; onTextEdited: workspace.dirty = true }
                    Text { text: "Tags · comma-separated"; color: Theme.text }
                    MetadataField { id: tagsEdit; objectName: "organizerTagsEdit"; Layout.fillWidth: true; onTextEdited: workspace.dirty = true }
                    Text { text: "Categories · comma-separated"; color: Theme.text }
                    MetadataField { id: categoriesEdit; objectName: "organizerCategoriesEdit"; Layout.fillWidth: true; onTextEdited: workspace.dirty = true }
                    Text { text: "Libraries · comma-separated"; color: Theme.text }
                    MetadataField { id: librariesEdit; objectName: "organizerLibrariesEdit"; Layout.fillWidth: true; onTextEdited: workspace.dirty = true }
                    RowLayout {
                        CheckBox { id: favoriteEdit; text: "Favorite"; leftPadding: 24; onClicked: workspace.dirty = true }
                        CheckBox { id: hiddenEdit; text: "Hidden"; leftPadding: 24; onClicked: workspace.dirty = true }
                    }
                    Text { text: "Description"; color: Theme.text }
                    TextArea { id: descriptionEdit; color: Theme.text; placeholderText: "Add a description"; background: Rectangle { radius: 6; color: Theme.panel; border.color: parent.activeFocus ? Theme.text : Theme.line } objectName: "organizerDescriptionEdit"; Layout.fillWidth: true; Layout.preferredHeight: 120; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; onTextChanged: if (activeFocus) workspace.dirty = true }
                    RowLayout {
                        SocialButton { objectName: "organizerSave"; text: "Save metadata"; enabled: workspace.dirty && workspace.service && !workspace.service.uncertain; onClicked: workspace.save() }
                        SocialButton { text: "Discard draft"; enabled: workspace.dirty; onClicked: workspace.reloadForm() }
                        Text { Layout.fillWidth: true; text: workspace.dirty ? "Unsaved draft" : "No unsaved edits"; color: Theme.muted; textFormat: Text.PlainText }
                    }
                    Text { Layout.fillWidth: true; text: "Edits are native, audited and reversible. Imported originals and provider identities are preserved."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
                }
            }
        }
        ScrollView {
            id: agentScroll; contentWidth: availableWidth
            visible: workspace.section === 1; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            ColumnLayout {
                width: agentScroll.availableWidth; spacing: 10
                Text { Layout.fillWidth: true; text: "Library assistant"; color: Theme.text; font.bold: true; font.pixelSize: 18 }
                Text { Layout.fillWidth: true; text: "Choose what you want help with, review the request, then ask your connected assistant. Changes are shown for review before they are saved."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
                Text { Layout.fillWidth: true; text: workspace.agentService ? workspace.agentService.status : "Agent gateway unavailable"; color: Theme.text; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
                RowLayout {
                    SocialButton { objectName: "organizerConnectAgents"; text: "Connect agents"; enabled: workspace.agentService && workspace.agentService.available && !workspace.agentService.busy; onClicked: workspace.agentService.connectGateway() }
                    ComboBox { id: agentChoice; objectName: "organizerAgentChoice"; Layout.fillWidth: true; model: workspace.agentService ? workspace.agentService.profiles : []; textRole: "name" }
                }
                Text { Layout.fillWidth: true; text: "Working on: " + (workspace.service && workspace.service.selectedGame.title ? workspace.service.selectedGame.title : "Select a game in Edit library"); color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    SocialButton { objectName: "organizerHelpMetadata"; text: "Fix metadata"; onClicked: workspace.prepareAssistantTask("metadata") }
                    SocialButton { objectName: "organizerHelpOrganize"; text: "Organize tags & collections"; onClicked: workspace.prepareAssistantTask("organize") }
                    SocialButton { objectName: "organizerHelpDescription"; text: "Improve description"; onClicked: workspace.prepareAssistantTask("description") }
                }
                TextArea { id: agentTask; color: Theme.text; placeholderTextColor: Theme.muted; background: Rectangle { radius: 6; color: Theme.panel; border.color: parent.activeFocus ? Theme.text : Theme.line } objectName: "organizerAgentTask"; Layout.fillWidth: true; Layout.preferredHeight: 85; placeholderText: "Ask the selected agent to improve this game's metadata…"; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap }
                Text { Layout.fillWidth: true; text: "Returned edits are restricted to this game and the checked fields, and require your approval. The chosen backend profile keeps its existing tool permissions; this is not an OS sandbox."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
                CheckBox { text: "Advanced field permissions and run details"; leftPadding: 24; checked: workspace.agentDetails; onToggled: workspace.agentDetails = checked }
                RowLayout {
                    visible: workspace.agentDetails
                    CheckBox { id: allowTitle; text: "Title"; leftPadding: 24 }
                    CheckBox { id: allowPlatform; text: "Platform"; leftPadding: 24 }
                    CheckBox { id: allowTags; text: "Tags"; leftPadding: 24; checked: true }
                    CheckBox { id: allowCategories; text: "Categories"; leftPadding: 24; checked: true }
                }
                RowLayout {
                    visible: workspace.agentDetails
                    CheckBox { id: allowLibraries; text: "Libraries"; leftPadding: 24 }
                    CheckBox { id: allowDescription; text: "Description"; leftPadding: 24 }
                }
                RowLayout {
                    SocialButton {
                        objectName: "organizerRunAgent"; text: "Ask assistant — review changes"
                        enabled: workspace.agentService && workspace.agentService.connected && !workspace.agentService.busy && !workspace.agentService.unsettled && workspace.service && !workspace.service.busy && !!workspace.service.selectedGameId && agentChoice.currentIndex >= 0 && agentTask.text.trim().length > 0 && (allowTitle.checked || allowPlatform.checked || allowTags.checked || allowCategories.checked || allowLibraries.checked || allowDescription.checked)
                        onClicked: {
                            var fields = []
                            if (allowTitle.checked) fields.push("title")
                            if (allowPlatform.checked) fields.push("platform")
                            if (allowTags.checked) fields.push("tags")
                            if (allowCategories.checked) fields.push("categories")
                            if (allowLibraries.checked) fields.push("libraries")
                            if (allowDescription.checked) fields.push("description")
                            workspace.agentService.runTask(workspace.agentService.profiles[agentChoice.currentIndex].name, agentTask.text, workspace.service.selectedGameId, workspace.service.revision, fields)
                        }
                    }
                    SocialButton { objectName: "organizerInspectAgent"; text: "Check existing run"; enabled: workspace.agentService && !!workspace.agentService.attemptId && !workspace.agentService.busy; onClicked: workspace.agentService.inspectRun() }
                    SocialButton { visible: workspace.agentDetails; text: "Read run events"; enabled: workspace.agentService && workspace.agentService.connected && !workspace.agentService.busy; onClicked: workspace.agentService.readEvents() }
                }
                Text { Layout.fillWidth: true; visible: workspace.agentDetails && workspace.agentService && !!workspace.agentService.attemptId; text: "Attempt: " + (workspace.agentService ? workspace.agentService.attemptId : ""); color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere }
                Repeater {
                    model: workspace.agentService ? workspace.agentService.events : []
                    delegate: Text { visible: workspace.agentDetails; required property var modelData; Layout.fillWidth: true; text: modelData.event + " · " + JSON.stringify(modelData.data); textFormat: Text.PlainText; color: Theme.muted; wrapMode: Text.WrapAnywhere }
                }
                SocialButton { text: "Refresh agent activity"; enabled: workspace.service && workspace.service.available && !workspace.service.busy; onClicked: workspace.service.refreshAgentActivity() }
                Text { visible: !workspace.service || workspace.service.agents.length === 0; Layout.fillWidth: true; text: "No suggested changes yet. Connect an assistant above, choose a task and send it when ready. Opening this page does not start an AI request."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
                Repeater {
                    model: workspace.service ? workspace.service.agents : []
                    delegate: Text { visible: workspace.agentDetails; required property var modelData; Layout.fillWidth: true; text: modelData.principal_id + " · " + modelData.run_id + " · " + (modelData.connected ? "Connected" : modelData.revoked ? "Revoked" : modelData.expired ? "Expired" : "Not connected") + " · " + (modelData.allow_apply ? "Scoped auto-apply" : "Proposals only"); color: Theme.text; textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere }
                }
                Repeater {
                    model: workspace.service ? workspace.service.proposals : []
                    delegate: Frame {
                        required property var modelData
                        Layout.fillWidth: true
                        ColumnLayout {
                            anchors.fill: parent
                            Text { text: "Proposal · " + modelData.status; color: Theme.text; textFormat: Text.PlainText }
                            Text { Layout.fillWidth: true; text: JSON.stringify(modelData.preview, null, 2); textFormat: Text.PlainText; color: Theme.muted; font.family: "monospace"; wrapMode: Text.WrapAnywhere }
                            RowLayout {
                                SocialButton { text: "Apply reviewed changes"; enabled: modelData.status === "pending" && workspace.service && !workspace.service.busy && !workspace.dirty; onClicked: workspace.service.acceptProposal(modelData.proposal_id, modelData.preview.revision) }
                                SocialButton { text: "Reject"; enabled: modelData.status === "pending" && workspace.service && !workspace.service.busy && !workspace.dirty; onClicked: workspace.service.rejectProposal(modelData.proposal_id) }
                            }
                        }
                    }
                }
            }
        }
        ScrollView {
            visible: workspace.section === 2; Layout.fillWidth: true; Layout.fillHeight: true
            TextArea { text: workspace.service ? JSON.stringify(workspace.service.audit, null, 2) : "No audit"; readOnly: true; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; color: Theme.text; font.family: "monospace" }
        }
        ScrollView {
            visible: workspace.section === 3; Layout.fillWidth: true; Layout.fillHeight: true
            TextArea { objectName: "organizerReport"; text: "Historical imported baseline (not your current edits):\n\n" + workspace.baselineReport; readOnly: true; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; color: Theme.muted }
        }
    }
}
