import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "overlay_components"

Item {
    id: panel
    objectName: "overlayLibraryPanel"

    property var libraryBridge: null
    property bool hasActiveFocus: false

    signal openFullRequested()
    signal closeOverlayRequested()

    // Navigation state
    // section: 0=NowPlaying, 1=Search, 2=Recent, 3=Pinned, 4=AllInstalled
    property int currentSection: {
        if (libraryBridge && libraryBridge.hasRunningGame) return 0
        return 1
    }
    property int nowPlayingAction: 0 // 0=Resume, 1=Quit
    property int recentIndex: 0
    property int pinnedIndex: 0
    property int installedIndex: 0
    property int switchAction: 0 // 0=ConfirmSwitch, 1=CancelSwitch
    property int quitAction: 0 // 0=ConfirmQuit, 1=CancelQuit

    // On-screen letters for searching with a controller (Y opens them; A types; B closes).
    readonly property var letterKeys: ["A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T",
                                       "U","V","W","X","Y","Z","0","1","2","3","4","5","6","7","8","9","Space","Del","Clear","Done"]
    readonly property int letterColumns: 10
    property bool lettersOpen: false
    property int letterIndex: 0

    function typeLetter(index) {
        var key = letterKeys[index]
        if (key === "Done") { lettersOpen = false; return }
        if (key === "Del") searchField.text = searchField.text.slice(0, -1)
        else if (key === "Clear") searchField.text = ""
        else searchField.text += key === "Space" ? " " : key.toLowerCase()
    }

    // Keep the focused section on screen while moving with the d-pad.
    function revealFocused() {
        var item = [nowPlayingCard, searchBarContainer, recentListView, pinnedListView, installedListView][currentSection]
        if (!item || !item.visible) return
        var top = item.mapToItem(contentColumn, 0, 0).y - (currentSection >= 2 ? 22 : 4)
        var bottom = item.mapToItem(contentColumn, 0, 0).y + item.height + (currentSection === 1 && lettersOpen ? letterGrid.height + 8 : 4)
        var maxY = Math.max(0, guideScroll.contentHeight - guideScroll.height)
        if (top < guideScroll.contentY) guideScroll.contentY = Math.max(0, top)
        else if (bottom > guideScroll.contentY + guideScroll.height) guideScroll.contentY = Math.min(maxY, bottom - guideScroll.height)
    }
    onCurrentSectionChanged: Qt.callLater(revealFocused)
    onLettersOpenChanged: Qt.callLater(revealFocused)

    readonly property bool isSwitching: libraryBridge ? libraryBridge.isSwitching : false
    readonly property bool isQuitting: libraryBridge ? libraryBridge.isQuitting : false
    readonly property bool hasSelectedGame: (libraryBridge && libraryBridge.selectedGame && libraryBridge.selectedGame.id) ? true : false
    property bool updatePromptVisible: false
    property string updatePromptGameId: ""
    property int updatePromptAction: 0

    function requestOverlayPlay(gameId) {
        var gid = String(gameId || "")
        if (!gid) return
        if (typeof gameInstalls !== "undefined" && gameInstalls && typeof gameInstalls.requiresUpdatePrompt === "function" && gameInstalls.requiresUpdatePrompt(gid)) {
            updatePromptGameId = gid
            updatePromptAction = 0
            updatePromptVisible = true
            return
        }
        if (libraryBridge) libraryBridge.playGame(gid)
    }

    function chooseOverlayUpdateFirst() {
        var gid = updatePromptGameId
        updatePromptVisible = false
        updatePromptGameId = ""
        if (libraryBridge) libraryBridge.clearSelectedGame()
        if (gid && typeof gameInstalls !== "undefined" && gameInstalls && typeof gameInstalls.updateGame === "function") {
            gameInstalls.updateGame(gid)
        }
    }

    function chooseOverlayPlayAnyway() {
        var gid = updatePromptGameId
        updatePromptVisible = false
        updatePromptGameId = ""
        if (gid && libraryBridge) libraryBridge.playGame(gid)
    }

    function chooseOverlayCancelUpdatePrompt() {
        updatePromptVisible = false
        updatePromptGameId = ""
    }

    function navigate(action) {
        // 0. Modal: Pending update prompt ("Update first (recommended)" / "Play anyway" / "Cancel")
        if (updatePromptVisible) {
            if (action === "up" || action === "left") {
                updatePromptAction = (updatePromptAction + 2) % 3
                return true
            }
            if (action === "down" || action === "right") {
                updatePromptAction = (updatePromptAction + 1) % 3
                return true
            }
            if (action === "accept") {
                if (updatePromptAction === 0) chooseOverlayUpdateFirst()
                else if (updatePromptAction === 1) chooseOverlayPlayAnyway()
                else chooseOverlayCancelUpdatePrompt()
                return true
            }
            if (action === "back") {
                chooseOverlayCancelUpdatePrompt()
                return true
            }
            return true
        }

        // 1. Modal: Switch confirmation ("Switch to X? Y will be closed.")
        if (isSwitching) {
            if (action === "left" || action === "right") {
                switchAction = switchAction === 0 ? 1 : 0
                return true
            }
            if (action === "accept") {
                if (switchAction === 0) {
                    if (libraryBridge) libraryBridge.confirmSwitch()
                } else {
                    if (libraryBridge) libraryBridge.cancelSwitch()
                }
                return true
            }
            if (action === "back") {
                if (libraryBridge) libraryBridge.cancelSwitch()
                return true
            }
            return true
        }

        // 2. Modal: Quit confirmation
        if (isQuitting) {
            if (action === "left" || action === "right") {
                quitAction = quitAction === 0 ? 1 : 0
                return true
            }
            if (action === "accept") {
                if (quitAction === 0) {
                    if (libraryBridge) libraryBridge.confirmQuit()
                } else {
                    if (libraryBridge) libraryBridge.cancelQuit()
                }
                return true
            }
            if (action === "back") {
                if (libraryBridge) libraryBridge.cancelQuit()
                return true
            }
            return true
        }

        // 3. Game Detail Card modal
        if (hasSelectedGame) {
            if (action === "accept") {
                if (libraryBridge && libraryBridge.selectedGame) panel.requestOverlayPlay(libraryBridge.selectedGame.id)
                return true
            }
            if (action === "back") {
                if (libraryBridge) libraryBridge.clearSelectedGame()
                return true
            }
            return true
        }

        // 4. On-screen letters
        if (lettersOpen) {
            var n = letterKeys.length
            if (action === "left") letterIndex = (letterIndex + n - 1) % n
            else if (action === "right") letterIndex = (letterIndex + 1) % n
            else if (action === "up") {
                if (letterIndex >= 36) letterIndex = 30 + Math.min(5, Math.round((letterIndex - 36) * 9 / 3) - 0)
                else if (letterIndex >= letterColumns) letterIndex -= letterColumns
            } else if (action === "down") {
                if (letterIndex >= 36) {}
                else if (letterIndex + letterColumns < 36) letterIndex += letterColumns
                else letterIndex = 36 + Math.min(3, Math.floor((letterIndex % letterColumns) * 4 / letterColumns))
            }
            else if (action === "accept") typeLetter(letterIndex)
            else if (action === "back" || action === "details" || action === "search") lettersOpen = false
            return true
        }

        // 5. Quick key: Y button jumps to Search with the on-screen letters
        if (action === "details" || action === "search") {
            currentSection = 1
            lettersOpen = true
            searchField.forceActiveFocus()
            return true
        }

        // 5. Back button: if search has query, clear it; else let overlay handle back
        if (action === "back") {
            if (searchField.text.length > 0) {
                searchField.text = ""
                return true
            }
            return false
        }

        // 6. Section & Item Navigation
        var hasRunning = libraryBridge ? libraryBridge.hasRunningGame : false
        var hasPinned = (libraryBridge && libraryBridge.pinnedGames) ? (libraryBridge.pinnedGames.length > 0) : false
        var recentCount = (libraryBridge && libraryBridge.recentGames) ? libraryBridge.recentGames.length : 0
        var pinnedCount = hasPinned ? libraryBridge.pinnedGames.length : 0
        var installedCount = libraryBridge ? libraryBridge.searchResultCount : 0

        if (action === "up") {
            if (currentSection === 4) {
                if (installedIndex > 0) {
                    installedIndex--
                    installedListView.positionViewAtIndex(installedIndex, ListView.Contain)
                } else {
                    currentSection = hasPinned ? 3 : (recentCount > 0 ? 2 : 1)
                }
            } else if (currentSection === 3) {
                currentSection = recentCount > 0 ? 2 : 1
            } else if (currentSection === 2) {
                currentSection = 1
            } else if (currentSection === 1) {
                if (hasRunning) currentSection = 0
            }
            return true
        }

        if (action === "down") {
            if (currentSection === 0) {
                currentSection = 1
            } else if (currentSection === 1) {
                if (recentCount > 0) currentSection = 2
                else if (hasPinned) currentSection = 3
                else currentSection = 4
            } else if (currentSection === 2) {
                if (hasPinned) currentSection = 3
                else currentSection = 4
            } else if (currentSection === 3) {
                currentSection = 4
            } else if (currentSection === 4) {
                if (installedIndex < installedCount - 1) {
                    installedIndex++
                    installedListView.positionViewAtIndex(installedIndex, ListView.Contain)
                }
            }
            Qt.callLater(revealFocused)
            return true
        }

        if (action === "left") {
            if (currentSection === 0) {
                nowPlayingAction = 0
            } else if (currentSection === 2 && recentCount > 0) {
                recentIndex = Math.max(0, recentIndex - 1)
                recentListView.positionViewAtIndex(recentIndex, ListView.Contain)
            } else if (currentSection === 3 && pinnedCount > 0) {
                pinnedIndex = Math.max(0, pinnedIndex - 1)
                pinnedListView.positionViewAtIndex(pinnedIndex, ListView.Contain)
            }
            return true
        }

        if (action === "right") {
            if (currentSection === 0) {
                nowPlayingAction = 1
            } else if (currentSection === 2 && recentCount > 0) {
                recentIndex = Math.min(recentCount - 1, recentIndex + 1)
                recentListView.positionViewAtIndex(recentIndex, ListView.Contain)
            } else if (currentSection === 3 && pinnedCount > 0) {
                pinnedIndex = Math.min(pinnedCount - 1, pinnedIndex + 1)
                pinnedListView.positionViewAtIndex(pinnedIndex, ListView.Contain)
            }
            return true
        }

        if (action === "accept") {
            if (currentSection === 0) {
                if (nowPlayingAction === 0) {
                    if (libraryBridge) libraryBridge.resumeGame()
                } else {
                    if (libraryBridge) libraryBridge.requestQuit()
                }
            } else if (currentSection === 1) {
                lettersOpen = true
                searchField.forceActiveFocus()
            } else if (currentSection === 2 && recentCount > 0) {
                var rg = libraryBridge.recentGames[recentIndex]
                if (rg && libraryBridge) libraryBridge.selectGame(rg.id)
            } else if (currentSection === 3 && pinnedCount > 0) {
                var pg = libraryBridge.pinnedGames[pinnedIndex]
                if (pg && libraryBridge) libraryBridge.selectGame(pg.id)
            } else if (currentSection === 4 && installedCount > 0) {
                var ig = {id: libraryBridge.searchResultId(installedIndex)}
                if (ig && libraryBridge) libraryBridge.selectGame(ig.id)
            }
            return true
        }

        return false
    }

    // Opaque like the Xbox guide, so widgets behind it never show through the game list.
    Rectangle { anchors.fill: parent; z: -1; color: Theme.panel; radius: Theme.cornerRadius / 2 }

    // Connections
    Connections {
        target: libraryBridge
        function onCloseOverlayRequested() {
            panel.closeOverlayRequested()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 6

        // 1. Header Bar: Title + Full Page Link
        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            Text {
                text: "Library"
                color: Theme.text
                font.bold: true
                font.pixelSize: 14
                textFormat: Text.PlainText
                Layout.fillWidth: true
            }

            OverlayControl {
                objectName: "overlayLibraryOpenFullButton"
                text: "Full library"
                compact: true
                hint: "Open full library page"
                onClicked: panel.openFullRequested()
            }
        }

        // 2. Main Scrollable Guide Area
        Flickable {
            id: guideScroll
            objectName: "overlayLibraryScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: width
            contentHeight: contentColumn.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: OverlayScrollBar {}
            Behavior on contentY { enabled: !guideScroll.moving; NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }

            ColumnLayout {
                id: contentColumn
                width: guideScroll.width - 6
                spacing: 8

                // --- NOW PLAYING SECTION ---
                Rectangle {
                    id: nowPlayingCard
                    objectName: "overlayLibraryNowPlayingCard"
                    Layout.fillWidth: true
                    implicitHeight: 88
                    visible: libraryBridge ? libraryBridge.hasRunningGame : false
                    radius: Theme.cornerRadius / 2
                    color: (panel.hasActiveFocus && panel.currentSection === 0) ? Theme.hover : Theme.panel
                    border.width: 1
                    border.color: (panel.hasActiveFocus && panel.currentSection === 0) ? Theme.text : Theme.line

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 8

                        // Cover thumbnail
                        Rectangle {
                            width: 52
                            height: 72
                            radius: 4
                            clip: true
                            color: Theme.main

                            Image {
                                anchors.fill: parent
                                source: (libraryBridge && libraryBridge.runningGame) ? (libraryBridge.runningGame.coverUrl || "") : ""
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                                sourceSize: Qt.size(52, 72)
                                visible: status === Image.Ready
                            }

                            Text {
                                anchors.centerIn: parent
                                text: (libraryBridge && libraryBridge.runningGame) ? (libraryBridge.runningGame.initials || "") : ""
                                color: Theme.muted; font.bold: true; textFormat: Text.PlainText
                                font.pixelSize: 16
                                visible: !parent.children[0].visible
                            }
                        }

                        // Info & Actions
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            spacing: 3

                            Text {
                                text: "NOW PLAYING"
                                color: Theme.text
                                font.pixelSize: 9
                                font.bold: true
                                textFormat: Text.PlainText
                            }

                            Text {
                                text: (libraryBridge && libraryBridge.runningGame) ? (libraryBridge.runningGame.title || "") : ""
                                color: Theme.text
                                font.bold: true
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                                textFormat: Text.PlainText
                            }

                            Text {
                                text: (libraryBridge && libraryBridge.runningGame) ? ("Session: " + (libraryBridge.runningGame.sessionDuration || "0m")) : ""
                                color: Theme.muted
                                font.pixelSize: 10
                                textFormat: Text.PlainText
                            }

                            RowLayout {
                                spacing: 6
                                Layout.topMargin: 2

                                OverlayControl {
                                    objectName: "overlayLibraryResumeButton"
                                    text: "Resume"
                                    compact: true
                                    hint: "Return to active game"
                                    highlighted: panel.hasActiveFocus && panel.currentSection === 0 && panel.nowPlayingAction === 0
                                    onClicked: {
                                        if (libraryBridge) libraryBridge.resumeGame()
                                    }
                                }

                                OverlayControl {
                                    objectName: "overlayLibraryQuitButton"
                                    text: "Quit game"
                                    compact: true
                                    hint: "Close the running game"
                                    highlighted: panel.hasActiveFocus && panel.currentSection === 0 && panel.nowPlayingAction === 1
                                    onClicked: {
                                        if (libraryBridge) libraryBridge.requestQuit()
                                    }
                                }
                            }
                        }
                    }
                }

                // --- SEARCH BAR ---
                Rectangle {
                    id: searchBarContainer
                    Layout.fillWidth: true
                    implicitHeight: 32
                    radius: Theme.cornerRadius / 2
                    color: Theme.main
                    border.width: 1
                    border.color: (panel.hasActiveFocus && panel.currentSection === 1) ? Theme.text : Theme.line

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        spacing: 6

                        TextInput {
                            id: searchField
                            objectName: "overlayLibrarySearchField"
                            Layout.fillWidth: true
                            color: Theme.text
                            font.family: Theme.family
                            font.pixelSize: 11
                            selectByMouse: true
                            clip: true

                            Text {
                                text: "Search installed games… (Y)"
                                color: Theme.muted
                                font: parent.font
                                visible: !parent.text && !parent.activeFocus
                                textFormat: Text.PlainText
                            }

                            onTextChanged: {
                                if (libraryBridge) libraryBridge.setSearchQuery(text)
                            }
                        }

                        Text {
                            text: "Y"
                            color: Theme.muted
                            font.bold: true
                            font.pixelSize: 9
                            visible: !searchField.text
                        }

                        OverlayControl {
                            text: "✕"
                            compact: true
                            visible: searchField.text.length > 0
                            onClicked: {
                                searchField.text = ""
                                if (libraryBridge) libraryBridge.setSearchQuery("")
                            }
                        }
                    }
                }

                // --- ON-SCREEN LETTERS (controller search) ---
                ColumnLayout {
                    id: letterGrid
                    objectName: "overlayLibraryLetters"
                    Layout.fillWidth: true
                    visible: panel.lettersOpen
                    spacing: 3
                Grid {
                    id: letterKeysGrid
                    Layout.fillWidth: true
                    columns: panel.letterColumns
                    spacing: 3
                    Repeater {
                        model: panel.letterKeys.slice(0, 36)
                        delegate: OverlayControl {
                            required property string modelData
                            required property int index
                            objectName: "overlayLibraryLetter_" + modelData
                            text: modelData
                            compact: true
                            width: (letterKeysGrid.width - letterKeysGrid.spacing * (panel.letterColumns - 1)) / panel.letterColumns
                            highlighted: panel.lettersOpen && panel.letterIndex === index
                            onClicked: { panel.letterIndex = index; panel.typeLetter(index) }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Repeater {
                        model: panel.letterKeys.slice(36)
                        delegate: OverlayControl {
                            required property string modelData
                            required property int index
                            objectName: "overlayLibraryLetter_" + modelData
                            text: modelData
                            compact: true
                            Layout.fillWidth: true
                            highlighted: panel.lettersOpen && panel.letterIndex === 36 + index
                            onClicked: { panel.letterIndex = 36 + index; panel.typeLetter(36 + index) }
                        }
                    }
                }
                }

                // --- RECENT GAMES ROW ---
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    visible: (libraryBridge && libraryBridge.recentGames) ? (libraryBridge.recentGames.length > 0) : false

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "Recent"
                            color: Theme.text
                            font.bold: true
                            font.pixelSize: 11
                            textFormat: Text.PlainText
                            Layout.fillWidth: true
                        }
                        Text {
                            text: (libraryBridge && libraryBridge.recentGames) ? String(libraryBridge.recentGames.length) : ""
                            color: Theme.muted
                            font.pixelSize: 10
                            textFormat: Text.PlainText
                        }
                    }

                    ListView {
                        id: recentListView
                        objectName: "overlayLibraryRecentList"
                        Layout.fillWidth: true
                        implicitHeight: 110
                        orientation: ListView.Horizontal
                        spacing: 6
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        model: libraryBridge ? libraryBridge.recentGames : []

                        delegate: Rectangle {
                            required property var modelData
                            required property int index
                            width: 76
                            height: 108
                            radius: Theme.cornerRadius / 2
                            color: (panel.hasActiveFocus && panel.currentSection === 2 && panel.recentIndex === index) ? Theme.hover : Theme.panel
                            border.width: 1
                            border.color: (panel.hasActiveFocus && panel.currentSection === 2 && panel.recentIndex === index) ? Theme.text : Theme.line

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 4
                                spacing: 2

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    radius: 3
                                    clip: true
                                    color: Theme.main

                                    Image {
                                        anchors.fill: parent
                                        source: modelData.coverUrl || ""
                                        fillMode: Image.PreserveAspectCrop
                                        asynchronous: true
                                        sourceSize: Qt.size(70, 80)
                                        visible: status === Image.Ready
                                    }

                                    Text {
                                        anchors.centerIn: parent
                                        text: modelData.initials || ""
                                        color: Theme.muted; font.bold: true; textFormat: Text.PlainText
                                        font.pixelSize: 18
                                        visible: !parent.children[0].visible
                                    }
                                }

                                Text {
                                    text: modelData.title || ""
                                    color: Theme.text
                                    font.pixelSize: 9
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                    textFormat: Text.PlainText
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: {
                                    panel.currentSection = 2
                                    panel.recentIndex = index
                                    if (libraryBridge) libraryBridge.selectGame(modelData.id)
                                }
                            }
                        }
                    }
                }

                // --- PINNED / FAVORITES ROW ---
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    visible: (libraryBridge && libraryBridge.pinnedGames) ? (libraryBridge.pinnedGames.length > 0) : false

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "Pinned ★"
                            color: Theme.text
                            font.bold: true
                            font.pixelSize: 11
                            textFormat: Text.PlainText
                            Layout.fillWidth: true
                        }
                        Text {
                            text: (libraryBridge && libraryBridge.pinnedGames) ? String(libraryBridge.pinnedGames.length) : ""
                            color: Theme.muted
                            font.pixelSize: 10
                            textFormat: Text.PlainText
                        }
                    }

                    ListView {
                        id: pinnedListView
                        objectName: "overlayLibraryPinnedList"
                        Layout.fillWidth: true
                        implicitHeight: 110
                        orientation: ListView.Horizontal
                        spacing: 6
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        model: libraryBridge ? libraryBridge.pinnedGames : []

                        delegate: Rectangle {
                            required property var modelData
                            required property int index
                            width: 76
                            height: 108
                            radius: Theme.cornerRadius / 2
                            color: (panel.hasActiveFocus && panel.currentSection === 3 && panel.pinnedIndex === index) ? Theme.hover : Theme.panel
                            border.width: 1
                            border.color: (panel.hasActiveFocus && panel.currentSection === 3 && panel.pinnedIndex === index) ? Theme.text : Theme.line

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 4
                                spacing: 2

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    radius: 3
                                    clip: true
                                    color: Theme.main

                                    Image {
                                        anchors.fill: parent
                                        source: modelData.coverUrl || ""
                                        fillMode: Image.PreserveAspectCrop
                                        asynchronous: true
                                        sourceSize: Qt.size(70, 80)
                                        visible: status === Image.Ready
                                    }

                                    Text {
                                        anchors.centerIn: parent
                                        text: modelData.initials || ""
                                        color: Theme.muted; font.bold: true; textFormat: Text.PlainText
                                        font.pixelSize: 18
                                        visible: !parent.children[0].visible
                                    }
                                }

                                Text {
                                    text: modelData.title || ""
                                    color: Theme.text
                                    font.pixelSize: 9
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                    textFormat: Text.PlainText
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: {
                                    panel.currentSection = 3
                                    panel.pinnedIndex = index
                                    if (libraryBridge) libraryBridge.selectGame(modelData.id)
                                }
                            }
                        }
                    }
                }

                // --- ALL INSTALLED GAMES LIST ---
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: searchField.text ? "Search Results" : "All Installed"
                            color: Theme.text
                            font.bold: true
                            font.pixelSize: 11
                            textFormat: Text.PlainText
                            Layout.fillWidth: true
                        }
                        Text {
                            text: libraryBridge ? String(libraryBridge.searchResultCount) : "0"
                            color: Theme.muted
                            font.pixelSize: 10
                            textFormat: Text.PlainText
                        }
                    }

                    ListView {
                        id: installedListView
                        objectName: "overlayLibraryInstalledList"
                        Layout.fillWidth: true
                        implicitHeight: Math.min(300, count * 52)
                        spacing: 3
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        model: libraryBridge ? libraryBridge.searchModel : null

                        delegate: Rectangle {
                            required property var modelData
                            required property int index
                            width: installedListView.width
                            height: 48
                            radius: Theme.cornerRadius / 2
                            color: (panel.hasActiveFocus && panel.currentSection === 4 && panel.installedIndex === index) ? Theme.hover : Theme.panel
                            border.width: 1
                            border.color: (panel.hasActiveFocus && panel.currentSection === 4 && panel.installedIndex === index) ? Theme.text : Theme.line

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 4
                                spacing: 8

                                // Small cover thumbnail
                                Rectangle {
                                    width: 34
                                    height: 42
                                    radius: 3
                                    clip: true
                                    color: Theme.main

                                    Image {
                                        anchors.fill: parent
                                        source: modelData.coverUrl || ""
                                        fillMode: Image.PreserveAspectCrop
                                        asynchronous: true
                                        sourceSize: Qt.size(34, 42)
                                        visible: status === Image.Ready
                                    }

                                    Text {
                                        anchors.centerIn: parent
                                        text: modelData.initials || ""
                                        color: Theme.muted; font.bold: true; textFormat: Text.PlainText
                                        font.pixelSize: 12
                                        visible: !parent.children[0].visible
                                    }
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 1

                                    Text {
                                        text: modelData.title || ""
                                        color: Theme.text
                                        font.bold: true
                                        font.pixelSize: 11
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                        textFormat: Text.PlainText
                                    }

                                    Text {
                                        text: (modelData.platform || "PC") + (modelData.lastPlayed ? (" · " + modelData.lastPlayed) : "")
                                        color: Theme.muted
                                        font.pixelSize: 9
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                        textFormat: Text.PlainText
                                    }
                                }

                                Text {
                                    text: "▶"
                                    color: Theme.muted
                                    font.pixelSize: 11
                                    Layout.rightMargin: 4
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: {
                                    panel.currentSection = 4
                                    panel.installedIndex = index
                                    if (libraryBridge) libraryBridge.selectGame(modelData.id)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ==========================================
    // 3. MODAL OVERLAYS (Detail Card, Switch, Quit)
    // ==========================================

    // --- GAME DETAIL CARD (Cover, Title, Platform, Last Played, Playtime, Play) ---
    Rectangle {
        id: gameDetailModal
        objectName: "overlayLibraryGameDetailModal"
        anchors.fill: parent
        color: Theme.panel
        visible: panel.hasSelectedGame && !panel.isSwitching && !panel.isQuitting
        z: 20

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: "Game Details"
                    color: Theme.muted
                    font.pixelSize: 11
                    textFormat: Text.PlainText
                    Layout.fillWidth: true
                }
                OverlayControl {
                    objectName: "overlayLibraryCardBackButton"
                    text: "Back (B)"
                    compact: true
                    onClicked: {
                        if (libraryBridge) libraryBridge.clearSelectedGame()
                    }
                }
            }

            // Big cover art & title
            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                Rectangle {
                    width: 72
                    height: 96
                    radius: 4
                    clip: true
                    color: Theme.main

                    Image {
                        anchors.fill: parent
                        source: (libraryBridge && libraryBridge.selectedGame) ? (libraryBridge.selectedGame.coverUrl || "") : ""
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        sourceSize: Qt.size(72, 96)
                        visible: status === Image.Ready
                    }

                    Text {
                        anchors.centerIn: parent
                        text: (libraryBridge && libraryBridge.selectedGame) ? (libraryBridge.selectedGame.initials || "") : ""
                        color: Theme.muted; font.bold: true; textFormat: Text.PlainText
                        font.pixelSize: 22
                        visible: !parent.children[0].visible
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Text {
                        text: (libraryBridge && libraryBridge.selectedGame) ? (libraryBridge.selectedGame.title || "") : ""
                        color: Theme.text
                        font.bold: true
                        font.pixelSize: 14
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                        textFormat: Text.PlainText
                    }

                    Text {
                        text: "Platform: " + ((libraryBridge && libraryBridge.selectedGame) ? (libraryBridge.selectedGame.platform || "PC") : "PC")
                        color: Theme.muted
                        font.pixelSize: 10
                        textFormat: Text.PlainText
                    }

                    Text {
                        text: "Last played: " + ((libraryBridge && libraryBridge.selectedGame && libraryBridge.selectedGame.lastPlayed) ? libraryBridge.selectedGame.lastPlayed : "Never")
                        color: Theme.muted
                        font.pixelSize: 10
                        textFormat: Text.PlainText
                    }

                    Text {
                        text: "Total playtime: " + ((libraryBridge && libraryBridge.selectedGame && libraryBridge.selectedGame.playtime) ? libraryBridge.selectedGame.playtime : "0 mins")
                        color: Theme.muted
                        font.pixelSize: 10
                        textFormat: Text.PlainText
                    }
                }
            }

            Item { Layout.fillHeight: true }

            // Big Primary Play Button
            OverlayControl {
                objectName: "overlayLibraryPlayButton"
                text: "Play"
                Layout.fillWidth: true
                implicitHeight: 38
                highlighted: true
                onClicked: {
                    if (libraryBridge && libraryBridge.selectedGame) {
                        panel.requestOverlayPlay(libraryBridge.selectedGame.id)
                    }
                }
            }
        }
    }

    // --- PENDING UPDATE LAUNCH PROMPT MODAL ---
    Rectangle {
        id: overlayUpdateLaunchPrompt
        objectName: "overlayUpdateLaunchPrompt"
        anchors.fill: parent
        color: Theme.panel
        visible: panel.updatePromptVisible
        z: 35

        ColumnLayout {
            anchors.centerIn: parent
            width: parent.width - 32
            spacing: 10

            Text {
                text: "Update available"
                color: Theme.text
                font.bold: true
                font.pixelSize: 15
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
                textFormat: Text.PlainText
            }

            Text {
                text: "An update is pending for " + ((libraryBridge && libraryBridge.selectedGame && libraryBridge.selectedGame.title) ? libraryBridge.selectedGame.title : "this game") + "."
                color: Theme.muted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
                textFormat: Text.PlainText
            }

            OverlayControl {
                objectName: "overlayUpdatePromptUpdateFirstButton"
                text: "Update first (recommended)"
                Layout.fillWidth: true
                implicitHeight: 34
                highlighted: panel.updatePromptAction === 0
                onClicked: {
                    panel.updatePromptAction = 0
                    panel.chooseOverlayUpdateFirst()
                }
            }

            OverlayControl {
                objectName: "overlayUpdatePromptPlayAnywayButton"
                text: "Play anyway"
                Layout.fillWidth: true
                implicitHeight: 34
                highlighted: panel.updatePromptAction === 1
                onClicked: {
                    panel.updatePromptAction = 1
                    panel.chooseOverlayPlayAnyway()
                }
            }

            OverlayControl {
                objectName: "overlayUpdatePromptCancelButton"
                text: "Cancel"
                Layout.fillWidth: true
                implicitHeight: 34
                highlighted: panel.updatePromptAction === 2
                onClicked: {
                    panel.updatePromptAction = 2
                    panel.chooseOverlayCancelUpdatePrompt()
                }
            }
        }
    }

    // --- SWITCH CONFIRMATION MODAL ("Switch to X? Y will be closed.") ---
    Rectangle {
        id: switchModal
        objectName: "overlayLibrarySwitchModal"
        anchors.fill: parent
        color: Theme.panel
        visible: panel.isSwitching
        z: 30

        ColumnLayout {
            anchors.centerIn: parent
            width: parent.width - 32
            spacing: 12

            Text {
                text: libraryBridge && libraryBridge.switchClosing ? "Switching game" : "Switch game?"
                color: Theme.text
                font.bold: true
                font.pixelSize: 15
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
                textFormat: Text.PlainText
            }

            Text {
                text: libraryBridge ? libraryBridge.switchStatus : ""
                color: Theme.text
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
                textFormat: Text.PlainText
            }

            Text {
                text: libraryBridge && libraryBridge.switchClosing
                      ? "The new game starts as soon as this one has closed."
                      : "Waystone waits until the running game has closed before starting the new one."
                color: Theme.muted
                font.pixelSize: 10
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
                textFormat: Text.PlainText
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Layout.topMargin: 8

                OverlayControl {
                    objectName: "overlayLibraryConfirmSwitchButton"
                    visible: !(libraryBridge && libraryBridge.switchClosing)
                    text: "Switch game"
                    Layout.fillWidth: true
                    implicitHeight: 34
                    highlighted: panel.switchAction === 0
                    onClicked: {
                        if (libraryBridge) libraryBridge.confirmSwitch()
                    }
                }

                OverlayControl {
                    objectName: "overlayLibraryCancelSwitchButton"
                    text: "Cancel"
                    Layout.fillWidth: true
                    implicitHeight: 34
                    highlighted: panel.switchAction === 1
                    onClicked: {
                        if (libraryBridge) libraryBridge.cancelSwitch()
                    }
                }
            }
        }
    }

    // --- QUIT CONFIRMATION MODAL ---
    Rectangle {
        id: quitModal
        objectName: "overlayLibraryQuitModal"
        anchors.fill: parent
        color: Theme.panel
        visible: panel.isQuitting
        z: 30

        ColumnLayout {
            anchors.centerIn: parent
            width: parent.width - 32
            spacing: 12

            Text {
                text: "Quit game?"
                color: Theme.text
                font.bold: true
                font.pixelSize: 15
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
                textFormat: Text.PlainText
            }

            Text {
                text: "Quit " + ((libraryBridge && libraryBridge.runningGame) ? libraryBridge.runningGame.title : "the active game") + "?"
                color: Theme.text
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
                textFormat: Text.PlainText
            }

            Text {
                text: "Save your progress first. Waystone closes only this game's processes."
                color: Theme.muted
                font.pixelSize: 10
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
                textFormat: Text.PlainText
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Layout.topMargin: 8

                OverlayControl {
                    objectName: "overlayLibraryConfirmQuitButton"
                    text: "Quit game"
                    Layout.fillWidth: true
                    implicitHeight: 34
                    highlighted: panel.quitAction === 0
                    onClicked: {
                        if (libraryBridge) libraryBridge.confirmQuit()
                    }
                }

                OverlayControl {
                    objectName: "overlayLibraryCancelQuitButton"
                    text: "Cancel"
                    Layout.fillWidth: true
                    implicitHeight: 34
                    highlighted: panel.quitAction === 1
                    onClicked: {
                        if (libraryBridge) libraryBridge.cancelQuit()
                    }
                }
            }
        }
    }
}
