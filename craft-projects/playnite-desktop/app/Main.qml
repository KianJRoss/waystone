import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    signal assistantBeat(string kind)
    onAssistantBeat: function(kind) { if (kind !== "launch") root.uiSound(kind) }
    objectName: "libraryWindow"
    width: 1280; height: 800
    minimumWidth: 900; minimumHeight: 560
    visible: !headlessCheck
    title: "Waystone"
    color: Theme.background
    font.family: Theme.family; font.pixelSize: 14
    palette.window: Theme.background
    palette.windowText: Theme.text
    palette.base: Theme.background
    palette.text: Theme.text
    palette.button: Theme.panel
    palette.buttonText: Theme.text
    palette.highlight: Theme.hover
    palette.highlightedText: Theme.text
    palette.mid: Theme.line
    palette.dark: Theme.background
    palette.light: Theme.main
    palette.alternateBase: Theme.panel
    palette.toolTipBase: Theme.popup
    palette.toolTipText: Theme.text
    palette.brightText: Theme.text
    palette.link: Theme.text
    palette.linkVisited: Theme.muted
    palette.accent: Theme.text
    palette.placeholderText: Theme.muted
    readonly property var nativeService: typeof nativeProviders !== "undefined" ? nativeProviders : ({notice: "Native service unavailable in this isolated view", rows: [], pageLabel: "0 store records", canPrevious: false, canNext: false, detail: "", aiResult: "Native control API unavailable", retroCount: 0, retroRows: [], retroPageLabel: "Unavailable", retroDetail: "", retroNotice: "Native database not selected", retroPlatforms: ["All platforms"]})
    readonly property var connectionService: typeof nativeConnections !== "undefined" ? nativeConnections : ({available: false, notice: "No explicit native connections selected", providerKeys: ["All native providers"], providerLabels: ["All native providers"], rows: [], detail: "", pageLabel: "0 observations", canPrevious: false, canNext: false})
    readonly property var linkManager: typeof catalogLinkView !== "undefined" ? catalogLinkView : ({storeEnabled: false, status: "Link review unavailable in this isolated view.", rows: [], history: [], latestAction: "No link changes have been recorded.", undoableAuditId: 0, error: "", previewText: "", resolution: ({}), createLinkState: function() { return false }, selectCandidates: function() {}, preview: function() { return "" }, apply: function() { return "" }, undo: function() { return ""}})
    readonly property string linkStatePathValue: typeof linkStatePath !== "undefined" ? linkStatePath : ""
    readonly property var localLibraryService: typeof nativeLocalLibrary !== "undefined" ? nativeLocalLibrary : ({rows: [], providers: [], count: 0, notice: "No native inventory selected", pageLabel: "0 observations", canNext: false, canPrevious: false, detail: ""})
    readonly property var refreshService: typeof nativeRefresh !== "undefined" ? nativeRefresh : null
    property string nativeReloadError: ""
    property bool quitRequested: false
    readonly property bool assistantAnimationEnabled: typeof assistantPreferences !== "undefined" && assistantPreferences ? assistantPreferences.showAnimation : true
    readonly property string assistantAnimationSpeed: typeof assistantPreferences !== "undefined" && assistantPreferences ? assistantPreferences.speed : "Normal"
    readonly property var launchViewRef: typeof launchView !== "undefined" ? launchView : null
    readonly property bool assistantAnimating: typeof assistantDirector !== "undefined" && assistantDirector.active
    readonly property string assistantAnimationPhase: typeof assistantDirector !== "undefined" ? assistantDirector.phase : "idle"
    readonly property string assistantAnimationTarget: typeof assistantDirector !== "undefined" ? assistantDirector.targetGameId : ""
    onClosing: function(close) {
        // A normal window close leaves the app-owned input runtime forwarding.
        // Reopen through the existing DBus/Waystone launcher; Quit is explicit.
        if (!quitRequested && typeof controllerStudioHost !== "undefined" && controllerStudioHost.running) {
            close.accepted = false
            root.hide()
            return
        }
        if (typeof providerJobs !== "undefined" && providerJobs && !providerJobs.shutdown()) {
            close.accepted=false
            return
        }
        if (typeof saveManagement !== "undefined" && saveManagement && !saveManagement.shutdown()) {
            close.accepted=false
            nativeReloadError="A save operation is stopping safely. Close again after it finishes."
            return
        }
        if (typeof xboxVoiceMedia !== "undefined" && xboxVoiceMedia) xboxVoiceMedia.shutdown()
        if (typeof xodusSocialView !== "undefined" && xodusSocialView) xodusSocialView.shutdown()
    }
    property string romBrowsePlatform: ""
    property string nativeBrowseLabel: ""
    property string catalogBrowseLabel: ""
    property string nativeProvider: "All native providers"
    readonly property var nativeProviderKeys: ["All native providers", "Any exact native link", "No exact native link"].concat(connectionService.providerKeys.slice(1))
    readonly property var nativeProviderLabels: ["All native providers", "Any exact native link", "No exact native link"].concat(connectionService.providerLabels.slice(1))
    function openControllers() { controllerDashboard.open() }
    function openFriends() { socialDashboard.open() }
    // Store and platform sign-ins have one home: Settings > Accounts (Social has Friends / Xbox party / Audio only).
    function openFriendsPlatforms() { settingsDashboard.selectedCategory = 0; root.openSettings() }
    function openAchievements() { achievementsDashboard.open() }
    function showLibraryView() { navigatePage("library") }
    function openSettings() { navigatePage("settings") }
    function openSettingsAccounts() { settingsDashboard.selectedCategory = 0; root.openSettings() }
    function openSteamLibrarySettings() {
        settingsDashboard.selectedCategory = 0
        root.openSettings()
        Qt.callLater(function() { settingsDashboard.scrollToSteamLibrarySection() })
    }
    function openManagementOverview(section) { managementOverview.openSection(section) }
    function updateNativeBrowser() {
        root.nativeBrowseLabel = nativeProviderChoice.currentText
        if (connectionService.available) connectionService.setFilters(connectionService.providerKeys[nativeProviderChoice.currentIndex], ["All join states", "matched", "unmatched", "ambiguous"][nativeJoinChoice.currentIndex], nativeConnectionSearch.text)
    }
    property string viewMode: initialView
    property bool organizerMode: initialOrganizerView
    property string activePage: initialOrganizerView ? "organizer" : "library"
    property var pageHistory: []
    readonly property var socialPages: ["friends", "achievements"]
    readonly property var emulationPages: ["emulation", "roms"]
    readonly property var pageTitles: ({library: "Library", store: "Store search", organizer: "Organizer", controllers: "Controllers", friends: "Social", achievements: "Social", downloads: "Downloads & installs", emulation: "Emulation", saves: "Save management", mods: "Mods", overview: managementOverview.title, roms: romDialog.title, catalogs: nativeDialog.title, connections: connectionDialog.title, settings: "Settings"})
    // UI sounds (Settings > General > Sounds). Quiet while a game runs; see native_services/ui_sounds.py.
    function uiSound(event) { if (typeof uiSounds !== "undefined" && uiSounds) uiSounds.play(event) }
    function navigatePage(destination) {
        if (consoleMode) toggleConsole()
        if (activePage === destination) return
        uiSound("open")
        pageHistory = pageHistory.concat([activePage])
        activePage = destination
        organizerMode = destination === "organizer"
    }
    function goBack() {
        uiSound("back")
        var history = pageHistory.slice()
        var destination = history.length ? history.pop() : "library"
        pageHistory = history
        activePage = destination
        organizerMode = destination === "organizer"
    }
    onOrganizerModeChanged: {
        if (organizerMode && activePage !== "organizer") navigatePage("organizer")
        else if (!organizerMode && activePage === "organizer") navigatePage("library")
    }
    onActivePageChanged: refreshControllerRouting()
    property alias explorerVisible: root.filterVisible  // Explorer and Filters are one panel (Kian 2026-11-22)
    property bool filterVisible: true
    property bool inspectorVisible: true
    property int projectionRevision: 0
    property string selectedId: ""
    property var selectedGame: { var revision = libraryModel.organizerRevision + root.projectionRevision; return libraryModel.overview(selectedId) }
    property string query: ""
    property string source: "All sources"
    property string platform: "All platforms"
    property string status: "All states"
    property var collectionFilters: ({})
    property string facetQuery: ""
    readonly property var collectionFieldNames: ["libraries", "genres", "categories", "publishers", "developers", "tags", "favorite", "completion", "hidden", "release_decade", "playtime_bucket", "play_count"]
    readonly property var collectionFieldLabels: ({libraries: "My libraries", genres: "Genres", categories: "Categories", publishers: "Publishers", developers: "Developers", tags: "Tags", favorite: "Favorites", completion: "Completion", hidden: "Visibility", release_decade: "Release decade", playtime_bucket: "Playtime", play_count: "Play count"})
    readonly property var collectionFields: { let revision = libraryModel.organizerRevision + root.projectionRevision; return collectionFieldNames.filter(field => libraryModel.collectionValues(field).length > 0) }
    readonly property var collectionLabels: collectionFields.map(field => collectionFieldLabels[field])
    property string linkSearch: ""
    property var selectedLinkCandidates: []
    property bool linkPreviewReady: false
    property bool controllerRoutingReady: false
    function refreshControllerRouting() {
        if (!controllerRoutingReady || typeof controllerNavigation === "undefined") return
        if (partyOverlay.visible && partyOverlay.active) {
            controllerNavigation.setContext("overlay")
            return
        }
        if (consoleMode && active) {
            controllerNavigation.setContext("console")
            return
        }
        // Desktop mode: the controller drives the normal UI too while Waystone has focus (Kian 2026-11-24).
        // (Never in headless/test sessions: they must not poll a controller.)
        var headless = typeof headlessCheck !== "undefined" && headlessCheck
        controllerNavigation.setContext(active && !headless ? "desktop" : "")
    }
    // Guide / Xbox button: Big Screen when no game runs (its Guide panel if already there), the overlay in a game.
    function controllerGuide() {
        var game = typeof launchView !== "undefined" && launchView ? launchView.activeGame : null
        if (game && (game.state === "running" || game.state === "launching")) { togglePartyOverlay(); return }
        if (partyOverlay.visible) { togglePartyOverlay(); return }
        if (visibility === Window.Hidden || visibility === Window.Minimized) showNormal()
        if (!consoleMode) toggleConsole()
        else if (typeof consoleView.toggleGuide === "function") consoleView.toggleGuide()
        raise(); requestActivate()
    }
    // Desktop library with a controller: move through the open view, A plays (or opens details), B goes back,
    // LB/RB switch the view, LT/RT jump a page, Y searches, Start toggles the details pane.
    function desktopLibraryView() {
        if (viewMode === "details") return compactList
        if (viewMode === "list") return columnsList
        return grid
    }
    function desktopNavigate(action) {
        if (detailsPane && detailsPane.updatePromptVisible) {
            uiSound(action === "back" ? "back" : "select")
            detailsPane.navigateUpdatePrompt(action)
            return
        }
        if (action === "back") {
            if (settingsDashboard.visible) { settingsDashboard.close(); return }
            if (controllerDashboard.item && controllerDashboard.item.visible) { controllerDashboard.close(); return }
            if (managementOverview.visible) { managementOverview.close(); return }
            if (socialDashboard.visible) { socialDashboard.close(); return }
            if (downloadsDashboard.visible) { downloadsDashboard.close(); return }
            if (emulationDashboard.visible) { emulationDashboard.close(); return }
            if (saveDashboard.visible) { saveDashboard.close(); return }
            if (activePage !== "library" && activePage !== "organizer") { goBack(); return }
            uiSound("back")
            if (query.length) query = ""
            return
        }
        if (activePage !== "library" || organizerMode) return
        var view = desktopLibraryView()
        if (action === "up" || action === "down" || action === "left" || action === "right"
                || action === "previous_page" || action === "next_page") {
            if (!view.activeFocus) {
                var row = selectedId ? libraryModel.rowOf(selectedId) : -1
                view.currentIndex = row >= 0 ? row : 0
                view.forceActiveFocus()
                if (row >= 0) return        // first press just picks up the current selection
            }
            var cols = view === grid ? Math.max(1, Math.floor(grid.width / grid.cellWidth)) : 1
            var pageRows = Math.max(1, Math.floor(view.height / (view === grid ? grid.cellHeight : 34)))
            var step = action === "up" ? -cols : action === "down" ? cols
                     : action === "left" ? (view === grid ? -1 : -cols) : action === "right" ? (view === grid ? 1 : cols)
                     : action === "previous_page" ? -cols * pageRows : cols * pageRows
            var count = libraryModel.visibleCount !== undefined ? libraryModel.visibleCount : view.count
            var next = Math.max(0, Math.min(count - 1, view.currentIndex + step))
            if (next !== view.currentIndex) { view.currentIndex = next; view.positionViewAtIndex(next, GridView.Contain) }
            return
        }
        if (action === "accept") {
            if (!selectedId) return
            if (viewMode === "grid" && !inspectorVisible) { uiSound("open"); inspectorVisible = true; return }
            if (typeof gameInstalls !== "undefined" && gameInstalls && typeof gameInstalls.requiresUpdatePrompt === "function" && gameInstalls.requiresUpdatePrompt(selectedId)) {
                uiSound("open")
                if (!inspectorVisible) inspectorVisible = true
                var opts = selectedLaunchOptions()
                detailsPane.openUpdateLaunchPrompt(opts.length > 0 ? opts[0] : null)
                return
            }
            uiSound("select")
            playGameById(selectedId)
            return
        }
        if (action === "previous_section" || action === "next_section") {
            var modes = ["grid", "details", "list"]
            var i = (modes.indexOf(viewMode) + (action === "next_section" ? 1 : 2)) % 3
            uiSound("move"); setView(modes[i]); return
        }
        if (action === "search") { uiSound("open"); search.forceActiveFocus(); search.selectAll(); return }
        if (action === "menu") { uiSound("open"); inspectorVisible = !inspectorVisible; return }
    }
    onActiveChanged: refreshControllerRouting()
    onConsoleModeChanged: refreshControllerRouting()
    Connections {
        target: typeof controllerNavigation !== "undefined" ? controllerNavigation : null
        function onAction(action) {
            if (action === "guide") { root.controllerGuide(); return }
            if (controllerNavigation.context === "desktop") { root.desktopNavigate(action); return }
            if (controllerNavigation.context === "overlay") {
                if (typeof partyOverlay.navigate === "function") partyOverlay.navigate(action)
            } else if (controllerNavigation.context === "console") {
                if (action === "menu") {
                    if (typeof consoleView.navigate === "function") consoleView.navigate("menu")
                    else root.togglePartyOverlay()
                }
                else if (action === "back") {
                    if (settingsDashboard.visible) { settingsDashboard.close(); return }
                    if (controllerDashboard.item && controllerDashboard.item.visible) { controllerDashboard.close(); return }
                    if (managementOverview.visible) { managementOverview.close(); return }
                    if (socialDashboard.visible) { socialDashboard.close(); return }
                    if (downloadsDashboard.visible) { downloadsDashboard.close(); return }
                    if (emulationDashboard.visible) { emulationDashboard.close(); return }
                    if (saveDashboard.visible) { saveDashboard.close(); return }
                    if (typeof consoleView.navigate === "function") consoleView.navigate(action)
                }
                else if (typeof consoleView.navigate === "function") consoleView.navigate(action)
            }
        }
    }
    property bool consoleMode: false
    property int consolePreviousVisibility: Window.Windowed
    function toggleConsole() {
        if (!consoleMode) {
            controllerDashboard.close(); socialDashboard.close(); managementOverview.close();
            consolePreviousVisibility = visibility;
            consoleMode = true;
            if (typeof controllerStudioHost !== "undefined" && controllerStudioHost && !controllerStudioHost.running) {
                if (typeof controllerStudioHost.start === "function") controllerStudioHost.start()
                else if (typeof controllerStudioHost.open === "function") controllerStudioHost.open()
            }
            if (typeof headlessCheck === "undefined" || !headlessCheck) showFullScreen();
            consoleView.forceActiveFocus()
        }
        else {
            consoleMode = false;
            if (typeof headlessCheck === "undefined" || !headlessCheck) {
                if (consolePreviousVisibility === Window.Maximized) showMaximized();
                else if (consolePreviousVisibility !== Window.FullScreen) showNormal()
            }
        }
    }
    function selectedNativeRecords() {
        var revision = root.projectionRevision
        if (typeof nativeLocalLibrary === "undefined" || !nativeLocalLibrary) return []
        var targets = root.selectedGame.editTargets || [{id: root.selectedId}]
        var rows = []; var seen = {}
        targets.forEach(function(t) { nativeLocalLibrary.recordsForGame(t.id).forEach(function(r) {
            if (!seen[r.variant_key]) { rows.push(r); seen[r.variant_key] = true }
        }) })
        return rows
    }
    function selectedLaunchOptions() { return launchOptionsFor(root.selectedGame, root.selectedId) }
    function launchOptionsFor(game, gameId) {
        game = game || ({})
        var gameTitle = game.title || ""
        var rows = []
        var seen = {}
        var seenProviderProduct = {}
        function addOption(rec, fallbackTargetId) {
            if (!rec) return
            var st = (rec.state || "installed").toLowerCase()
            var isInstalled = (st === "installed" || st === "present" || st === "verified")
            if (!isInstalled) return
            var rk = (rec.provider || "") + "\t" + (rec.variant_key || "")
            var pp = (rec.provider || "") + "\t" + (rec.product_id || rec.provider_game_id || "")
            if (rk !== "\t" && seen[rk]) return
            if (pp !== "\t" && seenProviderProduct[pp]) return
            if (rk !== "\t") seen[rk] = true
            if (pp !== "\t") seenProviderProduct[pp] = true
            var provLabel = rec.provider === "Steam" ? "Play on Steam" : (rec.provider === "Epic" ? "Play on Epic" : (rec.provider === "GOG" ? "Play on GOG" : (rec.provider === "Xbox" ? "Play on Xbox" : (rec.provider === "Amazon" ? "Play on Amazon" : (rec.provider || "Play")))))
            rows.push({
                row_type: "play",
                label: provLabel,
                provider: rec.provider || "",
                variant_key: rec.variant_key || "",
                product_id: rec.product_id || rec.provider_game_id || "",
                title: rec.title || gameTitle || "Game",
                state: rec.state || "installed",
                displayLabel: provLabel,
                targetId: rec.targetId || fallbackTargetId || gameId || "",
                rawRecord: rec
            })
        }

        if (typeof nativeLocalLibrary !== "undefined" && nativeLocalLibrary) {
            var targets = game.editTargets || [{id: gameId}]
            targets.forEach(function(tgt) {
                var records = nativeLocalLibrary.recordsForGame(tgt.id) || []
                records.forEach(function(rec) { addOption(rec, tgt.id) })
            })
        }

        if (game.installedVariants && game.installedVariants.length > 0) {
            game.installedVariants.forEach(function(v) { addOption(v, v.targetId || gameId) })
        }

        return rows
    }
    // Quick library (overlay) Play: the same launch options and LaunchView path as the Library Play button,
    // for any game even when the current filters hide it.
    function playGameById(gameId) {
        if (!gameId || typeof launchView === "undefined" || !launchView) return false
        var options = launchOptionsFor(libraryModel.overview(gameId), gameId)
        if (!options.length) return false
        var opt = options[0]
        var rec = opt.rawRecord || opt
        launchView.setTarget(rec.variant_key || opt.targetId, {
            canonical_game_id: rec.canonical_game_id || gameId, variant_key: rec.variant_key || opt.variant_key,
            product_id: rec.product_id || rec.provider_game_id || opt.product_id, provider: rec.provider || opt.provider,
            platform: rec.platform, title: rec.title || opt.title, state: rec.state || opt.state })
        var result = launchView.play()
        return !!result && result.status === "handed_off"
    }
    function requestAssistantPlayById(gameId) {
        return assistantDirector.requestPlay(gameId)
    }
    function cancelAssistantAnimationAndLaunch() {
        return assistantDirector.userInputCancel()
    }
    Connections {
        target: typeof overlayLibrary !== "undefined" ? overlayLibrary : null
        function onLaunchRequested(gameId) {
            if (!root.playGameById(gameId)) overlayLibrary.reportLaunchResult(gameId, typeof launchView !== "undefined" && launchView ? (launchView.lastError || "This game has no installed launch option") : "Launch unavailable")
        }
    }
    Connections { target: typeof nativeLocalLibrary !== "undefined" ? nativeLocalLibrary : null; function onChanged() { root.projectionRevision += 1 } }
    Connections { target: typeof gameInstalls !== "undefined" ? gameInstalls : null; function onUpdatesChanged() { root.projectionRevision += 1 } }
    Connections { target: libraryModel; function onUpdatesChanged() { root.projectionRevision += 1 } }
    property bool overlayFromConsole: false
    function togglePartyOverlay() {
        if (!partyOverlay.visible) overlayFromConsole = root.active && root.consoleMode
        partyOverlay.toggle()
    }
    property int latestLinkAuditId: 0
    property var statusValues: ["All states", "installed", "declared-installed", "registered", "not-installed", "not-owned", "unknown"]
    property var statusLabels: ["All states", "Installed", "Needs local check", "In library", "Not installed", "No longer in source library", "Not checked"]
    property var sortKeys: ["title", "sources", "platforms", "installState", "variantCount"]
    property var sortLabels: ["Name", "Library", "Platform", "Install state", "Records"]
    property int sortIndex: 0
    property bool descending: false
    property int coverWidth: 126
    property var columnWidths: [220, 145, 110, 240, 85]
    property int tableWidth: columnWidths.reduce((a,b) => a+b, 0)
    readonly property bool filtersActive: query.length > 0 || source !== "All sources" || platform !== "All platforms" || status !== "All states" || nativeProvider !== "All native providers" || Object.keys(collectionFilters).length > 0

    function explorerValues() {
        if (explorerField.currentIndex === 0) return sourceOptions
        if (explorerField.currentIndex === 1) return platformOptions
        if (explorerField.currentIndex === 2) return statusValues
        if (explorerField.currentIndex >= 3) return ["All " + collectionLabels[explorerField.currentIndex - 3].toLowerCase()].concat(libraryModel.collectionValues(collectionFields[explorerField.currentIndex - 3]))
        return []
    }
    function filteredExplorerValues() {
        let values = explorerValues()
        if (explorerField.currentIndex < 3 || !facetQuery.trim().length) return values
        let needle = facetQuery.trim().toLowerCase()
        return values.length ? [values[0]].concat(values.slice(1).filter(value => value.toLowerCase().includes(needle))) : []
    }
    function applyCollectionFilters(filters) {
        collectionFilters = filters
        libraryModel.setCollectionFilters(filters)
        reconcileSelection()
    }
    function toggleCollection(field, value) {
        let next = Object.assign({}, collectionFilters)
        if (!value) delete next[field]
        else {
            let chosen = (next[field] || []).slice()
            let index = chosen.indexOf(value)
            if (index >= 0) chosen.splice(index, 1); else chosen.push(value)
            if (chosen.length) next[field] = chosen; else delete next[field]
        }
        applyCollectionFilters(next)
    }
    function collectionSummary() {
        let parts = []
        for (let field of Object.keys(collectionFilters)) {
            let values = collectionFilters[field]
            parts.push(collectionLabels[collectionFields.indexOf(field)] + ": " + values.join(" or "))
        }
        return parts.join(" · ")
    }
    function visibleLinkCandidates() {
        let rows = root.linkManager.rows
        let needle = linkSearch.trim().toLowerCase()
        if (!needle.length) return rows
        return rows.filter(row => row.records.some(record => (record.title + " " + record.sources.join(" ")).toLowerCase().includes(needle)) || row.evidence.join(" ").toLowerCase().includes(needle))
    }
    function chooseLinkCandidate(candidateId, checked) {
        let ids = selectedLinkCandidates.slice()
        let position = ids.indexOf(candidateId)
        if (checked && position < 0) ids.push(candidateId)
        else if (!checked && position >= 0) ids.splice(position, 1)
        selectedLinkCandidates = ids
        linkPreviewReady = false
        root.linkManager.selectCandidates(ids)
    }
    function openAccountLinkReview() {
        if (!root.linkManager.storeEnabled && root.linkStatePathValue.length > 0)
            root.linkManager.createLinkState(root.linkStatePathValue, true)
        root.latestLinkAuditId = root.linkManager.undoableAuditId || 0
        if (root.linkManager.storeEnabled) linkDialog.open()
    }
    function openMods() {
        root.navigatePage("mods")
    }

    function openSaveManager() {
        saveDashboard.selectedGameId=root.selectedId
        saveDashboard.selectedGameTitle=root.selectedGame.title || ""
        saveDashboard.selectedGameSource=root.selectedGame.sources || ""
        saveDashboard.open()
    }

    function applyFilters() { libraryModel.setFilters(query, source, platform, status) }
    function reconcileSelection() {
        if (libraryModel.rowOf(selectedId) < 0) selectedId = libraryModel.idAt(0)
        else selectedId = libraryModel.representativeFor(selectedId) || selectedId
        grid.currentIndex = libraryModel.rowOf(selectedId)
        compactList.currentIndex = libraryModel.rowOf(selectedId)
        columnsList.currentIndex = libraryModel.rowOf(selectedId)
    }
    function selectGame(id) { selectedId = id; reconcileSelection() }
    function clearFilters() { nativeProvider = "All native providers"; query = ""; source = "All sources"; platform = "All platforms"; status = "All states"; applyCollectionFilters({}); applyFilters() }
    function setSource(value) { navigatePage("library"); organizerMode = false; source = value; applyFilters() }
    function setPage(value) {
        navigatePage(value === "Organizer" ? "organizer" : "library")
        organizerMode = value === "Organizer"
        if (!organizerMode) { clearFilters(); if (value === "Installed") { status = "installed"; applyFilters() } }
    }
    function setView(value) { navigatePage("library"); organizerMode = false; viewMode = value; if (value === "list") inspectorVisible = false; reconcileSelection() }
    function sortBy(index) { if (sortIndex === index) descending = !descending; else { sortIndex = index; descending = false } }
    function resizeColumn(index, width) { let values = columnWidths.slice(); values[index] = Math.max(80, width); columnWidths = values }
    onQueryChanged: applyFilters()
    onSourceChanged: applyFilters()
    onPlatformChanged: applyFilters()
    onStatusChanged: applyFilters()
    onNativeProviderChanged: libraryModel.setNativeFilter(nativeProvider)
    onSortIndexChanged: libraryModel.setSort(sortKeys[sortIndex], descending)
    onDescendingChanged: libraryModel.setSort(sortKeys[sortIndex], descending)
    Component.onCompleted: { libraryModel.setSort("title", false); reconcileSelection(); latestLinkAuditId = linkManager.undoableAuditId || 0; controllerRoutingReady = true; refreshControllerRouting()
        if (typeof controllerNavigation !== "undefined" && controllerNavigation && !(typeof headlessCheck !== "undefined" && headlessCheck))
            controllerNavigation.setGuideWatch(true) }
    Connections { target: libraryModel; function onModelReset() { root.projectionRevision += 1; root.reconcileSelection() } }
    Connections { target: typeof catalogLinkView !== "undefined" ? catalogLinkView : null; function onChanged() { root.latestLinkAuditId = root.linkManager.undoableAuditId || 0 } }
    Connections { target: typeof artworkView !== "undefined" ? artworkView : null; function onOperationFinished() { root.projectionRevision += 1 } }
    Shortcut { sequence: "Ctrl+F"; onActivated: { root.navigatePage("library"); root.organizerMode = false; search.forceActiveFocus(); search.selectAll() } }
    Shortcut { sequence: "Ctrl+E"; onActivated: root.filterVisible = !root.filterVisible }
    Shortcut { sequence: "Ctrl+G"; onActivated: root.filterVisible = !root.filterVisible }
    Shortcut { sequence: "Escape"; enabled: root.consoleMode; onActivated: { if (typeof consoleView.handleEscape === "function") consoleView.handleEscape(); else root.toggleConsole() } }
    Shortcut { sequence: "F11"; onActivated: root.toggleConsole() }
    Shortcut { sequence: "Ctrl+Shift+O"; onActivated: root.togglePartyOverlay() }
    Shortcut { sequence: "Escape"; enabled: !root.consoleMode; onActivated: { if (root.activePage !== "library" && root.activePage !== "organizer") root.goBack(); else { root.uiSound("back"); if (root.query.length) root.query = ""; else search.focus = false } } }

    // Neutral Daze scrollbar: no platform-style blue thumb/focus fallback.
    component DazeScrollBar: ScrollBar {
        implicitWidth: Theme.scrollbarWidth
        implicitHeight: Theme.scrollbarWidth
        padding: 2
        contentItem: Rectangle {
            implicitWidth: 8; implicitHeight: 8
            radius: 2
            color: parent.pressed ? Theme.text : parent.hovered ? Theme.glyph : Theme.main
        }
        background: Rectangle { color: Theme.background }
    }

    RowLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.preferredWidth: 116; Layout.fillHeight: true
            color: Theme.background
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.main }
            ColumnLayout {
                anchors.fill: parent; anchors.bottomMargin: 8; spacing: 3
                // Waystone logo (assets/brand, Kian's "Tall three" cairn); click = Library home.
                Item {
                    objectName: "railBrand"
                    Layout.preferredWidth: 114; Layout.preferredHeight: 92
                    Column {
                        anchors.centerIn: parent; spacing: 0
                        Image {
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: 60; height: 60
                            source: Qt.resolvedUrl("../../../assets/brand/waystone-mark.svg")
                            sourceSize: Qt.size(120, 120)
                            smooth: true; mipmap: true
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: "WAYSTONE"
                            color: Theme.text; font.family: Theme.family; font.pixelSize: 10; font.bold: true
                            font.letterSpacing: 2.2
                        }
                    }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.showLibraryView() }
                }
                Column {
                    Layout.preferredWidth: 114; spacing: 3
                    CompactButton { objectName: "railLibrary"; width: 114; height: 40; text: "Library"; hint: "All games: filter, edit, install and play"; selected: root.activePage === "library" || root.activePage === "organizer"; onClicked: root.showLibraryView() }
                    CompactButton { objectName: "railStore"; width: 114; height: 40; text: "Store"; hint: "Search one game across supported retailer offers"; selected: root.activePage === "store"; onClicked: root.navigatePage("store") }
                    CompactButton { objectName: "railDownloads"; selected: root.activePage === "downloads"; width: 114; height: 40; text: "Downloads"; hint: "Downloads and installations"; onClicked: downloadsDashboard.open() }
                    CompactButton { objectName: "railMods"; selected: root.activePage === "mods"; width: 114; height: 40; text: "Mods"; hint: "Mod profiles, reviewed deployment and undo"; onClicked: root.openMods() }
                    CompactButton { objectName: "railSaves"; selected: root.activePage === "saves"; width: 114; height: 40; text: "Saves"; hint: "Save backups and restores"; enabled: typeof saveManagement !== "undefined" && saveManagement !== null; onClicked: root.openSaveManager() }
                    CompactButton { objectName: "railEmulation"; selected: root.emulationPages.indexOf(root.activePage) >= 0 || (root.activePage === "overview" && managementOverview.section === "roms"); width: 114; height: 40; text: "Emulation"; hint: "Emulator systems, profiles and ROMs"; onClicked: emulationDashboard.open() }
                    CompactButton { objectName: "railControllers"; selected: root.activePage === "controllers"; width: 114; height: 40; text: "Controllers"; hint: "Controller profiles and mapping"; onClicked: root.openControllers() }
                    CompactButton { objectName: "railSocial"; selected: root.socialPages.indexOf(root.activePage) >= 0; width: 114; height: 40; text: "Social"; hint: "Friends, parties and achievements"; onClicked: socialDashboard.open() }
                }
                Item { Layout.fillHeight: true }
                // Settings and the app actions sit at the bottom of the bar.
                CompactButton { objectName: "railSettings"; Layout.preferredWidth: 114; Layout.preferredHeight: 40; selected: root.activePage === "settings" || (root.activePage === "overview" && managementOverview.section !== "roms"); text: "Settings"; hint: "Accounts, storage, store and advanced tools"; onClicked: root.openSettings() }
                Row {
                    spacing: 3
                    CompactButton { objectName: "railConsole"; width: 36; height: 34; text: "⛶"; hint: "Console / big picture mode · F11"; onClicked: root.toggleConsole() }
                    CompactButton { objectName: "railOverlay"; width: 36; height: 34; text: "◱"; hint: "In-game overlay · Ctrl+Shift+O"; onClicked: root.togglePartyOverlay() }
                    CompactButton { objectName: "railQuit"; width: 36; height: 34; text: "⏻"; hint: "Quit Waystone (stops controller forwarding)"; onClicked: { root.quitRequested = true; root.close() } }
                }
            }

        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 48
                visible: root.activePage !== "library" && root.activePage !== "organizer"
                color: Theme.panel
                RowLayout {
                    anchors.fill: parent; anchors.margins: 8
                    CompactButton { objectName: "workspaceBack"; implicitWidth: 76; text: "‹ Back"; onClicked: root.goBack() }
                    Text { objectName: "workspaceTitle"; text: root.pageTitles[root.activePage] || "Waystone"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                    Item { implicitWidth: 16 }
                    Row {
                        objectName: "sectionTabs"; spacing: 4
                        visible: root.socialPages.indexOf(root.activePage) >= 0 || root.emulationPages.indexOf(root.activePage) >= 0 || (root.activePage === "overview" && managementOverview.section === "roms")
                        CompactButton { objectName: "tabFriends"; visible: root.socialPages.indexOf(root.activePage) >= 0; text: "Friends & parties"; selected: root.activePage === "friends"; onClicked: socialDashboard.open() }
                        CompactButton { objectName: "tabAchievements"; visible: root.socialPages.indexOf(root.activePage) >= 0; implicitWidth: 120; text: "Achievements"; selected: root.activePage === "achievements"; onClicked: achievementsDashboard.open() }
                        CompactButton { objectName: "tabSystems"; visible: !(root.socialPages.indexOf(root.activePage) >= 0); implicitWidth: 100; text: "Systems"; selected: root.activePage === "emulation"; onClicked: emulationDashboard.open() }
                        CompactButton { objectName: "tabRoms"; visible: !(root.socialPages.indexOf(root.activePage) >= 0); implicitWidth: 80; text: "ROMs"; selected: root.activePage === "roms" || root.activePage === "overview"; onClicked: managementOverview.openSection("roms") }
                    }
                    Item { Layout.fillWidth: true }
                }
            }
            Item {
                id: workspaceContainer; objectName: "workspaceContainer"
                Layout.fillWidth: true; Layout.fillHeight: true
        ColumnLayout {
            id: libraryWorkspace; objectName: "libraryWorkspace"
            anchors.fill: parent; spacing: 0
            visible: root.activePage === "library" || root.activePage === "organizer"
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 50
                color: Theme.panel
                Rectangle { anchors.bottom: parent.bottom; height: 1; width: parent.width; color: Theme.main }
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; spacing: 6
                    TextField {
                        id: search; objectName: "searchField"
                        Layout.preferredWidth: Math.min(300, root.width * 0.23); Layout.preferredHeight: 30
                        text: root.query; onTextEdited: { root.organizerMode = false; root.query = text }
                        placeholderText: "Search library…  Ctrl+F"
                        font.family: Theme.family; font.pixelSize: 13
                        color: Theme.text; placeholderTextColor: Theme.muted; selectByMouse: true
                        selectionColor: Theme.hover
                        Accessible.name: "Search library"
                        background: Rectangle { color: Theme.background; radius: Theme.cornerRadius; border.color: search.activeFocus ? Theme.text : Theme.line }
                    }
                    CompactButton { text: "×"; hint: "Clear search"; visible: root.query.length > 0; onClicked: root.query = "" }
                    Rectangle { width: 1; height: 23; color: Theme.main }
                    CompactButton { objectName: "detailsViewButton"; text: "◧"; hint: "Details view"; selected: root.viewMode === "details" && !root.organizerMode; onClicked: root.setView("details") }
                    CompactButton { objectName: "gridViewButton"; text: "▦"; hint: "Grid view"; selected: root.viewMode === "grid" && !root.organizerMode; onClicked: root.setView("grid") }
                    CompactButton { objectName: "listViewButton"; text: "☷"; hint: "Column list view"; selected: root.viewMode === "list" && !root.organizerMode; onClicked: root.setView("list") }
                    Rectangle { width: 1; height: 23; color: Theme.main }
                    CompactCombo {
                        id: sortCombo; objectName: "sortCombo"
                        Layout.preferredWidth: 130
                        model: root.sortLabels; currentIndex: root.sortIndex
                        Accessible.name: "Sort library by"
                        onActivated: { root.organizerMode = false; root.sortIndex = currentIndex }
                    }
                    CompactButton { objectName: "sortDirection"; text: root.descending ? "↓" : "↑"; hint: root.descending ? "Descending — click for ascending" : "Ascending — click for descending"; onClicked: { root.organizerMode = false; root.descending = !root.descending } }
                    CompactButton { objectName: "filterToggle"; implicitWidth: root.filtersActive ? 100 : 70; text: root.filtersActive ? "Filters •" : "Filters"; hint: "Show or hide filters · Ctrl+G (active filters remain applied)"; selected: root.filterVisible || root.filtersActive; onClicked: { root.organizerMode = false; root.filterVisible = !root.filterVisible } }
                    // Preserved as invisible object for test harness compatibility; duplicates/ROMs/device are managed via Organizer and AI
                    CompactButton {
                        objectName: "linkAccountsButton"
                        visible: false
                        implicitWidth: 0; implicitHeight: 0
                        text: "Link duplicates"; hint: "Review possible duplicate source/account records; nothing is linked automatically"
                        enabled: root.linkManager.storeEnabled || (root.linkStatePathValue.length > 0 && root.linkManager.status.indexOf("does not match") < 0)
                        onClicked: root.openAccountLinkReview()
                    }
                    Item { Layout.fillWidth: true }
                    CompactButton { objectName: "organizerButton"; implicitWidth: 64; text: root.organizerMode ? "Done" : "Edit"; hint: "Edit library metadata, review agent proposals and duplicate links"; selected: root.organizerMode; onClicked: root.organizerMode ? root.showLibraryView() : root.navigatePage("organizer") }
                    CompactButton { objectName: "inspectorToggle"; text: "ⓘ"; hint: "Toggle game details pane"; selected: root.inspectorVisible || root.viewMode === "details"; onClicked: { root.organizerMode = false; if (root.viewMode === "details") root.setView("grid"); else root.inspectorVisible = !root.inspectorVisible } }
                }
            }
            SplitView {
                id: outerSplit; objectName: "outerSplit"
                Layout.fillWidth: true; Layout.fillHeight: true
                orientation: Qt.Horizontal
                handle: Rectangle { implicitWidth: 5; color: SplitHandle.hovered || SplitHandle.pressed ? Theme.hover : Theme.background; Rectangle { anchors.centerIn: parent; width: 1; height: parent.height; color: Theme.main } }
                Rectangle {
                    id: explorer; objectName: "explorerPanel"
                    visible: root.explorerVisible && !root.organizerMode
                    SplitView.preferredWidth: 195; SplitView.minimumWidth: root.width < 1100 ? 125 : 155; SplitView.maximumWidth: 350
                    color: Theme.background
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 8; spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            CompactCombo { id: explorerField; objectName: "explorerField"; Layout.fillWidth: true; Layout.minimumWidth: 80; Layout.preferredWidth: 120; model: ["Libraries", "Platforms", "Install states"].concat(root.collectionLabels); Accessible.name: "Explore by"; onActivated: root.facetQuery = "" }
                            CompactButton { text: "×"; hint: "Hide filters · Ctrl+G"; onClicked: root.filterVisible = false }
                        }
                        CompactCombo { objectName: "nativeConnectionFilter"; visible: root.connectionService.available; Layout.fillWidth: true; model: root.nativeProviderLabels; currentIndex: Math.max(0, root.nativeProviderKeys.indexOf(root.nativeProvider)); Accessible.name: "Filter by native connection"; onActivated: root.nativeProvider = root.nativeProviderKeys[currentIndex] }
                        TextField {
                            id: facetSearch; objectName: "facetSearch"
                            visible: explorerField.currentIndex >= 3 && explorerField.currentIndex < root.collectionFields.length + 3
                            Layout.fillWidth: true; Layout.preferredHeight: 29
                            text: root.facetQuery; onTextEdited: root.facetQuery = text
                            placeholderText: "Filter " + (explorerField.currentText || "categories").toLowerCase() + "…"
                            color: Theme.text; placeholderTextColor: Theme.muted
                            font.family: Theme.family; font.pixelSize: 12
                            background: Rectangle { color: Theme.background; radius: Theme.cornerRadius; border.color: facetSearch.activeFocus ? Theme.text : Theme.line }
                            Accessible.name: "Search collection values"
                        }
                        Text { text: "Full-catalog counts"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                        ListView {
                            id: facets; objectName: "explorerItems"
                            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                            model: root.filteredExplorerValues()
                            ScrollBar.vertical: DazeScrollBar {}
                            delegate: ItemDelegate {
                                id: facet
                                required property string modelData
                                required property int index
                                width: facets.width; height: 30
                                property string collectionKey: explorerField.currentIndex >= 3 ? root.collectionFields[explorerField.currentIndex - 3] : ""
                                property bool chosen: explorerField.currentIndex === 0 ? root.source === modelData : explorerField.currentIndex === 1 ? root.platform === modelData : explorerField.currentIndex === 2 ? root.status === modelData : (index === 0 ? !(root.collectionFilters[collectionKey] || []).length : (root.collectionFilters[collectionKey] || []).indexOf(modelData) >= 0)
                                property string label: explorerField.currentIndex === 2 ? root.statusLabels[index] : modelData
                                contentItem: RowLayout {
                                    Text { text: facet.label; textFormat: Text.PlainText; Layout.fillWidth: true; color: facet.chosen ? Theme.glyph : Theme.text; font.family: Theme.family; font.pixelSize: 13; elide: Text.ElideRight }
                                    Text { text: facet.index === 0 ? libraryModel.totalCount : libraryModel.facetCount(explorerField.currentIndex < 3 ? ["source", "platform", "status"][explorerField.currentIndex] : root.collectionFields[explorerField.currentIndex - 3], facet.modelData); color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                                }
                                background: Rectangle { radius: Theme.cornerRadius; color: facet.chosen ? Theme.main : facet.hovered ? Theme.panel : "transparent" }
                                onClicked: { if (explorerField.currentIndex === 0) root.source = modelData; else if (explorerField.currentIndex === 1) root.platform = modelData; else if (explorerField.currentIndex === 2) root.status = modelData; else root.toggleCollection(root.collectionFields[explorerField.currentIndex - 3], facet.index === 0 ? "" : modelData) }
                                PlainToolTip { objectName: "explorerToolTip"; visible: facet.hovered && !headlessCheck; text: facet.label }
                            }
                        }
                    }
                }
                Item {
                    SplitView.fillWidth: true; SplitView.minimumWidth: root.viewMode === "details" || root.inspectorVisible ? 440 : 330
                    ColumnLayout {
                        anchors.fill: parent; spacing: 0
                        Rectangle {
                            Layout.fillWidth: true; Layout.preferredHeight: 31
                            color: Theme.background
                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
                                Text { text: root.organizerMode ? "Organizer" : root.collectionSummary().length ? root.collectionSummary() : root.source === "All sources" ? (root.status === "installed" ? "Installed games" : "All games") : root.source; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; textFormat: Text.PlainText; elide: Text.ElideRight; Layout.maximumWidth: 430 }
                                Text { text: root.organizerMode ? "Edit metadata · agent workspace" : libraryModel.visibleCount + " of " + libraryModel.totalCount; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                                Item { Layout.fillWidth: true }
                                Text { visible: typeof launcherLibrary !== "undefined" && launcherLibrary !== null; text: visible ? launcherLibrary.notice : ""; Layout.maximumWidth: 330; color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; elide: Text.ElideRight; textFormat: Text.PlainText }
                                CompactButton { objectName: "clearFiltersButton"; implicitHeight: 26; text: "Clear filters"; visible: root.filtersActive && !root.organizerMode; hint: "Reset search, library, platform and install state"; onClicked: root.clearFilters() }
                                CompactButton { objectName: "refreshLauncherLibrary"; implicitHeight: 26; visible: typeof launcherLibrary !== "undefined" && launcherLibrary !== null; enabled: visible && !launcherLibrary.busy; text: visible && launcherLibrary.busy ? "Refreshing…" : "Refresh library"; hint: "Read this device's installed games and local launcher libraries; does not contact stores"; onClicked: launcherLibrary.refresh() }
                            }
                        }
                        SplitView {
                            id: innerSplit; objectName: "innerSplit"
                            Layout.fillWidth: true; Layout.fillHeight: true
                            visible: !root.organizerMode
                            orientation: Qt.Horizontal
                            handle: Rectangle { implicitWidth: 5; color: SplitHandle.hovered || SplitHandle.pressed ? Theme.hover : Theme.main }
                            Item {
                                SplitView.preferredWidth: root.viewMode === "details" ? 255 : 700
                                SplitView.fillWidth: root.viewMode !== "details"
                                SplitView.minimumWidth: root.viewMode === "details" ? 150 : 200
                                GridView {
                                    id: grid; objectName: "coverGrid"
                                    anchors.fill: parent; anchors.margins: 6
                                    visible: root.viewMode === "grid"
                                    clip: true; model: libraryModel
                                    cellWidth: root.coverWidth + 12
                                    cellHeight: Math.round(root.coverWidth * 620 / 456) + 46
                                    bottomMargin: 8
                                    keyNavigationEnabled: true
                                    ScrollBar.vertical: DazeScrollBar {}
                                    onCurrentIndexChanged: { if (activeFocus && currentIndex >= 0) { root.uiSound("move"); root.selectGame(libraryModel.idAt(currentIndex)) } }
                                    delegate: Item {
                                        id: coverDelegate
                                        required property string workId
                                        required property string title
                                        required property string coverUrl
                                        required property string stateLabel
                                        required property int variantCount
                                        required property bool hasUpdate
                                        required property string updateLabel
                                        required property int index
                                        width: grid.cellWidth; height: grid.cellHeight
                                        Rectangle {
                                            anchors.fill: parent; anchors.margins: 2
                                            color: root.selectedId === workId ? Theme.main : "transparent"
                                            radius: Theme.cornerRadius
                                            border.width: coverMouse.containsMouse ? 1 : 0
                                            border.color: root.selectedId === workId ? Theme.glyph : Theme.line
                                        }
                                        MissingCover {
                                            id: gridArtwork
                                            objectName: "gridArtwork"
                                            x: 6; y: 6; width: root.coverWidth; height: Math.round(root.coverWidth * 620 / 456)
                                            artworkSource: coverDelegate.coverUrl
                                            loadEnabled: grid.visible && !root.organizerMode && coverDelegate.y + coverDelegate.height > grid.contentY && coverDelegate.y < grid.contentY + grid.height
                                            onLoadEnabledChanged: if (loadEnabled && !coverDelegate.coverUrl && typeof libraryModel.requestRibbon === "function") libraryModel.requestRibbon(coverDelegate.workId)
                                        }
                                        Rectangle {
                                            objectName: "gridUpdateBadge"
                                            visible: coverDelegate.hasUpdate
                                            x: 10
                                            y: 10
                                            width: Math.min(root.coverWidth - 8, gridUpdateBadgeText.implicitWidth + 12)
                                            height: 20
                                            radius: 4
                                            color: Theme.panel
                                            border.width: 1
                                            border.color: Theme.glyph
                                            Text {
                                                id: gridUpdateBadgeText
                                                objectName: "gridUpdateBadgeText"
                                                anchors.centerIn: parent
                                                width: parent.width - 8
                                                text: coverDelegate.updateLabel.length > 0 ? coverDelegate.updateLabel : "Update available"
                                                textFormat: Text.PlainText
                                                color: Theme.glyph
                                                font.family: Theme.family
                                                font.pixelSize: 10
                                                font.bold: true
                                                elide: Text.ElideRight
                                                horizontalAlignment: Text.AlignHCenter
                                            }
                                        }
                                        Text { x: 6; y: gridArtwork.y + gridArtwork.height + 3; width: root.coverWidth; height: 16; text: title; textFormat: Text.PlainText; color: Theme.text; font.family: Theme.family; font.pixelSize: 13; elide: Text.ElideRight; horizontalAlignment: Text.AlignHCenter }
                                        Text { objectName: "gridStatusCaption"; x: 6; y: gridArtwork.y + gridArtwork.height + 21; width: root.coverWidth; height: 15; text: coverDelegate.hasUpdate ? (coverDelegate.updateLabel || "Update available") : (variantCount > 1 ? variantCount + " records · " + stateLabel : stateLabel); textFormat: Text.PlainText; color: coverDelegate.hasUpdate ? Theme.glyph : Theme.muted; font.family: Theme.family; font.pixelSize: 12; elide: Text.ElideRight; horizontalAlignment: Text.AlignHCenter }
                                        MouseArea {
                                            id: coverMouse; anchors.fill: parent; hoverEnabled: true
                                            onClicked: { root.uiSound("select"); root.selectGame(workId); grid.forceActiveFocus() }
                                            onDoubleClicked: { root.uiSound("open"); root.selectGame(workId); root.setView("details") }
                                        }
                                        PlainToolTip { objectName: "gridToolTip"; visible: coverMouse.containsMouse && !headlessCheck; text: title + "\n" + stateLabel + "\n" + workId }
                                        Accessible.name: title + ", " + stateLabel
                                        Accessible.role: Accessible.ListItem
                                    }
                                }
                                ListView {
                                    id: compactList; objectName: "compactGameList"
                                    anchors.fill: parent; visible: root.viewMode === "details"
                                    clip: true; model: libraryModel; keyNavigationEnabled: true
                                    ScrollBar.vertical: DazeScrollBar {}
                                    onCurrentIndexChanged: { if (activeFocus && currentIndex >= 0) { root.uiSound("move"); root.selectGame(libraryModel.idAt(currentIndex)) } }
                                    delegate: ItemDelegate {
                                        id: detailsRow
                                        required property string workId
                                        required property string title
                                        required property string iconUrl
                                        required property string stateLabel
                                        required property bool hasUpdate
                                        required property string updateLabel
                                        required property int index
                                        width: compactList.width; height: 30
                                        contentItem: RowLayout {
                                            spacing: 8
                                            Image {
                                                objectName: "compactGameIcon"
                                                Layout.preferredWidth: visible ? 22 : 0; Layout.preferredHeight: 22
                                                source: compactList.visible && !root.organizerMode && detailsRow.y + detailsRow.height > compactList.contentY && detailsRow.y < compactList.contentY + compactList.height ? detailsRow.iconUrl : ""
                                                sourceSize: Qt.size(32, 32)
                                                asynchronous: true; cache: true; retainWhileLoading: false
                                                fillMode: Image.PreserveAspectFit
                                                visible: status === Image.Ready
                                            }
                                            Text { text: detailsRow.title; textFormat: Text.PlainText; Layout.fillWidth: true; color: Theme.text; font.family: Theme.family; font.pixelSize: 13; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; leftPadding: 4 }
                                            Rectangle {
                                                objectName: "compactRowUpdateBadge"
                                                visible: detailsRow.hasUpdate
                                                Layout.preferredWidth: compactUpdateText.implicitWidth + 10
                                                Layout.preferredHeight: 18
                                                radius: 3
                                                color: Theme.main
                                                border.width: 1
                                                border.color: Theme.glyph
                                                Text {
                                                    id: compactUpdateText
                                                    anchors.centerIn: parent
                                                    text: "Update"
                                                    textFormat: Text.PlainText
                                                    color: Theme.glyph
                                                    font.family: Theme.family
                                                    font.pixelSize: 10
                                                    font.bold: true
                                                }
                                            }
                                        }
                                        background: Rectangle { color: root.selectedId === workId ? Theme.main : hovered ? Theme.panel : "transparent"; Rectangle { width: 2; height: parent.height; color: Theme.glyph; visible: root.selectedId === workId } }
                                        onClicked: { root.uiSound("select"); root.selectGame(workId); compactList.forceActiveFocus() }
                                        PlainToolTip { objectName: "detailsToolTip"; visible: detailsRow.hovered && !headlessCheck; text: title + "\n" + stateLabel + "\n" + workId }
                                    }
                                }
                                Flickable {
                                    id: columnFlick; objectName: "columnView"
                                    anchors.fill: parent; visible: root.viewMode === "list"
                                    contentWidth: Math.max(width, root.tableWidth); contentHeight: height
                                    clip: true; flickableDirection: Flickable.HorizontalFlick
                                    ScrollBar.horizontal: DazeScrollBar {}
                                    Column {
                                        width: columnFlick.contentWidth; height: columnFlick.height
                                        Row {
                                            id: headers; height: 32; width: parent.width
                                            Repeater {
                                                model: root.sortLabels
                                                delegate: Rectangle {
                                                    required property string modelData
                                                    required property int index
                                                    width: root.columnWidths[index]; height: 32; color: Theme.panel
                                                    Text { anchors.fill: parent; anchors.leftMargin: 10; verticalAlignment: Text.AlignVCenter; text: modelData + (root.sortIndex === index ? root.descending ? "  ↓" : "  ↑" : ""); color: root.sortIndex === index ? Theme.glyph : Theme.text; font.family: Theme.family; font.pixelSize: 13 }
                                                    MouseArea { anchors.fill: parent; onClicked: root.sortBy(index) }
                                                    MouseArea {
                                                        width: 6; height: parent.height; anchors.right: parent.right; cursorShape: Qt.SplitHCursor
                                                        property real startX: 0; property real startWidth: 0
                                                        onPressed: mouse => { startX = mapToItem(null, mouse.x, mouse.y).x; startWidth = root.columnWidths[index] }
                                                        onPositionChanged: mouse => { if (pressed) root.resizeColumn(index, startWidth + mapToItem(null, mouse.x, mouse.y).x - startX) }
                                                        Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.line }
                                                    }
                                                }
                                            }
                                        }
                                        ListView {
                                            id: columnsList; objectName: "columnRows"
                                            width: parent.width; height: parent.height - headers.height - 12
                                            clip: true; model: libraryModel; keyNavigationEnabled: true
                                            ScrollBar.vertical: DazeScrollBar { x: columnFlick.width - width }
                                            onCurrentIndexChanged: { if (activeFocus && currentIndex >= 0) root.selectGame(libraryModel.idAt(currentIndex)) }
                                            delegate: Rectangle {
                                                id: tableRow
                                                required property string workId
                                                required property string title
                                                required property string sources
                                                required property string platforms
                                                required property string stateLabel
                                                required property int variantCount
                                                required property int index
                                                property var cells: [title, sources, platforms, stateLabel, String(variantCount)]
                                                width: columnsList.width; height: 30
                                                color: root.selectedId === workId ? Theme.main : rowMouse.containsMouse ? Theme.panel : index % 2 ? Theme.panel : Theme.background
                                                Row {
                                                    Repeater {
                                                        model: tableRow.cells
                                                        delegate: Text { required property string modelData; required property int index; width: root.columnWidths[index]; height: 30; leftPadding: 10; rightPadding: 8; text: modelData; textFormat: Text.PlainText; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; color: Theme.text; font.family: Theme.family; font.pixelSize: 13 }
                                                    }
                                                }
                                                MouseArea { id: rowMouse; anchors.fill: parent; hoverEnabled: true; onClicked: { root.selectGame(tableRow.workId); columnsList.forceActiveFocus() } onDoubleClicked: { root.selectGame(tableRow.workId); root.setView("details") } }
                                                PlainToolTip { objectName: "listToolTip"; visible: rowMouse.containsMouse && !headlessCheck; text: title + "\n" + stateLabel + "\n" + workId }
                                            }
                                        }
                                    }
                                }
                                Text {
                                    anchors.centerIn: parent
                                    visible: libraryModel.visibleCount === 0
                                    text: "No games match these filters."
                                    color: Theme.muted; font.family: Theme.family; font.pixelSize: 14
                                }
                            }
                            DetailsPane {
                                id: detailsPane; objectName: "gameDetails"
                                onAchievementsRequested: function(key) { achievementsDashboard.selectedGame = key; achievementsDashboard.open() }
                                onInstallRequested: function(key) { if (typeof gameInstalls !== "undefined" && gameInstalls) gameInstalls.install(key) }
                                visible: root.viewMode === "details" || root.inspectorVisible
                                SplitView.preferredWidth: 300; SplitView.minimumWidth: 285
                                SplitView.fillWidth: root.viewMode === "details"
                                game: root.selectedGame
                                compactMode: root.viewMode !== "details"
                                onCloseRequested: { root.inspectorVisible = false; if (root.viewMode === "details") root.setView("grid") }
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            visible: root.organizerMode
                            RowLayout {
                                Layout.fillWidth: true
                                SocialButton { text: "Review account / duplicate links"; onClicked: { if (!root.linkManager.storeEnabled) root.linkManager.createLinkState(root.linkStatePathValue, true); else linkDialog.open() } }
                                Text { Layout.fillWidth: true; text: root.linkManager.status; color: Theme.muted; textFormat: Text.PlainText; elide: Text.ElideRight }
                            }
                            OrganizerWorkspace {
                                Layout.fillWidth: true; Layout.fillHeight: true; Layout.margins: 12
                                initialGameId: root.selectedId
                                baselineReport: organizerNotice
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true; Layout.preferredHeight: 32
                            visible: root.viewMode === "grid" && !root.organizerMode
                            Layout.leftMargin: 12; Layout.rightMargin: 12
                            Text { text: "Zoom"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                            Slider { objectName: "coverZoom"; Layout.preferredWidth: 160; from: 96; to: 184; stepSize: 2; value: root.coverWidth; onMoved: root.coverWidth = value; Accessible.name: "Cover size" }
                            Text { text: root.coverWidth + " px"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 25
                color: Theme.panel
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
                    Text { text: (libraryModel.organizerEnabled ? "Local Organizer enabled · " : "Read-only · ") + catalogLabel; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                    Text { text: "• " + (root.organizerMode ? libraryModel.totalCount + " reviewed" : libraryModel.visibleCount + " games"); color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                    Item { Layout.fillWidth: true }
                    Text { text: root.selectedId ? (root.selectedGame.title || "") : "No selection"; textFormat: Text.PlainText; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; elide: Text.ElideRight; Layout.maximumWidth: 240 }
                }
            }
        }
            }
        }
    }
    MenuOverviewDashboard {
        id: managementOverview; parent: workspaceContainer; routed: true; pageId: "overview"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack()
        onDrillRequested: function(section, key, label) {
            if (section === "catalogs" && key === "emulation") {
                managementOverview.openSection("catalog-systems")
                return
            }
            if (section === "connections") {
                root.nativeBrowseLabel = label
                nativeProviderChoice.currentIndex = Math.max(0, root.connectionService.providerKeys.indexOf(key))
                nativeJoinChoice.currentIndex = 0
                nativeConnectionSearch.text = ""
                root.updateNativeBrowser()
                connectionDialog.open()
            } else if (section === "roms") {
                root.romBrowsePlatform = key
                romDialog.open()
            } else if (key === "control") {
                root.organizerMode = true
            } else {
                root.catalogBrowseLabel = label
                var storeKey = root.nativeService && root.nativeService.storeCatalogKey ? root.nativeService.storeCatalogKey : ""
                var isStoreCatalog = storeKey.length > 0 && key === storeKey
                nativePage.currentIndex = isStoreCatalog ? 0 : 1
                if (isStoreCatalog) { nativeCatalogSearch.text = ""; root.nativeService.search("") }
                else {
                    retroSearchField.text = ""
                    root.nativeService.searchRetro("")
                    retroPlatformChoice.currentIndex = Math.max(0, root.nativeService.retroPlatforms.indexOf(key.substring(10)))
                    root.nativeService.setRetroPlatform(key.indexOf("emulation:") === 0 ? key.substring(10) : "All platforms")
                }
                nativeDialog.open()
            }
        }
        onActionRequested: function(section) {
            if (section === "connections") { if (root.refreshService && !root.refreshService.busy) root.refreshService.refresh() }
            else if (section === "accounts") { root.openFriendsPlatforms() }
            else if (section === "roms") { if (typeof launchView !== "undefined" && launchView && !launchView.romScannerBusy) launchView.scanRoms() }
            else if (section === "profiles") { emulationDashboard.open() }
            else { downloadsDashboard.open() }
        }
    }
    EmulationDashboard { id: emulationDashboard; parent: workspaceContainer; routed: true; pageId: "emulation"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack() }
    SaveManagementDashboard {
        id: saveDashboard; parent: workspaceContainer; routed: true; pageId: "saves"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack()
    }
    ConsoleLibrary {
        id: consoleView; anchors.fill: parent; z: 100; visible: root.consoleMode
        gameModel: libraryModel; game: root.selectedGame; selectedId: root.selectedId
        installations: root.selectedLaunchOptions()
        launchStatus: typeof launchView !== "undefined" && launchView ? launchView.lastError || launchView.launchStatus : "Launch unavailable"
        onSelected: function(id) { root.selectGame(id) }
        onDesktopRequested: root.toggleConsole()
        onSocialRequested: socialDashboard.open()
        onEmulationRequested: managementOverview.openSection("roms")
        onControllersRequested: controllerDashboard.open()
        onPlayRequested: function(rec) {
            if (typeof launchView !== "undefined" && launchView && rec) {
                if (rec.rawRecord) {
                    launchView.setTarget(rec.rawRecord.variant_key, {canonical_game_id:rec.rawRecord.canonical_game_id || root.selectedId, variant_key:rec.rawRecord.variant_key, product_id:rec.rawRecord.product_id, provider:rec.rawRecord.provider, title:rec.rawRecord.title, state:rec.rawRecord.state})
                } else if (rec.variant_key) {
                    launchView.setTarget(rec.variant_key, {canonical_game_id:rec.canonical_game_id || root.selectedId, variant_key:rec.variant_key, product_id:rec.product_id, provider:rec.provider, title:rec.title, state:rec.state})
                }
                launchView.play()
            } else if (typeof launchView !== "undefined" && launchView && root.selectedId) {
                launchView.setTarget(root.selectedId, {canonical_game_id:root.selectedId, variant_key:root.selectedId, title:(root.selectedGame ? root.selectedGame.title : "")})
                launchView.play()
            }
        }
    }
    AssistantDirector {
        id: assistantDirector
        hostWindow: root
        gameModel: libraryModel
        launchView: root.launchViewRef
        desktopGrid: grid
        desktopDetailsList: compactList
        desktopColumnList: columnsList
        consoleLibrary: consoleView
        playButton: root.consoleMode ? consoleView.assistantPlayButton : detailsPane.assistantPlayButton
        animationEnabled: root.assistantAnimationEnabled
        speed: root.assistantAnimationSpeed
        pace: typeof assistantPreferences !== "undefined" && assistantPreferences ? assistantPreferences.pace : 1.0
        onAssistantBeat: function(kind) { root.assistantBeat(kind) }
    }
    Binding { target: detailsPane; property: "assistantPressed"; value: !root.consoleMode && assistantDirector.pressed }
    Binding { target: consoleView; property: "assistantPressed"; value: root.consoleMode && assistantDirector.pressed }
    Connections {
        target: typeof controllerNavigation !== "undefined" ? controllerNavigation : null
        function onAction(_action) { assistantDirector.userInputCancel() }
    }
    DesktopOverlay {
        id: partyOverlay
        controllerProfilePicker: Component {
            ColumnLayout {
                spacing: 8
                ControllerProfilePicker {
                    objectName: "overlayControllerProfilePicker"
                    Layout.fillWidth: true
                    compact: true
                    gameId: typeof controllerProfiles !== "undefined" && controllerProfiles ? controllerProfiles.runtimeGameId : ""
                }
                VirtualPadPicker {
                    objectName: "overlayVirtualPadPicker"
                    Layout.fillWidth: true
                    compact: true
                    gameId: typeof controllerProfiles !== "undefined" && controllerProfiles ? controllerProfiles.runtimeGameId : ""
                    gameTitle: typeof launchView !== "undefined" && launchView ? (launchView.selectedTitle || "") : ""
                }
            }
        }
        socialView: typeof xodusSocialView !== "undefined" ? xodusSocialView : null
        voiceView: typeof xboxVoiceMedia !== "undefined" ? xboxVoiceMedia : null
        currentGame: typeof launchView !== "undefined" && launchView.activeGame && launchView.activeGame.state === "running" ? ({title:launchView.activeGame.title,platform:"Tracked native session",playtime:""}) : ({})
        onReturnToGameRequested: {
            if (root.overlayFromConsole && root.consoleMode) {
                root.raise(); root.requestActivate(); consoleView.forceActiveFocus()
            }
            root.overlayFromConsole = false
        }
        onDownloadsRequested: { root.showNormal(); root.raise(); root.requestActivate(); downloadsDashboard.open() }
        onSettingsRequested: { root.showNormal(); root.raise(); root.requestActivate(); root.openSettings() }
        onActiveChanged: root.refreshControllerRouting()
        onVisibleChanged: root.refreshControllerRouting()
        onControllersRequested: { root.showNormal(); root.raise(); root.requestActivate(); controllerDashboard.open() }
        onLibraryRequested: { root.showLibraryView(); if (root.consoleMode) root.toggleConsole(); root.showNormal(); root.raise(); root.requestActivate() }
        onConnectionsRequested: { root.show(); root.requestActivate(); managementOverview.openSection("connections") }
    }
    AchievementsDashboard {
        id: achievementsDashboard; parent: workspaceContainer; routed: true; pageId: "achievements"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack()
        onClosed: if (typeof platformSocial !== "undefined") platformSocial.selectGame(detailsPane.editTargetId || root.selectedId)
    }
    Connections { target: achievementsDashboard; function onVisibleChanged() { root.refreshControllerRouting() } }
    Component { id: nativeControllers; NativeControllerStudio { routed: true; pageId: "controllers"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack() } }
    Component { id: embeddedControllers; ControllerStudio { routed: true; pageId: "controllers"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack() } }
    Loader {
        id: storefrontDashboard; objectName: "storefrontWorkspaceLoader"
        parent: workspaceContainer; anchors.fill: parent
        active: root.activePage === "store"
        visible: active
        sourceComponent: Component {
            StorefrontDashboard {
                viewModel: typeof priceComparison !== "undefined" ? priceComparison : null
                routed: true
                pageId: "store"
                currentPage: root.activePage
                onBackRequested: root.goBack()
                onAccountsRequested: root.openFriendsPlatforms()
            }
        }
        onVisibleChanged: root.refreshControllerRouting()
    }
    Loader {
        id: modDashboard; objectName: "modWorkspaceLoader"
        parent: workspaceContainer; anchors.fill: parent
        active: root.activePage === "mods"
        visible: active
        sourceComponent: Component {
            ModManagementPage { viewModel: typeof modManagement !== "undefined" ? modManagement : null }
        }
        onVisibleChanged: root.refreshControllerRouting()
    }
    Loader {
        id: controllerDashboard
        parent: workspaceContainer; anchors.fill: parent
        // Built on the first visit, then kept: rebuilding it cost ~0.6-0.75 s on EVERY open. While hidden the
        // page's own lifecycle (WorkspacePage closed()) releases live polling, so nothing runs in the background.
        property bool visited: false
        active: visited || root.activePage === "controllers"
        // Built in small steps across frames, so neither the idle pre-build nor a first open freezes the window.
        asynchronous: true
        visible: root.activePage === "controllers"
        sourceComponent: typeof nativeControllerEnabled !== "undefined" && nativeControllerEnabled ? nativeControllers : embeddedControllers
        // Navigate first: an inactive Loader has no item to open.
        function open() { root.navigatePage("controllers") }
        function close() { if (item) item.close() }
    }
    Connections { target: root; function onActivePageChanged() { if (root.activePage === "controllers") controllerDashboard.visited = true } }
    Timer {
        // Pre-build Controllers once the window has settled (skipped in headless checks).
        interval: 4000; running: !(typeof headlessCheck !== "undefined" && headlessCheck); repeat: false
        onTriggered: controllerDashboard.visited = true
    }
    Connections { target: controllerDashboard.item; function onVisibleChanged() { root.refreshControllerRouting() } }
    Connections { target: socialDashboard; function onVisibleChanged() { root.refreshControllerRouting() } }
    Connections { target: managementOverview; function onVisibleChanged() { root.refreshControllerRouting() } }
    Connections { target: saveDashboard; function onVisibleChanged() { root.refreshControllerRouting() } }
    Connections { target: downloadsDashboard; function onVisibleChanged() { root.refreshControllerRouting() } }
    Connections { target: emulationDashboard; function onVisibleChanged() { root.refreshControllerRouting() } }
    SocialDashboard { id: socialDashboard; parent: workspaceContainer; routed: true; pageId: "friends"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack(); onConnectionsRequested: root.openFriendsPlatforms() }
    function openDownloads() { downloadsDashboard.open() }
    GameInstallPrompt { parent: Overlay.overlay }
    DownloadsDashboard { id: downloadsDashboard; parent: workspaceContainer; routed: true; pageId: "downloads"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack() }
    SettingsDashboard { id: settingsDashboard; parent: workspaceContainer; routed: true; pageId: "settings"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack() }
    Connections { target: settingsDashboard; function onVisibleChanged() { root.refreshControllerRouting() } }
    Loader {
        objectName: "voiceMediaLoader"
        width: 1; height: 1
        active: typeof xboxVoiceMedia !== "undefined" && xboxVoiceMedia && xboxVoiceMedia.active
        source: "VoiceMediaPane.qml"
    }
    WorkspacePage {
        id: romDialog; parent: workspaceContainer; routed: true; pageId: "roms"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack(); objectName: "romInventoryDialog"
        title: "ROM library / " + root.romBrowsePlatform
        background: Rectangle { color: Theme.popup; border.color: Theme.line }
        ColumnLayout {
            anchors.fill: parent; spacing: 8
            CompactButton { implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding; objectName: "romOverviewBack"; text: "‹ All systems"; onClicked: { managementOverview.openSection("roms") } }
            Text {
                Layout.fillWidth: true
                text: "Explicit local ROM scanning. Roots: " + (typeof launchView !== "undefined" && launchView ? (launchView.romRoots || []).join(", ") : "None configured")
                color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                CompactButton {
                    objectName: "scanRomsButton"
                    Layout.preferredWidth: 140
                    text: (typeof launchView !== "undefined" && launchView && launchView.romScannerBusy) ? "Scanning…" : "Scan ROM Roots"
                    enabled: typeof launchView !== "undefined" && launchView && !launchView.romScannerBusy
                    hint: "Scan explicit ROM roots on demand; never scans during application startup"
                    onClicked: if (typeof launchView !== "undefined" && launchView) launchView.scanRoms()
                }
                Text {
                    Layout.fillWidth: true
                    text: (typeof launchView !== "undefined" && launchView ? launchView.romCount : 0) + " ROMs discovered"
                    color: Theme.text; font.pixelSize: 12
                }
            }
            ListView {
                id: romList; objectName: "romListView"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 3
                model: typeof launchView !== "undefined" && launchView ? launchView.romRecords.filter(function(row) { return row.platform === root.romBrowsePlatform }) : []
                ScrollBar.vertical: DazeScrollBar {}
                delegate: Rectangle {
                    required property var modelData
                    width: romList.width; height: 50
                    color: (typeof launchView !== "undefined" && launchView && launchView.selectedRomId === modelData.rom_id) ? Theme.main : Theme.panel
                    radius: Theme.cornerRadius
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8
                        Column {
                            Layout.fillWidth: true
                            Text { width: parent.width; text: modelData.file_name; color: Theme.text; font.pixelSize: 13; elide: Text.ElideRight }
                            Text { width: parent.width; text: modelData.platform + " · Local file present · " + (modelData.launchable ? "Launchable" : (modelData.unlaunchable_reason || "Unverified")) + " · " + (modelData.emulator_name || "None"); color: Theme.muted; font.pixelSize: 11; elide: Text.ElideRight }
                        }
                        CompactButton {
                            Layout.preferredWidth: 70
                            text: "Select"
                            onClicked: if (typeof launchView !== "undefined" && launchView) launchView.selectRom(modelData.rom_id)
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                CompactButton {
                    objectName: "romPlayButton"
                    Layout.preferredWidth: 90
                    text: (typeof launchView !== "undefined" && launchView && launchView.gameState === "running") ? "Running" : (typeof launchView !== "undefined" && launchView ? launchView.playLabel : "Play ROM")
                    enabled: typeof launchView !== "undefined" && launchView && !!launchView.selectedRomId && launchView.romRecords.some(function(row) { return row.rom_id === launchView.selectedRomId && row.platform === root.romBrowsePlatform })
                    hint: "Launch selected ROM with verified emulator profile; never executes shell"
                    onClicked: if (typeof launchView !== "undefined" && launchView) { launchView.selectRom(launchView.selectedRomId); launchView.play() }
                }
                Text {
                    objectName: "romPlayStatus"
                    Layout.fillWidth: true
                    text: typeof launchView !== "undefined" && launchView ? (launchView.lastError.length > 0 ? ("Error: " + launchView.lastError) : (launchView.launchStatus === "handed_off" ? ("PID " + launchView.lastPid) : "")) : ""
                    color: typeof launchView !== "undefined" && launchView && launchView.lastError.length > 0 ? Theme.text : Theme.muted
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
            }
        }
    }
    Dialog {
        id: linkDialog; objectName: "accountLinkReviewDialog"
        title: "Review duplicate account/source records"
        anchors.centerIn: parent; width: Math.min(root.width - 80, 900); height: Math.min(root.height - 70, 680)
        modal: true; standardButtons: Dialog.Close
        background: Rectangle { color: Theme.popup; border.color: Theme.line }
        ColumnLayout {
            anchors.fill: parent; spacing: 8
            Text { Layout.fillWidth: true; text: "Candidates are suggestions, not duplicates. Review title, source/account and platform evidence. Apply keeps every source record and can be undone."; wrapMode: Text.WordWrap; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
            Text { objectName: "accountLinkHistory"; Layout.fillWidth: true; text: root.linkManager.latestAction; color: Theme.glyph; font.family: Theme.family; font.pixelSize: 12; textFormat: Text.PlainText }
            TextField {
                objectName: "accountLinkSearch"; Layout.fillWidth: true
                text: root.linkSearch; onTextEdited: root.linkSearch = text
                placeholderText: "Search title or source/account…"; color: Theme.text; placeholderTextColor: Theme.muted
                background: Rectangle { color: Theme.background; border.color: Theme.line; radius: Theme.cornerRadius }
            }
            ListView {
                id: linkRows; objectName: "accountLinkCandidates"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 3
                model: root.visibleLinkCandidates(); ScrollBar.vertical: DazeScrollBar {}
                delegate: Rectangle {
                    required property var modelData
                    width: linkRows.width; height: 132
                    color: modelData.selected ? Theme.main : Theme.background; radius: Theme.cornerRadius
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 6; spacing: 8
                        CheckBox {
                            objectName: "candidateSelect"; checked: root.selectedLinkCandidates.indexOf(modelData.candidateId) >= 0
                            enabled: !modelData.blocked; Accessible.name: "Select duplicate candidate"
                            onToggled: root.chooseLinkCandidate(modelData.candidateId, checked)
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 2
                            Repeater {
                                model: modelData.records
                                delegate: Text {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    text: modelData.title + " — Source/account: " + modelData.sources.join(" / ") +
                                          " — Platform: " + (modelData.platforms.length ? modelData.platforms.join(" / ") : "Unknown") +
                                          " — Edition: " + (modelData.editions.length ? modelData.editions.join(" / ") : "Unknown (not supplied)")
                                    color: Theme.text; textFormat: Text.PlainText; font.family: Theme.family; font.pixelSize: 12
                                    wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                text: "Evidence: " + modelData.evidence.join(" · ") +
                                      (modelData.blocked ? (modelData.linked ? " · Already linked" : " · Conflict — not linkable") : " · " + modelData.confidence)
                                color: modelData.blocked ? Theme.muted : Theme.glyph; textFormat: Text.PlainText; font.family: Theme.family; font.pixelSize: 11
                                wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                            }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Text { Layout.fillWidth: true; text: root.linkManager.error.length ? root.linkManager.error : root.linkManager.previewText; color: root.linkManager.error.length ? Theme.glyph : Theme.muted; font.family: Theme.family; font.pixelSize: 12; elide: Text.ElideRight }
                CompactButton {
                    objectName: "previewAccountLinksButton"; Layout.preferredWidth: 110; text: "Preview links"; enabled: selectedLinkCandidates.length > 0
                    onClicked: { root.linkManager.selectCandidates(root.selectedLinkCandidates); root.linkManager.preview(root.selectedLinkCandidates); root.linkPreviewReady = root.linkManager.previewText.length > 0 }
                }
                CompactButton {
                    objectName: "applyAccountLinksButton"; Layout.preferredWidth: 165; text: "Apply reviewed links"; enabled: root.linkPreviewReady
                    onClicked: {
                        let result = root.linkManager.apply("ui-link-" + Date.now())
                        if (result.length) { root.latestLinkAuditId = JSON.parse(result).audit_id; root.linkPreviewReady = false; root.selectedLinkCandidates = [] }
                    }
                }
                CompactButton {
                    objectName: "undoAccountLinksButton"; Layout.preferredWidth: 125; text: "Undo latest link"; enabled: root.latestLinkAuditId > 0
                    onClicked: { let result = root.linkManager.undo(root.latestLinkAuditId, "ui-undo-" + Date.now()); if (result.length) { root.latestLinkAuditId = 0; root.selectedLinkCandidates = [] } }
                }
            }
        }
    }
    Connections {
        target: typeof launcherLibrary !== "undefined" ? launcherLibrary : null
        function onRefreshed() {
            if (launcherLibrary && launcherLibrary.lastRefreshSucceeded && firstRunGuide)
                firstRunGuide.openOnceAfterSync()
        }
    }
    Dialog {
        id: firstRunDialog; objectName: "firstRunLibraryGuide"
        title: "Set up your Waystone library"
        anchors.centerIn: parent; width: Math.min(root.width - 48, 760); height: Math.min(root.height - 48, 680)
        modal: true; standardButtons: Dialog.NoButton
        visible: typeof firstRunGuide !== "undefined" && firstRunGuide !== null && firstRunGuide.visible
        onRejected: if (firstRunGuide) firstRunGuide.dismiss()
        background: Rectangle { color: Theme.popup; border.color: Theme.line }
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 18; spacing: 12
            Text { Layout.fillWidth: true; text: "Your library is built from local launcher data. Installed status comes from this device; account libraries depend on the accounts you choose to connect."; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13; wrapMode: Text.WordWrap }
            Text { text: "Found on this device"; color: Theme.text; font.family: Theme.family; font.bold: true; font.pixelSize: 16 }
            ListView {
                objectName: "firstRunSourceCounts"
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(contentHeight, 160); clip: true; spacing: 2
                model: typeof launcherLibrary !== "undefined" && launcherLibrary ? launcherLibrary.sourceCounts : []
                delegate: RowLayout {
                    required property var modelData
                    width: ListView.view.width
                    Text { Layout.fillWidth: true; text: modelData.provider; color: Theme.text; font.family: Theme.family; font.pixelSize: 13 }
                    Text { text: String(modelData.count) + (modelData.count === 1 ? " game" : " games"); color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
            Text { text: "Optional next steps"; color: Theme.text; font.family: Theme.family; font.bold: true; font.pixelSize: 16 }
            Text { Layout.fillWidth: true; text: typeof steamLibrarySettings !== "undefined" && steamLibrarySettings && !steamLibrarySettings.configured ? "• Add Steam Web API details to include owned games that are not installed here." : "• Steam library account is connected; installed games are still determined locally."; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap }
            Text { Layout.fillWidth: true; text: typeof ribbonArtworkSettings !== "undefined" && ribbonArtworkSettings && !ribbonArtworkSettings.keyConfigured ? "• SteamGridDB key is optional; Steam, Epic and GOG artwork sources are checked on demand." : "• SteamGridDB artwork fallback is configured."; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap }
            Text { Layout.fillWidth: true; text: typeof launcherLibrary !== "undefined" && launcherLibrary && launcherLibrary.sourceStates.Emulation && Number(launcherLibrary.sourceStates.Emulation.configured_roots) === 0 ? "• Add ROM folders from Emulation to include your existing ROM collection." : "• ROM folders are configured; Waystone only scans those selected folders."; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap }
            Text { Layout.fillWidth: true; text: "• Connect Epic, GOG or Amazon in Accounts to import their cached owned-game libraries."; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap }
            Text { objectName: "firstRunVirtualPadGuidance"; Layout.fillWidth: true; text: typeof firstRunGuide !== "undefined" && firstRunGuide ? firstRunGuide.virtualPadGuidance : "• Set up virtual-controller access in Controllers if /dev/uinput or /dev/uhid is unavailable."; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap }
            Text { Layout.fillWidth: true; visible: typeof firstRunGuide !== "undefined" && !!(firstRunGuide && firstRunGuide.status); text: typeof firstRunGuide !== "undefined" && firstRunGuide ? firstRunGuide.status : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                CompactButton { objectName: "firstRunAccounts"; text: "Accounts"; onClicked: { firstRunGuide.dismiss(); settingsDashboard.selectedCategory = 0; root.openSettings() } }
                CompactButton { objectName: "firstRunRoms"; text: "ROM folders"; onClicked: { firstRunGuide.dismiss(); root.navigatePage("emulation") } }
                CompactButton { objectName: "firstRunControllers"; text: "Controllers"; onClicked: { firstRunGuide.dismiss(); root.openControllers() } }
                CompactButton {
                    objectName: "firstRunInstallUdevRule"
                    visible: typeof firstRunGuide !== "undefined" && firstRunGuide !== null && !firstRunGuide.virtualPadAccessReady
                    text: "Install udev rule (pkexec)"
                    hint: "Install packaging/arch/70-waystone-input.rules to /etc/udev/rules.d/ and reload udev"
                    onClicked: if (firstRunGuide) firstRunGuide.installUdevRule()
                }
                Item { Layout.fillWidth: true }
                CompactButton { objectName: "finishFirstRunGuide"; text: "Done"; onClicked: firstRunGuide.dismiss() }
            }
        }
    }
    Dialog {
        id: snapshotDialog
        title: "Snapshot information"
        anchors.centerIn: parent
        width: Math.min(root.width - 80, 760); height: Math.min(root.height - 80, 430)
        modal: true; standardButtons: Dialog.Close
        background: Rectangle { color: Theme.popup; border.color: Theme.line }
        ScrollView {
            anchors.fill: parent; clip: true; contentWidth: availableWidth
            TextArea { width: parent.width; text: catalogNotice; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: Theme.text; font.family: Theme.family; font.pixelSize: 13; background: null }
        }
    }
    WorkspacePage {
        id: nativeDialog; parent: workspaceContainer; routed: true; pageId: "catalogs"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack(); objectName: "nativeProvidersDialog"
        title: "Native catalogs / " + root.catalogBrowseLabel
        background: Rectangle { color: Theme.popup; border.color: Theme.line }
        ColumnLayout {
            anchors.fill: parent; spacing: 8
            CompactButton { implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding; objectName: "catalogOverviewBack"; text: nativePage.currentIndex === 1 ? "‹ All systems" : "‹ All catalogs"; onClicked: { managementOverview.openSection(nativePage.currentIndex === 1 ? "catalog-systems" : "catalogs") } }
            CompactCombo {
                id: nativePage; objectName: "nativeServiceSelector"; visible: false; Layout.fillWidth: true
                model: root.nativeService && root.nativeService.serviceTabs ? root.nativeService.serviceTabs : ["Store catalog — not owned library", "Emulation — game catalog", "Native read-only control API — no AI model configured"]
                Accessible.name: "Native service view"
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; visible: nativePage.currentIndex === 0
                ScrollView {
                    Layout.fillWidth: true; Layout.preferredHeight: 120; clip: true; contentWidth: availableWidth
                    TextArea { width: parent.width; text: root.nativeService.notice; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: Theme.muted; font.pixelSize: 12; background: null }
                }
                TextField {
                    id: nativeCatalogSearch; objectName: "nativeCatalogSearch"; Layout.fillWidth: true
                    placeholderText: "Search cached store names or native source keys (separate from library)"
                    color: Theme.text; selectByMouse: true
                    onTextEdited: root.nativeService.search(text)
                    background: Rectangle { color: Theme.background; border.color: Theme.line }
                }
                ListView {
                    id: nativeRows; objectName: "nativeCatalogRows"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    model: root.nativeService.rows
                    ScrollBar.vertical: DazeScrollBar {}
                    delegate: ItemDelegate {
                        required property var modelData
                        width: nativeRows.width; height: 40
                        contentItem: Text { text: modelData.name + "  ·  " + modelData.sourceKey; textFormat: Text.PlainText; color: Theme.text; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { color: parent.hovered ? Theme.hover : Theme.panel }
                        onClicked: root.nativeService.select(modelData.sourceId)
                        PlainToolTip { visible: parent.hovered && !headlessCheck; text: modelData.name + "\n" + modelData.sourceKey }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    CompactButton { implicitWidth: 85; text: "Previous"; enabled: root.nativeService.canPrevious; onClicked: root.nativeService.turnPage(-1) }
                    Text { text: root.nativeService.pageLabel; textFormat: Text.PlainText; color: Theme.muted; Layout.fillWidth: true }
                    CompactButton { implicitWidth: 65; text: "Next"; enabled: root.nativeService.canNext; onClicked: root.nativeService.turnPage(1) }
                }
                ScrollView {
                    Layout.fillWidth: true; Layout.preferredHeight: 125; clip: true; contentWidth: availableWidth
                    TextArea { width: parent.width; text: root.nativeService.detail || "Select a store record to inspect native identity. Download, install, refresh and launch are unavailable."; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: Theme.text; font.pixelSize: 12; background: null }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; visible: nativePage.currentIndex === 1
                ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 100; clip: true; contentWidth: availableWidth
                    TextArea { width: parent.width; text: root.nativeService.retroNotice; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: Theme.muted; background: null }
                }
                CompactCombo { id: retroPlatformChoice; visible: false; Layout.fillWidth: true; model: root.nativeService.retroPlatforms; Accessible.name: "Emulation catalog platform"; onActivated: root.nativeService.setRetroPlatform(currentText) }

                TextField { id: retroSearchField; Layout.fillWidth: true; maximumLength: 256; placeholderText: "Search native database by title prefix (cache fallback: title/platform)"; color: Theme.text; onTextEdited: root.nativeService.searchRetro(text); background: Rectangle { color: Theme.background; border.color: Theme.line } }
                ListView {
                    id: retroRows; objectName: "retroMigratedRows"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    model: root.nativeService.retroRows; ScrollBar.vertical: DazeScrollBar {}
                    delegate: ItemDelegate {
                        required property var modelData; required property int index
                        width: retroRows.width; height: 40
                        contentItem: Text { text: modelData.title + " · " + modelData.platform; textFormat: Text.PlainText; color: Theme.text; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { color: parent.hovered ? Theme.hover : Theme.panel }
                        onClicked: root.nativeService.selectRetro(index)
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    CompactButton { implicitWidth: 85; text: "Previous"; onClicked: root.nativeService.turnRetroPage(-1) }
                    Text { text: root.nativeService.retroPageLabel; color: Theme.muted; textFormat: Text.PlainText; Layout.fillWidth: true }
                    CompactButton { implicitWidth: 65; text: "Next"; onClicked: root.nativeService.turnRetroPage(1) }
                }
                ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 150; clip: true; contentWidth: availableWidth
                    TextArea { width: parent.width; text: root.nativeService.retroDetail || "Select an entry to inspect original IDs and imported state claims (not validated native installations)."; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: Theme.text; background: null }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; visible: nativePage.currentIndex === 2
                Text { Layout.fillWidth: true; text: "Imported-snapshot baseline only; local Organizer overrides are NOT included in these results. No AI classification/chat, external models, listeners, source writes or launch actions."; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: Theme.muted }
                RowLayout {
                    Layout.fillWidth: true
                    TextField { id: nativeAIQuery; Layout.fillWidth: true; maximumLength: 256; placeholderText: "Optional library title query (blank = review, first 20 records)"; color: Theme.text; background: Rectangle { color: Theme.background; border.color: Theme.line } }
                    CompactButton { implicitWidth: 65; text: "Read"; onClicked: root.nativeService.reviewLibrary(nativeAIQuery.text) }
                }
                ScrollView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                    TextArea { width: parent.width; text: root.nativeService.aiResult; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: Theme.text; font.pixelSize: 12; background: null }
                }
            }
        }
    }

    WorkspacePage {
        id: connectionDialog; parent: workspaceContainer; routed: true; pageId: "connections"; currentPage: root.activePage; onNavigationRequested: function(destination) { root.navigatePage(destination) }; onBackRequested: root.goBack(); objectName: "nativeConnectionsDialog"
        title: "Linux connections / " + root.nativeBrowseLabel
        background: Rectangle { color: Theme.background; border.color: Theme.main }
        contentItem: ColumnLayout {
            spacing: 10
            CompactButton { implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding; objectName: "connectionOverviewBack"; text: "‹ All providers"; onClicked: { managementOverview.openSection("connections") } }
            Text { objectName: "nativeConnectionsNotice"; Layout.fillWidth: true; text: root.connectionService.notice; textFormat: Text.PlainText; color: Theme.muted; wrapMode: Text.WordWrap; font.pixelSize: 12 }
            RowLayout {
                Layout.fillWidth: true
                CompactCombo { id: nativeProviderChoice; objectName: "nativeConnectionProviderChoice"; Layout.preferredWidth: 200; model: root.connectionService.providerLabels; onActivated: root.updateNativeBrowser() }
                CompactCombo { id: nativeJoinChoice; objectName: "nativeConnectionJoinChoice"; Layout.preferredWidth: 160; model: ["All join states", "Exact matched", "Unmatched", "Ambiguous"]; onActivated: root.updateNativeBrowser() }
                TextField { id: nativeConnectionSearch; Layout.fillWidth: true; maximumLength: 256; placeholderText: "Filter native observations by title / exact product-ID text"; color: Theme.text; onTextEdited: root.updateNativeBrowser(); background: Rectangle { color: Theme.panel; border.color: Theme.main } }
            }
            ListView {
                objectName: "nativeConnectionsList"; Layout.fillWidth: true; Layout.fillHeight: true
                ScrollBar.vertical: DazeScrollBar {}
                clip: true; model: root.connectionService.rows; spacing: 1
                delegate: ItemDelegate {
                    required property var modelData
                    width: ListView.view.width; height: 63
                    background: Rectangle { color: parent.hovered ? Theme.hover : Theme.panel }
                    contentItem: ColumnLayout {
                        Text { Layout.fillWidth: true; text: modelData.title + " · " + modelData.provider; textFormat: Text.PlainText; color: Theme.text; elide: Text.ElideRight }
                        Text { Layout.fillWidth: true; text: modelData.productId + " · " + modelData.joinState + " · " + modelData.observation; textFormat: Text.PlainText; color: Theme.muted; elide: Text.ElideRight; font.pixelSize: 12 }
                    }
                    onClicked: root.connectionService.select(modelData.variantKey)
                }
            }
            RowLayout {
                Layout.fillWidth: true
                CompactButton { Layout.preferredWidth: 85; text: "Previous"; enabled: root.connectionService.canPrevious; onClicked: root.connectionService.page(-1) }
                Text { Layout.fillWidth: true; text: root.connectionService.pageLabel; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12 }
                CompactButton { Layout.preferredWidth: 65; text: "Next"; enabled: root.connectionService.canNext; onClicked: root.connectionService.page(1) }
            }
            ScrollView {
                Layout.fillWidth: true; Layout.preferredHeight: 190; clip: true; contentWidth: availableWidth
                TextArea { objectName: "nativeConnectionDetail"; width: parent.width; text: root.connectionService.detail || "Select an observation for original IDs, explicit host/time and verification scope. Unmatched items are retained, never title-merged. No launch action exists."; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: Theme.text; font.pixelSize: 12; background: null }
            }
        }
    }

}
