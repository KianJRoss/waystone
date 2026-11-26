import QtQuick
import "../../craft-projects/playnite-desktop/app" as Waystone

Window {
    id: host
    width: 520; height: 200; visible: false
    title: "Waystone assistant animation test"
    property bool consoleMode: false
    property int consoleSelectedIndex: 0
    property string activePage: "settings"
    property bool organizerMode: false
    property string query: "saved query"
    property string source: "All sources"
    property string platform: "All platforms"
    property string status: "installed"
    property string nativeProvider: "All native providers"
    property var collectionFilters: ({tag: ["test"]})
    property string viewMode: "grid"
    property bool inspectorVisible: true
    property var played: []
    property var beatLog: []
    property bool filtered: true
    property var allIds: Array.from({length: 100}, function(_x, i) { return "game-" + i })
    property string requestId: ""
    property var lastRequest: ({})
    property real fakeProgress: -1
    property bool cancelRequested: false
    property bool animationSetting: true
    property bool runningSetting: false
    readonly property bool animationActive: director.active
    readonly property bool playPressed: director.pressed
    readonly property string assistantPhase: director.phase
    readonly property real gridY: grid.contentY
    readonly property real consoleGridY: consoleGrid.contentY

    QtObject {
        id: model
        function rowOf(id) { return host.filtered ? (id === "game-0" ? 0 : -1) : host.allIds.indexOf(id) }
        function idAt(index) { return host.filtered ? (index === 0 ? "game-0" : "") : (host.allIds[index] || "") }
        function overview(id) { return host.allIds.indexOf(id) >= 0 ? ({id: id, title: id}) : ({}) }
    }
    QtObject {
        id: launcher
        property var activeGame: ({})
        property string launchDetail: ""
        property string playLabel: "Play"
    }

    function showLibraryView() { activePage = "library" }
    function clearFilters() { filtered = false; query = ""; status = "All states"; collectionFilters = ({}) }
    function applyCollectionFilters(filters) { collectionFilters = filters }
    function applyFilters() { filtered = query.length > 0 || status === "installed" || Object.keys(collectionFilters).length > 0 }
    function navigatePage(page) { activePage = page }
    function setView(mode) { viewMode = mode }
    function selectGame(id) { selectedId = id }
    property string selectedId: "game-0"
    function playGameById(id) { played = played.concat([id]); return true }
    function enableConsole() { consoleMode = true; activePage = "social" }
    onRequestIdChanged: lastRequest = director.requestPlay(requestId)
    onFakeProgressChanged: if (fakeProgress >= 0) director.advanceFakeClock(fakeProgress)
    onCancelRequestedChanged: if (cancelRequested) director.userInputCancel()
    onAnimationSettingChanged: director.animationEnabled = animationSetting
    onRunningSettingChanged: launcher.activeGame = runningSetting ? ({state: "running", gameId: "other"}) : ({})

    GridView {
        id: grid
        objectName: "testGameGrid"
        anchors.fill: parent
        cellWidth: 100; cellHeight: 50
        model: host.filtered ? 1 : host.allIds.length
        delegate: Rectangle {
            required property int index
            width: grid.cellWidth; height: grid.cellHeight
            color: index === 20 ? "#d85b39" : "#353535"
            Text { anchors.centerIn: parent; text: "Game " + index; color: "white" }
        }
    }
    QtObject {
        id: testConsole
        property var assistantGrid: consoleGrid
        property string activeSection: "library"
        property string detailsTarget: "source"
        function selectIndex(index) {
            host.consoleSelectedIndex = index
            host.selectedId = host.allIds[index] || ""
            consoleGrid.currentIndex = index
        }
    }
    GridView {
        id: consoleGrid
        objectName: "testConsoleGrid"
        anchors.fill: parent
        visible: host.consoleMode
        cellWidth: 100; cellHeight: 50
        model: host.allIds.length
        delegate: Rectangle {
            required property int index
            width: consoleGrid.cellWidth; height: consoleGrid.cellHeight
            color: index === 30 ? "#d85b39" : "#353535"
            Text { anchors.centerIn: parent; text: "Console game " + index; color: "white" }
        }
    }
    Rectangle {
        id: fakePlayButton
        objectName: "fakePlayButton"
        anchors.bottom: parent.bottom; anchors.right: parent.right
        width: 120; height: 50
        property bool down: director.pressed
        color: down ? "#f0bf55" : "#454545"
        Text { anchors.centerIn: parent; text: "Play"; color: "white" }
    }
    Waystone.AssistantDirector {
        id: director
        hostWindow: host
        gameModel: model
        launchView: launcher
        desktopGrid: grid
        consoleLibrary: testConsole
        playButton: fakePlayButton
        animationEnabled: true
        speed: "Normal"
        fakeClock: true
        onAssistantBeat: function(kind) { host.beatLog = host.beatLog.concat([kind]) }
    }
}
