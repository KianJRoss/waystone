import QtQuick
import QtQuick.Controls
import QtQuick.Window
import QtQuick.Layouts
import QtCore
import "."
import "overlay_components"

QtObject {
    id: manager
    objectName: "desktopOverlayManager"
    property bool visible: false

    // Public API
    property bool editing: false
    readonly property bool visibleEditor: editing
    readonly property bool active: editing && surface.visible
    property var socialView: null
    property var voiceView: null
    property var audioRoutes: typeof voiceAudioRoutes !== "undefined" ? voiceAudioRoutes : null
    property var currentGame: null
    property var toolsBridge: typeof overlayTools !== "undefined" ? overlayTools : null
    property var libraryBridge: typeof overlayLibrary !== "undefined" ? overlayLibrary : null
    // W3 owns the picker/model. This optional slot is inert until supplied.
    property Component controllerProfilePicker: null

    property bool persistenceEnabled: typeof headlessCheck !== "undefined" && !headlessCheck
    property url layoutLocation: "file://" + StandardPaths.writableLocation(StandardPaths.AppConfigLocation) + "/overlay-widgets-v1.ini"
    property rect boundsOverride: Qt.rect(0, 0, 0, 0)
    property int navigationWidget: 0
    property int toolbarIndex: 0
    property int toolIndex: 0
    property bool layoutMode: false
    property string layoutAction: "move"
    readonly property var windows: [toolbarWindow, partyWindow, friendsWindow, toolsWindow, libraryWindow]
    readonly property rect screenArea: {
        var monitor = Qt.application.screens.length ? Qt.application.screens[0] : null
        return boundsOverride.width > 0 && boundsOverride.height > 0 ? boundsOverride
            : (monitor ? Qt.rect(monitor.virtualX, monitor.virtualY, monitor.width, monitor.height) : Qt.rect(0, 0, 800, 600))
    }
    readonly property rect itemBounds: Qt.rect(0, 0, screenArea.width, screenArea.height)
    readonly property var inputRegions: windows.filter(function(widget) { return widget.shown && (manager.editing || widget.pinned) })
        .map(function(widget) { return {x: widget.x, y: widget.y, width: widget.width, height: widget.height} })
    readonly property bool surfaceNeeded: editing || windows.some(function(widget) { return widget.shown && widget.pinned })
    property var surfaceHost: typeof overlaySurfaceHost !== "undefined" ? overlaySurfaceHost : null
    readonly property string surfaceStatus: surfaceHost ? surfaceHost.status : "Offscreen fixture: no native input-mask host"
    property bool surfaceReady: surfaceHost === null
    onSurfaceHostChanged: scheduleMask()
    function scheduleMask() { maskTimer.restart() }
    function focusSurface() {
        // Flags and mask-gated visibility settle in the same event-loop turn.
        if (!editing || !surface.visible || (surface.flags & Qt.WindowDoesNotAcceptFocus)) return
        surface.raise(); surface.requestActivate(); windows[navigationWidget].requestActivate()
    }
    onInputRegionsChanged: scheduleMask()
    onEditingChanged: scheduleMask()
    onSurfaceNeededChanged: scheduleMask()
    property var maskTimer: Timer {
        interval: 0
        onTriggered: manager.surfaceReady = manager.surfaceHost ? manager.surfaceHost.updateRegions(surface, manager.inputRegions, manager.editing) : true
    }
    property var surfaceWindow: Window {
        id: surface
        objectName: "overlaySurfaceWindow"
        title: "Waystone Overlay"
        color: "transparent"
        x: manager.screenArea.x; y: manager.screenArea.y
        width: manager.screenArea.width; height: manager.screenArea.height
        flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.X11BypassWindowManagerHint
             | Qt.NoDropShadowWindowHint | (manager.editing ? 0 : Qt.WindowDoesNotAcceptFocus)
        visible: manager.surfaceNeeded && (!manager.surfaceHost || (manager.surfaceHost.supported && manager.surfaceReady))
        transientParent: null
        onFlagsChanged: { if (manager.surfaceHost) manager.surfaceReady = false; manager.scheduleMask() }
        onVisibleChanged: {
            manager.scheduleMask()
            // Production visibility is admitted asynchronously after masking;
            // focus requests made earlier in open() can target a hidden window.
            if (visible && manager.editing) Qt.callLater(manager.focusSurface)
        }
        onWidthChanged: manager.scheduleMask()
        onHeightChanged: manager.scheduleMask()
        onActiveChanged: if (manager.editing && active) manager.scheduleMask()
        onClosing: function(close) { close.accepted = false; manager.closeOverlay() }
        Rectangle {
            anchors.fill: parent
            color: Qt.rgba(Theme.background.r, Theme.background.g, Theme.background.b, manager.editing ? 0.42 : 0)
        }
        Shortcut { sequence: "Escape"; enabled: manager.editing; context: Qt.WindowShortcut; onActivated: manager.closeOverlay() }
    }
    function defaultGeometry(index) {
        var area = itemBounds
        var left = area.x + 16, top = area.y + 16
        var body = Math.max(140, area.height - 216)
        if (index === 0) return Qt.rect(left, top, Math.min(680, area.width - 32), 176)
        if (index === 1) return Qt.rect(left, top + 192, Math.min(380, area.width / 2 - 24), Math.min(420, body))
        if (index === 2) return Qt.rect(left + Math.min(380, area.width / 2 - 24) + 24, top + 192, Math.min(320, area.width / 2 - 24), area.width >= 1100 ? 360 : Math.max(140, Math.floor((body - 16) / 2)))
        // Quick library: its own column right of Tools on wide screens; in front of Party on narrow ones.
        if (index === 4) return area.width >= 1504
            ? Qt.rect(left + 1052, top, 420, Math.min(720, area.height - 32))
            : Qt.rect(left, top + 192, Math.min(400, area.width - 32), Math.min(560, body))
        if (area.width >= 1100) return Qt.rect(left + 748, top + 192, 280, 300)
        return Qt.rect(left + Math.min(380, area.width / 2 - 24) + 24, top + 208 + Math.max(140, Math.floor((body - 16) / 2)), Math.min(320, area.width / 2 - 24), Math.max(140, Math.floor((body - 16) / 2)))
    }
    function selectWidget(index) {
        navigationWidget = index
        for (var i = 0; i < windows.length; i++) windows[i].z = i === index ? 10 : i
        windows[index].requestActivate()
    }
    function cycleWidget(step) {
        for (var offset = 1; offset <= windows.length; offset++) {
            var index = (navigationWidget + step * offset + windows.length * 2) % windows.length
            if (windows[index].shown) { selectWidget(index); return }
        }
    }
    function beginArrange(index, action) {
        if (!editing) return
        layoutMode = true; layoutAction = action; selectWidget(index)
    }
    function finishArrange() {
        if (layoutMode) windows[navigationWidget].commitGeometry()
        layoutMode = false
    }
    function resetLayout() {
        for (var i = 0; i < windows.length; i++) windows[i].resetLayout()
    }
    function toolbarAction(index) {
        if (index === 0) partyWindow.shown = !partyWindow.shown
        else if (index === 1) friendsWindow.shown = !friendsWindow.shown
        else if (index === 2) toolsWindow.shown = !toolsWindow.shown
        else if (index === 3) { if (layoutMode) finishArrange(); else beginArrange(1, "move") }
        else if (index === 4) resetLayout()
        else if (index === 5) closeOverlay()
        else if (index === 6) toggleLibrary()
    }
    function toggleLibrary() {
        libraryWindow.shown = !libraryWindow.shown
        if (libraryWindow.shown) {
            selectWidget(4)
        }
    }
    function openLibrary() {
        libraryWindow.shown = true
        selectWidget(4)
    }
    function openTool(name) { toolsWindow.openTool(name) }
    function closeTool() { toolsWindow.closeTool() }
    function toolAction(index, forceFull) {
        var full = forceFull === true
        if (index === 0) {
            if (full || !libraryBridge) {
                closeOverlay()
                libraryRequested()
            } else {
                openLibrary()
            }
        } else if (index === 1) {
            if (full) {
                closeOverlay()
                downloadsRequested()
            } else {
                openTool("downloads")
            }
        } else if (index === 2) {
            if (full) {
                closeOverlay()
                controllersRequested()
            } else {
                openTool("controllers")
            }
        } else if (index === 3) {
            closeOverlay()
            settingsRequested()
        } else if (index === 4) {
            if (full) {
                closeOverlay()
                settingsRequested()
            } else {
                openTool("volume")
            }
        } else if (index === 5) {
            openTool("screenshot")
        } else if (index === 6) {
            if (full) {
                closeOverlay()
                settingsRequested()
            } else {
                openTool("saves")
            }
        } else if (index === 7) {
            if (full) {
                closeOverlay()
                settingsRequested()
            } else {
                openTool("performance")
            }
        }
    }

    // Signals (forward existing hub signals)
    signal libraryRequested()
    signal controllersRequested()
    signal connectionsRequested()
    signal downloadsRequested()
    signal settingsRequested()
    signal returnToGameRequested()

    // Overlay sounds use context "overlay": they still play while a game runs (the overlay sits on top of it).
    function overlaySound(event) { if (typeof uiSounds !== "undefined" && uiSounds) uiSounds.playIn(event, "overlay") }
    readonly property var navigationSounds: ({ left: "move", right: "move", up: "move", down: "move",
        accept: "select", next_section: "move", previous_section: "move" })  // "back" sounds in closeOverlay
    function open() {
        overlaySound("open")
        editing = true
        navigationWidget = 0; toolbarIndex = 0
        closeTool()
        toolbarWindow.shown = true
        manager.visible = true
        var noGame = !currentGame || !currentGame.title || (libraryBridge && !libraryBridge.hasRunningGame)
        if (libraryBridge !== null && noGame) {
            libraryWindow.shown = true
            selectWidget(4)  // like the Xbox guide: with no game running, the library has focus
        }
        _applyEditingToWidgets()
        Qt.callLater(focusSurface)
        scheduleMask()
    }

    function closeOverlay() {
        if (editing) overlaySound("back")
        finishArrange()
        for (var i = 0; i < windows.length; i++) windows[i].saveGeometry()
        closeTool()
        editing = false
        toolbarWindow.shown = false
        _applyEditingToWidgets()
        // Hide unpinned widget windows; keep pinned visible
        _hideUnpinnedIfNotShown()
        manager.visible = false
        returnToGameRequested()
    }

    function toggle() {
        if (editing) closeOverlay()
        else open()
    }

    function navigate(action) {
        if (!editing) return false
        if (navigationSounds[action]) overlaySound(navigationSounds[action])
        if (!windows[navigationWidget].shown) selectWidget(0)
        if (layoutMode) {
            if (action === "back" || action === "menu") { finishArrange(); return true }
            if (action === "accept") { layoutAction = layoutAction === "move" ? "resize" : "move"; return true }
            if (action === "next_section" || action === "previous_section") { windows[navigationWidget].commitGeometry(); cycleWidget(action === "next_section" ? 1 : -1); return true }
            return windows[navigationWidget].navigateLayout(action)
        }
        if (action === "back" || action === "menu") {
            if (navigationWidget === 3 && toolsWindow.activeTool !== "") {
                if (toolsWindow.activeTool === "controllers") { closeTool(); return true }
                closeTool()
                return true
            }
            closeOverlay(); return true
        }
        if (action === "next_section" || action === "previous_section") { cycleWidget(action === "next_section" ? 1 : -1); return true }
        if (navigationWidget === 0) {
            if (action === "accept") toolbarAction(toolbarIndex)
            else if (action === "right") toolbarIndex = (toolbarIndex + 1) % 6
            else if (action === "left") toolbarIndex = (toolbarIndex + 5) % 6
            else if (action === "down" || action === "up") toolbarIndex = (toolbarIndex + 3) % 6
            else return false
            return true
        }
        if (navigationWidget === 1) return partyWidget.navigate(action)
        if (navigationWidget === 2) return friendsPanel.navigate(action)
        if (navigationWidget === 3) {
            if (toolsWindow.activeTool !== "") {
                if (toolsWindow.activeTool === "controllers") return panelController.navigate(action)
                if (action === "back") { closeTool(); return true }
                return true
            }
            var count = (toolsColumn && toolsColumn.toolCount) ? toolsColumn.toolCount : 7
            if (action === "accept") toolAction(toolIndex)
            else if (action === "down" || action === "right") toolIndex = (toolIndex + 1) % count
            else if (action === "up" || action === "left") toolIndex = (toolIndex + count - 1) % count
            else return false
            Qt.callLater(revealTool)
            return true
        }
        if (navigationWidget === 4) return libraryPanel.navigate(action)
        return false
    }

    function revealTool() {
        var control = toolsColumn.children[toolIndex]
        if (!control) return
        if (control.y < toolsScroll.contentY) toolsScroll.contentY = control.y
        else if (control.y + control.height > toolsScroll.contentY + toolsScroll.height) toolsScroll.contentY = control.y + control.height - toolsScroll.height
    }

    function _applyEditingToWidgets() {
        for (var i = 0; i < windows.length; i++) windows[i].restoreVisibility()
    }

    function _hideUnpinnedIfNotShown() {
        // Visibility belongs to each window policy; never hide the manager parent.
        partyWindow.restoreVisibility()
        friendsWindow.restoreVisibility()
        toolsWindow.restoreVisibility()
        libraryWindow.restoreVisibility()
    }

    // Toolbar window
    property var toolbarWidget: OverlayWidgetItem {
        id: toolbarWindow
        parent: surface.contentItem
        minimumHeight: Math.min(176, layoutBounds.height)
        widgetId: "toolbar"
        persistenceEnabled: manager.persistenceEnabled
        layoutLocation: manager.layoutLocation
        boundsOverride: manager.itemBounds
        initialGeometry: manager.defaultGeometry(0)
        layoutSelected: manager.editing && manager.navigationWidget === 0
        layoutAction: manager.layoutMode && manager.navigationWidget === 0 ? manager.layoutAction : ""
        onSelectionRequested: manager.selectWidget(0)
        onArrangeRequested: function(action) { manager.beginArrange(0, action) }
        onActiveChanged: if (active && manager.editing) manager.selectWidget(0)
        objectName: "overlayToolbarWindow"
        widgetTitle: "Overlay"
        shown: false
        pinned: false
        pinningAllowed: false
        editing: manager.editing

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6

            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                OverlayControl {
                    objectName: "overlayToggleLibrary"
                    text: libraryWindow.shown ? "Quick Library ✓" : "Quick Library"
                    hint: "Toggle compact in-overlay quick library"
                    Layout.fillWidth: true
                    onClicked: manager.toggleLibrary()
                }
            }

            GridLayout {
                Layout.fillWidth: true; columns: 3; columnSpacing: 6; rowSpacing: 6
                OverlayControl { objectName: "overlayToggleParty"; text: partyWindow.shown ? "Party ✓" : "Party"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 0 && manager.toolbarIndex === 0; onClicked: manager.toolbarAction(0) }
                OverlayControl { objectName: "overlayToggleFriends"; text: friendsWindow.shown ? "Friends ✓" : "Friends"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 0 && manager.toolbarIndex === 1; onClicked: manager.toolbarAction(1) }
                OverlayControl { objectName: "overlayToggleTools"; text: toolsWindow.shown ? "Tools ✓" : "Tools"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 0 && manager.toolbarIndex === 2; onClicked: manager.toolbarAction(2) }
                OverlayControl { objectName: "overlayArrangeLayout"; text: manager.layoutMode ? "Done arranging" : "Arrange widgets"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 0 && manager.toolbarIndex === 3; onClicked: manager.toolbarAction(3) }
                OverlayControl { objectName: "overlayResetLayout"; text: "Reset layout"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 0 && manager.toolbarIndex === 4; onClicked: manager.toolbarAction(4) }
                OverlayControl { objectName: "overlayCloseEditor"; text: "Close overlay"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 0 && manager.toolbarIndex === 5; onClicked: manager.toolbarAction(5) }
            }
            Text {
                Layout.fillWidth: true
                objectName: "overlayNavigationHint"
                visible: !manager.layoutMode || manager.navigationWidget !== 0
                text: manager.layoutMode ? "D-pad: " + manager.layoutAction + " · A: move/size · LB/RB: widget · B: done" : "LB/RB: switch widget · A: choose · B: return"
                color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; wrapMode: Text.WordWrap; textFormat: Text.PlainText
            }
        }
    }

    property var toolbarWatcher: Connections { target: toolbarWindow; function onShownChanged() { if (!toolbarWindow.shown && manager.editing) manager.closeOverlay() } }

    // Party widget
    property var partyWindowOwner: OverlayWidgetItem {
        id: partyWindow
        parent: surface.contentItem
        minimumHeight: Math.min(260, layoutBounds.height)
        widgetId: "party"
        persistenceEnabled: manager.persistenceEnabled
        layoutLocation: manager.layoutLocation
        boundsOverride: manager.itemBounds
        initialGeometry: manager.defaultGeometry(1)
        layoutSelected: manager.editing && manager.navigationWidget === 1
        layoutAction: manager.layoutMode && manager.navigationWidget === 1 ? manager.layoutAction : ""
        onSelectionRequested: manager.selectWidget(1)
        onArrangeRequested: function(action) { manager.beginArrange(1, action) }
        onActiveChanged: if (active && manager.editing) manager.selectWidget(1)
        objectName: "overlayPartyWindow"
        widgetTitle: "Party & Voice"
        shown: true
        pinned: false
        editing: manager.editing

        OverlayPartyPanel {
            id: partyWidget
            objectName: "overlayPartyWidget"
            anchors.fill: parent
            anchors.margins: 6
            hasActiveFocus: manager.editing && manager.navigationWidget === 1 && !manager.layoutMode
            socialView: manager.socialView
            voiceView: manager.voiceView
            audioRoutes: manager.audioRoutes
        }
    }

    // Friends widget (provider actions are exposed only when the provider supports them)
    property var friendsWidget: OverlayWidgetItem {
        id: friendsWindow
        parent: surface.contentItem
        minimumHeight: Math.min(140, layoutBounds.height)
        widgetId: "friends"
        persistenceEnabled: manager.persistenceEnabled
        layoutLocation: manager.layoutLocation
        boundsOverride: manager.itemBounds
        initialGeometry: manager.defaultGeometry(2)
        layoutSelected: manager.editing && manager.navigationWidget === 2
        layoutAction: manager.layoutMode && manager.navigationWidget === 2 ? manager.layoutAction : ""
        onSelectionRequested: manager.selectWidget(2)
        onArrangeRequested: function(action) { manager.beginArrange(2, action) }
        onActiveChanged: if (active && manager.editing) manager.selectWidget(2)
        objectName: "overlayFriendsWindow"
        widgetTitle: "Friends"
        shown: true
        pinned: false
        editing: manager.editing

        OverlayFriendsPanel {
            id: friendsPanel
            objectName: "overlayFriendsList"
            anchors.fill: parent
            hasActiveFocus: manager.editing && manager.navigationWidget === 2 && !manager.layoutMode
            socialView: manager.socialView
            voiceView: manager.voiceView
        }
    }

    // Tools widget
    property var toolsWidget: OverlayWidgetItem {
        id: toolsWindow
        parent: surface.contentItem
        minimumHeight: Math.min(140, layoutBounds.height)
        widgetId: "tools"
        persistenceEnabled: manager.persistenceEnabled
        layoutLocation: manager.layoutLocation
        boundsOverride: manager.itemBounds
        initialGeometry: manager.defaultGeometry(3)
        layoutSelected: manager.editing && manager.navigationWidget === 3
        layoutAction: manager.layoutMode && manager.navigationWidget === 3 ? manager.layoutAction : ""
        onSelectionRequested: manager.selectWidget(3)
        onArrangeRequested: function(action) { manager.beginArrange(3, action) }
        onActiveChanged: if (active && manager.editing) manager.selectWidget(3)
        objectName: "overlayToolsWindow"
        widgetTitle: "Tools"
        shown: true
        pinned: false
        editing: manager.editing

        property string activeTool: ""
        property rect geometryBeforeControllerStudio: Qt.rect(0, 0, 0, 0)
        function openTool(name) {
            if (name === "controllers" && activeTool !== "controllers") {
                geometryBeforeControllerStudio = rectangle()
                minimumWidth = Math.min(720, Math.max(1, layoutBounds.width))
                minimumHeight = Math.min(520, Math.max(1, layoutBounds.height))
                var w = Math.min(maximumWidth, Math.max(minimumWidth, layoutBounds.width - 32))
                var h = Math.min(maximumHeight, Math.max(minimumHeight, layoutBounds.height - 32))
                applyGeometry({x: Math.max(0, (layoutBounds.width - w) / 2), y: Math.max(0, (layoutBounds.height - h) / 2), width: w, height: h})
            }
            activeTool = name
        }
        function closeTool() {
            var restore = activeTool === "controllers" ? geometryBeforeControllerStudio : Qt.rect(0, 0, 0, 0)
            activeTool = ""
            if (restore.width > 0 && restore.height > 0) {
                minimumWidth = Math.min(220, Math.max(1, layoutBounds.width))
                minimumHeight = Math.min(140, Math.max(1, layoutBounds.height))
                applyGeometry({x: restore.x, y: restore.y, width: restore.width, height: restore.height})
                geometryBeforeControllerStudio = Qt.rect(0, 0, 0, 0)
            }
        }

        Flickable {
            id: toolsScroll
            objectName: "overlayToolsScroll"
            anchors.fill: parent; anchors.margins: 10
            contentWidth: width; contentHeight: toolsColumn.implicitHeight
            clip: true; boundsBehavior: Flickable.StopAtBounds
            visible: toolsWindow.activeTool === ""
            ScrollBar.vertical: OverlayScrollBar { id: toolsBar }
            ColumnLayout {
                id: toolsColumn
                objectName: "overlayToolsColumn"
                width: toolsScroll.width - (toolsBar.visible ? 10 : 0)
                height: implicitHeight
                spacing: 8
                readonly property int toolCount: {
                    var perf = manager.toolsBridge ? manager.toolsBridge.performanceAvailable : false
                    return perf ? 8 : 7
                }

                OverlayControl { objectName: "overlayActionLibrary"; text: "Library"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 3 && toolsWindow.activeTool === "" && manager.toolIndex === 0; onClicked: manager.toolAction(0) }
                OverlayControl { objectName: "overlayActionDownloads"; text: "Downloads"; hint: "Active downloads & installs"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 3 && toolsWindow.activeTool === "" && manager.toolIndex === 1; onClicked: manager.toolAction(1) }
                OverlayControl { objectName: "overlayActionControllers"; text: "Controllers"; hint: "Controller profile & studio"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 3 && toolsWindow.activeTool === "" && manager.toolIndex === 2; onClicked: manager.toolAction(2) }
                OverlayControl { objectName: "overlayActionSettings"; text: "Settings"; hint: "Settings · accounts are in Settings > Accounts"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 3 && toolsWindow.activeTool === "" && manager.toolIndex === 3; onClicked: manager.toolAction(3) }
                OverlayControl { objectName: "overlayActionVolume"; text: "Volume"; hint: "Master, game & voice sliders"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 3 && toolsWindow.activeTool === "" && manager.toolIndex === 4; onClicked: manager.toolAction(4) }
                OverlayControl { objectName: "overlayActionScreenshot"; text: "Screenshot"; hint: "Take screenshot to Pictures/Waystone"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 3 && toolsWindow.activeTool === "" && manager.toolIndex === 5; onClicked: manager.toolAction(5) }
                OverlayControl { objectName: "overlayActionSaves"; text: "Save Backup"; hint: "Back up running game saves via Ludusavi"; Layout.fillWidth: true; highlighted: manager.navigationWidget === 3 && toolsWindow.activeTool === "" && manager.toolIndex === 6; onClicked: manager.toolAction(6) }
                OverlayControl {
                    objectName: "overlayActionPerformance"
                    text: "Performance"
                    hint: "FPS and frame time telemetry"
                    Layout.fillWidth: true
                    visible: manager.toolsBridge ? manager.toolsBridge.performanceAvailable : false
                    highlighted: manager.navigationWidget === 3 && toolsWindow.activeTool === "" && manager.toolIndex === 7
                    onClicked: manager.toolAction(7)
                }

                Loader {
                    objectName: "overlayControllerProfileSlot"
                    Layout.fillWidth: true
                    active: manager.controllerProfilePicker !== null
                    sourceComponent: manager.controllerProfilePicker
                }
                Rectangle {
                    objectName: "overlayControllerProfilePlaceholder"
                    Layout.fillWidth: true
                    implicitHeight: 60
                    visible: manager.controllerProfilePicker === null
                    radius: Theme.cornerRadius / 2
                    color: Qt.rgba(Theme.main.r, Theme.main.g, Theme.main.b, 0.30)
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 9; spacing: 3
                        Text { text: "Controller profile"; color: Theme.text; font.family: Theme.family; font.pixelSize: 12; textFormat: Text.PlainText; Layout.fillWidth: true; elide: Text.ElideRight }
                        Text { text: "Picker not connected"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; textFormat: Text.PlainText; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                }
            }
        }

        // Compact tool panels
        OverlayToolDownloads {
            id: panelDownloads
            objectName: "overlayToolDownloadsPanel"
            anchors.fill: parent; anchors.margins: 10
            visible: toolsWindow.activeTool === "downloads"
            toolsBridge: manager.toolsBridge
            onOpenFullRequested: manager.toolAction(1, true)
            onBackRequested: toolsWindow.closeTool()
        }

        OverlayToolController {
            id: panelController
            objectName: "overlayToolControllerPanel"
            anchors.fill: parent; anchors.margins: 10
            visible: toolsWindow.activeTool === "controllers"
            toolsBridge: manager.toolsBridge
            controllerProfilePicker: manager.controllerProfilePicker
            onOpenFullRequested: manager.toolAction(2, true)
            onBackRequested: toolsWindow.closeTool()
        }

        OverlayToolVolume {
            id: panelVolume
            objectName: "overlayToolVolumePanel"
            anchors.fill: parent; anchors.margins: 10
            visible: toolsWindow.activeTool === "volume"
            toolsBridge: manager.toolsBridge
            onOpenFullRequested: manager.toolAction(3, true)
            onBackRequested: toolsWindow.closeTool()
        }

        OverlayToolScreenshot {
            id: panelScreenshot
            objectName: "overlayToolScreenshotPanel"
            anchors.fill: parent; anchors.margins: 10
            visible: toolsWindow.activeTool === "screenshot"
            toolsBridge: manager.toolsBridge
            surfaceWindow: surface
            onOpenFullRequested: manager.toolAction(3, true)
            onBackRequested: toolsWindow.closeTool()
        }

        OverlayToolSaves {
            id: panelSaves
            objectName: "overlayToolSavesPanel"
            anchors.fill: parent; anchors.margins: 10
            visible: toolsWindow.activeTool === "saves"
            toolsBridge: manager.toolsBridge
            currentGame: manager.currentGame
            onOpenFullRequested: manager.toolAction(3, true)
            onBackRequested: toolsWindow.closeTool()
        }

        OverlayToolPerformance {
            id: panelPerformance
            objectName: "overlayToolPerformancePanel"
            anchors.fill: parent; anchors.margins: 10
            visible: toolsWindow.activeTool === "performance"
            toolsBridge: manager.toolsBridge
            onOpenFullRequested: manager.toolAction(3, true)
            onBackRequested: toolsWindow.closeTool()
        }
    }

    // Quick Library widget
    property var libraryWidget: OverlayWidgetItem {
        id: libraryWindow
        parent: surface.contentItem
        minimumWidth: Math.min(320, layoutBounds.width)
        minimumHeight: Math.min(260, layoutBounds.height)
        widgetId: "library"
        persistenceEnabled: manager.persistenceEnabled
        layoutLocation: manager.layoutLocation
        boundsOverride: manager.itemBounds
        initialGeometry: manager.defaultGeometry(4)
        layoutSelected: manager.editing && manager.navigationWidget === 4
        layoutAction: manager.layoutMode && manager.navigationWidget === 4 ? manager.layoutAction : ""
        onSelectionRequested: manager.selectWidget(4)
        onArrangeRequested: function(action) { manager.beginArrange(4, action) }
        onActiveChanged: if (active && manager.editing) manager.selectWidget(4)
        objectName: "overlayLibraryWindow"
        widgetTitle: "Library"
        shown: false
        pinned: false
        editing: manager.editing

        OverlayLibraryPanel {
            id: libraryPanel
            objectName: "overlayLibraryPanel"
            anchors.fill: parent
            anchors.margins: 6
            hasActiveFocus: manager.editing && manager.navigationWidget === 4 && !manager.layoutMode
            libraryBridge: manager.libraryBridge
            onOpenFullRequested: manager.toolAction(0, true)
            onCloseOverlayRequested: manager.closeOverlay()
        }
    }
}
