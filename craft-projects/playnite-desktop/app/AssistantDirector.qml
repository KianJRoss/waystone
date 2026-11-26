import QtQuick

// Visual-only assistant narration for actions already authorized by MCP.
// Launch authority remains Main.qml.playGameById; this component only sequences UI.
Item {
    id: director
    visible: false
    width: 0; height: 0

    property var hostWindow: null
    property var gameModel: null
    property var launchView: null
    property var desktopGrid: null
    // Kian 2026-11-24: animate in whichever library view is open (grid, details list, column list), never switch it.
    property var desktopDetailsList: null
    property var desktopColumnList: null
    property var consoleLibrary: null
    property var playButton: null
    property bool animationEnabled: true
    property bool fakeClock: false
    property string speed: "Normal"
    property bool active: false
    property bool pressed: false
    property string targetGameId: ""
    property string phase: "idle"
    property var savedView: ({})
    property int lastMoveRow: -1
    property int animationDuration: 0
    property int targetRow: -1
    property real scrollFrom: 0
    property real scrollTo: 0
    property bool filtersTemporarilyCleared: false

    signal assistantBeat(string kind)
    signal finished(string status)

    function requestPlay(gameId) {
        if (active) return {status: "busy", animating: true}
        var current = launchView ? launchView.activeGame : ({})
        if (!animationEnabled || (current && ["launching", "running", "unknown"].indexOf(current.state) >= 0)) {
            var immediate = hostWindow.playGameById(gameId)
            return {status: immediate ? "started" : "rejected", animating: false}
        }
        if (!gameModel || !gameModel.overview(gameId).id)
            return {status: "rejected", animating: false}

        targetGameId = gameId
        targetTitle = (gameModel.overview(gameId) || {}).title || ""
        saveView()
        active = true
        phase = "opening"
        if (hostWindow.visibility === Window.Hidden || hostWindow.visibility === Window.Minimized) {
            if (hostWindow.consoleMode) hostWindow.showFullScreen()
            else hostWindow.showNormal()
        }
        if (!hostWindow.consoleMode) hostWindow.showLibraryView()
        hostWindow.raise()
        hostWindow.requestActivate()
        assistantBeat("open")
        if (gameModel.rowOf(gameId) < 0) {
            filtersTemporarilyCleared = true
            hostWindow.clearFilters()
        }
        phase = "preparing"
        if (!fakeClock) prepareTimer.start()
        return {status: "started", animating: true}
    }

    function saveView() {
        savedView = {
            activePage: hostWindow.activePage,
            organizerMode: hostWindow.organizerMode,
            query: hostWindow.query,
            sourceFilter: hostWindow.source,
            platform: hostWindow.platform,
            status: hostWindow.status,
            nativeProvider: hostWindow.nativeProvider,
            collectionFilters: Object.assign({}, hostWindow.collectionFilters),
            viewMode: hostWindow.viewMode,
            inspectorVisible: hostWindow.inspectorVisible,
            selectedId: hostWindow.selectedId
        }
    }

    function restoreView() {
        if (!savedView || !hostWindow) return
        if (!filtersTemporarilyCleared) return
        if (!hostWindow.consoleMode) hostWindow.navigatePage(savedView.activePage || "library")
        hostWindow.organizerMode = !!savedView.organizerMode
        hostWindow.query = savedView.query || ""
        hostWindow.source = savedView.sourceFilter || "All sources"
        hostWindow.platform = savedView.platform || "All platforms"
        hostWindow.status = savedView.status || "All states"
        hostWindow.nativeProvider = savedView.nativeProvider || "All native providers"
        hostWindow.viewMode = savedView.viewMode || "grid"
        hostWindow.inspectorVisible = savedView.inspectorVisible !== false
        hostWindow.applyCollectionFilters(savedView.collectionFilters || {})
        hostWindow.applyFilters()
        if (savedView.selectedId) hostWindow.selectGame(savedView.selectedId)
        filtersTemporarilyCleared = false
    }

    // Per-speed pacing (ms). Normal is the default.
    // pace: Settings > General slider, 0.5 (quick) .. 1 (normal) .. 3 (very slow); scales every beat.
    property real pace: 1.0
    function timing() {
        var k = Math.max(0.5, Math.min(3.0, pace))
        return { perRow: 280 * k, min: 1800 * k, max: 5000 * k, minRows: Math.round(12 * Math.sqrt(k)),
                 dwell: 700 * k, press: Math.min(600, 300 * Math.sqrt(k)) }
    }

    function activeGrid() {
        if (hostWindow.consoleMode && consoleLibrary) return consoleLibrary.assistantGrid
        if (hostWindow.viewMode === "details" && desktopDetailsList) return desktopDetailsList
        if (hostWindow.viewMode === "list" && desktopColumnList) return desktopColumnList
        return desktopGrid
    }

    // Row geometry for a GridView (cells) or a ListView (one item per row).
    function rowHeight(view) {
        if (view.cellHeight !== undefined) return Math.max(1, view.cellHeight)
        return Math.max(1, view.count > 0 ? view.contentHeight / view.count : 40)
    }
    function itemsPerRow(view) {
        return view.cellWidth !== undefined ? Math.max(1, Math.floor(view.width / Math.max(1, view.cellWidth))) : 1
    }

    // ---- The glide (Kian 2026-11-24): "actually looked like a controller gliding down the games ... actively
    // showed the games it was passing and its info". A path of real games from the current selection to the
    // target; each step highlights the game, shows its info and ticks. Slow at both ends, quick in the middle.
    property var path: []
    property int stepIndex: -1
    property var stepTimes: []
    property var passingGame: ({})

    function buildPath(grid, startIndex, targetIndex) {
        var cols = itemsPerRow(grid)
        var count = gameModel && gameModel.visibleCount !== undefined ? gameModel.visibleCount : (grid.count || targetIndex + 1)
        var startRow = Math.floor(startIndex / cols), startCol = startIndex % cols
        var endRow = Math.floor(targetIndex / cols), endCol = targetIndex % cols
        var dir = endRow >= startRow ? 1 : -1
        var allRows = []
        for (var r = startRow; r !== endRow + dir; r += dir) allRows.push(r)
        // Long runs: keep the first and last few rows one by one, sample the middle evenly.
        var maxSteps = Math.max(8, Math.round(22 * Math.sqrt(Math.max(0.5, pace))))
        var rows = allRows
        if (allRows.length > maxSteps) {
            var head = 4, tail = 6, middle = maxSteps - head - tail
            rows = allRows.slice(0, head)
            var span = allRows.length - head - tail
            for (var i = 1; i <= middle; i++) rows.push(allRows[head + Math.round(i * span / (middle + 1))])
            rows = rows.concat(allRows.slice(allRows.length - tail))
        }
        var out = []
        for (var k = 0; k < rows.length; k++) {
            var idx = Math.min(count - 1, rows[k] * cols + startCol)
            if (out.length === 0 || out[out.length - 1] !== idx) out.push(idx)
        }
        var col = startCol
        while (col !== endCol) { col += endCol > col ? 1 : -1; out.push(endRow * cols + col) }
        if (out[out.length - 1] !== targetIndex) out.push(targetIndex)
        return out
    }

    function prepareTarget() {
        if (!active) return
        var grid = activeGrid()
        var index = gameModel ? gameModel.rowOf(targetGameId) : -1
        if (!grid || index < 0) {
            finish("rejected")
            return
        }
        var cols = itemsPerRow(grid)
        targetRow = Math.floor(index / cols)
        var selected = hostWindow.selectedId ? gameModel.rowOf(hostWindow.selectedId) : -1
        var startIndex = selected >= 0 ? selected : Math.max(0, Math.floor(grid.contentY / rowHeight(grid))) * cols
        // Always travel a visible run of games: if the target is close, start that many rows before it.
        var minRows = timing().minRows
        if (Math.abs(targetRow - Math.floor(startIndex / cols)) < minRows)
            startIndex = Math.max(0, (targetRow - minRows) * cols + (index % cols))
        path = buildPath(grid, startIndex, index)
        // Eased step times: slow at both ends, quick in the middle; the Pace slider scales them.
        var times = [], n = path.length
        for (var k = 0; k < n; k++) {
            var ends = n > 1 ? Math.sin(Math.PI * k / (n - 1)) : 0
            // ~400 ms per game at both ends, ~180 ms mid-run at pace 1: long enough to read each passing game.
            times.push(Math.round(Math.max(60, 400 * pace * (1 - 0.55 * ends))))
        }
        stepTimes = times
        stepIndex = -1
        lastMoveRow = -1
        phase = "scrolling"
        if (!fakeClock) {
            stepTo(0)
            stepTimer.interval = stepTimes[0]
            stepTimer.start()
        }
    }

    // Highlight one game on the path, keep it centred, show its info, tick.
    function stepTo(k) {
        var grid = activeGrid()
        if (!grid || k < 0 || k >= path.length) return
        stepIndex = k
        var idx = path[k]
        var id = gameModel.idAt(idx)
        var oldY = grid.contentY
        if (hostWindow.consoleMode && consoleLibrary) consoleLibrary.selectIndex(idx)
        else hostWindow.selectGame(id)
        var rh = rowHeight(grid)
        var rowTop = Math.floor(idx / itemsPerRow(grid)) * rh
        var maxY = Math.max(0, grid.contentHeight - grid.height)
        var y = Math.max(0, Math.min(maxY, rowTop - (grid.height - rh) / 2))
        if (fakeClock) grid.contentY = y
        else {
            glideScroll.stop()
            glideScroll.target = grid
            glideScroll.from = oldY
            glideScroll.to = y
            glideScroll.duration = Math.max(60, (stepTimes[k] || 120) * 0.9)
            grid.contentY = oldY
            glideScroll.start()
        }
        var info = gameModel.overview(id) || {}
        passingGame = { title: info.title || "", detail: [info.sources, info.platforms].filter(function(x) { return x }).join(" · "),
                        state: info.state || "", cover: info.coverUrl || "" }
        if (k > 0) assistantBeat("move")
    }

    function selectTarget() {
        if (!active) return
        if (hostWindow.consoleMode) {
            consoleLibrary.selectIndex(gameModel.rowOf(targetGameId))
            consoleLibrary.activeSection = "details"
            consoleLibrary.detailsTarget = "play"
        } else {
            hostWindow.selectGame(targetGameId)
        }
        assistantBeat("select")
        phase = "pressing"
        if (fakeClock) { pressed = true; return }
        dwellTimer.interval = timing().dwell
        dwellTimer.start()
    }

    // Deterministic test seam: advance to the next visual phase without sleeping.
    // Production input and the QML timers continue to use real time.
    function advanceFakeClock(progress) {
        if (!fakeClock || !active) return false
        if (phase === "preparing") {
            prepareTarget()
            return true
        }
        if (phase === "scrolling") {
            var bounded = Math.max(0, Math.min(1, progress))
            var k = Math.round(bounded * (path.length - 1))
            for (var i = stepIndex + 1; i <= k; i++) stepTo(i)
            if (bounded >= 1) selectTarget()
            return true
        }
        if (phase === "pressing") {
            pressed = false
            launchNow()
            return true
        }
        return false
    }

    function userInputCancel() {
        if (!active) return false
        scrollAnimation.stop()
        moveBeatTimer.stop()
        stepTimer.stop()
        glideScroll.stop()
        prepareTimer.stop()
        dwellTimer.stop()
        pressTimer.stop()
        pressed = false
        if (hostWindow.consoleMode) {
            var index = gameModel.rowOf(targetGameId)
            if (index >= 0) consoleLibrary.selectIndex(index)
        } else {
            hostWindow.selectGame(targetGameId)
        }
        launchNow()
        return true
    }

    function launchNow() {
        if (!active) return
        assistantBeat("launch")
        var ok = hostWindow.playGameById(targetGameId)
        restoreView()
        active = false
        phase = "idle"
        finished(ok ? "started" : "rejected")
    }

    function finish(status) {
        scrollAnimation.stop()
        moveBeatTimer.stop()
        stepTimer.stop()
        glideScroll.stop()
        prepareTimer.stop()
        dwellTimer.stop()
        pressTimer.stop()
        pressed = false
        restoreView()
        active = false
        phase = "idle"
        finished(status)
    }

    Timer {
        id: prepareTimer
        interval: 450
        repeat: false
        onTriggered: director.prepareTarget()
    }

    Timer {
        id: dwellTimer
        repeat: false
        onTriggered: {
            director.pressed = true
            pressTimer.interval = director.timing().press
            pressTimer.start()
        }
    }

    Timer {
        id: pressTimer
        interval: 300
        repeat: false
        onTriggered: {
            director.pressed = false
            director.launchNow()
        }
    }

    Timer {
        id: moveBeatTimer
        interval: 83
        repeat: true
        onTriggered: {
            var grid = director.activeGrid()
            if (!grid) return
            var row = Math.floor(grid.contentY / director.rowHeight(grid))
            if (row !== director.lastMoveRow) {
                director.lastMoveRow = row
                director.assistantBeat("move")
            }
        }
    }

    // Info card for the game the cursor is passing (shown in every library view, also when the details pane is hidden).
    property string targetTitle: ""
    Rectangle {
        id: passingCard
        objectName: "assistantPassingCard"
        parent: director.hostWindow ? director.hostWindow.contentItem : null
        visible: director.active && (director.phase === "scrolling" || director.phase === "pressing") && !!director.passingGame.title
        z: 1000
        width: 460; height: 104
        anchors.horizontalCenter: parent ? parent.horizontalCenter : undefined
        anchors.bottom: parent ? parent.bottom : undefined
        anchors.bottomMargin: 30
        radius: Theme.cornerRadius
        color: Theme.panel
        border.color: Theme.main; border.width: 1
        Row {
            anchors.fill: parent; anchors.margins: 12; spacing: 14
            Image {
                width: 60; height: 80
                source: passingCard.visible ? (director.passingGame.cover || "") : ""
                fillMode: Image.PreserveAspectCrop; asynchronous: true; smooth: true
            }
            Column {
                width: parent.width - 74; spacing: 3
                anchors.verticalCenter: parent.verticalCenter
                Text { width: parent.width; text: "Assistant · going to " + director.targetTitle; color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; elide: Text.ElideRight }
                Text { width: parent.width; text: director.passingGame.title || ""; color: Theme.text; font.family: Theme.family; font.pixelSize: 17; font.bold: true; elide: Text.ElideRight }
                Text { width: parent.width; text: director.passingGame.detail || ""; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; elide: Text.ElideRight }
                Text { width: parent.width; text: director.passingGame.state || ""; color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; elide: Text.ElideRight }
            }
        }
    }

    Timer {
        id: stepTimer
        repeat: false
        onTriggered: {
            var next = director.stepIndex + 1
            if (next >= director.path.length) { director.selectTarget(); return }
            director.stepTo(next)
            interval = director.stepTimes[next] || 120
            start()
        }
    }

    NumberAnimation {
        id: glideScroll
        property: "contentY"
        easing.type: Easing.OutCubic
    }

    NumberAnimation {
        id: scrollAnimation
        property: "contentY"
        easing.type: Easing.InOutCubic
        onFinished: {
            moveBeatTimer.stop()
            director.selectTarget()
        }
    }

    Connections {
        target: typeof controllerNavigation !== "undefined" ? controllerNavigation : null
        function onAction(_action) { director.userInputCancel() }
    }
}
