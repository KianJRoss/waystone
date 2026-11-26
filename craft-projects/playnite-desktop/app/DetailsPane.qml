import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
Rectangle {
    id: pane
    signal achievementsRequested(string gameKey)
    signal installRequested(string gameKey)
    property alias assistantPlayButton: playButton
    property bool assistantPressed: false
    property var game: ({})
    readonly property var installJob: {
        var records = typeof gameInstalls !== "undefined" && gameInstalls ? gameInstalls.jobs : []
        var key = pane.editTargetId || pane.game.id
        return records.find(function(job) { return job.game_id === key }) || ({})
    }
    readonly property bool installPending: !!installJob.phase && ["completed", "failed", "cancelled", "handed_off", "invalid", "unsupported"].indexOf(installJob.phase) < 0

    readonly property var updateInfo: {
        var rev = (typeof gameInstalls !== "undefined" && gameInstalls) ? gameInstalls.updateRevision : 0
        var key = pane.editTargetId || (pane.game ? pane.game.id : "")
        if (!key) return ({})
        if (typeof gameInstalls !== "undefined" && gameInstalls && typeof gameInstalls.updateInfoFor === "function") {
            var info = gameInstalls.updateInfoFor(key)
            if (info && info.productId) return info
        }
        if (pane.game && pane.game.updateInfo && pane.game.updateInfo.productId) return pane.game.updateInfo
        return ({})
    }
    readonly property bool hasUpdate: !!updateInfo && !!updateInfo.productId
    readonly property string updateBadgeText: hasUpdate ? String(updateInfo.statusLabel || "Update available") : ""
    readonly property bool isUpdatingJob: (!!updateInfo && !!updateInfo.isUpdating) || (installPending && installJob.operation === "update")
    readonly property bool isSteamPendingUpdate: hasUpdate && !!updateInfo.isSteamPending
    readonly property bool requiresLaunchUpdatePrompt: hasUpdate && !!updateInfo.blocksLaunch

    property bool updatePromptVisible: false
    property int updatePromptFocusIndex: 0
    property var pendingLaunchOption: null
    property string protonStatusGame: ""

    function triggerUpdateAction() {
        var key = pane.editTargetId || (pane.game ? pane.game.id : "")
        if (!key || typeof gameInstalls === "undefined" || !gameInstalls) return false
        if (pane.isSteamPendingUpdate && typeof gameInstalls.openSteamDownloads === "function") {
            return gameInstalls.openSteamDownloads()
        }
        if (typeof gameInstalls.updateGame === "function") {
            return gameInstalls.updateGame(key)
        }
        return false
    }

    function openUpdateLaunchPrompt(opt) {
        pendingLaunchOption = opt || null
        updatePromptFocusIndex = 0
        updatePromptVisible = true
    }

    function chooseUpdateFirst() {
        updatePromptVisible = false
        pendingLaunchOption = null
        triggerUpdateAction()
    }

    function choosePlayAnyway() {
        var opt = pendingLaunchOption
        updatePromptVisible = false
        pendingLaunchOption = null
        executeLaunchImmediate(opt)
    }

    function chooseCancelLaunch() {
        updatePromptVisible = false
        pendingLaunchOption = null
    }

    function navigateUpdatePrompt(action) {
        if (!updatePromptVisible) return false
        if (action === "up" || action === "left") {
            updatePromptFocusIndex = (updatePromptFocusIndex + 2) % 3
            return true
        }
        if (action === "down" || action === "right") {
            updatePromptFocusIndex = (updatePromptFocusIndex + 1) % 3
            return true
        }
        if (action === "accept") {
            if (updatePromptFocusIndex === 0) chooseUpdateFirst()
            else if (updatePromptFocusIndex === 1) choosePlayAnyway()
            else chooseCancelLaunch()
            return true
        }
        if (action === "back") {
            chooseCancelLaunch()
            return true
        }
        return false
    }

    readonly property bool isGameInstalled: {
        if (!pane.game || !pane.game.id) return false
        if (pane.game.state && (pane.game.state.indexOf("Installed") >= 0 || pane.game.state.toLowerCase() === "installed")) return true
        if (typeof nativeLocalLibrary !== "undefined" && nativeLocalLibrary) {
            var recs = nativeLocalLibrary.recordsForGame(pane.editTargetId || pane.game.id)
            if (recs && recs.length > 0) return true
        }
        if (pane.game.installedVariants && pane.game.installedVariants.length > 0) return true
        var opts = pane.getLaunchOptions()
        for (var i = 0; i < opts.length; i++) {
            if (opts[i].state && (opts[i].state.toLowerCase() === "installed" || opts[i].state.indexOf("Installed") >= 0)) return true
        }
        return false
    }

    function getLaunchOptions() {
        var options = []
        var seenKeys = {}
        var seenProviderProduct = {}

        function providerProductKey(rec) {
            return (rec && rec.provider ? rec.provider : "") + "\t" + (rec && rec.product_id ? rec.product_id : "")
        }

        function addRecordOption(rec) {
            if (!rec) return
            var vkey = rec.variant_key || ""
            var ppkey = providerProductKey(rec)
            if (vkey && seenKeys[vkey]) return
            if (ppkey && ppkey !== "\t" && seenProviderProduct[ppkey]) return
            var st = (rec.state || "installed").toLowerCase()
            var isInstalled = (st === "installed" || st === "present" || st === "verified")
            if (!isInstalled) return
            if (vkey) seenKeys[vkey] = true
            if (ppkey && ppkey !== "\t") seenProviderProduct[ppkey] = true
            var provLabel = rec.provider === "Steam" ? "Play on Steam" : (rec.provider === "Epic" ? "Play on Epic" : (rec.provider === "GOG" ? "Play on GOG" : (rec.provider === "Xbox" ? "Play on Xbox" : (rec.provider === "Amazon" ? "Play on Amazon" : (rec.provider === "Local" ? "Play" : (rec.provider || "Play"))))))
            options.push({
                row_type: "play",
                label: provLabel,
                provider: rec.provider || "",
                variant_key: vkey,
                product_id: rec.product_id || rec.provider_game_id || "",
                title: rec.title || pane.game.title || "Game",
                state: rec.state || "installed",
                displayLabel: provLabel,
                targetId: rec.targetId || pane.editTargetId || "",
                rawRecord: rec
            })
        }

        if (typeof nativeLocalLibrary !== "undefined" && nativeLocalLibrary) {
            var targets = pane.game.editTargets || []
            targets.forEach(function(tgt) {
                var records = nativeLocalLibrary.recordsForGame(tgt.id) || []
                records.forEach(addRecordOption)
                // no "available" fallback for uninstalled variants
            })
        }

        if (pane.game.installedVariants && pane.game.installedVariants.length > 0) {
            pane.game.installedVariants.forEach(addRecordOption)
        }

        return options
    }

    function executeLaunchImmediate(opt) {
        if (typeof launchView === "undefined" || !launchView) return
        if (!opt) {
            var options = pane.getLaunchOptions()
            if (options.length >= 1) {
                opt = options[0]
            } else {
                launchView.setTarget("", {})
                launchView.play()
                return
            }
        }
        if (opt.rawRecord) {
            launchView.setTarget(opt.variant_key, {
                canonical_game_id: pane.game.id,
                variant_key: opt.variant_key,
                product_id: opt.product_id,
                provider: opt.provider,
                platform: opt.platform,
                title: opt.title,
                state: opt.state
            })
        } else {
            launchView.setTarget(opt.targetId, {
                canonical_game_id: pane.game.id,
                variant_key: opt.variant_key,
                product_id: opt.product_id,
                title: opt.title
            })
        }
        launchView.play()
    }

    function launchWithOption(opt) {
        if (!opt || typeof launchView === "undefined" || !launchView) return
        if (pane.requiresLaunchUpdatePrompt) {
            pane.openUpdateLaunchPrompt(opt)
            return
        }
        pane.executeLaunchImmediate(opt)
    }


    property bool closable: true
    property bool compactMode: false
    property bool expandedEvidence: false
    property bool expandedNativeEvidence: false
    signal closeRequested()
    property string editTargetId: pane.game.id || ""
    property var editBaseline: ({})
    readonly property var viewAdapter: typeof artworkView !== "undefined" ? artworkView : null
    readonly property string selectedArtworkRole: {
        var idx = artworkRoleCombo ? artworkRoleCombo.currentIndex : 0
        return ["cover", "background", "icon", "hero", "logo"][idx] || "cover"
    }
    readonly property string currentArtworkOverrideUrl: {
        return (pane.viewAdapter && pane.viewAdapter.storeEnabled) ? pane.viewAdapter.artworkUrl(pane.editTargetId, pane.selectedArtworkRole) : ""
    }
    function resetOrganizerForm() {
        var values = libraryModel.organizerEditValues(pane.editTargetId)
        pane.editBaseline = values
        organizerTags.text = values.tags
        organizerTitle.text = values.title || ""
        organizerPlatform.text = values.platform || ""
        organizerCategories.text = values.categories || "[]"
        organizerLibraries.text = values.libraries || "[]"
        organizerDescription.text = values.description || ""
        organizerFavorite.checked = values.favorite
    }
    function previewMetadataForm() {
        var changes = {}
        if (organizerTitle.text !== (editBaseline.title || "")) changes.title = organizerTitle.text
        if (organizerPlatform.text !== (editBaseline.platform || "")) changes.platform = organizerPlatform.text
        if (organizerCategories.text !== (editBaseline.categories || "[]")) {
            try { changes.categories = JSON.parse(organizerCategories.text) }
            catch (error) { libraryModel.previewMetadata(pane.editTargetId, "invalid"); return }
        }
        if (organizerLibraries.text !== (editBaseline.libraries || "[]")) {
            try { changes.libraries = JSON.parse(organizerLibraries.text) }
            catch (error) { libraryModel.previewMetadata(pane.editTargetId, "invalid"); return }
        }
        if (organizerDescription.text !== (editBaseline.description || "")) changes.description = organizerDescription.text
        libraryModel.previewMetadata(pane.editTargetId, JSON.stringify(changes))
    }
    onGameChanged: {
        pane.updatePromptVisible = false
        pane.pendingLaunchOption = null
        if (typeof epicRuntimeCombo !== "undefined") epicRuntimeCombo.currentIndex = 0
        if (typeof epicWineBinary !== "undefined") epicWineBinary.text = ""
        if (typeof epicWinePrefix !== "undefined") epicWinePrefix.text = ""
        let targets = pane.game.editTargets || []
        if (!targets.some(target => target.id === pane.editTargetId)) pane.editTargetId = pane.game.id || ""
        resetOrganizerForm()
        if (pane.viewAdapter) pane.viewAdapter.discardPreview()
    }
    Component.onCompleted: resetOrganizerForm()
    color: Theme.background
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 8
            Text { text: "Game details"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; Layout.fillWidth: true }
            CompactButton { text: "×"; hint: "Close game details"; visible: pane.closable; onClicked: pane.closeRequested() }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.main }
        ScrollView {
            id: scroll
            objectName: "detailsScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            contentHeight: detailsContent.height
            ScrollBar.vertical: ScrollBar {
                objectName: "detailsDazeScrollBar"
                parent: scroll
                x: scroll.width - width
                y: scroll.topPadding
                height: scroll.availableHeight
                implicitWidth: Theme.scrollbarWidth
                padding: 2
                contentItem: Rectangle { implicitWidth: 8; implicitHeight: 8; radius: 2; color: parent.pressed ? Theme.text : parent.hovered ? Theme.glyph : Theme.main }
                background: Rectangle { color: Theme.background }
            }
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            Item {
                id: detailsContent
                width: scroll.availableWidth
                // Real laid-out height: implicitHeight under-counts wrapped values (long tag lists), which cut off the
                // bottom of the panel so it could not be scrolled into view.
                height: Math.max(detailsColumn.implicitHeight, detailsColumn.childrenRect.height + 24)
                Image {
                    id: backdrop
                    objectName: "detailsBackground"
                    width: parent.width; height: Math.min(360, width * 9 / 16)
                    source: pane.visible && !pane.compactMode ? (pane.game.backgroundUrl || "") : ""
                    sourceSize.width: Math.max(1, Math.min(1024, Math.ceil(width)))
                    sourceSize.height: Math.max(1, Math.min(576, Math.ceil(height)))
                    asynchronous: true; cache: true; retainWhileLoading: false
                    fillMode: Image.PreserveAspectCrop
                    visible: !pane.compactMode && status === Image.Ready
                    opacity: 0.75
                }
                Rectangle {
                    width: backdrop.width; height: backdrop.height
                    visible: backdrop.visible
                    gradient: Gradient {
                        GradientStop { position: 0; color: Qt.rgba(Theme.background.r, Theme.background.g, Theme.background.b, 0.08) }
                        GradientStop { position: 1; color: Theme.background }
                    }
                }
            ColumnLayout {
                id: detailsColumn
                width: parent.width
                spacing: 12
                Item { Layout.fillWidth: true; implicitHeight: backdrop.visible ? backdrop.height * 0.5 : 2 }
                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    spacing: 12
                    Image {
                        objectName: "detailsHeaderIcon"
                        Layout.preferredWidth: visible ? (pane.compactMode ? 32 : 48) : 0
                        Layout.preferredHeight: pane.compactMode ? 32 : 48
                        source: pane.visible ? (pane.game.iconUrl || "") : ""
                        sourceSize: Qt.size(48, 48)
                        asynchronous: true; cache: true; retainWhileLoading: false
                        fillMode: Image.PreserveAspectFit
                        visible: status === Image.Ready
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: pane.game.title || "Select a game"
                            textFormat: Text.PlainText; wrapMode: Text.Wrap
                            color: Theme.text; font.family: Theme.family; font.pixelSize: pane.compactMode ? 14 : 29
                        }
                        Text {
                            Layout.fillWidth: true
                            text: pane.game.id ? "Install status: " + pane.game.state : "No selection in the current results"
                            textFormat: Text.PlainText; wrapMode: Text.WordWrap
                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 12
                        }
                        Flow {
                            Layout.fillWidth: true
                            visible: !!pane.game.id && typeof launchView !== "undefined" && launchView !== null
                            spacing: 8
                            Rectangle {
                                objectName: "detailsInstalledBadge"
                                visible: pane.isGameInstalled && !pane.installPending
                                width: 80
                                height: 30
                                radius: 4
                                color: Theme.panel
                                border.color: Theme.line
                                border.width: 1
                                Text {
                                    anchors.centerIn: parent
                                    text: "Installed"
                                    textFormat: Text.PlainText
                                    color: Theme.muted
                                    font.family: Theme.family
                                    font.pixelSize: 12
                                    font.bold: true
                                }
                            }
                            Rectangle {
                                objectName: "detailsUpdateBadge"
                                visible: pane.hasUpdate
                                width: Math.max(114, detailsUpdateBadgeLabel.implicitWidth + 16)
                                height: 30
                                radius: 4
                                color: Theme.main
                                border.color: Theme.glyph
                                border.width: 1
                                Text {
                                    id: detailsUpdateBadgeLabel
                                    objectName: "detailsUpdateBadgeLabel"
                                    anchors.centerIn: parent
                                    text: pane.updateBadgeText
                                    textFormat: Text.PlainText
                                    color: Theme.glyph
                                    font.family: Theme.family
                                    font.pixelSize: 11
                                    font.bold: true
                                }
                            }
                            CompactButton {
                                objectName: "detailsInstallButton"
                                visible: !pane.isGameInstalled || (pane.installPending && !pane.isUpdatingJob)
                                width: 120
                                text: pane.installPending ? (pane.installJob.phase === "queued" ? "Queued" : "Installing…") : "Install"
                                enabled: !pane.installPending
                                hint: "Install this game using its linked store and saved installation drive"
                                onClicked: pane.installRequested(pane.editTargetId || pane.game.id)
                            }
                            CompactButton {
                                id: updateButton
                                objectName: "detailsUpdateButton"
                                visible: pane.hasUpdate || pane.isUpdatingJob
                                width: pane.isSteamPendingUpdate ? 156 : 86
                                text: pane.isSteamPendingUpdate ? "Open Steam downloads" : (pane.isUpdatingJob ? "Updating…" : "Update")
                                enabled: !pane.isUpdatingJob
                                hint: pane.isSteamPendingUpdate ? "Open Steam's downloads page to finish this Steam update" : "Update this game using its store CLI and track progress in Downloads"
                                onClicked: pane.triggerUpdateAction()
                            }
                            CompactButton {
                                id: playButton
                                objectName: "detailsPlayButton"
                                down: pane.assistantPressed
                                width: 80
                                text: (typeof launchView !== "undefined" && launchView && launchView.gameState === "running") ? "Playing" : (typeof launchView !== "undefined" && launchView ? launchView.playLabel : "Play")
                                hint: "Launch with configured native profile; never runs shell"
                                onClicked: {
                                    if (typeof launchView !== "undefined" && launchView && pane.game.id) {
                                        var options = pane.getLaunchOptions()
                                        if (options.length === 0) {
                                            if (pane.requiresLaunchUpdatePrompt) {
                                                pane.openUpdateLaunchPrompt(null)
                                                return
                                            }
                                            launchView.setTarget("", {})
                                            launchView.play()
                                            return
                                        }
                                        if (options.length === 1) {
                                            pane.launchWithOption(options[0])
                                            return
                                        }
                                        platformLaunchDialog.launchOptions = options
                                        platformLaunchDialog.open()
                                    }
                                }
                            }
                        }
                        Text {
                            objectName: "detailsPlayStatus"
                            Layout.fillWidth: true
                            visible: typeof launchView !== "undefined" && launchView && (launchView.lastError.length > 0 || launchView.launchStatus === "handed_off" || launchView.launchDetail.length > 0)
                            text: typeof launchView !== "undefined" && launchView ? (launchView.lastError.length > 0 ? ("Failed: " + launchView.lastError) : (launchView.launchDetail.length > 0 ? launchView.launchDetail : ("PID " + launchView.lastPid))) : ""
                            textFormat: Text.PlainText
                            color: typeof launchView !== "undefined" && launchView && launchView.lastError.length > 0 ? Theme.text : Theme.muted
                            font.pixelSize: 11
                            elide: Text.ElideRight
                        }
                        RowLayout {
                            id: gameMonitorRow
                            objectName: "detailsGameMonitorRow"
                            Layout.fillWidth: true
                            spacing: 8
                            visible: pane.isGameInstalled && typeof launchView !== "undefined" && launchView
                                     && launchView.monitorChoices.length > 1
                            readonly property var options: {
                                if (typeof launchView === "undefined" || !launchView) return []
                                var list = [{ label: "Default (" + launchView.defaultMonitorLabel + ")", description: "" }]
                                var choices = launchView.monitorChoices
                                for (var i = 0; i < choices.length; ++i) list.push(choices[i])
                                list.push({ label: "Wherever it opens", description: "any" })
                                return list
                            }
                            Text {
                                text: "Monitor"
                                color: Theme.muted; font.family: Theme.family; font.pixelSize: 12
                            }
                            CompactCombo {
                                objectName: "detailsGameMonitor"
                                Layout.preferredWidth: 300
                                model: gameMonitorRow.options
                                textRole: "label"
                                currentIndex: {
                                    if (typeof launchView === "undefined" || !launchView || !pane.game) return 0
                                    var rev = launchView.monitorRevision
                                    var choice = launchView.gameMonitorFor(pane.game.id)
                                    for (var i = 0; i < gameMonitorRow.options.length; ++i)
                                        if (gameMonitorRow.options[i].description === choice) return i
                                    return 0
                                }
                                onActivated: function(index) { if (pane.game) launchView.setGameMonitorFor(pane.game.id, gameMonitorRow.options[index].description) }
                            }
                        }
                        // Store prices live in Store only (one home per feature): not on games you already have.
                        ColumnLayout {
                            objectName: "epicWaystoneRuntimeSettings"
                            Layout.fillWidth: true
                            visible: typeof launchView !== "undefined" && launchView
                                     && launchView.epicDirectLaunchAvailable
                                     && launchView.isEpicDirectTarget
                            spacing: 6
                            Text {
                                text: "Waystone launch runtime"
                                textFormat: Text.PlainText
                                color: Theme.text
                                font.pixelSize: 12
                                font.bold: true
                            }
                            Text {
                                Layout.fillWidth: true
                                text: (typeof launchView !== "undefined" && launchView) ? launchView.epicRuntimeStatus : ""
                                textFormat: Text.PlainText
                                wrapMode: Text.WordWrap
                                color: Theme.muted
                                font.pixelSize: 11
                            }
                            ComboBox {
                                id: epicRuntimeCombo
                                objectName: "epicRuntimeCombo"
                                Layout.fillWidth: true
                                model: ["Choose runtime…", "Native Linux", "Wine (Windows)"]
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: epicRuntimeCombo.currentIndex === 2
                                TextField {
                                    id: epicWineBinary
                                    objectName: "epicWineBinary"
                                    Layout.fillWidth: true
                                    placeholderText: "Wine executable"
                                    text: ""
                                }
                                CompactButton {
                                    text: "Browse…"
                                    hint: "Select a Wine executable for this game"
                                    onClicked: epicWineFileDialog.open()
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: epicRuntimeCombo.currentIndex === 2
                                TextField {
                                    id: epicWinePrefix
                                    objectName: "epicWinePrefix"
                                    Layout.fillWidth: true
                                    placeholderText: "Wine prefix directory"
                                    text: ""
                                }
                                CompactButton {
                                    text: "Browse…"
                                    hint: "Select this game's Wine prefix directory"
                                    onClicked: epicWinePrefixDialog.open()
                                }
                            }
                            CompactButton {
                                objectName: "saveEpicRuntimeButton"
                                Layout.fillWidth: true
                                text: "Save Waystone runtime"
                                enabled: epicRuntimeCombo.currentIndex === 1
                                         || (epicRuntimeCombo.currentIndex === 2
                                             && epicWineBinary.text.length > 0
                                             && epicWinePrefix.text.length > 0)
                                onClicked: {
                                    if (epicRuntimeCombo.currentIndex === 1)
                                        launchView.configureEpicRuntime("native", "", "")
                                    else if (epicRuntimeCombo.currentIndex === 2)
                                        launchView.configureEpicRuntime("wine", epicWineBinary.text, epicWinePrefix.text)
                                }
                            }
                        }
                        FileDialog {
                            id: epicWineFileDialog
                            title: "Select Wine executable"
                            fileMode: FileDialog.OpenFile
                            onAccepted: epicWineBinary.text = selectedFile.toLocalFile()
                        }
                        FolderDialog {
                            id: epicWinePrefixDialog
                            title: "Select Wine prefix"
                            onAccepted: epicWinePrefix.text = selectedFolder.toLocalFile()
                        }
                        Text {
                            objectName: "detailsInstallStatus"
                            Layout.fillWidth: true; visible: !!pane.installJob.message
                            text: pane.installJob.message || ""; textFormat: Text.PlainText
                            color: Theme.muted; font.pixelSize: 11; wrapMode: Text.WordWrap
                        }
                        CompactButton {
                            objectName: "detailsInstallOptionsButton"; Layout.fillWidth: true
                            visible: !!pane.game.id && !pane.isGameInstalled; enabled: !pane.installPending
                            text: "Installation options…"; hint: "Optionally choose another store copy or drive before installing"
                            onClicked: if (typeof gameInstalls !== "undefined" && gameInstalls) gameInstalls.options(pane.editTargetId || pane.game.id)
                        }
                    }
                    MissingCover {
                        objectName: "detailsCover"
                        Layout.preferredWidth: 86; Layout.preferredHeight: 118
                        visible: !!pane.game.id && !pane.compactMode
                        artworkSource: pane.game.coverUrl || ""
                        loadEnabled: pane.visible && !pane.compactMode
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    visible: (pane.game.nativeConnectionCount || 0) > 0
                    spacing: 6
                    Text { text: "Native Linux connection observations"; textFormat: Text.PlainText; color: Theme.text; font.family: Theme.family; font.pixelSize: 14 }
                    Text { objectName: "selectedNativeConnections"; Layout.fillWidth: true; text: pane.game.nativeConnections || ""; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: Theme.text; font.pixelSize: 12 }
                    Text { Layout.fillWidth: true; text: pane.game.nativeConnectionNotice || ""; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 11 }
                    CompactButton { Layout.fillWidth: true; text: (pane.expandedNativeEvidence ? "▾" : "▸") + " Native IDs & verification scope"; hint: "Inspect exact native links separately from imported install claims"; onClicked: pane.expandedNativeEvidence = !pane.expandedNativeEvidence }
                    TextArea { objectName: "selectedNativeEvidence"; Layout.fillWidth: true; visible: pane.expandedNativeEvidence; text: pane.game.nativeConnectionEvidence || ""; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: Theme.text; font.pixelSize: 12; background: Rectangle { color: Theme.panel; border.color: Theme.main } }
                }
                ControllerProfilePicker {
                    objectName: "detailsControllerProfilePicker"
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    visible: !!pane.game.id
                    gameId: pane.game.id || ""
                }
                VirtualPadPicker {
                    objectName: "detailsVirtualPadPicker"
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    visible: !!pane.game.id
                    gameId: pane.game.id || ""
                    gameTitle: pane.game.title || ""
                }
                // Proton version for this game (W19b): pick another version if an update breaks it.
                // Writes the launcher's own setting (Steam, Heroic or the Waystone launch entry).
                ColumnLayout {
                    id: protonRow
                    objectName: "detailsProtonRow"
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    spacing: 6
                    property string pendingValue: ""
                    property bool showVersions: false
                    readonly property var info: {
                        if (typeof protonView === "undefined" || !protonView || !pane.game || !pane.game.id
                                || !pane.isGameInstalled) return ({ supported: false })
                        var rev = protonView.revision
                        var opts = pane.getLaunchOptions()
                        for (var i = 0; i < opts.length; ++i) {
                            var r = protonView.gameRuntime(opts[i].provider || "", opts[i].product_id || "",
                                                           opts[i].targetId || "")
                            if (r && r.supported) return r
                        }
                        // Waystone-only entries (e.g. a modlist launcher) are found by the game's own id.
                        var own = protonView.gameRuntime("", "", pane.editTargetId || pane.game.id)
                        return own && own.supported ? own : ({ supported: false })
                    }
                    readonly property var choices: {
                        if (!info.supported) return []
                        var list = []
                        if (info.hasDefault) list.push({ label: "Default (" + info["default"] + ")", value: "" })
                        for (var i = 0; i < info.options.length; ++i)
                            list.push({ label: info.options[i], value: info.options[i] })
                        list.push({ label: "Install another version…", value: "__install__" })
                        return list
                    }
                    readonly property bool statusIsMine: typeof protonView !== "undefined" && protonView
                                                         && pane.protonStatusGame === (info.gameId || "")
                    visible: info.supported === true
                    Text {
                        Layout.fillWidth: true
                        text: "Proton version"
                        textFormat: Text.PlainText
                        color: Theme.text; font.family: Theme.family; font.pixelSize: 14
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        CompactCombo {
                            id: protonCombo
                            objectName: "detailsProtonVersion"
                            Layout.fillWidth: true
                            implicitHeight: 34
                            Accessible.name: "Proton version for this game"
                            enabled: typeof protonView !== "undefined" && protonView && !protonView.busy
                            model: protonRow.choices
                            textRole: "label"
                            currentIndex: {
                                var cur = protonRow.info.current || ""
                                for (var i = 0; i < protonRow.choices.length; ++i)
                                    if (protonRow.choices[i].value === cur) return i
                                return 0
                            }
                            onActivated: function(index) {
                                var choice = protonRow.choices[index]
                                if (!choice) return
                                if (choice.value === "__install__") {
                                    protonCombo.currentIndex = Qt.binding(function() {
                                        var cur = protonRow.info.current || ""
                                        for (var i = 0; i < protonRow.choices.length; ++i)
                                            if (protonRow.choices[i].value === cur) return i
                                        return 0
                                    })
                                    protonRow.showVersions = true
                                    if (protonView.releases.length === 0) protonView.checkReleases()
                                    return
                                }
                                if (choice.value === (protonRow.info.current || "")) return
                                protonRow.pendingValue = choice.value
                                pane.protonStatusGame = protonRow.info.gameId
                                protonView.setGameRuntime(protonRow.info.store, protonRow.info.gameId, choice.value)
                            }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "Per-game choice · used from the next launch · " + ({
                            steam: "saved in Steam's settings for this game",
                            heroic: "saved in Heroic's settings for this game",
                            waystone: "saved in this game's Waystone launch entry" })[protonRow.info.store || "waystone"]
                        textFormat: Text.PlainText; wrapMode: Text.WordWrap
                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 10
                    }
                    Text {
                        objectName: "detailsProtonStatus"
                        Layout.fillWidth: true
                        visible: protonRow.statusIsMine && protonView.gameStatus.length > 0
                        text: visible ? protonView.gameStatus : ""
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 11
                    }
                    CompactButton {
                        objectName: "detailsProtonCloseSteam"
                        visible: protonRow.statusIsMine && protonView.gameNeedsSteamClose
                        text: "Close Steam and apply"
                        hint: "Steam rewrites its settings while it runs; Waystone closes it, then saves"
                        onClicked: protonView.closeSteamAndApply(protonRow.info.store, protonRow.info.gameId,
                                                                 protonRow.pendingValue)
                    }
                    ColumnLayout {
                        objectName: "protonVersionsPanel"
                        Layout.fillWidth: true
                        visible: protonRow.showVersions
                        spacing: 8
                        Text {
                            Layout.fillWidth: true
                            text: "Install an older or newer GE-Proton, then pick it for this game. Downloads are checked against the release's checksum."
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 12
                        }
                        Text {
                            Layout.fillWidth: true
                            visible: typeof protonView !== "undefined" && protonView && protonView.status.length > 0
                            text: visible ? protonView.status : ""
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                            color: Theme.text; font.family: Theme.family; font.pixelSize: 12
                        }
                        ListView {
                            objectName: "protonReleaseList"
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.min(count, 6) * 36
                            clip: true
                            ScrollBar.vertical: ScrollBar {}
                            model: typeof protonView !== "undefined" && protonView ? protonView.releases : []
                            delegate: RowLayout {
                                width: ListView.view.width
                                height: 36
                                spacing: 8
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.tag + "  ·  " + modelData.sizeMb + " MB"
                                    textFormat: Text.PlainText
                                    color: Theme.text; font.family: Theme.family; font.pixelSize: 12
                                }
                                Text {
                                    visible: modelData.installed
                                    text: "Installed"
                                    color: Theme.muted; font.family: Theme.family; font.pixelSize: 12
                                }
                                CompactButton {
                                    visible: !modelData.installed
                                    enabled: typeof protonView !== "undefined" && protonView && !protonView.busy
                                    text: "Install"
                                    hint: "Download " + modelData.tag + " into Steam's compatibility tools folder"
                                    onClicked: protonView.installRelease(modelData.tag)
                                }
                            }
                        }
                        CompactButton {
                            text: "Done"
                            hint: "Hide the list of versions"
                            visible: typeof protonView !== "undefined" && protonView && !protonView.busy
                            onClicked: protonRow.showVersions = false
                        }
                        CompactButton {
                            visible: typeof protonView !== "undefined" && protonView && protonView.busy === "install"
                            text: "Cancel download"
                            hint: "Stop the download; nothing is installed"
                            onClicked: protonView.cancel()
                        }
                    }
                }
                SocialButton {
                    Layout.leftMargin: 14; Layout.rightMargin: 14
                    text: "Achievements…"
                    onClicked: pane.achievementsRequested(pane.editTargetId || pane.game.id || "")
                }
                GameProgressPanel {
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    visible: !!pane.game.id
                    service: typeof gameProgressView !== "undefined" ? gameProgressView : null
                    gameKey: pane.visible ? (pane.editTargetId || pane.game.id || "") : ""
                }
                PluginFeaturesPanel {
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    canonicalId: pane.editTargetId || pane.game.id || ""
                }
                GridLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    columns: 2; columnSpacing: 12; rowSpacing: 10
                    visible: !!pane.game.id
                    Repeater {
                        model: [
                            ["Library", pane.game.sources || "Unknown"],
                            ["Platform", pane.game.platforms || "Unknown"],
                            ["Install records", String(pane.game.variantCount || 0)],
                            ["Host", pane.game.hosts || "Unknown"],
                            ["Observed", pane.game.observed || "Unknown"],
                            ["Time played", pane.game.playtime || "Not supplied"],
                            ["Last played", pane.game.last_activity || "Not supplied"],
                            ["Completion", pane.game.completion || "Not supplied"],
                            ["Genre", pane.game.genres || "Not supplied"],
                            ["Release date", pane.game.release_date || "Not supplied"],
                            ["Developer", pane.game.developers || "Not supplied"],
                            ["Publisher", pane.game.publishers || "Not supplied"],
                            ["Tags", pane.game.tags || "Not supplied"],
                            ["Categories", pane.game.categories || "Not supplied"],
                            ["Play count", pane.game.playCount || "Not supplied"],
                            ["Favorite", pane.game.favorite || "Not supplied"],
                            ["Hidden", pane.game.hidden || "Not supplied"]
                        ].filter(function(row, index) { return index < 10 || row[1] !== "Not supplied" })
                        delegate: RowLayout {
                            required property var modelData
                            Layout.columnSpan: 2; Layout.fillWidth: true
                            Text { text: modelData[0]; Layout.preferredWidth: 92; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                            Text { text: modelData[1]; textFormat: Text.PlainText; Layout.fillWidth: true; Layout.preferredWidth: 100; wrapMode: Text.WrapAnywhere; color: Theme.text; font.family: Theme.family; font.pixelSize: 13 }
                        }
                    }
                }
                Rectangle { Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14; height: 1; color: Theme.main }
                Text {
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    text: "Description"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14
                }
                Text {
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    objectName: "gameDescription"
                    text: pane.game.description || "No description supplied in the selected import."
                    textFormat: Text.PlainText
                    color: Theme.muted; font.family: Theme.family; font.pixelSize: 13; wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    text: pane.game.metadataNotice || ""; visible: text.length > 0; textFormat: Text.PlainText
                    color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; wrapMode: Text.WordWrap
                }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    objectName: "organizerEditor"
                    visible: libraryModel.organizerEnabled && !!pane.game.id
                    spacing: 8
                    Text { text: "Native Organizer · local overrides"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14 }
                    CompactCombo {
                        objectName: "organizerEditTarget"; Layout.fillWidth: true
                        visible: (pane.game.editTargets || []).length > 1
                        model: (pane.game.editTargets || []).map(target => target.label)
                        currentIndex: Math.max(0, (pane.game.editTargets || []).findIndex(target => target.id === pane.editTargetId))
                        Accessible.name: "Exact account/source record to edit"
                        onActivated: { pane.editTargetId = pane.game.editTargets[currentIndex].id; libraryModel.discardOrganizerPreview(); pane.resetOrganizerForm() }
                    }
                    Text { Layout.fillWidth: true; text: "Edit this exact record. Changes are local, survive provider refresh, and can be undone. Categories and libraries use JSON string arrays."; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap }
                    TextField { id: organizerTitle; objectName: "organizerTitle"; Layout.fillWidth: true; maximumLength: 512; placeholderText: "Game title"; color: Theme.text; onTextEdited: libraryModel.discardOrganizerPreview() }
                    TextField { id: organizerPlatform; objectName: "organizerPlatform"; Layout.fillWidth: true; maximumLength: 128; placeholderText: "Platform"; color: Theme.text; onTextEdited: libraryModel.discardOrganizerPreview() }
                    TextField { id: organizerCategories; objectName: "organizerCategories"; Layout.fillWidth: true; maximumLength: 16384; placeholderText: '["Backlog", "Co-op"]'; color: Theme.text; onTextEdited: libraryModel.discardOrganizerPreview() }
                    TextField { id: organizerLibraries; objectName: "organizerLibraries"; Layout.fillWidth: true; maximumLength: 16384; placeholderText: '["Favorites", "Nintendo"]'; color: Theme.text; onTextEdited: libraryModel.discardOrganizerPreview() }
                    TextArea { id: organizerDescription; objectName: "organizerDescription"; Layout.fillWidth: true; Layout.preferredHeight: 90; placeholderText: "Description"; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; color: Theme.text; onTextChanged: libraryModel.discardOrganizerPreview() }
                    CompactButton { objectName: "metadataPreviewButton"; text: "Preview metadata changes"; onClicked: pane.previewMetadataForm() }
                    TextField {
                        id: organizerTags; objectName: "organizerTags"
                        Layout.fillWidth: true; maximumLength: 16384
                        placeholderText: '["Backlog", "Weekend"]'; color: Theme.text
                        onTextEdited: libraryModel.discardOrganizerPreview()
                        background: Rectangle { color: Theme.panel; border.color: Theme.main }
                    }
                    CheckBox { id: organizerFavorite; objectName: "organizerFavorite"; text: "Favorite (native override)"; palette.windowText: Theme.text; onClicked: libraryModel.discardOrganizerPreview() }
                    RowLayout {
                        Layout.fillWidth: true
                        CompactButton { objectName: "organizerPreviewButton"; Layout.preferredWidth: 85; text: "Preview"; hint: "Preview local tags/favorite changes without writing"; onClicked: libraryModel.previewOrganizer(pane.editTargetId, organizerTags.text, organizerFavorite.checked) }
                        CompactButton { objectName: "organizerApplyButton"; Layout.preferredWidth: 68; text: "Apply"; hint: "Save exactly the previewed local overrides"; enabled: libraryModel.organizerPendingGame === pane.editTargetId; onClicked: libraryModel.applyOrganizer(pane.editTargetId) }
                        CompactButton { objectName: "organizerUndoButton"; Layout.preferredWidth: 110; text: "Undo latest"; hint: "Undo latest unchanged local apply (global across games)"; enabled: libraryModel.organizerCanUndo; onClicked: libraryModel.undoOrganizer() }
                    }
                    CompactButton { objectName: "organizerResetButton"; Layout.fillWidth: true; text: "Preview imported fallback"; hint: "Preview removing only native tags/favorite overrides"; onClicked: libraryModel.previewOrganizerReset(pane.editTargetId) }
                    Text { objectName: "organizerStatus"; Layout.fillWidth: true; text: libraryModel.organizerMessage; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap }
                    TextArea {
                        objectName: "organizerPreviewText"; Layout.fillWidth: true
                        visible: libraryModel.organizerPendingGame === pane.editTargetId
                        text: libraryModel.organizerPreview; textFormat: TextEdit.PlainText
                        readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere
                        color: Theme.text; font.pixelSize: 12
                        background: Rectangle { color: Theme.panel; border.color: Theme.main }
                    }
                    CompactButton { Layout.fillWidth: true; text: "Local audit history"; hint: "Show latest twenty local apply/undo receipts"; onClicked: organizerAuditText.visible = !organizerAuditText.visible }
                    TextArea {
                        id: organizerAuditText; objectName: "organizerAuditText"; Layout.fillWidth: true; visible: false
                        text: libraryModel.organizerAudit; textFormat: TextEdit.PlainText
                        readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere
                        color: Theme.text; font.pixelSize: 12
                        background: Rectangle { color: Theme.panel; border.color: Theme.main }
                    }
                }
                ColumnLayout {
                    id: artworkOverrideEditor
                    objectName: "artworkOverrideEditor"
                    Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                    visible: !!(pane.viewAdapter && pane.viewAdapter.storeEnabled && pane.game.id)
                    spacing: 8

                    Text { text: "Manual Artwork · local overrides"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14 }

                    CompactCombo {
                        id: artworkEditTarget
                        objectName: "artworkEditTarget"
                        Layout.fillWidth: true
                        visible: (pane.game.editTargets || []).length > 1
                        model: (pane.game.editTargets || []).map(target => target.label)
                        currentIndex: Math.max(0, (pane.game.editTargets || []).findIndex(target => target.id === pane.editTargetId))
                        Accessible.name: "Exact account/source record for artwork"
                        onActivated: {
                            pane.editTargetId = pane.game.editTargets[currentIndex].id
                            if (pane.viewAdapter) pane.viewAdapter.discardPreview()
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text { text: "Artwork role:"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                        CompactCombo {
                            id: artworkRoleCombo
                            objectName: "artworkRoleSelector"
                            Layout.fillWidth: true
                            model: ["Cover", "Background", "Icon", "Hero banner", "Transparent logo"]
                            onActivated: {
                                if (pane.viewAdapter) pane.viewAdapter.discardPreview()
                            }
                        }
                    }

                    Text {
                        objectName: "artworkRoleStatus"
                        Layout.fillWidth: true
                        text: pane.currentArtworkOverrideUrl.length > 0 ? "Active manual override applied for this role." : "Using default catalog artwork."
                        textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap
                    }

                    FileDialog {
                        id: artworkFileDialog
                        objectName: "artworkFileDialog"
                        title: "Choose local artwork (" + pane.selectedArtworkRole + ")"
                        nameFilters: ["Image files (*.png *.jpg *.jpeg *.webp *.bmp *.ico)", "All files (*)"]
                        onAccepted: {
                            if (pane.viewAdapter) {
                                pane.viewAdapter.preview(pane.editTargetId, pane.selectedArtworkRole, selectedFile)
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        CompactButton {
                            id: chooseArtworkButton
                            objectName: "chooseArtworkButton"
                            Layout.fillWidth: true
                            text: "Choose " + pane.selectedArtworkRole + "…"
                            hint: "Select a local image file to preview"
                            onClicked: artworkFileDialog.open()
                        }
                        CompactButton {
                            id: clearArtworkButton
                            objectName: "clearArtworkButton"
                            Layout.preferredWidth: 90
                            text: "Reset role"
                            hint: "Preview removing override to restore original media"
                            enabled: pane.currentArtworkOverrideUrl.length > 0
                            onClicked: {
                                if (pane.viewAdapter) {
                                    pane.viewAdapter.preview(pane.editTargetId, pane.selectedArtworkRole, "")
                                }
                            }
                        }
                    }

                    Rectangle {
                        id: artworkPreviewBox
                        objectName: "artworkPreviewBox"
                        Layout.fillWidth: true
                        visible: !!(pane.viewAdapter && pane.viewAdapter.activePreview && pane.viewAdapter.activePreview.valid && pane.viewAdapter.activePreview.canonicalId === pane.editTargetId && pane.viewAdapter.activePreview.role === pane.selectedArtworkRole)
                        color: Theme.panel
                        border.color: Theme.main
                        radius: Theme.cornerRadius
                        implicitHeight: previewContent.implicitHeight + 16
                        ColumnLayout {
                            id: previewContent
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 8
                            Text {
                                text: pane.viewAdapter && pane.viewAdapter.activePreview && pane.viewAdapter.activePreview.isClear ? "Preview: Remove override" : "Preview: New " + pane.selectedArtworkRole
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 13
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                visible: !!(pane.viewAdapter && pane.viewAdapter.activePreview && !pane.viewAdapter.activePreview.isClear)
                                Image {
                                    id: previewThumbnail
                                    objectName: "artworkPreviewThumbnail"
                                    Layout.preferredWidth: pane.selectedArtworkRole === "background" ? 110 : 54
                                    Layout.preferredHeight: pane.selectedArtworkRole === "cover" ? 74 : pane.selectedArtworkRole === "background" ? 62 : 54
                                    fillMode: Image.PreserveAspectFit
                                    cache: false
                                    asynchronous: true
                                    Binding on source {
                                        value: pane.viewAdapter ? pane.viewAdapter.previewImageUrl : ""
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    Text {
                                        text: (pane.viewAdapter && pane.viewAdapter.activePreview ? (pane.viewAdapter.activePreview.format || "").toUpperCase() : "") + " • " + (pane.viewAdapter && pane.viewAdapter.activePreview ? (pane.viewAdapter.activePreview.width || 0) + "×" + (pane.viewAdapter.activePreview.height || 0) : "")
                                        color: Theme.text; font.family: Theme.family; font.pixelSize: 12
                                    }
                                    Text {
                                        text: Math.round(((pane.viewAdapter && pane.viewAdapter.activePreview ? pane.viewAdapter.activePreview.bytes : 0) || 0) / 1024) + " KB"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 11
                                    }
                                }
                            }
                            Text {
                                visible: !!(pane.viewAdapter && pane.viewAdapter.activePreview && pane.viewAdapter.activePreview.isClear)
                                text: "Original catalog artwork will be restored after applying."
                                color: Theme.muted; font.family: Theme.family; font.pixelSize: 12
                                wrapMode: Text.WordWrap; Layout.fillWidth: true
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                CompactButton {
                                    id: applyArtworkButton
                                    objectName: "applyArtworkButton"
                                    Layout.fillWidth: true
                                    text: "Apply"
                                    hint: "Commit artwork override"
                                    onClicked: {
                                        if (pane.viewAdapter) pane.viewAdapter.apply()
                                    }
                                }
                                CompactButton {
                                    id: cancelArtworkButton
                                    objectName: "cancelArtworkButton"
                                    Layout.preferredWidth: 70
                                    text: "Cancel"
                                    hint: "Discard preview"
                                    onClicked: {
                                        if (pane.viewAdapter) pane.viewAdapter.discardPreview()
                                    }
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        CompactButton {
                            id: undoArtworkButton
                            objectName: "undoArtworkButton"
                            Layout.fillWidth: true
                            text: (pane.viewAdapter && pane.viewAdapter.undoableAuditId > 0) ? ("Undo latest override (#" + pane.viewAdapter.undoableAuditId + ")") : "Undo latest override"
                            hint: "Undo the most recently applied override"
                            enabled: !!(pane.viewAdapter && pane.viewAdapter.undoableAuditId > 0)
                            onClicked: {
                                if (pane.viewAdapter) pane.viewAdapter.undo()
                            }
                        }
                    }

                    Text {
                        id: artworkStatus
                        objectName: "artworkStatus"
                        Layout.fillWidth: true
                        text: pane.viewAdapter ? (pane.viewAdapter.error.length > 0 ? pane.viewAdapter.error : pane.viewAdapter.status) : ""
                        color: (pane.viewAdapter && pane.viewAdapter.error.length > 0) ? Theme.text : Theme.muted
                        font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap
                        visible: text.length > 0
                    }
                }
                Rectangle { Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14; height: 1; color: Theme.main }
                CompactButton {
                    objectName: "evidenceToggle"
                    Layout.leftMargin: 8; Layout.fillWidth: true
                    text: (pane.expandedEvidence ? "▾" : "▸") + "  Source identities & provenance"
                    hint: "Show original IDs, observations, evidence and display-only recipes"
                    selected: pane.expandedEvidence
                    onClicked: pane.expandedEvidence = !pane.expandedEvidence
                }
                TextArea {
                    objectName: "provenanceText"
                    Layout.fillWidth: true; Layout.leftMargin: 8; Layout.rightMargin: 8
                    visible: pane.expandedEvidence
                    text: pane.game.provenance || "Select a game to inspect its source variants."
                    textFormat: TextEdit.PlainText
                    readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere
                    color: Theme.text; selectionColor: Theme.hover
                    font.family: Theme.family; font.pixelSize: 12
                    background: Rectangle { color: Theme.panel; border.color: Theme.main }
                }
                Item { height: 12 }
            }
            }
        }
    }

    Dialog {
        id: platformLaunchDialog
        objectName: "platformLaunchDialog"
        title: "Choose Launcher / Console"
        modal: true
        anchors.centerIn: parent
        width: Math.min(460, parent.width - 40)
        standardButtons: Dialog.Cancel

        property var launchOptions: []

        background: Rectangle {
            color: Theme.panel
            radius: 8
            border.color: Theme.line
            border.width: 1
        }

        contentItem: ColumnLayout {
            spacing: 12

            Text {
                text: pane.game.title || "Select Platform"
                textFormat: Text.PlainText
                color: Theme.text
                font.pixelSize: 14
                font.bold: true
                Layout.fillWidth: true
                elide: Text.ElideRight
            }

            Text {
                text: "Multiple launch options or platforms are available for this game:"
                textFormat: Text.PlainText
                color: Theme.muted
                font.pixelSize: 12
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }

            ListView {
                id: launchOptionList
                objectName: "launchOptionList"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(240, Math.max(48, count * 44))
                clip: true
                model: platformLaunchDialog.launchOptions

                delegate: Rectangle {
                    id: optionDelegate
                    objectName: "launchOptionItem_" + index
                    width: launchOptionList.width
                    height: 40
                    color: optHover.hovered ? Theme.hover : Theme.panel
                    radius: 4
                    border.color: optHover.hovered ? Theme.main : Theme.line
                    border.width: 1

                    HoverHandler { id: optHover }

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 8

                        Text {
                            text: modelData.displayLabel || (modelData.provider + (modelData.platform ? " (" + modelData.platform + ")" : ""))
                            textFormat: Text.PlainText
                            color: Theme.text
                            font.pixelSize: 12
                            font.bold: true
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }

                        Text {
                            text: modelData.state || "Ready"
                            textFormat: Text.PlainText
                            color: Theme.muted
                            font.pixelSize: 11
                        }

                        CompactButton {
                            text: "Play"
                            Layout.preferredWidth: 54
                            implicitHeight: 26
                            onClicked: {
                                platformLaunchDialog.close()
                                pane.launchWithOption(modelData)
                            }
                        }
                    }

                    MouseArea {
                        anchors.fill: parent
                        z: -1
                        onClicked: {
                            platformLaunchDialog.close()
                            pane.launchWithOption(modelData)
                        }
                    }
                }
            }
        }
    }

    Rectangle {
        id: updateLaunchPrompt
        objectName: "updateLaunchPrompt"
        anchors.fill: parent
        visible: pane.updatePromptVisible
        z: 60
        color: "#cc080808"

        MouseArea {
            anchors.fill: parent
            onClicked: pane.chooseCancelLaunch()
        }

        Rectangle {
            objectName: "updateLaunchPromptCard"
            anchors.centerIn: parent
            width: Math.min(360, parent.width - 28)
            height: promptCol.implicitHeight + 36
            radius: 8
            color: Theme.panel
            border.color: Theme.line
            border.width: 1

            MouseArea { anchors.fill: parent }

            ColumnLayout {
                id: promptCol
                anchors.fill: parent
                anchors.margins: 18
                spacing: 10

                Text {
                    objectName: "updatePromptTitle"
                    Layout.fillWidth: true
                    text: "Update available"
                    textFormat: Text.PlainText
                    color: Theme.text
                    font.family: Theme.family
                    font.pixelSize: 16
                    font.bold: true
                }

                Text {
                    objectName: "updatePromptMessage"
                    Layout.fillWidth: true
                    text: "An update is pending for " + (pane.game && pane.game.title ? pane.game.title : "this game")
                          + ". Epic / Heroic games may block launching until updated."
                    textFormat: Text.PlainText
                    color: Theme.muted
                    font.family: Theme.family
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }

                CompactButton {
                    objectName: "updatePromptUpdateFirstButton"
                    Layout.fillWidth: true
                    implicitHeight: 34
                    selected: pane.updatePromptFocusIndex === 0
                    text: "Update first (recommended)"
                    onClicked: {
                        pane.updatePromptFocusIndex = 0
                        pane.chooseUpdateFirst()
                    }
                }

                CompactButton {
                    objectName: "updatePromptPlayAnywayButton"
                    Layout.fillWidth: true
                    implicitHeight: 34
                    selected: pane.updatePromptFocusIndex === 1
                    text: "Play anyway"
                    onClicked: {
                        pane.updatePromptFocusIndex = 1
                        pane.choosePlayAnyway()
                    }
                }

                CompactButton {
                    objectName: "updatePromptCancelButton"
                    Layout.fillWidth: true
                    implicitHeight: 34
                    selected: pane.updatePromptFocusIndex === 2
                    text: "Cancel"
                    onClicked: {
                        pane.updatePromptFocusIndex = 2
                        pane.chooseCancelLaunch()
                    }
                }
            }
        }
    }
}
