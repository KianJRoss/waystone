import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "controller_native" as Native

FocusScope {
    id: consoleRoot
    objectName: "consoleLibrary"
    focus: visible

    property var gameModel: null
    property var game: ({})
    property string selectedId: ""
    property var installations: []
    property bool canPlay: false
    property string launchStatus: ""
    // Narrow UI automation surface for AssistantDirector
    property alias assistantGrid: cards
    property alias assistantPlayButton: consolePlayBtn
    property bool assistantPressed: false

    signal selected(string id)
    signal playRequested(var record)
    signal desktopRequested()
    signal socialRequested()
    signal emulationRequested()
    signal controllersRequested()

    // ------------------------------------------------------------------
    // Top-level navigation state
    // ------------------------------------------------------------------
    readonly property var tabNames: ["Home", "Library", "Systems", "Friends", "Controllers", "Settings"]
    property int currentTab: 0
    property string subView: ""          // "" | "system" | "game"
    property string subViewReturnTab: "0"
    property bool guideOpen: false
    property bool bindWizardOpen: false
    property bool stickTuningOpen: false
    property bool updatePromptOpen: false
    property string updatePromptGameId: ""
    property string updatePromptTitle: ""
    property bool liveControllerTestOpen: false
    property bool librarySearchOpen: false

    // Focus tracking: focusId identifies the currently focused interactive element
    property string focusId: "homeHeroPlay"
    property string focusZone: "stage"   // "stage" | "libraryGrid" | "systemRomList" | "guide"

    // Hero & Game Page state
    property string heroGameId: ""
    property var heroOverview: ({})
    // Ribbon art (W18): ask the library for a game's hero + logo when it is shown; refresh when it arrives.
    function requestRibbon(id) { if (id && gameModel && gameModel.requestRibbon) gameModel.requestRibbon(String(id)) }
    onHeroOverviewChanged: requestRibbon(heroOverview ? heroOverview.id : "")
    onDetailOverviewChanged: requestRibbon(detailOverview ? detailOverview.id : "")
    Connections {
        target: consoleRoot.gameModel
        ignoreUnknownSignals: true
        function onArtworkUpdated(workId) {
            if (consoleRoot.heroOverview && consoleRoot.heroOverview.id === workId)
                consoleRoot.heroOverview = consoleRoot.gameModel.overview(workId) || ({})
            if (consoleRoot.detailOverview && consoleRoot.detailOverview.id === workId)
                consoleRoot.detailOverview = consoleRoot.gameModel.overview(workId) || ({})
        }
        function onUpdatesChanged() {
            if (consoleRoot.heroGameId && consoleRoot.gameModel)
                consoleRoot.heroOverview = consoleRoot.gameModel.overview(consoleRoot.heroGameId) || ({})
            if (consoleRoot.detailGameId && consoleRoot.gameModel)
                consoleRoot.detailOverview = consoleRoot.gameModel.overview(consoleRoot.detailGameId) || ({})
        }
    }
    property string detailGameId: ""
    property var detailOverview: ({})
    property int selectedVariantIndex: 0
    property int pinnedRevision: 0
    readonly property int metaRev: (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.metaRevision : 0
    readonly property int updateRev: (typeof gameInstalls !== "undefined" && gameInstalls) ? gameInstalls.updateRevision : 0

    function updateInfoFor(gid) {
        var rev = consoleRoot.updateRev
        var key = String(gid || "")
        if (!key) return ({})
        if (typeof gameInstalls !== "undefined" && gameInstalls && typeof gameInstalls.updateInfoFor === "function") {
            var info = gameInstalls.updateInfoFor(key)
            if (info && info.productId) return info
        }
        return ({})
    }

    function gameHasUpdate(gid) {
        var info = updateInfoFor(gid)
        return !!info && !!info.productId
    }

    function gameRequiresUpdatePrompt(gid) {
        var info = updateInfoFor(gid)
        return !!info && !!info.productId && !!info.blocksLaunch
    }

    function triggerUpdateForGame(gid) {
        var key = String(gid || consoleRoot.detailGameId || consoleRoot.selectedId || consoleRoot.heroGameId || "")
        if (!key || typeof gameInstalls === "undefined" || !gameInstalls) return false
        var info = updateInfoFor(key)
        if (info && info.isSteamPending && typeof gameInstalls.openSteamDownloads === "function") {
            consoleRoot.playSound("select")
            consoleRoot.showToast("Opening Steam downloads")
            return gameInstalls.openSteamDownloads()
        }
        if (typeof gameInstalls.updateGame === "function") {
            var ok = gameInstalls.updateGame(key)
            if (ok) {
                consoleRoot.playSound("select")
                consoleRoot.showToast("Updating " + (info && info.title ? info.title : "game"))
            } else if (gameInstalls.prompt && gameInstalls.prompt.message) {
                consoleRoot.playSound("back")
                consoleRoot.showToast(String(gameInstalls.prompt.message))
            }
            return ok
        }
        return false
    }

    // Library filter & sort state
    property string activeLibraryChip: "All"
    property string activeLibraryLetter: ""
    property int activeSortIndex: 0
    readonly property var sortChoices: [
        { field: "title", desc: false, label: "Title A–Z" },
        { field: "installState", desc: false, label: "Installed first" },
        { field: "sources", desc: false, label: "Store" }
    ]

    // Systems sub-view state
    property string currentSystemId: "n64"
    property var currentSystemInfo: ({})
    property var currentSystemRoms: []
    property int systemRomIndex: 0
    readonly property var selectedRom: (currentSystemRoms && systemRomIndex >= 0 && systemRomIndex < currentSystemRoms.length)
                                       ? currentSystemRoms[systemRomIndex] : ({})

    // Clock & Toast
    property string clockText: "21:40"
    property string toastMessage: ""

    // Artwork URLs for MissingCover (whitelisted in test_desktop.py)
    // Fallbacks in order: heroUrl (ribbon) -> backgroundUrl -> coverUrl -> generated hue gradient
    readonly property url heroCoverUrl: {
        var h = heroOverview && heroOverview.heroUrl ? String(heroOverview.heroUrl) : ""
        var bg = heroOverview && heroOverview.backgroundUrl ? String(heroOverview.backgroundUrl) : ""
        var cov = heroOverview && heroOverview.coverUrl ? String(heroOverview.coverUrl) : ""
        return h.length > 0 ? h : (bg.length > 0 ? bg : cov)
    }
    readonly property url detailCoverUrl: {
        var h = detailOverview && detailOverview.heroUrl ? String(detailOverview.heroUrl) : ""
        var bg = detailOverview && detailOverview.backgroundUrl ? String(detailOverview.backgroundUrl) : ""
        var cov = detailOverview && detailOverview.coverUrl ? String(detailOverview.coverUrl) : ""
        return h.length > 0 ? h : (bg.length > 0 ? bg : cov)
    }
    // Transparent game logo for the ribbon (validated logoUrl role); empty = show the title as text.
    readonly property url heroLogoUrl: heroOverview && heroOverview.logoUrl ? String(heroOverview.logoUrl) : ""
    readonly property url detailLogoUrl: detailOverview && detailOverview.logoUrl ? String(detailOverview.logoUrl) : ""
    readonly property url selectedRomCoverUrl: {
        var cov = selectedRom && selectedRom.coverUrl ? String(selectedRom.coverUrl) : ""
        return cov
    }

    // Active running game helpers
    readonly property bool hasActiveGame: {
        if (typeof launchView !== "undefined" && launchView && launchView.activeGame && launchView.activeGame.state === "running")
            return true
        if (typeof overlayLibrary !== "undefined" && overlayLibrary && overlayLibrary.hasRunningGame)
            return true
        return false
    }
    readonly property string activeGameTitle: {
        if (typeof launchView !== "undefined" && launchView && launchView.activeGame && launchView.activeGame.title)
            return String(launchView.activeGame.title)
        if (typeof overlayLibrary !== "undefined" && overlayLibrary && overlayLibrary.runningGame && overlayLibrary.runningGame.title)
            return String(overlayLibrary.runningGame.title)
        return "Running game"
    }

    // Sound pack label helper
    readonly property string currentSoundPackLabel: {
        if (typeof uiSounds === "undefined" || !uiSounds) return "PS2 — Crystal"
        var cur = String(uiSounds.pack || "ps2")
        var packs = uiSounds.packs || []
        for (var i = 0; i < packs.length; i++) {
            if (packs[i] && packs[i].id === cur) return String(packs[i].name || packs[i].label || cur)
        }
        return cur
    }

    // Recent games for Home shelf (uses overlayLibrary.recentGames when available, falls back to top catalog games)
    property var homeRecentGames: []
    property var homeSystemsList: []
    property var libraryChipsModel: ["All", "Installed", "Steam", "GOG", "Epic", "Xbox", "ROMs"]

    function playSound(eventName) {
        if (typeof uiSounds !== "undefined" && uiSounds) {
            uiSounds.play(eventName)
        }
    }

    function titleHue(titleText) {
        var s = String(titleText || "Waystone")
        var h = 210
        for (var i = 0; i < s.length; i++) {
            h = (h * 31 + s.charCodeAt(i)) % 360
        }
        return h
    }

    onSystemRomIndexChanged: {
        if (systemRomListView && systemRomIndex >= 0 && systemRomIndex < currentSystemRoms.length) {
            systemRomListView.positionViewAtIndex(systemRomIndex, ListView.Contain)
        }
    }

    function showToast(msg) {
        toastMessage = String(msg || "")
        toastTimer.restart()
    }

    function updateClock() {
        var d = new Date()
        var hh = String(d.getHours()).padStart(2, "0")
        var mm = String(d.getMinutes()).padStart(2, "0")
        clockText = hh + ":" + mm
    }

    Timer {
        id: clockTimer
        interval: 15000
        running: consoleRoot.visible
        repeat: true
        triggeredOnStart: true
        onTriggered: consoleRoot.updateClock()
    }

    Timer {
        id: toastTimer
        interval: 2200
        repeat: false
        onTriggered: consoleRoot.toastMessage = ""
    }

    // ------------------------------------------------------------------
    // Data synchronization
    // ------------------------------------------------------------------
    function refreshHomeData() {
        var recents = []
        var seen = {}
        if (typeof overlayLibrary !== "undefined" && overlayLibrary && overlayLibrary.recentGames) {
            var rawRecents = overlayLibrary.recentGames
            for (var i = 0; i < rawRecents.length && recents.length < 8; i++) {
                var r = rawRecents[i]
                if (r && r.id && !seen[r.id]) {
                    seen[r.id] = true
                    var ov = gameModel ? gameModel.overview(r.id) : ({})
                    recents.push({
                        id: String(r.id),
                        title: String(r.title || ov.title || "Game"),
                        sources: String(r.sources || ov.sources || "Library"),
                        lastPlayed: String(r.lastPlayed || ov.last_activity || "Recently"),
                        playtime: String(r.playtime || ov.playtime || ""),
                        coverUrl: String(r.coverUrl || ov.coverUrl || ""),
                        backgroundUrl: String(ov.backgroundUrl || r.coverUrl || "")
                    })
                }
            }
        }
        if (gameModel && recents.length < 8) {
            var count = Math.min(gameModel.visibleCount, 24)
            for (var j = 0; j < count && recents.length < 8; j++) {
                var gid = gameModel.idAt(j)
                if (gid && !seen[gid]) {
                    seen[gid] = true
                    var gov = gameModel.overview(gid)
                    recents.push({
                        id: String(gid),
                        title: String(gov.title || "Game"),
                        sources: String(gov.sources || "Library"),
                        lastPlayed: String(gov.last_activity && gov.last_activity !== "Not supplied" ? gov.last_activity : "Ready to play"),
                        playtime: String(gov.playtime && gov.playtime !== "Not supplied" ? gov.playtime.split(" (")[0] : ""),
                        coverUrl: String(gov.coverUrl || ""),
                        backgroundUrl: String(gov.backgroundUrl || gov.coverUrl || "")
                    })
                }
            }
        }
        homeRecentGames = recents
        for (var ri = 0; ri < recents.length; ri++) requestRibbon(recents[ri].id)

        if (!heroGameId || (gameModel && gameModel.rowOf(heroGameId) < 0 && recents.length > 0)) {
            if (recents.length > 0) heroGameId = recents[0].id
            else if (selectedId) heroGameId = selectedId
            else if (gameModel && gameModel.visibleCount > 0) heroGameId = gameModel.idAt(0)
        }
        if (heroGameId && gameModel) {
            heroOverview = gameModel.overview(heroGameId) || ({})
        } else if (recents.length > 0) {
            heroOverview = recents[0]
        }

        if (typeof bigScreenView !== "undefined" && bigScreenView) {
            var sys = bigScreenView.systems || []
            homeSystemsList = sys.slice(0, 8)
        }

        // Build dynamic store chips prioritizing main stores first
        var chips = ["All", "Installed"]
        var preferredStores = ["Steam", "GOG", "Epic", "Xbox", "Mod Organizer", "Local", "Amazon"]
        if (typeof sourceOptions !== "undefined" && sourceOptions && sourceOptions.length > 1) {
            for (var p = 0; p < preferredStores.length; p++) {
                if (sourceOptions.indexOf(preferredStores[p]) >= 0 && chips.indexOf(preferredStores[p]) < 0) {
                    chips.push(preferredStores[p])
                }
            }
            var excludedChipSources = (typeof excludedConsoleChipSources !== "undefined" && excludedConsoleChipSources) ? excludedConsoleChipSources : []
            for (var s = 0; s < sourceOptions.length && chips.length < 7; s++) {
                var src = String(sourceOptions[s])
                if (src !== "All sources" && src !== "Emulation" && excludedChipSources.indexOf(src) < 0 && chips.indexOf(src) < 0) {
                    chips.push(src)
                }
            }
        } else {
            for (var ds = 0; ds < 4; ds++) chips.push(preferredStores[ds])
        }
        if (chips.indexOf("ROMs") < 0) chips.push("ROMs")
        libraryChipsModel = chips
    }

    function refreshDetailData() {
        var targetId = detailGameId || selectedId || heroGameId
        if (!targetId && gameModel && gameModel.visibleCount > 0) {
            targetId = gameModel.idAt(0)
        }
        if (targetId && gameModel) {
            detailOverview = gameModel.overview(targetId) || ({})
        } else {
            detailOverview = game || ({})
        }
        if (typeof gameProgressView !== "undefined" && gameProgressView && targetId) {
            if (gameProgressView.gameKey !== targetId) {
                gameProgressView.selectGame(targetId)
            }
        }
        if (typeof modManagement !== "undefined" && modManagement && targetId) {
            if (modManagement.selectedGameId !== targetId) {
                modManagement.selectGame(targetId)
            }
        }
    }

    function applyLibraryChip(chipName) {
        activeLibraryChip = chipName
        if (!gameModel) return
        var q = activeLibraryLetter
        if (chipName === "All") {
            gameModel.setFilters(q, "All sources", "All platforms", "All states")
        } else if (chipName === "Installed") {
            gameModel.setFilters(q, "All sources", "All platforms", "installed")
        } else if (chipName === "ROMs") {
            gameModel.setFilters(q, "Emulation", "All platforms", "All states")
        } else {
            gameModel.setFilters(q, chipName, "All platforms", "All states")
        }
        if (cards.count > 0) {
            cards.currentIndex = 0
        }
    }

    function applyLibraryLetter(letterPrefix) {
        activeLibraryLetter = letterPrefix
        applyLibraryChip(activeLibraryChip)
    }

    function cycleLibrarySort() {
        activeSortIndex = (activeSortIndex + 1) % sortChoices.length
        var choice = sortChoices[activeSortIndex]
        if (gameModel) {
            gameModel.setSort(choice.field, choice.desc)
        }
        showToast("Sort: " + choice.label)
        playSound("select")
    }

    function switchTab(newTabIndex) {
        var count = tabNames.length
        var idx = ((newTabIndex % count) + count) % count
        guideOpen = false
        bindWizardOpen = false
        subView = ""
        currentTab = idx
        focusZone = "stage"
        if (idx === 0) {
            focusId = "homeHeroPlay"
        } else if (idx === 1) {
            if (cards.count > 0) {
                focusZone = "libraryGrid"
                if (cards.currentIndex < 0) cards.currentIndex = 0
                focusId = "consoleGrid"
            } else {
                focusId = "libraryChip_0"
            }
        } else if (idx === 2) {
            var sys = (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.systems : []
            focusId = sys.length > 0 ? "systemCard_0" : "systemNeedsSortingCard"
        } else if (idx === 3) {
            focusId = "partyStartRow"
        } else if (idx === 4) {
            focusId = "ctrlProfileRow"
        } else if (idx === 5) {
            focusId = "setSoundEnabledRow"
        }
        playSound("move")
    }

    function openGamePage(gameId) {
        var gid = String(gameId || selectedId || heroGameId || "")
        if (!gid && gameModel && gameModel.visibleCount > 0) gid = gameModel.idAt(0)
        if (!gid) return
        subViewReturnTab = String(currentTab)
        detailGameId = gid
        selectedVariantIndex = 0
        consoleRoot.selected(gid)
        refreshDetailData()
        subView = "game"
        focusZone = "stage"
        focusId = "gamePlayButton"
        playSound("open")
    }

    function openSystemDetail(systemId) {
        var sid = String(systemId || "n64")
        subViewReturnTab = String(currentTab)
        currentSystemId = sid
        if (typeof bigScreenView !== "undefined" && bigScreenView) {
            currentSystemInfo = bigScreenView.systemInfo(sid)
            currentSystemRoms = bigScreenView.romsForSystem(sid)
        } else {
            currentSystemInfo = { id: sid, name: sid.toUpperCase(), maker: "System", count: 0, emu: "RetroArch", hue: 210 }
            currentSystemRoms = []
        }
        systemRomIndex = 0
        subView = "system"
        if (currentSystemRoms.length > 0) {
            focusZone = "systemRomList"
            focusId = "systemRomList"
        } else {
            focusZone = "stage"
            focusId = "romPlayButton"
        }
        playSound("open")
    }

    function toggleGuide() {
        if (bindWizardOpen) {
            bindWizardOpen = false
            focusZone = "stage"
            focusId = "ctrlBindWizardRow"
            playSound("back")
            return
        }
        guideOpen = !guideOpen
        if (guideOpen) {
            focusZone = "guide"
            focusId = hasActiveGame ? "guideResumeGameRow" : "guideHomeRow"
            playSound("open")
        } else {
            restoreStageFocus()
            playSound("back")
        }
    }

    function restoreStageFocus() {
        if (subView === "game") {
            focusZone = "stage"
            focusId = "gamePlayButton"
        } else if (subView === "system") {
            if (currentSystemRoms.length > 0) {
                focusZone = "systemRomList"
                focusId = "systemRomList"
            } else {
                focusZone = "stage"
                focusId = "romPlayButton"
            }
        } else if (currentTab === 1 && cards.count > 0) {
            focusZone = "libraryGrid"
            focusId = "consoleGrid"
        } else {
            focusZone = "stage"
            var list = collectFocusables()
            if (list.length > 0) focusId = list[0].focusId
        }
    }

    function openUpdatePrompt(gid) {
        var key = String(gid || (subView === "game" ? detailGameId : heroGameId) || selectedId || "")
        var info = updateInfoFor(key)
        updatePromptGameId = key
        updatePromptTitle = String((info && info.title) ? info.title : ((subView === "game" && detailOverview && detailOverview.title) ? detailOverview.title : ((heroOverview && heroOverview.title) ? heroOverview.title : "this game")))
        updatePromptOpen = true
        focusZone = "stage"
        focusId = "updatePromptUpdateFirst"
        playSound("open")
    }

    function closeUpdatePrompt() {
        updatePromptOpen = false
        restoreStageFocus()
    }

    function triggerPlayImmediate() {
        playSound("launch")
        if (installations && installations.length > 0) {
            var idx = Math.max(0, Math.min(selectedVariantIndex, installations.length - 1))
            consoleRoot.playRequested(installations[idx])
        } else {
            consoleRoot.playRequested(null)
        }
    }

    function triggerPlay() {
        var gid = String((subView === "game" ? detailGameId : heroGameId) || selectedId || "")
        if (gameRequiresUpdatePrompt(gid)) {
            openUpdatePrompt(gid)
            return
        }
        if (!canPlay) return
        triggerPlayImmediate()
    }

    function cycleSoundPack() {
        if (typeof uiSounds === "undefined" || !uiSounds) return
        var packs = uiSounds.packs || []
        if (packs.length === 0) return
        var cur = uiSounds.pack || "ps2"
        var nextIdx = 0
        for (var i = 0; i < packs.length; i++) {
            if (packs[i].id === cur) {
                nextIdx = (i + 1) % packs.length
                break
            }
        }
        var nextPack = packs[nextIdx]
        var nextName = String(nextPack.name || nextPack.label || nextPack.id)
        uiSounds.setPack(nextPack.id)
        uiSounds.preview(nextPack.id, "select")
        showToast("Sound pack: " + nextName)
    }

    function stepSoundVolume(delta) {
        if (typeof uiSounds === "undefined" || !uiSounds) return
        var nextVol = Math.max(0, Math.min(100, uiSounds.volume + delta))
        uiSounds.setVolume(nextVol)
        playSound("move")
        showToast("Volume: " + nextVol + "%")
    }

    function handleEscape() {
        if (updatePromptOpen || bindWizardOpen || stickTuningOpen || guideOpen || librarySearchOpen || subView !== "") {
            navigate("back")
        } else {
            consoleRoot.desktopRequested()
        }
    }

    onVisibleChanged: {
        if (visible) {
            updateClock()
            refreshHomeData()
            if (selectedId && gameModel) {
                var row = gameModel.rowOf(selectedId)
                if (row >= 0) cards.currentIndex = row
            } else if (gameModel && gameModel.visibleCount > 0) {
                cards.currentIndex = 0
                consoleRoot.selected(gameModel.idAt(0))
            }
            consoleRoot.forceActiveFocus()
        } else {
            guideOpen = false
            bindWizardOpen = false
            updatePromptOpen = false
            librarySearchOpen = false
        }
    }

    onSelectedIdChanged: {
        if (!selectedId || !gameModel) return
        var row = gameModel.rowOf(selectedId)
        if (row >= 0 && cards.currentIndex !== row) {
            cards.currentIndex = row
        }
        if (!heroGameId) {
            heroGameId = selectedId
            heroOverview = gameModel.overview(heroGameId) || ({})
        }
        if (subView === "game" && detailGameId === selectedId) {
            refreshDetailData()
        }
    }

    Connections {
        target: consoleRoot.visible ? consoleRoot.gameModel : null
        function onModelReset() {
            consoleRoot.refreshHomeData()
            if (consoleRoot.subView === "game") consoleRoot.refreshDetailData()
        }
        function onCountChanged() {
            consoleRoot.refreshHomeData()
        }
    }

    Connections {
        target: (consoleRoot.visible && typeof bigScreenView !== "undefined") ? bigScreenView : null
        function onSystemsChanged() {
            consoleRoot.refreshHomeData()
            if (consoleRoot.subView === "system") {
                consoleRoot.currentSystemInfo = bigScreenView.systemInfo(consoleRoot.currentSystemId)
                consoleRoot.currentSystemRoms = bigScreenView.romsForSystem(consoleRoot.currentSystemId)
            }
        }
        function onPinnedChanged() {
            consoleRoot.pinnedRevision += 1
        }
    }

    // ------------------------------------------------------------------
    // Dynamic Focusable Tree Walker (No persistent QQuickItem references)
    // ------------------------------------------------------------------
    function isFocusableInActiveScope(item) {
        if (!item || !item.visible || item.width <= 0 || item.height <= 0) return false
        if (bindWizardOpen) return false
        if (updatePromptOpen) return item.focusScope === "updatePrompt"
        if (guideOpen) return item.focusScope === "guide"
        if (subView === "game") return item.focusScope === "game"
        if (subView === "system") return item.focusScope === "system"
        return item.focusScope === ("tab_" + currentTab)
    }

    function _walkFocusables(node, out, seen) {
        if (!node || !node.visible || node.width <= 0 || node.height <= 0) return
        if (node.isBigScreenFocusable) {
            if (isFocusableInActiveScope(node) && !seen[node.focusId]) {
                seen[node.focusId] = true
                out.push(node)
            }
            return
        }
        var kids = node.children
        if (kids) {
            for (var i = 0; i < kids.length; i++) {
                _walkFocusables(kids[i], out, seen)
            }
        }
        if (node.contentItem && node.contentItem !== node) {
            _walkFocusables(node.contentItem, out, seen)
        }
    }

    function collectFocusables() {
        var result = []
        var seen = {}
        if (bindWizardOpen) return result
        if (updatePromptOpen) {
            _walkFocusables(consoleUpdateLaunchPrompt, result, seen)
        } else if (guideOpen) {
            _walkFocusables(guideDrawer, result, seen)
        } else if (subView === "game") {
            _walkFocusables(gamePageView, result, seen)
        } else if (subView === "system") {
            _walkFocusables(systemDetailView, result, seen)
        } else {
            var views = [homeTabView, libraryTabView, systemsTabView, friendsTabView, controllersTabView, settingsTabView]
            if (currentTab >= 0 && currentTab < views.length) {
                _walkFocusables(views[currentTab], result, seen)
            }
        }
        return result
    }

    function collectFocusableIds() {
        var list = collectFocusables()
        var ids = []
        for (var i = 0; i < list.length; i++) {
            if (list[i] && list[i].focusId) ids.push(String(list[i].focusId))
        }
        return ids.join(",")
    }

    function findFocusableById(fid) {
        var list = collectFocusables()
        for (var i = 0; i < list.length; i++) {
            if (list[i] && list[i].focusId === fid) {
                return list[i]
            }
        }
        return null
    }

    function ensureFocusableVisible(item) {
        if (!item) return
        var p = item.parent
        while (p && p !== consoleRoot) {
            if (typeof p.contentX === "number" && typeof p.contentWidth === "number" && p.contentWidth > p.width) {
                var box = item.mapToItem(p.contentItem, 0, 0, item.width, item.height)
                if (box.x < p.contentX + 24) {
                    p.contentX = Math.max(0, box.x - 24)
                } else if (box.x + box.width > p.contentX + p.width - 24) {
                    p.contentX = Math.min(p.contentWidth - p.width, box.x + box.width - p.width + 24)
                }
            }
            if (typeof p.contentY === "number" && typeof p.contentHeight === "number" && p.contentHeight > p.height) {
                var vbox = item.mapToItem(p.contentItem, 0, 0, item.width, item.height)
                if (vbox.y < p.contentY + 20) {
                    p.contentY = Math.max(0, vbox.y - 20)
                } else if (vbox.y + vbox.height > p.contentY + p.height - 20) {
                    p.contentY = Math.min(p.contentHeight - p.height, vbox.y + vbox.height - p.height + 20)
                }
            }
            p = p.parent
        }
    }

    function moveSpatial(dir) {
        var candidates = collectFocusables()
        if (candidates.length === 0) return false
        var current = findFocusableById(focusId)
        if (!current) {
            focusId = candidates[0].focusId
            ensureFocusableVisible(candidates[0])
            playSound("move")
            return true
        }

        // First check explicit shelf/indexed neighbors for horizontal rows so off-screen items scroll into view smoothly
        if ((dir === "left" || dir === "right") && current.focusId.indexOf("_") > 0) {
            var parts = current.focusId.split("_")
            var prefix = parts[0]
            var idx = parseInt(parts[1], 10)
            if (!isNaN(idx)) {
                var neighborId = prefix + "_" + (dir === "right" ? (idx + 1) : (idx - 1))
                var neighbor = findFocusableById(neighborId)
                if (neighbor) {
                    focusId = neighbor.focusId
                    ensureFocusableVisible(neighbor)
                    if (neighbor.onFocusedCallback) neighbor.onFocusedCallback()
                    playSound("move")
                    return true
                }
            }
        }

        var cRect = current.mapToItem(consoleRoot, 0, 0, current.width, current.height)
        var cx = cRect.x + cRect.width / 2
        var cy = cRect.y + cRect.height / 2

        var best = null
        var bestScore = 1e12

        // Pass 1: cone-constrained spatial match
        for (var i = 0; i < candidates.length; i++) {
            var el = candidates[i]
            if (el === current) continue
            var r = el.mapToItem(consoleRoot, 0, 0, el.width, el.height)
            var ex = r.x + r.width / 2
            var ey = r.y + r.height / 2
            var dx = ex - cx
            var dy = ey - cy
            if (dir === "right" && dx <= 6) continue
            if (dir === "left" && dx >= -6) continue
            if (dir === "down" && dy <= 6) continue
            if (dir === "up" && dy >= -6) continue

            var primary = (dir === "left" || dir === "right") ? Math.abs(dx) : Math.abs(dy)
            var cross = (dir === "left" || dir === "right") ? Math.abs(dy) : Math.abs(dx)
            if (cross > primary * 2.2 + 80) continue
            var score = primary + cross * 2.25
            if (score < bestScore) {
                bestScore = score
                best = el
            }
        }

        // Pass 2: half-plane fallback so focus never gets stuck
        if (!best) {
            for (var j = 0; j < candidates.length; j++) {
                var el2 = candidates[j]
                if (el2 === current) continue
                var r2 = el2.mapToItem(consoleRoot, 0, 0, el2.width, el2.height)
                var ex2 = r2.x + r2.width / 2
                var ey2 = r2.y + r2.height / 2
                var dx2 = ex2 - cx
                var dy2 = ey2 - cy
                if (dir === "right" && dx2 <= 4) continue
                if (dir === "left" && dx2 >= -4) continue
                if (dir === "down" && dy2 <= 4) continue
                if (dir === "up" && dy2 >= -4) continue
                var primary2 = (dir === "left" || dir === "right") ? Math.abs(dx2) : Math.abs(dy2)
                var cross2 = (dir === "left" || dir === "right") ? Math.abs(dy2) : Math.abs(dx2)
                var score2 = primary2 + cross2 * 1.5
                if (score2 < bestScore) {
                    bestScore = score2
                    best = el2
                }
            }
        }

        if (best) {
            var prevId = focusId
            focusId = best.focusId
            ensureFocusableVisible(best)
            if (best.onFocusedCallback) best.onFocusedCallback()
            // Keep selectedId moving when stepping right from initial Home state (for test_console_overlay.py)
            if (currentTab === 0 && subView === "" && prevId === "homeHeroPlay" && dir === "right" && gameModel && gameModel.visibleCount > 1) {
                var nextRow = (cards.currentIndex + 1) % gameModel.visibleCount
                cards.currentIndex = nextRow
                consoleRoot.selected(gameModel.idAt(nextRow))
            }
            playSound("move")
            return true
        }
        return false
    }

    function activateCurrentFocusable() {
        var current = findFocusableById(focusId)
        if (current && current.onActivatedCallback) {
            current.onActivatedCallback()
            return true
        }
        return false
    }

    // ------------------------------------------------------------------
    // Unified Controller & Keyboard Navigation Entrypoint
    // ------------------------------------------------------------------
    function navigate(action) {
        if (stickTuningOpen) {
            if (action === "back" || action === "menu") {
                if (bigScreenStickStudio.dirty) discardStickTuningDialog.open()
                else closeStickTuning(false)
                return
            }
            bigScreenStickFocus.action(action)
            return
        }
        if (updatePromptOpen) {
            if (action === "back" || action === "menu") {
                closeUpdatePrompt()
                playSound("back")
                return
            }
            if (action === "up" || action === "down" || action === "left" || action === "right") {
                moveSpatial(action)
                return
            }
            if (action === "accept") {
                activateCurrentFocusable()
                return
            }
            return
        }

        if (bindWizardOpen) {
            if (action === "back" || action === "menu") {
                bindWizardOpen = false
                focusZone = "stage"
                focusId = "ctrlBindWizardRow"
                playSound("back")
                return
            }
            if (typeof inlineBindWizard.navigate === "function") {
                inlineBindWizard.navigate(action)
            }
            return
        }

        if (action === "menu") {
            toggleGuide()
            return
        }

        if (guideOpen) {
            if (action === "back") {
                toggleGuide()
                return
            }
            if (action === "up" || action === "down" || action === "left" || action === "right") {
                moveSpatial(action)
                return
            }
            if (action === "accept") {
                activateCurrentFocusable()
                return
            }
            return
        }

        if (action === "previous_section") {
            switchTab(currentTab - 1)
            return
        }
        if (action === "next_section") {
            switchTab(currentTab + 1)
            return
        }

        // Back button handling
        if (action === "back") {
            if (librarySearchOpen) {
                librarySearchOpen = false
                playSound("back")
                return
            }
            if (liveControllerTestOpen) {
                liveControllerTestOpen = false
                playSound("back")
                return
            }
            if (subView !== "") {
                subView = ""
                var retTab = parseInt(subViewReturnTab, 10)
                if (!isNaN(retTab) && retTab >= 0 && retTab < tabNames.length) {
                    currentTab = retTab
                }
                restoreStageFocus()
                playSound("back")
                return
            }
            if (currentTab !== 0) {
                switchTab(0)
                playSound("back")
                return
            }
            playSound("back")
            consoleRoot.desktopRequested()
            return
        }

        // Context actions: X (options) and Y (search)
        if (action === "search") {
            if (currentTab === 1 && subView === "") {
                librarySearchOpen = !librarySearchOpen
                if (librarySearchOpen) {
                    focusZone = "stage"
                    focusId = "libraryLetter_0"
                    playSound("open")
                } else {
                    if (cards.count > 0) {
                        focusZone = "libraryGrid"
                        focusId = "consoleGrid"
                    }
                    playSound("back")
                }
            } else {
                switchTab(1)
                librarySearchOpen = true
                focusZone = "stage"
                focusId = "libraryLetter_0"
            }
            return
        }
        if (action === "options") {
            if (currentTab === 1 && subView === "") {
                cycleLibrarySort()
                return
            }
            if (subView === "system" && focusZone === "systemRomList") {
                focusZone = "stage"
                focusId = "romProfileButton"
                playSound("move")
                return
            }
            if (subView === "game") {
                focusZone = "stage"
                focusId = "gameMonitorRow"
                playSound("move")
                return
            }
        }

        // Library GridView navigation
        if (currentTab === 1 && subView === "" && focusZone === "libraryGrid") {
            var cols = Math.max(1, Math.floor(cards.width / cards.cellWidth))
            if (action === "left") {
                if (cards.currentIndex > 0) {
                    cards.currentIndex -= 1
                    consoleRoot.selected(gameModel.idAt(cards.currentIndex))
                    playSound("move")
                }
                return
            }
            if (action === "right") {
                if (cards.currentIndex + 1 < cards.count) {
                    cards.currentIndex += 1
                    consoleRoot.selected(gameModel.idAt(cards.currentIndex))
                    playSound("move")
                }
                return
            }
            if (action === "up") {
                if (cards.currentIndex - cols >= 0) {
                    cards.currentIndex -= cols
                    consoleRoot.selected(gameModel.idAt(cards.currentIndex))
                    playSound("move")
                } else {
                    focusZone = "stage"
                    var chipIdx = Math.max(0, libraryChipsModel.indexOf(activeLibraryChip))
                    focusId = "libraryChip_" + chipIdx
                    playSound("move")
                }
                return
            }
            if (action === "down") {
                if (cards.currentIndex + cols < cards.count) {
                    cards.currentIndex += cols
                    consoleRoot.selected(gameModel.idAt(cards.currentIndex))
                    playSound("move")
                } else if (cards.count > 0 && cards.currentIndex < cards.count - 1) {
                    cards.currentIndex = cards.count - 1
                    consoleRoot.selected(gameModel.idAt(cards.currentIndex))
                    playSound("move")
                }
                return
            }
            if (action === "previous_page") {
                if (cards.count > 0) {
                    cards.currentIndex = Math.max(0, cards.currentIndex - cols * 2)
                    consoleRoot.selected(gameModel.idAt(cards.currentIndex))
                    playSound("move")
                }
                return
            }
            if (action === "next_page") {
                if (cards.count > 0) {
                    cards.currentIndex = Math.min(cards.count - 1, cards.currentIndex + cols * 2)
                    consoleRoot.selected(gameModel.idAt(cards.currentIndex))
                    playSound("move")
                }
                return
            }
            if (action === "accept") {
                if (cards.count > 0 && cards.currentIndex >= 0) {
                    openGamePage(gameModel.idAt(cards.currentIndex))
                }
                return
            }
        }

        // Moving down from Library chips into Library GridView
        if (currentTab === 1 && subView === "" && focusZone === "stage" && action === "down") {
            if (focusId.indexOf("libraryChip_") === 0 && librarySearchOpen) {
                focusId = "libraryLetter_0"
                playSound("move")
                return
            }
            if ((focusId.indexOf("libraryChip_") === 0 || focusId.indexOf("libraryLetter_") === 0) && cards.count > 0) {
                focusZone = "libraryGrid"
                if (cards.currentIndex < 0) cards.currentIndex = 0
                focusId = "consoleGrid"
                playSound("move")
                return
            }
        }

        // System ROM ListView navigation
        if (subView === "system" && focusZone === "systemRomList") {
            if (action === "up") {
                if (systemRomIndex > 0) {
                    systemRomIndex -= 1
                    systemRomListView.positionViewAtIndex(systemRomIndex, ListView.Contain)
                    playSound("move")
                }
                return
            }
            if (action === "down") {
                if (systemRomIndex + 1 < currentSystemRoms.length) {
                    systemRomIndex += 1
                    systemRomListView.positionViewAtIndex(systemRomIndex, ListView.Contain)
                    playSound("move")
                }
                return
            }
            if (action === "previous_page") {
                if (currentSystemRoms.length > 0) {
                    systemRomIndex = Math.max(0, systemRomIndex - 6)
                    systemRomListView.positionViewAtIndex(systemRomIndex, ListView.Contain)
                    playSound("move")
                }
                return
            }
            if (action === "next_page") {
                if (currentSystemRoms.length > 0) {
                    systemRomIndex = Math.min(currentSystemRoms.length - 1, systemRomIndex + 6)
                    systemRomListView.positionViewAtIndex(systemRomIndex, ListView.Contain)
                    playSound("move")
                }
                return
            }
            if (action === "right") {
                focusZone = "stage"
                focusId = "romPlayButton"
                playSound("move")
                return
            }
            if (action === "accept") {
                if (selectedRom && selectedRom.rom_id && typeof bigScreenView !== "undefined" && bigScreenView) {
                    var ok = bigScreenView.playRom(selectedRom.rom_id)
                    playSound("launch")
                    showToast(ok ? ("Launching " + selectedRom.title) : ("Selected " + selectedRom.title + " in " + selectedRom.emu))
                }
                return
            }
        }

        // Moving left from System ROM detail buttons back to ROM list
        if (subView === "system" && focusZone === "stage" && action === "left") {
            if ((focusId === "romPlayButton" || focusId === "romProfileButton") && currentSystemRoms.length > 0) {
                focusZone = "systemRomList"
                focusId = "systemRomList"
                playSound("move")
                return
            }
        }

        // Volume left/right adjustment when volume row is focused in Settings
        if (currentTab === 5 && subView === "" && focusId === "setSoundVolumeRow") {
            if (action === "left") {
                stepSoundVolume(-10)
                return
            }
            if (action === "right") {
                stepSoundVolume(10)
                return
            }
        }

        // Standard 2D spatial navigation
        if (action === "left" || action === "right" || action === "up" || action === "down") {
            moveSpatial(action)
            return
        }
        if (action === "accept") {
            activateCurrentFocusable()
            return
        }
    }

    function closeStickTuning(discard) {
        if (discard && bigScreenStickStudio.dirty) bigScreenStickStudio.refreshRoutes()
        stickTuningOpen = false
        focusZone = "stage"
        focusId = "ctrlStickTuningRow"
        playSound("back")
    }

    function loadBigScreenStickProfile() {
        if (!stickTuningOpen || typeof unifiedController === "undefined" || !unifiedController
                || unifiedController.busy || !unifiedController.available || bigScreenStickStudio.dirty) return
        var gid = String(selectedId || heroGameId || "")
        var name = ""
        if (gid && typeof controllerProfiles !== "undefined" && controllerProfiles
                && typeof controllerProfiles.resolveProfile === "function")
            name = String(controllerProfiles.resolveProfile(gid) || "")
        if (name && unifiedController.profiles.indexOf(name) >= 0 && unifiedController.editingProfile !== name)
            unifiedController.loadProfile(name)
    }

    Connections {
        target: typeof unifiedController !== "undefined" ? unifiedController : null
        function onChanged() { Qt.callLater(consoleRoot.loadBigScreenStickProfile) }
    }

    Keys.onLeftPressed: navigate("left")
    Keys.onRightPressed: navigate("right")
    Keys.onUpPressed: navigate("up")
    Keys.onDownPressed: navigate("down")
    Keys.onReturnPressed: navigate("accept")
    Keys.onEnterPressed: navigate("accept")
    Keys.onSpacePressed: navigate("accept")
    Keys.onBackPressed: navigate("back")
    Keys.onEscapePressed: handleEscape()
    Keys.onMenuPressed: navigate("menu")
    Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Backspace) { navigate("back"); event.accepted = true }
        else if (event.key === Qt.Key_Q || event.key === Qt.Key_BracketLeft) { navigate("previous_section"); event.accepted = true }
        else if (event.key === Qt.Key_E || event.key === Qt.Key_BracketRight) { navigate("next_section"); event.accepted = true }
        else if (event.key === Qt.Key_G || event.key === Qt.Key_Home) { navigate("menu"); event.accepted = true }
        else if (event.key === Qt.Key_X) { navigate("options"); event.accepted = true }
        else if (event.key === Qt.Key_Y || event.key === Qt.Key_Slash) { navigate("search"); event.accepted = true }
        else if (event.key === Qt.Key_PageUp) { navigate("previous_page"); event.accepted = true }
        else if (event.key === Qt.Key_PageDown) { navigate("next_page"); event.accepted = true }
    }

    // ------------------------------------------------------------------
    // Focusable Item Component
    // ------------------------------------------------------------------
    component BigScreenFocusable: Rectangle {
        id: fItem
        readonly property bool isBigScreenFocusable: true
        property string focusId: ""
        property string focusScope: "tab_0"
        property var onActivatedCallback: null
        property var onFocusedCallback: null
        readonly property bool isFocused: consoleRoot.focusZone !== "libraryGrid"
                                          && consoleRoot.focusZone !== "systemRomList"
                                          && consoleRoot.focusId === fItem.focusId
        objectName: focusId
        radius: 6
        color: isFocused ? "#272727" : Theme.panel
        border.width: isFocused ? 2 : 1
        border.color: isFocused ? "#ffffff" : Theme.line

        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            onClicked: {
                consoleRoot.focusZone = (fItem.focusScope === "guide") ? "guide" : "stage"
                consoleRoot.focusId = fItem.focusId
                if (fItem.onFocusedCallback) fItem.onFocusedCallback()
                if (fItem.onActivatedCallback) fItem.onActivatedCallback()
            }
        }
    }

    // ------------------------------------------------------------------
    // Background Canvas
    // ------------------------------------------------------------------
    Rectangle {
        anchors.fill: parent
        color: Theme.background
    }

    // ------------------------------------------------------------------
    // Main 3-Row Console Frame: Top Bar (68px) + Stage + Hint Bar (54px)
    // ------------------------------------------------------------------
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ==============================================================
        // 1. TOP BAR
        // ==============================================================
        Rectangle {
            id: topBar
            objectName: "bigScreenTopBar"
            Layout.fillWidth: true
            Layout.preferredHeight: 68
            color: "#0b0b0b"

            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.line
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 44
                anchors.rightMargin: 44
                spacing: 24

                // Logo Mark + Wordmark
                Row {
                    spacing: 10
                    Layout.alignment: Qt.AlignVCenter
                    Image {
                        objectName: "bigScreenBrandLogo"
                        anchors.verticalCenter: parent.verticalCenter
                        width: 28; height: 28
                        source: Qt.resolvedUrl("../../../assets/brand/waystone-mark.svg")
                        sourceSize: Qt.size(56, 56)
                        smooth: true; mipmap: true
                    }
                    Text { textFormat: Text.PlainText;
                        objectName: "bigScreenBrand"
                        anchors.verticalCenter: parent.verticalCenter
                        text: "WAYSTONE"
                        color: Theme.text
                        font.family: Theme.family
                        font.pixelSize: 16
                        font.bold: true
                        font.letterSpacing: 3
                    }
                }

                // LB glyph + 6 Tabs + RB glyph
                Row {
                    spacing: 8
                    Layout.alignment: Qt.AlignVCenter

                    Rectangle {
                        width: 28; height: 22; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.panel; border.width: 1; border.color: Theme.main
                        Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: "LB"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; font.bold: true }
                    }

                    Repeater {
                        model: consoleRoot.tabNames
                        delegate: Rectangle {
                            id: tabPill
                            objectName: "bigScreenTab_" + modelData
                            readonly property bool active: consoleRoot.currentTab === index && consoleRoot.subView === ""
                            width: tabLabel.implicitWidth + 28
                            height: 36
                            radius: 6
                            color: active ? "#f2f2f2" : "transparent"
                            border.width: (consoleRoot.currentTab === index && consoleRoot.subView !== "") ? 1 : 0
                            border.color: Theme.main

                            Text { textFormat: Text.PlainText;
                                id: tabLabel
                                anchors.centerIn: parent
                                text: modelData
                                color: tabPill.active ? "#090909" : (consoleRoot.currentTab === index ? Theme.text : Theme.muted)
                                font.family: Theme.family
                                font.pixelSize: 15
                                font.bold: true
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: consoleRoot.switchTab(index)
                            }
                        }
                    }

                    Rectangle {
                        width: 28; height: 22; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.panel; border.width: 1; border.color: Theme.main
                        Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: "RB"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; font.bold: true }
                    }
                }

                Item { Layout.fillWidth: true }

                // Right status cluster: friends online, controller + battery, clock
                Row {
                    spacing: 18
                    Layout.alignment: Qt.AlignVCenter

                    Row {
                        spacing: 7
                        anchors.verticalCenter: parent.verticalCenter
                        Rectangle {
                            width: 8; height: 8; radius: 4
                            anchors.verticalCenter: parent.verticalCenter
                            color: "#7cc88a"
                        }
                        Text { textFormat: Text.PlainText;
                            objectName: "topBarFriendsOnline"
                            text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.onlineFriendsCount : 0) + " friends online"
                            color: Theme.muted
                            font.family: Theme.family
                            font.pixelSize: 13
                        }
                    }

                    Rectangle {
                        height: 28
                        width: ctrlTopRow.implicitWidth + 18
                        radius: 5
                        color: Theme.panel
                        border.width: 1
                        border.color: Theme.line
                        anchors.verticalCenter: parent.verticalCenter

                        Row {
                            id: ctrlTopRow
                            anchors.centerIn: parent
                            spacing: 6
                            Text { textFormat: Text.PlainText;
                                text: "🎮"
                                font.pixelSize: 12
                            }
                            Text { textFormat: Text.PlainText;
                                objectName: "topBarControllerStatus"
                                text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.controllerName : "Controller")
                                      + " · "
                                      + ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.controllerBatteryText : "Ready")
                                color: Theme.muted
                                font.family: Theme.family
                                font.pixelSize: 12
                            }
                        }
                    }

                    Text { textFormat: Text.PlainText;
                        objectName: "topBarClock"
                        anchors.verticalCenter: parent.verticalCenter
                        text: consoleRoot.clockText
                        color: Theme.text
                        font.family: Theme.family
                        font.pixelSize: 14
                        font.bold: true
                    }
                }
            }
        }

        // ==============================================================
        // 2. STAGE (TABS + SUB-VIEWS)
        // ==============================================================
        Item {
            id: stageContainer
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            // ----------------------------------------------------------
            // TAB 0: HOME
            // ----------------------------------------------------------
            Item {
                id: homeTabView
                objectName: "bigScreenHomeTab"
                anchors.fill: parent
                anchors.leftMargin: 44
                anchors.rightMargin: 44
                anchors.topMargin: 22
                anchors.bottomMargin: 18
                visible: consoleRoot.currentTab === 0 && consoleRoot.subView === ""

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 18

                    // Top Hero + Side Column
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.max(250, Math.min(340, stageContainer.height * 0.43))
                        spacing: 20

                        // Left: "Jump back in" Hero Card
                        Rectangle {
                            id: homeHeroCard
                            objectName: "homeHeroCard"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.preferredWidth: 680
                            radius: 8
                            color: "#161616"
                            border.width: (consoleRoot.focusId === "homeHeroPlay" || consoleRoot.focusId === "homeHeroDetails") ? 2 : 1
                            border.color: (consoleRoot.focusId === "homeHeroPlay" || consoleRoot.focusId === "homeHeroDetails") ? "#ffffff" : Theme.line
                            clip: true

                            // Stylized hue backdrop when no raster cover is loaded
                            Rectangle {
                                anchors.fill: parent
                                gradient: Gradient {
                                    GradientStop {
                                        position: 0.0
                                        color: Qt.hsla(consoleRoot.titleHue(consoleRoot.heroOverview ? consoleRoot.heroOverview.title : "") / 360.0, 0.45, 0.25, 0.85)
                                    }
                                    GradientStop { position: 1.0; color: "#121212" }
                                }
                            }

                            MissingCover {
                                anchors.fill: parent
                                artworkSource: consoleRoot.heroCoverUrl
                                opacity: artworkReady ? 0.65 : 0.0
                            }

                            // Readability gradient overlay
                            Rectangle {
                                anchors.fill: parent
                                gradient: Gradient {
                                    GradientStop { position: 0.0; color: "#22000000" }
                                    GradientStop { position: 0.55; color: "#aa090909" }
                                    GradientStop { position: 1.0; color: "#f0080808" }
                                }
                            }

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 26
                                spacing: 8

                                Item { Layout.fillHeight: true }

                                Text { textFormat: Text.PlainText;
                                    text: "JUMP BACK IN"
                                    color: Theme.muted
                                    font.family: Theme.family
                                    font.pixelSize: 11
                                    font.bold: true
                                    font.letterSpacing: 1.8
                                }

                                Image {
                                    id: homeHeroLogo
                                    objectName: "homeHeroLogo"
                                    Layout.preferredWidth: 420; Layout.preferredHeight: 110
                                    visible: status === Image.Ready
                                    source: consoleRoot.heroLogoUrl
                                    fillMode: Image.PreserveAspectFit; horizontalAlignment: Image.AlignLeft
                                    asynchronous: true; smooth: true; mipmap: true
                                }
                                Text { textFormat: Text.PlainText;
                                    objectName: "homeHeroTitle"
                                    visible: !homeHeroLogo.visible
                                    Layout.fillWidth: true
                                    text: (consoleRoot.heroOverview && consoleRoot.heroOverview.title) ? consoleRoot.heroOverview.title : "Select a Game"
                                    color: Theme.text
                                    font.family: Theme.family
                                    font.pixelSize: 32
                                    font.bold: true
                                    elide: Text.ElideRight
                                }

                                Text { textFormat: Text.PlainText;
                                    objectName: "homeHeroMeta"
                                    Layout.fillWidth: true
                                    text: {
                                        var src = (consoleRoot.heroOverview && consoleRoot.heroOverview.sources) ? consoleRoot.heroOverview.sources : "Library"
                                        var lp = (consoleRoot.heroOverview && consoleRoot.heroOverview.last_activity && consoleRoot.heroOverview.last_activity !== "Not supplied")
                                                 ? ("Last played " + consoleRoot.heroOverview.last_activity) : "Ready to launch"
                                        var pt = (consoleRoot.heroOverview && consoleRoot.heroOverview.playtime && consoleRoot.heroOverview.playtime !== "Not supplied")
                                                 ? (" · " + String(consoleRoot.heroOverview.playtime).split(" (")[0]) : ""
                                        return src + " · " + lp + pt
                                    }
                                    color: Theme.muted
                                    font.family: Theme.family
                                    font.pixelSize: 14
                                    elide: Text.ElideRight
                                }

                                Row {
                                    spacing: 12
                                    Layout.topMargin: 8

                                    BigScreenFocusable {
                                        focusId: "homeHeroPlay"
                                        focusScope: "tab_0"
                                        width: 132
                                        height: 44
                                        color: isFocused ? "#ffffff" : "#2b2b2b"
                                        border.color: isFocused ? "#ffffff" : Theme.main
                                        onActivatedCallback: function() {
                                            if (consoleRoot.heroGameId) {
                                                consoleRoot.selected(consoleRoot.heroGameId)
                                            }
                                            if (consoleRoot.canPlay) {
                                                consoleRoot.triggerPlay()
                                            } else {
                                                consoleRoot.openGamePage(consoleRoot.heroGameId)
                                            }
                                        }
                                        Text { textFormat: Text.PlainText;
                                            anchors.centerIn: parent
                                            text: consoleRoot.canPlay ? "▶  Play" : "View & Play"
                                            color: parent.isFocused ? "#090909" : Theme.text
                                            font.family: Theme.family
                                            font.pixelSize: 15
                                            font.bold: true
                                        }
                                    }

                                    BigScreenFocusable {
                                        focusId: "homeHeroDetails"
                                        focusScope: "tab_0"
                                        width: 124
                                        height: 44
                                        onActivatedCallback: function() {
                                            consoleRoot.openGamePage(consoleRoot.heroGameId)
                                        }
                                        Text { textFormat: Text.PlainText;
                                            anchors.centerIn: parent
                                            text: "Details"
                                            color: Theme.text
                                            font.family: Theme.family
                                            font.pixelSize: 15
                                            font.bold: true
                                        }
                                    }
                                }
                            }
                        }

                        // Right: Party & Controller summary cards
                        ColumnLayout {
                            Layout.fillHeight: true
                            Layout.preferredWidth: 340
                            Layout.maximumWidth: 420
                            spacing: 14

                            BigScreenFocusable {
                                id: homePartyCard
                                focusId: "homePartyCard"
                                focusScope: "tab_0"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                onActivatedCallback: function() {
                                    consoleRoot.switchTab(3)
                                }

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 18
                                    spacing: 6
                                    Text { textFormat: Text.PlainText;
                                        text: "PARTY & FRIENDS"
                                        color: Theme.muted
                                        font.family: Theme.family
                                        font.pixelSize: 11
                                        font.bold: true
                                        font.letterSpacing: 1.4
                                    }
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: (typeof xodusSocialView !== "undefined" && xodusSocialView && xodusSocialView.partyActive)
                                              ? "Voice party active" : "Start or join a party"
                                        color: Theme.text
                                        font.family: Theme.family
                                        font.pixelSize: 18
                                        font.bold: true
                                        elide: Text.ElideRight
                                    }
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.onlineFriendsCount : 0)
                                              + " friends online · Press A for Party & Friends"
                                        color: Theme.muted
                                        font.family: Theme.family
                                        font.pixelSize: 13
                                        elide: Text.ElideRight
                                    }
                                }
                            }

                            BigScreenFocusable {
                                id: homeControllerCard
                                focusId: "homeControllerCard"
                                focusScope: "tab_0"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                onActivatedCallback: function() {
                                    consoleRoot.switchTab(4)
                                }

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 18
                                    spacing: 6
                                    Text { textFormat: Text.PlainText;
                                        text: "CONTROLLER"
                                        color: Theme.muted
                                        font.family: Theme.family
                                        font.pixelSize: 11
                                        font.bold: true
                                        font.letterSpacing: 1.4
                                    }
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.controllerName : "Xbox Wireless Controller"
                                        color: Theme.text
                                        font.family: Theme.family
                                        font.pixelSize: 18
                                        font.bold: true
                                        elide: Text.ElideRight
                                    }
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: "Profile: " + ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.activeProfileName : "Default")
                                              + " · " + ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.virtualPadLabel : "Xbox 360")
                                        color: Theme.muted
                                        font.family: Theme.family
                                        font.pixelSize: 13
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }

                    // Middle Shelf: Recent Games
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 168
                        spacing: 8

                        RowLayout {
                            Layout.fillWidth: true
                            Text { textFormat: Text.PlainText;
                                text: "Recent"
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 17
                                font.bold: true
                            }
                            Item { Layout.fillWidth: true }
                            Text { textFormat: Text.PlainText;
                                text: consoleRoot.homeRecentGames.length + (consoleRoot.homeRecentGames.length === 1 ? " game" : " games")
                                color: Theme.muted
                                font.family: Theme.family
                                font.pixelSize: 13
                            }
                        }

                        Flickable {
                            id: homeRecentFlick
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            contentWidth: homeRecentRow.implicitWidth
                            contentHeight: height
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds

                            Row {
                                id: homeRecentRow
                                height: parent.height
                                spacing: 14

                                Repeater {
                                    model: consoleRoot.homeRecentGames
                                    delegate: BigScreenFocusable {
                                        focusId: "homeRecent_" + index
                                        focusScope: "tab_0"
                                        width: 230
                                        height: homeRecentRow.height - 4
                                        clip: true
                                        onFocusedCallback: function() {
                                            if (modelData && modelData.id) consoleRoot.selected(modelData.id)
                                        }
                                        onActivatedCallback: function() {
                                            if (modelData && modelData.id) consoleRoot.openGamePage(modelData.id)
                                        }

                                        Rectangle {
                                            anchors.fill: parent
                                            anchors.margins: 2
                                            radius: 5
                                            gradient: Gradient {
                                                GradientStop {
                                                    position: 0.0
                                                    color: Qt.hsla(consoleRoot.titleHue(modelData.title) / 360.0, 0.42, 0.24, 0.8)
                                                }
                                                GradientStop { position: 1.0; color: "#141414" }
                                            }
                                        }

                                        MissingCover {
                                            anchors.fill: parent
                                            anchors.margins: 2
                                            artworkSource: modelData.coverUrl || ""
                                            opacity: artworkReady ? 0.72 : 0.0
                                        }

                                        Rectangle {
                                            anchors.fill: parent
                                            anchors.margins: 2
                                            radius: 5
                                            gradient: Gradient {
                                                GradientStop { position: 0.0; color: "#18000000" }
                                                GradientStop { position: 1.0; color: "#ee0c0c0c" }
                                            }
                                        }

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 12
                                            spacing: 4

                                            Rectangle {
                                                height: 20
                                                width: srcText.implicitWidth + 12
                                                radius: 4
                                                color: "#cc181818"
                                                border.width: 1
                                                border.color: Theme.line
                                                Text { textFormat: Text.PlainText;
                                                    id: srcText
                                                    anchors.centerIn: parent
                                                    text: modelData.sources || "Library"
                                                    color: Theme.muted
                                                    font.family: Theme.family
                                                    font.pixelSize: 10
                                                    font.bold: true
                                                }
                                            }

                                            Item { Layout.fillHeight: true }

                                            Text { textFormat: Text.PlainText;
                                                Layout.fillWidth: true
                                                text: modelData.title || "Game"
                                                color: Theme.text
                                                font.family: Theme.family
                                                font.pixelSize: 15
                                                font.bold: true
                                                elide: Text.ElideRight
                                            }

                                            Text { textFormat: Text.PlainText;
                                                Layout.fillWidth: true
                                                text: modelData.playtime ? (modelData.lastPlayed + " · " + modelData.playtime) : (modelData.lastPlayed || "Ready")
                                                color: Theme.muted
                                                font.family: Theme.family
                                                font.pixelSize: 11
                                                elide: Text.ElideRight
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // Bottom Shelf: Your Systems
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 128
                        spacing: 8

                        RowLayout {
                            Layout.fillWidth: true
                            Text { textFormat: Text.PlainText;
                                text: "Your systems"
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 17
                                font.bold: true
                            }
                            Item { Layout.fillWidth: true }
                            Text { textFormat: Text.PlainText;
                                text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.totalRomCount : 0) + " ROMs"
                                color: Theme.muted
                                font.family: Theme.family
                                font.pixelSize: 13
                            }
                        }

                        Flickable {
                            id: homeSystemsFlick
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            contentWidth: homeSystemsRow.implicitWidth
                            contentHeight: height
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds

                            Row {
                                id: homeSystemsRow
                                height: parent.height
                                spacing: 14

                                Repeater {
                                    model: consoleRoot.homeSystemsList
                                    delegate: BigScreenFocusable {
                                        focusId: "homeSystem_" + index
                                        focusScope: "tab_0"
                                        width: 230
                                        height: Math.max(92, homeSystemsRow.height - 4)
                                        clip: true
                                        onActivatedCallback: function() {
                                            if (modelData && modelData.id) consoleRoot.openSystemDetail(modelData.id)
                                        }

                                        // Top colored accent bar
                                        Rectangle {
                                            anchors.top: parent.top
                                            anchors.left: parent.left
                                            anchors.right: parent.right
                                            anchors.margins: 2
                                            height: 4
                                            radius: 2
                                            color: Qt.hsla(((modelData.hue || 205) % 360) / 360.0, 0.55, 0.52, 0.9)
                                        }

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 14
                                            spacing: 4

                                            RowLayout {
                                                Layout.fillWidth: true
                                                Text { textFormat: Text.PlainText;
                                                    text: modelData.maker || "Console"
                                                    color: Theme.muted
                                                    font.family: Theme.family
                                                    font.pixelSize: 11
                                                }
                                                Item { Layout.fillWidth: true }
                                                Rectangle {
                                                    height: 20
                                                    width: cntTxt.implicitWidth + 12
                                                    radius: 4
                                                    color: "#141414"
                                                    border.width: 1
                                                    border.color: Theme.line
                                                    Text { textFormat: Text.PlainText;
                                                        id: cntTxt
                                                        anchors.centerIn: parent
                                                        text: (modelData.count || 0) + ((modelData.count || 0) === 1 ? " game" : " games")
                                                        color: Theme.muted
                                                        font.family: Theme.family
                                                        font.pixelSize: 10
                                                        font.bold: true
                                                    }
                                                }
                                            }

                                            Item { Layout.fillHeight: true }

                                            Text { textFormat: Text.PlainText;
                                                Layout.fillWidth: true
                                                text: modelData.name || "System"
                                                color: Theme.text
                                                font.family: Theme.family
                                                font.pixelSize: 17
                                                font.bold: true
                                                elide: Text.ElideRight
                                            }

                                            Text { textFormat: Text.PlainText;
                                                Layout.fillWidth: true
                                                text: "Plays in " + (modelData.emu || "RetroArch")
                                                color: Theme.muted
                                                font.family: Theme.family
                                                font.pixelSize: 11
                                                elide: Text.ElideRight
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ----------------------------------------------------------
            // TAB 1: LIBRARY
            // ----------------------------------------------------------
            Item {
                id: libraryTabView
                objectName: "bigScreenLibraryTab"
                anchors.fill: parent
                anchors.leftMargin: 44
                anchors.rightMargin: 44
                anchors.topMargin: 20
                anchors.bottomMargin: 16
                visible: consoleRoot.currentTab === 1 && consoleRoot.subView === ""

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 14

                    // Filter chips & sort status row
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10

                        Row {
                            spacing: 8
                            Repeater {
                                model: consoleRoot.libraryChipsModel
                                delegate: BigScreenFocusable {
                                    focusId: "libraryChip_" + index
                                    focusScope: "tab_1"
                                    readonly property bool isActiveChip: consoleRoot.activeLibraryChip === modelData
                                    width: chipText.implicitWidth + 26
                                    height: 36
                                    color: isFocused ? "#ffffff" : (isActiveChip ? "#2d2d2d" : Theme.panel)
                                    border.color: isFocused ? "#ffffff" : (isActiveChip ? "#cfcfcf" : Theme.line)
                                    onActivatedCallback: function() {
                                        consoleRoot.applyLibraryChip(modelData)
                                        consoleRoot.playSound("select")
                                    }
                                    Text { textFormat: Text.PlainText;
                                        id: chipText
                                        anchors.centerIn: parent
                                        text: modelData === "All" && consoleRoot.gameModel
                                              ? ("All · " + consoleRoot.gameModel.totalCount)
                                              : (modelData === "ROMs" && typeof bigScreenView !== "undefined" && bigScreenView
                                                 ? ("ROMs · " + bigScreenView.totalRomCount) : modelData)
                                        color: parent.isFocused ? "#090909" : Theme.text
                                        font.family: Theme.family
                                        font.pixelSize: 13
                                        font.bold: true
                                    }
                                }
                            }
                        }

                        Item { Layout.fillWidth: true }

                        Text { textFormat: Text.PlainText;
                            objectName: "libraryVisibleSummary"
                            text: (consoleRoot.gameModel ? consoleRoot.gameModel.visibleCount : 0) + " shown · "
                                  + consoleRoot.sortChoices[consoleRoot.activeSortIndex].label
                                  + (consoleRoot.activeLibraryLetter ? (" · \"" + consoleRoot.activeLibraryLetter + "\"") : "")
                            color: Theme.muted
                            font.family: Theme.family
                            font.pixelSize: 13
                        }
                    }

                    // Optional controller-friendly A-Z / quick search row (toggled with Y)
                    Rectangle {
                        id: libraryLetterBar
                        objectName: "libraryLetterBar"
                        visible: consoleRoot.librarySearchOpen
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44
                        radius: 6
                        color: "#151515"
                        border.width: 1
                        border.color: Theme.line

                        Row {
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: parent.left
                            anchors.leftMargin: 12
                            spacing: 8

                            Repeater {
                                model: [
                                    { label: "All", query: "" },
                                    { label: "The", query: "the " },
                                    { label: "Star", query: "star" },
                                    { label: "Super", query: "super" },
                                    { label: "Final", query: "final" },
                                    { label: "Dark", query: "dark" },
                                    { label: "Dragon", query: "dragon" },
                                    { label: "Mario", query: "mario" },
                                    { label: "Zelda", query: "zelda" }
                                ]
                                delegate: BigScreenFocusable {
                                    focusId: "libraryLetter_" + index
                                    focusScope: "tab_1"
                                    width: lblTxt.implicitWidth + 20
                                    height: 30
                                    onActivatedCallback: function() {
                                        consoleRoot.applyLibraryLetter(modelData.query)
                                        consoleRoot.playSound("select")
                                    }
                                    Text { textFormat: Text.PlainText;
                                        id: lblTxt
                                        anchors.centerIn: parent
                                        text: modelData.label
                                        color: Theme.text
                                        font.family: Theme.family
                                        font.pixelSize: 12
                                        font.bold: true
                                    }
                                }
                            }
                        }
                    }

                    // Fast cover GridView backed directly by libraryModel
                    GridView {
                        id: cards
                        objectName: "consoleGrid"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        cellWidth: 210
                        cellHeight: 276
                        cacheBuffer: 560
                        model: consoleRoot.gameModel
                        currentIndex: 0
                        ScrollBar.vertical: ScrollBar {
                            policy: ScrollBar.AsNeeded
                        }

                        delegate: Item {
                            width: cards.cellWidth
                            height: cards.cellHeight
                            readonly property bool selectedCard: consoleRoot.focusZone === "libraryGrid" && GridView.isCurrentItem

                            Rectangle {
                                anchors.fill: parent
                                anchors.margins: 7
                                radius: 6
                                color: selectedCard ? "#262626" : Theme.panel
                                border.width: selectedCard ? 2 : 1
                                border.color: selectedCard ? "#ffffff" : Theme.line
                                clip: true

                                Rectangle {
                                    anchors.top: parent.top
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.margins: 2
                                    height: parent.height - 62
                                    radius: 4
                                    gradient: Gradient {
                                        GradientStop {
                                            position: 0.0
                                            color: Qt.hsla(consoleRoot.titleHue(title) / 360.0, 0.42, 0.24, 0.85)
                                        }
                                        GradientStop { position: 1.0; color: "#151515" }
                                    }
                                    Text { textFormat: Text.PlainText;
                                        visible: !tileCover.artworkReady
                                        anchors.centerIn: parent
                                        width: parent.width - 24
                                        text: title
                                        color: "#66ffffff"
                                        font.family: Theme.family
                                        font.pixelSize: 16
                                        font.bold: true
                                        wrapMode: Text.WordWrap
                                        horizontalAlignment: Text.AlignHCenter
                                        maximumLineCount: 3
                                        elide: Text.ElideRight
                                    }
                                }

                                MissingCover {
                                    id: tileCover
                                    anchors.top: parent.top
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.margins: 2
                                    height: parent.height - 62
                                    loadEnabled: consoleRoot.visible
                                    artworkSource: coverUrl
                                    opacity: artworkReady ? 1.0 : 0.0
                                }

                                // Source badge & installed dot overlay on cover
                                RowLayout {
                                    anchors.top: parent.top
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.margins: 10

                                    Rectangle {
                                        height: 20
                                        width: Math.min(130, tileSourceText.implicitWidth + 12)
                                        radius: 4
                                        color: "#d9121212"
                                        border.width: 1
                                        border.color: Theme.line
                                        Text { textFormat: Text.PlainText;
                                            id: tileSourceText
                                            anchors.centerIn: parent
                                            width: parent.width - 8
                                            text: sources || "Library"
                                            color: Theme.text
                                            font.family: Theme.family
                                            font.pixelSize: 10
                                            font.bold: true
                                            elide: Text.ElideRight
                                            horizontalAlignment: Text.AlignHCenter
                                        }
                                    }

                                    Item { Layout.fillWidth: true }

                                    Rectangle {
                                        objectName: "consoleTileUpdateBadge"
                                        visible: typeof hasUpdate !== "undefined" && hasUpdate
                                        height: 20
                                        width: tileUpdateText.implicitWidth + 12
                                        radius: 4
                                        color: "#e6192a1e"
                                        border.width: 1
                                        border.color: "#7cc88a"
                                        Text { textFormat: Text.PlainText;
                                            id: tileUpdateText
                                            anchors.centerIn: parent
                                            text: (typeof updateLabel !== "undefined" && updateLabel && updateLabel.length > 0) ? updateLabel : "Update available"
                                            color: "#8ee09e"
                                            font.family: Theme.family
                                            font.pixelSize: 9
                                            font.bold: true
                                        }
                                    }

                                    Rectangle {
                                        visible: installState === "installed" && !(typeof hasUpdate !== "undefined" && hasUpdate)
                                        width: 10; height: 10; radius: 5
                                        color: "#7cc88a"
                                        border.width: 1
                                        border.color: "#111111"
                                    }
                                }

                                Column {
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.bottom: parent.bottom
                                    anchors.margins: 10
                                    spacing: 2

                                    Text { textFormat: Text.PlainText;
                                        width: parent.width
                                        text: title
                                        color: Theme.text
                                        font.family: Theme.family
                                        font.pixelSize: 14
                                        font.bold: true
                                        elide: Text.ElideRight
                                    }

                                    Text { textFormat: Text.PlainText;
                                        width: parent.width
                                        text: stateLabel + " · " + platforms
                                        color: Theme.muted
                                        font.family: Theme.family
                                        font.pixelSize: 11
                                        elide: Text.ElideRight
                                    }
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: {
                                        consoleRoot.focusZone = "libraryGrid"
                                        consoleRoot.focusId = "consoleGrid"
                                        cards.currentIndex = index
                                        consoleRoot.selected(workId)
                                    }
                                    onDoubleClicked: {
                                        consoleRoot.focusZone = "libraryGrid"
                                        consoleRoot.focusId = "consoleGrid"
                                        cards.currentIndex = index
                                        consoleRoot.openGamePage(workId)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ----------------------------------------------------------
            // TAB 2: SYSTEMS (EmulationStation-style carousel)
            // ----------------------------------------------------------
            Item {
                id: systemsTabView
                objectName: "bigScreenSystemsTab"
                anchors.fill: parent
                anchors.leftMargin: 44
                anchors.rightMargin: 44
                anchors.topMargin: 24
                anchors.bottomMargin: 20
                visible: consoleRoot.currentTab === 2 && consoleRoot.subView === ""

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 18

                    RowLayout {
                        Layout.fillWidth: true
                        Text { textFormat: Text.PlainText;
                            text: "Systems"
                            color: Theme.text
                            font.family: Theme.family
                            font.pixelSize: 24
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText;
                            objectName: "systemsTotalSummary"
                            text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.systems.length : 0)
                                  + " systems · "
                                  + ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.totalRomCount : 0)
                                  + " ROMs"
                            color: Theme.muted
                            font.family: Theme.family
                            font.pixelSize: 14
                        }
                    }

                    // Horizontal EmulationStation-style carousel of system cards
                    Flickable {
                        id: systemsCarouselFlick
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        contentWidth: systemsCarouselRow.implicitWidth
                        contentHeight: height
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds

                        Row {
                            id: systemsCarouselRow
                            height: parent.height
                            spacing: 18

                            Repeater {
                                model: (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.systems : []
                                delegate: BigScreenFocusable {
                                    focusId: "systemCard_" + index
                                    focusScope: "tab_2"
                                    width: 310
                                    height: Math.max(240, systemsCarouselRow.height - 8)
                                    clip: true
                                    onActivatedCallback: function() {
                                        if (modelData && modelData.id) consoleRoot.openSystemDetail(modelData.id)
                                    }

                                    // Subtle system-hue gradient backdrop
                                    Rectangle {
                                        anchors.fill: parent
                                        anchors.margins: 2
                                        radius: 5
                                        gradient: Gradient {
                                            GradientStop {
                                                position: 0.0
                                                color: Qt.hsla(((modelData.hue || 205) % 360) / 360.0, 0.45, 0.22, 0.55)
                                            }
                                            GradientStop {
                                                position: 1.0
                                                color: "#151515"
                                            }
                                        }
                                    }

                                    // Top accent bar
                                    Rectangle {
                                        anchors.top: parent.top
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.margins: 2
                                        height: 5
                                        radius: 3
                                        color: Qt.hsla(((modelData.hue || 205) % 360) / 360.0, 0.6, 0.55, 0.95)
                                    }

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 24
                                        spacing: 10

                                        Text { textFormat: Text.PlainText;
                                            text: (modelData.maker || "Console").toUpperCase()
                                            color: Theme.muted
                                            font.family: Theme.family
                                            font.pixelSize: 12
                                            font.bold: true
                                            font.letterSpacing: 1.4
                                        }

                                        Item { Layout.fillHeight: true }

                                        Text { textFormat: Text.PlainText;
                                            Layout.fillWidth: true
                                            text: modelData.name || "System"
                                            color: Theme.text
                                            font.family: Theme.family
                                            font.pixelSize: 30
                                            font.bold: true
                                            wrapMode: Text.WordWrap
                                        }

                                        Text { textFormat: Text.PlainText;
                                            Layout.fillWidth: true
                                            text: (modelData.count || 0) + " games · " + (modelData.emu || "RetroArch")
                                            color: Theme.muted
                                            font.family: Theme.family
                                            font.pixelSize: 14
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // Bottom "Needs sorting" notice card for nintendo_multi & unknown
                    BigScreenFocusable {
                        id: systemNeedsSortingCard
                        focusId: "systemNeedsSortingCard"
                        focusScope: "tab_2"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 54
                        onActivatedCallback: function() {
                            var nCnt = (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.needsSortingNintendoCount : 0
                            consoleRoot.openSystemDetail(nCnt > 0 ? "nintendo_multi" : "unknown")
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 18
                            anchors.rightMargin: 18

                            Text { textFormat: Text.PlainText;
                                objectName: "systemsNeedsSortingText"
                                Layout.fillWidth: true
                                text: {
                                    var notice = (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.needsSortingNotice : ""
                                    return notice || "All discovered ROM folders are sorted by system."
                                }
                                color: Theme.muted
                                font.family: Theme.family
                                font.pixelSize: 13
                                elide: Text.ElideRight
                            }

                            Text { textFormat: Text.PlainText;
                                visible: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.needsSortingNotice : "").length > 0
                                text: "Inspect files ›"
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 13
                                font.bold: true
                            }
                        }
                    }
                }
            }

            // ----------------------------------------------------------
            // TAB 3: FRIENDS & PARTY
            // ----------------------------------------------------------
            Item {
                id: friendsTabView
                objectName: "bigScreenFriendsTab"
                anchors.fill: parent
                anchors.leftMargin: 44
                anchors.rightMargin: 44
                anchors.topMargin: 24
                anchors.bottomMargin: 20
                visible: consoleRoot.currentTab === 3 && consoleRoot.subView === ""

                RowLayout {
                    anchors.fill: parent
                    spacing: 24

                    // Left Column: Party
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 460
                        radius: 8
                        color: "#141414"
                        border.width: 1
                        border.color: Theme.line

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 12

                            RowLayout {
                                Layout.fillWidth: true
                                Text { textFormat: Text.PlainText;
                                    text: "Party"
                                    color: Theme.text
                                    font.family: Theme.family
                                    font.pixelSize: 20
                                    font.bold: true
                                }
                                Item { Layout.fillWidth: true }
                                Text { textFormat: Text.PlainText;
                                    text: (typeof xodusSocialView !== "undefined" && xodusSocialView && xodusSocialView.partyActive)
                                          ? "Active" : "Ready"
                                    color: Theme.muted
                                    font.family: Theme.family
                                    font.pixelSize: 13
                                }
                            }

                            Text { textFormat: Text.PlainText;
                                Layout.fillWidth: true
                                text: (typeof xodusSocialView !== "undefined" && xodusSocialView && xodusSocialView.statusText)
                                      ? xodusSocialView.statusText
                                      : "Start a party or join a friend to talk while you play."
                                color: Theme.muted
                                font.family: Theme.family
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }

                            BigScreenFocusable {
                                focusId: "partyStartRow"
                                focusScope: "tab_3"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof xodusSocialView !== "undefined" && xodusSocialView) {
                                        if (xodusSocialView.partyActive && typeof xodusSocialView.leaveParty === "function") {
                                            xodusSocialView.leaveParty()
                                            consoleRoot.showToast("Left voice party")
                                        } else if (typeof xodusSocialView.createParty === "function") {
                                            xodusSocialView.createParty()
                                            consoleRoot.showToast("Starting voice party…")
                                        }
                                    } else {
                                        consoleRoot.showToast("Party service ready")
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText;
                                        text: (typeof xodusSocialView !== "undefined" && xodusSocialView && xodusSocialView.partyActive)
                                              ? "Leave party" : "Start a party"
                                        color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true
                                    }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText; text: "Xbox Party ›"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "partyMicRow"
                                focusScope: "tab_3"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof xboxVoiceMedia !== "undefined" && xboxVoiceMedia && typeof xboxVoiceMedia.setMuted === "function") {
                                        xboxVoiceMedia.setMuted(!xboxVoiceMedia.muted)
                                        consoleRoot.showToast(xboxVoiceMedia.muted ? "Microphone muted" : "Microphone unmuted")
                                    } else {
                                        consoleRoot.showToast("Microphone toggled")
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Microphone"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        text: (typeof xboxVoiceMedia !== "undefined" && xboxVoiceMedia && xboxVoiceMedia.muted) ? "Muted" : "Unmuted"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "partyDeafenRow"
                                focusScope: "tab_3"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof xboxVoiceMedia !== "undefined" && xboxVoiceMedia && typeof xboxVoiceMedia.setDeafened === "function") {
                                        xboxVoiceMedia.setDeafened(!xboxVoiceMedia.deafened)
                                        consoleRoot.showToast(xboxVoiceMedia.deafened ? "Party audio deafened" : "Party audio active")
                                    } else {
                                        consoleRoot.showToast("Party audio toggled")
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Party audio"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        text: (typeof xboxVoiceMedia !== "undefined" && xboxVoiceMedia && xboxVoiceMedia.deafened) ? "Deafened" : "On"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "partyRefreshRow"
                                focusScope: "tab_3"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof platformSocial !== "undefined" && platformSocial && typeof platformSocial.refresh === "function") {
                                        platformSocial.refresh()
                                    }
                                    consoleRoot.showToast("Refreshed friends presence")
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Refresh presence"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText; text: "Sync ›"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                                }
                            }

                            Item { Layout.fillHeight: true }
                        }
                    }

                    // Right Column: Friends List
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 540
                        radius: 8
                        color: "#141414"
                        border.width: 1
                        border.color: Theme.line

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 12

                            RowLayout {
                                Layout.fillWidth: true
                                Text { textFormat: Text.PlainText;
                                    text: "Friends"
                                    color: Theme.text
                                    font.family: Theme.family
                                    font.pixelSize: 20
                                    font.bold: true
                                }
                                Item { Layout.fillWidth: true }
                                Text { textFormat: Text.PlainText;
                                    text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.onlineFriendsCount : 0) + " online"
                                    color: Theme.muted
                                    font.family: Theme.family
                                    font.pixelSize: 13
                                }
                            }

                            Flickable {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                contentWidth: width
                                contentHeight: friendsColumn.implicitHeight
                                clip: true

                                Column {
                                    id: friendsColumn
                                    width: parent.width
                                    spacing: 10

                                    Repeater {
                                        model: (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.friendsList : []
                                        delegate: BigScreenFocusable {
                                            focusId: "friendRow_" + index
                                            focusScope: "tab_3"
                                            width: friendsColumn.width
                                            height: 58
                                            onActivatedCallback: function() {
                                                var act = modelData.joinable ? "Joining " : "Invited "
                                                consoleRoot.showToast(act + modelData.name)
                                                consoleRoot.playSound("select")
                                            }

                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.leftMargin: 14
                                                anchors.rightMargin: 14
                                                spacing: 12

                                                Rectangle {
                                                    width: 36; height: 36; radius: 18
                                                    color: "#2a2a2a"
                                                    border.width: 1
                                                    border.color: modelData.online ? "#7cc88a" : Theme.line
                                                    Text { textFormat: Text.PlainText;
                                                        anchors.centerIn: parent
                                                        text: modelData.avatar || "FR"
                                                        color: Theme.text
                                                        font.family: Theme.family
                                                        font.pixelSize: 12
                                                        font.bold: true
                                                    }
                                                }

                                                ColumnLayout {
                                                    Layout.fillWidth: true
                                                    spacing: 2
                                                    Text { textFormat: Text.PlainText;
                                                        Layout.fillWidth: true
                                                        text: modelData.name || "Friend"
                                                        color: Theme.text
                                                        font.family: Theme.family
                                                        font.pixelSize: 15
                                                        font.bold: true
                                                        elide: Text.ElideRight
                                                    }
                                                    Text { textFormat: Text.PlainText;
                                                        Layout.fillWidth: true
                                                        text: modelData.status || "Online"
                                                        color: Theme.muted
                                                        font.family: Theme.family
                                                        font.pixelSize: 12
                                                        elide: Text.ElideRight
                                                    }
                                                }

                                                Text { textFormat: Text.PlainText;
                                                    text: modelData.joinable ? "Join ›" : (modelData.online ? "Invite ›" : "Offline")
                                                    color: modelData.online ? Theme.text : Theme.muted
                                                    font.family: Theme.family
                                                    font.pixelSize: 13
                                                    font.bold: modelData.online
                                                }
                                            }
                                        }
                                    }

                                    BigScreenFocusable {
                                        focusId: "friendsConnectRow"
                                        focusScope: "tab_3"
                                        visible: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.friendsList.length : 0) === 0
                                        width: friendsColumn.width
                                        height: 58
                                        onActivatedCallback: function() {
                                            if (typeof platformSocial !== "undefined" && platformSocial && typeof platformSocial.refresh === "function") {
                                                platformSocial.refresh()
                                            }
                                            consoleRoot.showToast("Checking cached Steam & Xbox friends…")
                                            consoleRoot.playSound("select")
                                        }
                                        RowLayout {
                                            anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                            Text { textFormat: Text.PlainText; text: "Load cached Steam & Xbox friends"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                            Item { Layout.fillWidth: true }
                                            Text { textFormat: Text.PlainText; text: "Refresh ›"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ----------------------------------------------------------
            // TAB 4: CONTROLLERS
            // ----------------------------------------------------------
            Item {
                id: controllersTabView
                objectName: "bigScreenControllersTab"
                anchors.fill: parent
                anchors.leftMargin: 44
                anchors.rightMargin: 44
                anchors.topMargin: 24
                anchors.bottomMargin: 20
                visible: consoleRoot.currentTab === 4 && consoleRoot.subView === ""

                RowLayout {
                    anchors.fill: parent
                    spacing: 24

                    // Left Column: Active Controller settings
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 500
                        radius: 8
                        color: "#141414"
                        border.width: 1
                        border.color: Theme.line

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 10

                            Text { textFormat: Text.PlainText;
                                objectName: "controllersActiveDeviceName"
                                text: (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.controllerName : "Xbox Wireless Controller"
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 20
                                font.bold: true
                            }

                            Text { textFormat: Text.PlainText;
                                text: (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.controllerStatusText : "Connected"
                                color: Theme.muted
                                font.family: Theme.family
                                font.pixelSize: 13
                            }

                            BigScreenFocusable {
                                focusId: "ctrlProfileRow"
                                focusScope: "tab_4"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof bigScreenView !== "undefined" && bigScreenView) {
                                        var nextProf = bigScreenView.cycleDefaultProfile()
                                        consoleRoot.showToast("Default profile: " + nextProf)
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Profile"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "ctrlProfileValue"
                                        text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.activeProfileName : "Default") + " ›"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "ctrlVirtualPadRow"
                                focusScope: "tab_4"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof bigScreenView !== "undefined" && bigScreenView) {
                                        var nextPad = bigScreenView.cycleVirtualPadType()
                                        consoleRoot.showToast("Virtual pad: " + nextPad)
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Virtual pad type"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "ctrlVirtualPadValue"
                                        text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.virtualPadLabel : "Default") + " ›"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "ctrlBindWizardRow"
                                focusScope: "tab_4"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    consoleRoot.bindWizardOpen = true
                                    consoleRoot.playSound("open")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Bind the whole controller"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText; text: "›"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 15 }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "ctrlGyroRow"
                                focusScope: "tab_4"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof bigScreenView !== "undefined" && bigScreenView) {
                                        var nextGyro = bigScreenView.cycleGyroPreset()
                                        consoleRoot.showToast("Gyro: " + nextGyro)
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Gyro"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "ctrlGyroValue"
                                        text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.gyroLabel : "Off · use right stick") + " ›"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "ctrlRumbleRow"
                                focusScope: "tab_4"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof bigScreenView !== "undefined" && bigScreenView) {
                                        var nextRumble = bigScreenView.toggleRumble()
                                        consoleRoot.showToast("Rumble: " + nextRumble)
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Rumble"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "ctrlRumbleValue"
                                        text: (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.rumbleLabel : "On"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            Item { Layout.fillHeight: true }
                        }
                    }

                    // Right Column: "This game" per-game controller overrides
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 500
                        radius: 8
                        color: "#141414"
                        border.width: 1
                        border.color: Theme.line

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 10

                            Text { textFormat: Text.PlainText;
                                text: "This game"
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 20
                                font.bold: true
                            }

                            Text { textFormat: Text.PlainText;
                                Layout.fillWidth: true
                                text: "Overrides for " + ((consoleRoot.heroOverview && consoleRoot.heroOverview.title) ? consoleRoot.heroOverview.title : "selected game")
                                color: Theme.muted
                                font.family: Theme.family
                                font.pixelSize: 13
                                elide: Text.ElideRight
                            }

                            BigScreenFocusable {
                                focusId: "ctrlGameProfileRow"
                                focusScope: "tab_4"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    var gid = consoleRoot.selectedId || consoleRoot.heroGameId
                                    if (gid && typeof bigScreenView !== "undefined" && bigScreenView) {
                                        var nextLbl = bigScreenView.cycleGameProfile(gid)
                                        consoleRoot.showToast("Game controller profile: " + nextLbl)
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: "Profile for " + ((consoleRoot.heroOverview && consoleRoot.heroOverview.title) ? consoleRoot.heroOverview.title : "this game")
                                        color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true
                                        elide: Text.ElideRight
                                    }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "ctrlGameProfileValue"
                                        text: ((consoleRoot.metaRev >= 0 && typeof bigScreenView !== "undefined" && bigScreenView)
                                               ? bigScreenView.gameProfileLabel(consoleRoot.selectedId || consoleRoot.heroGameId)
                                               : "Use default") + " ›"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "ctrlStickTuningRow"
                                focusScope: "tab_4"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof bigScreenStickStudio !== "undefined" && bigScreenStickStudio.dirty) {
                                        consoleRoot.showToast("Save or discard current controller edits first")
                                        return
                                    }
                                    stickTuningOpen = true
                                    bigScreenStickStudio.activeTab = "Sticks"
                                    if (typeof unifiedController !== "undefined" && unifiedController && !unifiedController.busy) {
                                        if (!unifiedController.available) unifiedController.refresh()
                                        else consoleRoot.loadBigScreenStickProfile()
                                    }
                                    consoleRoot.playSound("open")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Stick tuning"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "ctrlStickTuningValue"
                                        text: (typeof unifiedController !== "undefined" && unifiedController ? (unifiedController.editingProfile || "Default") : "Unavailable") + " · Sticks ›"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "ctrlTestRow"
                                focusScope: "tab_4"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    consoleRoot.liveControllerTestOpen = !consoleRoot.liveControllerTestOpen
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Test buttons and sticks"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText; text: consoleRoot.liveControllerTestOpen ? "Hide ›" : "›"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 14 }
                                }
                            }

                            // Deadband (Kian's controller hardware app: onboard profiles, deadzones, curves, lighting).
                            // A mouse-and-keyboard window, so Big Screen steps back to desktop before opening it.
                            BigScreenFocusable {
                                focusId: "ctrlDeadbandRow"
                                focusScope: "tab_4"
                                objectName: "ctrlDeadbandRow"
                                visible: typeof controllerDeviceCommands !== "undefined" && controllerDeviceCommands
                                         && controllerDeviceCommands.deadbandAvailable
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    consoleRoot.playSound("open")
                                    consoleRoot.desktopRequested()
                                    controllerDeviceCommands.openDeadband()
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Controller hardware (Deadband)"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText; text: "Onboard profiles, deadzones, lighting ›"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                                }
                            }

                            Rectangle {
                                objectName: "ctrlLiveTestReadout"
                                visible: consoleRoot.liveControllerTestOpen
                                Layout.fillWidth: true
                                Layout.preferredHeight: 82
                                radius: 6
                                color: "#101010"
                                border.width: 1
                                border.color: Theme.line

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 14
                                    spacing: 4
                                    Text { textFormat: Text.PlainText;
                                        text: "LIVE INPUT MONITOR"
                                        color: Theme.muted
                                        font.family: Theme.family
                                        font.pixelSize: 11
                                        font.bold: true
                                    }
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: "Left Stick: (0.00, 0.00) · Right Stick: (0.00, 0.00) · Triggers: LT 0% / RT 0%"
                                        color: Theme.text
                                        font.family: Theme.family
                                        font.pixelSize: 13
                                        elide: Text.ElideRight
                                    }
                                }
                            }

                            Item { Layout.fillHeight: true }
                        }
                    }
                }
            }

            // ----------------------------------------------------------
            // TAB 5: SETTINGS (Console subset: Sounds + Display & system)
            // ----------------------------------------------------------
            Item {
                id: settingsTabView
                objectName: "bigScreenSettingsTab"
                anchors.fill: parent
                anchors.leftMargin: 44
                anchors.rightMargin: 44
                anchors.topMargin: 24
                anchors.bottomMargin: 20
                visible: consoleRoot.currentTab === 5 && consoleRoot.subView === ""

                RowLayout {
                    anchors.fill: parent
                    spacing: 24

                    // Left Column: Sounds
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 500
                        radius: 8
                        color: "#141414"
                        border.width: 1
                        border.color: Theme.line

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 10

                            Text { textFormat: Text.PlainText;
                                text: "Sounds"
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 20
                                font.bold: true
                            }

                            BigScreenFocusable {
                                focusId: "setSoundEnabledRow"
                                focusScope: "tab_5"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof uiSounds !== "undefined" && uiSounds) {
                                        uiSounds.setEnabled(!uiSounds.enabled)
                                        if (uiSounds.enabled) uiSounds.play("select")
                                        consoleRoot.showToast("Interface sounds: " + (uiSounds.enabled ? "On" : "Off"))
                                    }
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Interface sounds"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "setSoundEnabledValue"
                                        text: (typeof uiSounds !== "undefined" && uiSounds && uiSounds.enabled) ? "On" : "Off"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "setSoundPackRow"
                                focusScope: "tab_5"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    consoleRoot.cycleSoundPack()
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Sound pack"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "setSoundPackValue"
                                        text: consoleRoot.currentSoundPackLabel + " ›"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "setSoundVolumeRow"
                                focusScope: "tab_5"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    var cur = (typeof uiSounds !== "undefined" && uiSounds) ? uiSounds.volume : 70
                                    var next = cur >= 100 ? 20 : cur + 20
                                    if (typeof uiSounds !== "undefined" && uiSounds) {
                                        uiSounds.setVolume(next)
                                        consoleRoot.playSound("select")
                                        consoleRoot.showToast("Volume: " + uiSounds.volume + "%")
                                    }
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Volume"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "setSoundVolumeValue"
                                        text: ((typeof uiSounds !== "undefined" && uiSounds) ? uiSounds.volume : 70) + "%"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            Item { Layout.fillHeight: true }
                        }
                    }

                    // Right Column: Display & system
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 500
                        radius: 8
                        color: "#141414"
                        border.width: 1
                        border.color: Theme.line

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 10

                            Text { textFormat: Text.PlainText;
                                text: "Display & system"
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 20
                                font.bold: true
                            }

                            BigScreenFocusable {
                                focusId: "setMonitorRow"
                                focusScope: "tab_5"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof bigScreenView !== "undefined" && bigScreenView) {
                                        var nextMon = bigScreenView.cycleDefaultMonitor()
                                        consoleRoot.showToast("Game monitor: " + nextMon)
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Game monitor"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "setMonitorValue"
                                        text: ((consoleRoot.metaRev >= 0 && typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.defaultMonitorLabel() : "Wherever the game opens") + " ›"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "setStartBigScreenRow"
                                focusScope: "tab_5"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof bigScreenView !== "undefined" && bigScreenView) {
                                        var on = bigScreenView.toggleStartInBigScreen()
                                        consoleRoot.showToast("Start in Big Screen: " + (on ? "On" : "Off"))
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Start in Big Screen"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "setStartBigScreenValue"
                                        text: (typeof bigScreenView !== "undefined" && bigScreenView && bigScreenView.startInBigScreen) ? "On" : "Off"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "setAssistantRow"
                                focusScope: "tab_5"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    if (typeof bigScreenView !== "undefined" && bigScreenView) {
                                        var summary = bigScreenView.cycleAssistantSetting()
                                        consoleRoot.showToast("Assistant indicator: " + summary)
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Show what the assistant is doing"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "setAssistantValue"
                                        text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.assistantSummary : "On · 1.0×") + " ›"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "setExitDesktopRow"
                                focusScope: "tab_5"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 50
                                onActivatedCallback: function() {
                                    consoleRoot.playSound("back")
                                    consoleRoot.desktopRequested()
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Exit to desktop"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText; text: "›"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 15 }
                                }
                            }

                            Item { Layout.fillHeight: true }
                        }
                    }
                }
            }

            // ----------------------------------------------------------
            // SUB-VIEW A: SYSTEM GAME LIST & ROM DETAIL
            // ----------------------------------------------------------
            Item {
                id: systemDetailView
                objectName: "bigScreenSystemDetailView"
                anchors.fill: parent
                anchors.leftMargin: 44
                anchors.rightMargin: 44
                anchors.topMargin: 22
                anchors.bottomMargin: 18
                visible: consoleRoot.subView === "system"

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 14

                    RowLayout {
                        Layout.fillWidth: true
                        Text { textFormat: Text.PlainText;
                            objectName: "systemDetailTitle"
                            text: (consoleRoot.currentSystemInfo && consoleRoot.currentSystemInfo.name) ? consoleRoot.currentSystemInfo.name : "System"
                            color: Theme.text
                            font.family: Theme.family
                            font.pixelSize: 24
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText;
                            objectName: "systemDetailMeta"
                            text: (consoleRoot.currentSystemRoms ? consoleRoot.currentSystemRoms.length : 0)
                                  + " games · plays in "
                                  + ((consoleRoot.currentSystemInfo && consoleRoot.currentSystemInfo.emu) ? consoleRoot.currentSystemInfo.emu : "RetroArch")
                            color: Theme.muted
                            font.family: Theme.family
                            font.pixelSize: 14
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 22

                        // Left: Scrollable ROM List
                        ListView {
                            id: systemRomListView
                            objectName: "systemRomList"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.preferredWidth: 520
                            clip: true
                            spacing: 8
                            model: consoleRoot.currentSystemRoms
                            currentIndex: consoleRoot.systemRomIndex
                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                            delegate: Rectangle {
                                width: systemRomListView.width
                                height: 50
                                radius: 6
                                readonly property bool rowFocused: consoleRoot.focusZone === "systemRomList" && index === consoleRoot.systemRomIndex
                                color: rowFocused ? "#272727" : Theme.panel
                                border.width: rowFocused ? 2 : 1
                                border.color: rowFocused ? "#ffffff" : Theme.line

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 16
                                    anchors.rightMargin: 16
                                    spacing: 12

                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: modelData.title || modelData.file_name || "ROM"
                                        color: Theme.text
                                        font.family: Theme.family
                                        font.pixelSize: 15
                                        font.bold: true
                                        elide: Text.ElideRight
                                    }

                                    Text { textFormat: Text.PlainText;
                                        text: modelData.region || modelData.emu || ""
                                        color: Theme.muted
                                        font.family: Theme.family
                                        font.pixelSize: 12
                                    }
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: {
                                        consoleRoot.focusZone = "systemRomList"
                                        consoleRoot.focusId = "systemRomList"
                                        consoleRoot.systemRomIndex = index
                                    }
                                    onDoubleClicked: {
                                        consoleRoot.systemRomIndex = index
                                        consoleRoot.navigate("accept")
                                    }
                                }
                            }
                        }

                        // Right: Selected ROM Detail Card
                        Rectangle {
                            id: systemRomDetailCard
                            objectName: "systemRomDetail"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.preferredWidth: 520
                            radius: 8
                            color: "#151515"
                            border.width: 1
                            border.color: Theme.line

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 22
                                spacing: 14

                                // Box art / stylized cover banner
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 170
                                    radius: 6
                                    color: "#1d1d1d"
                                    border.width: 1
                                    border.color: Theme.line
                                    clip: true

                                    MissingCover {
                                        anchors.fill: parent
                                        artworkSource: consoleRoot.selectedRomCoverUrl
                                        opacity: artworkReady ? 0.85 : 0.0
                                    }

                                    Rectangle {
                                        anchors.fill: parent
                                        gradient: Gradient {
                                            GradientStop {
                                                position: 0.0
                                                color: Qt.hsla((((consoleRoot.selectedRom && consoleRoot.selectedRom.hue) || 205) % 360) / 360.0, 0.45, 0.25, 0.55)
                                            }
                                            GradientStop { position: 1.0; color: "#cc111111" }
                                        }
                                    }

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 16
                                        RowLayout {
                                            Rectangle {
                                                height: 22
                                                width: sysTagTxt.implicitWidth + 14
                                                radius: 4
                                                color: "#cc141414"
                                                border.width: 1
                                                border.color: Theme.line
                                                Text { textFormat: Text.PlainText;
                                                    id: sysTagTxt
                                                    anchors.centerIn: parent
                                                    text: (consoleRoot.selectedRom && consoleRoot.selectedRom.systemName) ? consoleRoot.selectedRom.systemName : "ROM"
                                                    color: Theme.text
                                                    font.family: Theme.family
                                                    font.pixelSize: 11
                                                    font.bold: true
                                                }
                                            }
                                            Item { Layout.fillWidth: true }
                                            Text { textFormat: Text.PlainText;
                                                text: (consoleRoot.selectedRom && consoleRoot.selectedRom.region) ? consoleRoot.selectedRom.region : ""
                                                color: Theme.muted
                                                font.family: Theme.family
                                                font.pixelSize: 12
                                            }
                                        }
                                        Item { Layout.fillHeight: true }
                                        Text { textFormat: Text.PlainText;
                                            Layout.fillWidth: true
                                            text: (consoleRoot.selectedRom && consoleRoot.selectedRom.title) ? consoleRoot.selectedRom.title : "Select a ROM"
                                            color: Theme.text
                                            font.family: Theme.family
                                            font.pixelSize: 22
                                            font.bold: true
                                            elide: Text.ElideRight
                                        }
                                    }
                                }

                                // ROM Metadata grid
                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 2
                                    columnSpacing: 16
                                    rowSpacing: 8

                                    Text { textFormat: Text.PlainText; text: "System"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: (consoleRoot.selectedRom && consoleRoot.selectedRom.systemName) ? consoleRoot.selectedRom.systemName : "—"
                                        color: Theme.text; font.family: Theme.family; font.pixelSize: 13; font.bold: true
                                        elide: Text.ElideRight
                                    }

                                    Text { textFormat: Text.PlainText; text: "Emulator"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: (consoleRoot.selectedRom && consoleRoot.selectedRom.emu) ? consoleRoot.selectedRom.emu : "—"
                                        color: Theme.text; font.family: Theme.family; font.pixelSize: 13; font.bold: true
                                        elide: Text.ElideRight
                                    }

                                    Text { textFormat: Text.PlainText; text: "Controller"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: (consoleRoot.metaRev >= 0 && consoleRoot.selectedRom && consoleRoot.selectedRom.rom_id && typeof bigScreenView !== "undefined" && bigScreenView)
                                              ? bigScreenView.gameProfileLabel(consoleRoot.selectedRom.rom_id)
                                              : "Use default"
                                        color: Theme.text; font.family: Theme.family; font.pixelSize: 13
                                        elide: Text.ElideRight
                                    }

                                    Text { textFormat: Text.PlainText; text: "Last played"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: (consoleRoot.selectedRom && consoleRoot.selectedRom.lastPlayed) ? consoleRoot.selectedRom.lastPlayed : "Never"
                                        color: Theme.text; font.family: Theme.family; font.pixelSize: 13
                                        elide: Text.ElideRight
                                    }

                                    Text { textFormat: Text.PlainText; text: "Status"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                                    Text { textFormat: Text.PlainText;
                                        Layout.fillWidth: true
                                        text: (consoleRoot.selectedRom && consoleRoot.selectedRom.status) ? consoleRoot.selectedRom.status : "Ready"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 12
                                        elide: Text.ElideRight
                                    }
                                }

                                Item { Layout.fillHeight: true }

                                // ROM Actions
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 12

                                    BigScreenFocusable {
                                        focusId: "romPlayButton"
                                        focusScope: "system"
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 46
                                        color: isFocused ? "#ffffff" : "#2b2b2b"
                                        onActivatedCallback: function() {
                                            if (consoleRoot.selectedRom && consoleRoot.selectedRom.rom_id && typeof bigScreenView !== "undefined" && bigScreenView) {
                                                var ok = bigScreenView.playRom(consoleRoot.selectedRom.rom_id)
                                                consoleRoot.playSound("launch")
                                                consoleRoot.showToast(ok ? ("Launching " + consoleRoot.selectedRom.title) : ("Selected " + consoleRoot.selectedRom.title))
                                            }
                                        }
                                        Text { textFormat: Text.PlainText;
                                            anchors.centerIn: parent
                                            text: "▶  Play in " + ((consoleRoot.selectedRom && consoleRoot.selectedRom.emu) ? consoleRoot.selectedRom.emu : "Emulator")
                                            color: parent.isFocused ? "#090909" : Theme.text
                                            font.family: Theme.family
                                            font.pixelSize: 14
                                            font.bold: true
                                        }
                                    }

                                    BigScreenFocusable {
                                        focusId: "romProfileButton"
                                        focusScope: "system"
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 46
                                        onActivatedCallback: function() {
                                            if (consoleRoot.selectedRom && consoleRoot.selectedRom.rom_id && typeof bigScreenView !== "undefined" && bigScreenView) {
                                                var nextProf = bigScreenView.cycleGameProfile(consoleRoot.selectedRom.rom_id)
                                                consoleRoot.showToast("ROM controller profile: " + nextProf)
                                                consoleRoot.playSound("select")
                                            }
                                        }
                                        Text { textFormat: Text.PlainText;
                                            anchors.centerIn: parent
                                            text: "Profile: " + ((consoleRoot.metaRev >= 0 && consoleRoot.selectedRom && consoleRoot.selectedRom.rom_id && typeof bigScreenView !== "undefined" && bigScreenView)
                                                                 ? bigScreenView.gameProfileLabel(consoleRoot.selectedRom.rom_id) : "Use default")
                                            color: Theme.text
                                            font.family: Theme.family
                                            font.pixelSize: 13
                                            font.bold: true
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ----------------------------------------------------------
            // SUB-VIEW B: FULL-SCREEN GAME PAGE
            // ----------------------------------------------------------
            Item {
                id: gamePageView
                objectName: "bigScreenGamePage"
                anchors.fill: parent
                anchors.leftMargin: 44
                anchors.rightMargin: 44
                anchors.topMargin: 24
                anchors.bottomMargin: 20
                visible: consoleRoot.subView === "game"

                readonly property bool detailIsPinned: {
                    var rev = consoleRoot.pinnedRevision
                    var gid = consoleRoot.detailGameId || consoleRoot.selectedId
                    if (typeof bigScreenView !== "undefined" && bigScreenView && gid) {
                        return bigScreenView.isPinned(gid)
                    }
                    return false
                }
                readonly property var detailUpdateInfo: consoleRoot.updateInfoFor(consoleRoot.detailGameId || consoleRoot.selectedId)
                readonly property bool detailHasUpdate: !!detailUpdateInfo && !!detailUpdateInfo.productId
                readonly property string detailUpdateBadgeText: detailHasUpdate ? String(detailUpdateInfo.statusLabel || "Update available") : ""
                readonly property bool detailIsSteamPendingUpdate: detailHasUpdate && !!detailUpdateInfo.isSteamPending
                readonly property bool detailIsUpdating: !!detailUpdateInfo && !!detailUpdateInfo.isUpdating

                RowLayout {
                    anchors.fill: parent
                    spacing: 24

                    // Left: Hero Banner with Artwork + Play / Pin / Options
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 640
                        radius: 8
                        color: "#151515"
                        border.width: 1
                        border.color: Theme.line
                        clip: true

                        Rectangle {
                            anchors.fill: parent
                            gradient: Gradient {
                                GradientStop {
                                    position: 0.0
                                    color: Qt.hsla(consoleRoot.titleHue(consoleRoot.detailOverview ? consoleRoot.detailOverview.title : "") / 360.0, 0.45, 0.25, 0.85)
                                }
                                GradientStop { position: 1.0; color: "#121212" }
                            }
                        }

                        MissingCover {
                            anchors.fill: parent
                            artworkSource: consoleRoot.detailCoverUrl
                            opacity: artworkReady ? 0.65 : 0.0
                        }

                        Rectangle {
                            anchors.fill: parent
                            gradient: Gradient {
                                GradientStop { position: 0.0; color: "#22000000" }
                                GradientStop { position: 0.55; color: "#aa0a0a0a" }
                                GradientStop { position: 1.0; color: "#f4080808" }
                            }
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 28
                            spacing: 10

                            Item { Layout.fillHeight: true }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                Text { textFormat: Text.PlainText;
                                    text: ((consoleRoot.detailOverview && consoleRoot.detailOverview.sources)
                                           ? consoleRoot.detailOverview.sources : "Library").toUpperCase()
                                    color: Theme.muted
                                    font.family: Theme.family
                                    font.pixelSize: 12
                                    font.bold: true
                                    font.letterSpacing: 1.6
                                }
                                Rectangle {
                                    objectName: "gamePageUpdateBadge"
                                    visible: gamePageView.detailHasUpdate
                                    height: 24
                                    width: gamePageUpdateBadgeText.implicitWidth + 16
                                    radius: 4
                                    color: gamePageView.detailIsSteamPendingUpdate ? "#2b2418" : "#1c2d22"
                                    border.width: 1
                                    border.color: gamePageView.detailIsSteamPendingUpdate ? "#e0a958" : "#7cc88a"
                                    Text { textFormat: Text.PlainText;
                                        id: gamePageUpdateBadgeText
                                        objectName: "gamePageUpdateBadgeText"
                                        anchors.centerIn: parent
                                        text: gamePageView.detailUpdateBadgeText
                                        color: gamePageView.detailIsSteamPendingUpdate ? "#f0c37b" : "#8ee09e"
                                        font.family: Theme.family
                                        font.pixelSize: 11
                                        font.bold: true
                                    }
                                }
                                Item { Layout.fillWidth: true }
                            }

                            Image {
                                id: gamePageLogo
                                objectName: "gamePageLogo"
                                Layout.preferredWidth: 460; Layout.preferredHeight: 130
                                visible: status === Image.Ready
                                source: consoleRoot.detailLogoUrl
                                fillMode: Image.PreserveAspectFit; horizontalAlignment: Image.AlignLeft
                                asynchronous: true; smooth: true; mipmap: true
                            }
                            Text { textFormat: Text.PlainText;
                                objectName: "gamePageTitle"
                                visible: !gamePageLogo.visible
                                Layout.fillWidth: true
                                text: (consoleRoot.detailOverview && consoleRoot.detailOverview.title)
                                      ? consoleRoot.detailOverview.title
                                      : ((consoleRoot.game && consoleRoot.game.title) ? consoleRoot.game.title : "Game")
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 34
                                font.bold: true
                                wrapMode: Text.WordWrap
                            }

                            Text { textFormat: Text.PlainText;
                                Layout.fillWidth: true
                                text: (consoleRoot.detailOverview && consoleRoot.detailOverview.state)
                                      ? (consoleRoot.detailOverview.state + " · " + (consoleRoot.detailOverview.platforms || "PC"))
                                      : consoleRoot.launchStatus
                                color: Theme.muted
                                font.family: Theme.family
                                font.pixelSize: 14
                                elide: Text.ElideRight
                            }

                            Row {
                                spacing: 12
                                Layout.topMargin: 10

                                BigScreenFocusable {
                                    id: consolePlayBtn
                                    objectName: "consolePlayButton"
                                    focusId: "gamePlayButton"
                                    focusScope: "game"
                                    width: 140
                                    height: 46
                                    enabled: consoleRoot.canPlay || gamePageView.detailHasUpdate
                                    color: isFocused ? (enabled ? "#ffffff" : "#2d2d2d") : "#262626"
                                    onActivatedCallback: function() {
                                        if (consoleRoot.canPlay || gamePageView.detailHasUpdate) {
                                            consoleRoot.triggerPlay()
                                        } else {
                                            consoleRoot.showToast(consoleRoot.launchStatus || "Install or configure launch target first")
                                        }
                                    }
                                    Text { textFormat: Text.PlainText;
                                        anchors.centerIn: parent
                                        text: (consoleRoot.canPlay || gamePageView.detailHasUpdate) ? "▶  Play" : "Unavailable"
                                        color: (parent.isFocused && parent.enabled) ? "#090909" : (parent.enabled ? Theme.text : Theme.muted)
                                        font.family: Theme.family
                                        font.pixelSize: 15
                                        font.bold: true
                                    }
                                }

                                BigScreenFocusable {
                                    id: consoleUpdateBtn
                                    objectName: "consoleUpdateButton"
                                    focusId: "gameUpdateButton"
                                    focusScope: "game"
                                    visible: gamePageView.detailHasUpdate
                                    width: gamePageView.detailIsSteamPendingUpdate ? 188 : 126
                                    height: 46
                                    enabled: !gamePageView.detailIsUpdating
                                    color: isFocused ? "#ffffff" : "#1c2d22"
                                    border.color: isFocused ? "#ffffff" : "#7cc88a"
                                    onActivatedCallback: function() {
                                        var gid = consoleRoot.detailGameId || consoleRoot.selectedId
                                        consoleRoot.triggerUpdateForGame(gid)
                                    }
                                    Text { textFormat: Text.PlainText;
                                        anchors.centerIn: parent
                                        text: gamePageView.detailIsSteamPendingUpdate ? "Open Steam downloads" : (gamePageView.detailIsUpdating ? "Updating…" : "Update")
                                        color: parent.isFocused ? "#090909" : "#8ee09e"
                                        font.family: Theme.family
                                        font.pixelSize: 14
                                        font.bold: true
                                    }
                                }

                                BigScreenFocusable {
                                    focusId: "gamePinButton"
                                    focusScope: "game"
                                    width: 126
                                    height: 46
                                    onActivatedCallback: function() {
                                        var gid = consoleRoot.detailGameId || consoleRoot.selectedId
                                        if (gid && typeof bigScreenView !== "undefined" && bigScreenView) {
                                            var pinned = bigScreenView.togglePin(gid)
                                            consoleRoot.showToast(pinned ? "Pinned to quick access" : "Unpinned")
                                        }
                                        consoleRoot.playSound("select")
                                    }
                                    Text { textFormat: Text.PlainText;
                                        anchors.centerIn: parent
                                        text: gamePageView.detailIsPinned ? "Pinned ★" : "Pin"
                                        color: Theme.text
                                        font.family: Theme.family
                                        font.pixelSize: 14
                                        font.bold: true
                                    }
                                }

                                BigScreenFocusable {
                                    focusId: "gameOptionsButton"
                                    focusScope: "game"
                                    width: 126
                                    height: 46
                                    onActivatedCallback: function() {
                                        consoleRoot.focusId = "gameMonitorRow"
                                        consoleRoot.playSound("move")
                                    }
                                    Text { textFormat: Text.PlainText;
                                        anchors.centerIn: parent
                                        text: "Options"
                                        color: Theme.text
                                        font.family: Theme.family
                                        font.pixelSize: 14
                                        font.bold: true
                                    }
                                }
                            }
                        }
                    }

                    // Right: "About this game" panel
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 460
                        radius: 8
                        color: "#141414"
                        border.width: 1
                        border.color: Theme.line

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 10

                            Text { textFormat: Text.PlainText;
                                text: "About this game"
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 20
                                font.bold: true
                            }

                            BigScreenFocusable {
                                focusId: "gamePlaytimeRow"
                                focusScope: "game"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Play time"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        text: (consoleRoot.detailOverview && consoleRoot.detailOverview.playtime && consoleRoot.detailOverview.playtime !== "Not supplied")
                                              ? String(consoleRoot.detailOverview.playtime).split(" (")[0] : "0m"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "gameLastPlayedRow"
                                focusScope: "game"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Last played"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        text: (consoleRoot.detailOverview && consoleRoot.detailOverview.last_activity && consoleRoot.detailOverview.last_activity !== "Not supplied")
                                              ? consoleRoot.detailOverview.last_activity : "Never"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "gameAchievementsRow"
                                focusScope: "game"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Achievements"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "gameAchievementsValue"
                                        text: (consoleRoot.metaRev >= 0 && typeof bigScreenView !== "undefined" && bigScreenView)
                                              ? bigScreenView.gameAchievementsLabel(consoleRoot.detailGameId || consoleRoot.selectedId)
                                              : "None tracked"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "gameMonitorRow"
                                focusScope: "game"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46
                                onActivatedCallback: function() {
                                    var gid = consoleRoot.detailGameId || consoleRoot.selectedId
                                    if (gid && typeof bigScreenView !== "undefined" && bigScreenView) {
                                        var nextMon = bigScreenView.cycleGameMonitor(gid)
                                        consoleRoot.showToast("Monitor: " + nextMon)
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Monitor"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "gameMonitorValue"
                                        text: ((consoleRoot.metaRev >= 0 && typeof bigScreenView !== "undefined" && bigScreenView)
                                               ? bigScreenView.gameMonitorLabel(consoleRoot.detailGameId || consoleRoot.selectedId)
                                               : "Use default") + " ›"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "gameControllerRow"
                                focusScope: "game"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46
                                onActivatedCallback: function() {
                                    var gid = consoleRoot.detailGameId || consoleRoot.selectedId
                                    if (gid && typeof bigScreenView !== "undefined" && bigScreenView) {
                                        var nextProf = bigScreenView.cycleGameProfile(gid)
                                        consoleRoot.showToast("Controller profile: " + nextProf)
                                    }
                                    consoleRoot.playSound("select")
                                }
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Controller profile"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "gameControllerValue"
                                        text: ((consoleRoot.metaRev >= 0 && typeof bigScreenView !== "undefined" && bigScreenView)
                                               ? bigScreenView.gameProfileLabel(consoleRoot.detailGameId || consoleRoot.selectedId)
                                               : "Use default") + " ›"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            BigScreenFocusable {
                                focusId: "gameModsRow"
                                focusScope: "game"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                                    Text { textFormat: Text.PlainText; text: "Mods"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { textFormat: Text.PlainText;
                                        objectName: "gameModsValue"
                                        text: (consoleRoot.metaRev >= 0 && typeof bigScreenView !== "undefined" && bigScreenView)
                                              ? bigScreenView.gameModsLabel(consoleRoot.detailGameId || consoleRoot.selectedId)
                                              : "None"
                                        color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                                    }
                                }
                            }

                            Item { Layout.fillHeight: true }
                        }
                    }
                }
            }
        }

        // ==============================================================
        // 3. BOTTOM HINT BAR
        // ==============================================================
        Rectangle {
            id: hintBar
            objectName: "bigScreenHintBar"
            Layout.fillWidth: true
            Layout.preferredHeight: 54
            color: "#0b0b0b"

            Rectangle {
                anchors.top: parent.top
                width: parent.width
                height: 1
                color: Theme.line
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 44
                anchors.rightMargin: 44
                spacing: 20

                Row {
                    spacing: 6
                    Rectangle { width: 26; height: 20; radius: 4; color: Theme.panel; border.width: 1; border.color: Theme.main; Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: "LB"; color: Theme.text; font.family: Theme.family; font.pixelSize: 10; font.bold: true } }
                    Rectangle { width: 26; height: 20; radius: 4; color: Theme.panel; border.width: 1; border.color: Theme.main; Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: "RB"; color: Theme.text; font.family: Theme.family; font.pixelSize: 10; font.bold: true } }
                    Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; text: "Switch tabs"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                }

                Item { Layout.fillWidth: true }

                Row {
                    spacing: 18

                    Row {
                        spacing: 6
                        Rectangle { width: 22; height: 20; radius: 4; color: Theme.panel; border.width: 1; border.color: Theme.main; Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: "A"; color: Theme.text; font.family: Theme.family; font.pixelSize: 11; font.bold: true } }
                        Text { textFormat: Text.PlainText;
                            anchors.verticalCenter: parent.verticalCenter
                            text: consoleRoot.subView === "system" ? "Play ROM" : (consoleRoot.currentTab === 1 ? "Open game" : "Select")
                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                        }
                    }

                    Row {
                        spacing: 6
                        Rectangle { width: 22; height: 20; radius: 4; color: Theme.panel; border.width: 1; border.color: Theme.main; Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: "B"; color: Theme.text; font.family: Theme.family; font.pixelSize: 11; font.bold: true } }
                        Text { textFormat: Text.PlainText;
                            anchors.verticalCenter: parent.verticalCenter
                            text: consoleRoot.guideOpen ? "Close Guide" : (consoleRoot.subView !== "" ? "Back" : (consoleRoot.currentTab !== 0 ? "Home" : "Desktop"))
                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                        }
                    }

                    Row {
                        visible: consoleRoot.currentTab === 1 || consoleRoot.subView === "system" || consoleRoot.subView === "game"
                        spacing: 6
                        Rectangle { width: 22; height: 20; radius: 4; color: Theme.panel; border.width: 1; border.color: Theme.main; Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: "X"; color: Theme.text; font.family: Theme.family; font.pixelSize: 11; font.bold: true } }
                        Text { textFormat: Text.PlainText;
                            anchors.verticalCenter: parent.verticalCenter
                            text: consoleRoot.currentTab === 1 && consoleRoot.subView === "" ? "Sort" : "Options"
                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                        }
                    }

                    Row {
                        visible: consoleRoot.currentTab === 1 && consoleRoot.subView === ""
                        spacing: 6
                        Rectangle { width: 22; height: 20; radius: 4; color: Theme.panel; border.width: 1; border.color: Theme.main; Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: "Y"; color: Theme.text; font.family: Theme.family; font.pixelSize: 11; font.bold: true } }
                        Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; text: "Search"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                    }

                    Row {
                        spacing: 6
                        Rectangle { width: 24; height: 20; radius: 4; color: Theme.panel; border.width: 1; border.color: Theme.main; Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: "☰"; color: Theme.text; font.family: Theme.family; font.pixelSize: 11; font.bold: true } }
                        Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; text: "Guide"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                    }
                }
            }
        }
    }

    // ==================================================================
    // 4. GUIDE DRAWER (Menu button / G key — Xbox-style side drawer)
    // ==================================================================
    Rectangle {
        id: guideScrim
        objectName: "bigScreenGuideScrim"
        anchors.fill: parent
        z: 20
        visible: consoleRoot.guideOpen
        color: "#a8000000"

        MouseArea {
            anchors.fill: parent
            onClicked: consoleRoot.toggleGuide()
        }

        Rectangle {
            id: guideDrawer
            objectName: "bigScreenGuideDrawer"
            width: 480
            height: parent.height
            color: "#121212"

            Rectangle {
                anchors.right: parent.right
                width: 1
                height: parent.height
                color: Theme.line
            }

            MouseArea {
                anchors.fill: parent
                onClicked: {} // Absorb clicks inside drawer
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 28
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    Text { textFormat: Text.PlainText;
                        text: "Guide"
                        color: Theme.text
                        font.family: Theme.family
                        font.pixelSize: 22
                        font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    Text { textFormat: Text.PlainText;
                        text: consoleRoot.clockText
                        color: Theme.muted
                        font.family: Theme.family
                        font.pixelSize: 14
                    }
                }

                // Running game section (first when a game is running)
                BigScreenFocusable {
                    focusId: "guideResumeGameRow"
                    focusScope: "guide"
                    visible: consoleRoot.hasActiveGame
                    Layout.fillWidth: true
                    Layout.preferredHeight: 50
                    onActivatedCallback: function() {
                        consoleRoot.toggleGuide()
                    }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        Text { textFormat: Text.PlainText;
                            Layout.fillWidth: true
                            text: consoleRoot.activeGameTitle
                            color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true
                            elide: Text.ElideRight
                        }
                        Text { textFormat: Text.PlainText; text: "Running · Resume ›"; color: "#7cc88a"; font.family: Theme.family; font.pixelSize: 13; font.bold: true }
                    }
                }

                BigScreenFocusable {
                    focusId: "guideQuitGameRow"
                    focusScope: "guide"
                    visible: consoleRoot.hasActiveGame
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    onActivatedCallback: function() {
                        if (typeof launchView !== "undefined" && launchView && typeof launchView.stopActiveGame === "function") {
                            launchView.stopActiveGame()
                        }
                        consoleRoot.showToast("Stopped running game")
                        consoleRoot.toggleGuide()
                    }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        Text { textFormat: Text.PlainText; text: "Quit running game"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText; text: "Stop ›"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                    }
                }

                // Quick Jump Rows
                BigScreenFocusable {
                    focusId: "guideHomeRow"
                    focusScope: "guide"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    onActivatedCallback: function() { consoleRoot.switchTab(0) }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        Text { textFormat: Text.PlainText; text: "Home"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText; text: "Jump back in"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                    }
                }

                BigScreenFocusable {
                    focusId: "guideLibraryRow"
                    focusScope: "guide"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    onActivatedCallback: function() { consoleRoot.switchTab(1) }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        Text { textFormat: Text.PlainText; text: "Library"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText;
                            text: (consoleRoot.gameModel ? consoleRoot.gameModel.totalCount : 0) + ((consoleRoot.gameModel ? consoleRoot.gameModel.totalCount : 0) === 1 ? " game" : " games")
                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                        }
                    }
                }

                BigScreenFocusable {
                    focusId: "guideSystemsRow"
                    focusScope: "guide"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    onActivatedCallback: function() { consoleRoot.switchTab(2) }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        Text { textFormat: Text.PlainText; text: "Systems"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText;
                            text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.systems.length : 0)
                                  + " systems · "
                                  + ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.totalRomCount : 0) + " ROMs"
                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                        }
                    }
                }

                BigScreenFocusable {
                    focusId: "guideFriendsRow"
                    focusScope: "guide"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    onActivatedCallback: function() { consoleRoot.switchTab(3) }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        Text { textFormat: Text.PlainText; text: "Party & Friends"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText;
                            text: ((typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.onlineFriendsCount : 0) + " online"
                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                        }
                    }
                }

                BigScreenFocusable {
                    focusId: "guideControllersRow"
                    focusScope: "guide"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    onActivatedCallback: function() { consoleRoot.switchTab(4) }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        Text { textFormat: Text.PlainText; text: "Controllers"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText;
                            text: (typeof bigScreenView !== "undefined" && bigScreenView) ? bigScreenView.controllerName : "Controller"
                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 13
                        }
                    }
                }

                BigScreenFocusable {
                    focusId: "guideSettingsRow"
                    focusScope: "guide"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    onActivatedCallback: function() { consoleRoot.switchTab(5) }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        Text { textFormat: Text.PlainText; text: "Settings"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText; text: "Sounds & display"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                    }
                }

                BigScreenFocusable {
                    focusId: "guideSoundPackRow"
                    focusScope: "guide"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    onActivatedCallback: function() { consoleRoot.cycleSoundPack() }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        Text { textFormat: Text.PlainText; text: "Sound pack"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText; text: consoleRoot.currentSoundPackLabel + " ›"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                    }
                }

                // Quick recent game jump from Guide
                Repeater {
                    model: consoleRoot.homeRecentGames.slice(0, 2)
                    delegate: BigScreenFocusable {
                        focusId: "guideRecent_" + index
                        focusScope: "guide"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44
                        onActivatedCallback: function() {
                            consoleRoot.guideOpen = false
                            if (modelData && modelData.id) consoleRoot.openGamePage(modelData.id)
                        }
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                            Text { textFormat: Text.PlainText;
                                Layout.fillWidth: true
                                text: modelData.title || "Recent game"
                                color: Theme.text; font.family: Theme.family; font.pixelSize: 14
                                elide: Text.ElideRight
                            }
                            Text { textFormat: Text.PlainText; text: "Recent ›"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                        }
                    }
                }

                Item { Layout.fillHeight: true }

                BigScreenFocusable {
                    focusId: "guideDesktopRow"
                    focusScope: "guide"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    onActivatedCallback: function() {
                        consoleRoot.guideOpen = false
                        consoleRoot.playSound("back")
                        consoleRoot.desktopRequested()
                    }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        Text { textFormat: Text.PlainText; text: "Exit to desktop"; color: Theme.text; font.family: Theme.family; font.pixelSize: 15; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Text { textFormat: Text.PlainText; text: "F11"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13 }
                    }
                }
            }
        }
    }

    // ==================================================================
    // Full-screen controller stick editor: same native component as desktop/overlay.
    // ==================================================================
    Rectangle {
        id: bigScreenStickPage
        objectName: "bigScreenStickTuningPage"
        anchors.fill: parent
        z: 24
        visible: consoleRoot.stickTuningOpen
        color: Theme.background

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 28
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: "Stick tuning · " + (typeof unifiedController !== "undefined" && unifiedController && unifiedController.editingProfile
                        ? unifiedController.editingProfile : "Controller profile")
                    color: Theme.text; font.family: Theme.family; font.pixelSize: 24; font.bold: true
                }
                Button { text: "Back"; onClicked: consoleRoot.navigate("back") }
            }
            Native.UnifiedControllerStudio {
                id: bigScreenStickStudio
                objectName: "bigScreenStickStudio"
                Layout.fillWidth: true
                Layout.fillHeight: true
                compact: true
                activeTab: "Sticks"
                service: typeof unifiedController !== "undefined" ? unifiedController : null
                live: typeof controllerLiveView !== "undefined" ? controllerLiveView : null
                commands: typeof controllerCommands !== "undefined" ? controllerCommands : null
                profileCommands: typeof controllerProfileCommands !== "undefined" ? controllerProfileCommands : null
                deviceCommands: typeof controllerDeviceCommands !== "undefined" ? controllerDeviceCommands : null
            }
        }

        Native.ControllerFocusNavigation {
            id: bigScreenStickFocus
            objectName: "bigScreenStickFocus"
            anchors.fill: parent
            scope: bigScreenStickStudio
            active: consoleRoot.stickTuningOpen
            onBackRequested: {
                if (bigScreenStickStudio.dirty) discardStickTuningDialog.open()
                else consoleRoot.closeStickTuning(false)
            }
            Component.onCompleted: Qt.callLater(focusFirst)
        }
    }

    Dialog {
        id: discardStickTuningDialog
        objectName: "discardStickTuningDialog"
        title: "Discard stick tuning changes?"
        modal: true
        standardButtons: Dialog.Yes | Dialog.Cancel
        onAccepted: consoleRoot.closeStickTuning(true)
        Label { text: "Your staged stick settings have not been saved."; wrapMode: Text.WordWrap }
    }

    // ==================================================================
    // 5. INLINE CONTROLLER BIND WIZARD (No desktop dialog)
    // ==================================================================
    Rectangle {
        id: bindWizardModal
        objectName: "bigScreenBindWizardModal"
        anchors.fill: parent
        z: 25
        visible: consoleRoot.bindWizardOpen
        color: "#c8000000"

        Rectangle {
            anchors.centerIn: parent
            width: Math.min(720, parent.width - 120)
            height: Math.min(540, parent.height - 120)
            radius: 8
            color: "#141414"
            border.width: 2
            border.color: "#ffffff"
            clip: true

            OverlayControllerBindWizard {
                id: inlineBindWizard
                objectName: "bigScreenInlineBindWizard"
                anchors.fill: parent
                anchors.margins: 16
                service: typeof unifiedController !== "undefined" ? unifiedController : null
                onCancelRequested: {
                    consoleRoot.bindWizardOpen = false
                    consoleRoot.focusZone = "stage"
                    consoleRoot.focusId = "ctrlBindWizardRow"
                    consoleRoot.playSound("back")
                }
                onApplied: {
                    consoleRoot.bindWizardOpen = false
                    consoleRoot.focusZone = "stage"
                    consoleRoot.focusId = "ctrlBindWizardRow"
                    consoleRoot.showToast("Controller bindings applied")
                    consoleRoot.playSound("select")
                }
            }
        }
    }

    // ==================================================================
    // 5B. LAUNCH PROMPT WHEN UPDATE IS PENDING (Controller & Mouse)
    // ==================================================================
    Rectangle {
        id: consoleUpdateLaunchPrompt
        objectName: "consoleUpdateLaunchPrompt"
        anchors.fill: parent
        z: 28
        visible: consoleRoot.updatePromptOpen
        color: "#cc080808"

        MouseArea {
            anchors.fill: parent
            onClicked: {
                consoleRoot.closeUpdatePrompt()
                consoleRoot.playSound("back")
            }
        }

        Rectangle {
            objectName: "consoleUpdateLaunchPromptCard"
            anchors.centerIn: parent
            width: Math.min(480, parent.width - 80)
            height: consolePromptCol.implicitHeight + 48
            radius: 8
            color: "#141414"
            border.width: 2
            border.color: "#ffffff"

            MouseArea { anchors.fill: parent }

            ColumnLayout {
                id: consolePromptCol
                anchors.fill: parent
                anchors.margins: 24
                spacing: 14

                Text { textFormat: Text.PlainText;
                    objectName: "consoleUpdatePromptTitle"
                    Layout.fillWidth: true
                    text: "Update available"
                    color: Theme.text
                    font.family: Theme.family
                    font.pixelSize: 22
                    font.bold: true
                }

                Text { textFormat: Text.PlainText;
                    objectName: "consoleUpdatePromptMessage"
                    Layout.fillWidth: true
                    text: "An update is pending for " + (consoleRoot.updatePromptTitle || "this game")
                          + ". Epic / Heroic games may block launching until updated."
                    color: Theme.muted
                    font.family: Theme.family
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }

                BigScreenFocusable {
                    focusId: "updatePromptUpdateFirst"
                    focusScope: "updatePrompt"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    color: isFocused ? "#ffffff" : "#1c2d22"
                    border.color: isFocused ? "#ffffff" : "#7cc88a"
                    onActivatedCallback: function() {
                        var gid = consoleRoot.updatePromptGameId || consoleRoot.detailGameId || consoleRoot.selectedId
                        consoleRoot.closeUpdatePrompt()
                        consoleRoot.triggerUpdateForGame(gid)
                    }
                    Text { textFormat: Text.PlainText;
                        anchors.centerIn: parent
                        text: "Update first (recommended)"
                        color: parent.isFocused ? "#090909" : "#8ee09e"
                        font.family: Theme.family
                        font.pixelSize: 15
                        font.bold: true
                    }
                }

                BigScreenFocusable {
                    focusId: "updatePromptPlayAnyway"
                    focusScope: "updatePrompt"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    color: isFocused ? "#ffffff" : "#262626"
                    onActivatedCallback: function() {
                        consoleRoot.closeUpdatePrompt()
                        consoleRoot.triggerPlayImmediate()
                    }
                    Text { textFormat: Text.PlainText;
                        anchors.centerIn: parent
                        text: "Play anyway"
                        color: parent.isFocused ? "#090909" : Theme.text
                        font.family: Theme.family
                        font.pixelSize: 15
                        font.bold: true
                    }
                }

                BigScreenFocusable {
                    focusId: "updatePromptCancel"
                    focusScope: "updatePrompt"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    color: isFocused ? "#ffffff" : Theme.panel
                    onActivatedCallback: function() {
                        consoleRoot.closeUpdatePrompt()
                        consoleRoot.playSound("back")
                    }
                    Text { textFormat: Text.PlainText;
                        anchors.centerIn: parent
                        text: "Cancel"
                        color: parent.isFocused ? "#090909" : Theme.muted
                        font.family: Theme.family
                        font.pixelSize: 15
                        font.bold: true
                    }
                }
            }
        }
    }

    // ==================================================================
    // 6. TOAST PILL
    // ==================================================================
    Rectangle {
        id: toastPill
        objectName: "bigScreenToast"
        visible: consoleRoot.toastMessage.length > 0
        z: 30
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 72
        width: toastLabel.implicitWidth + 36
        height: 40
        radius: 6
        color: "#f2f2f2"

        Text { textFormat: Text.PlainText;
            id: toastLabel
            anchors.centerIn: parent
            text: consoleRoot.toastMessage
            color: "#090909"
            font.family: Theme.family
            font.pixelSize: 14
            font.bold: true
        }
    }
}
