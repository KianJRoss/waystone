import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtCore
import "."
import "overlay_components/LayoutGeometry.js" as Geometry
import "overlay_components"

Window {
    id: root
    property string widgetTitle: "Widget"
    property string widgetId: ""
    property bool pinned: false
    property bool pinningAllowed: true
    property bool editing: false
    property bool layoutSelected: false
    property string layoutAction: ""
    property bool shown: true
    // Waystone currently launches with xcb. Keep frame gestures in-app so edge
    // and grid release behavior is deterministic; compositor handoff is opt-in.
    property bool useNativeSystemGestures: false
    readonly property bool unmanagedPinned: pinned && !editing
    // Standalone/fixture windows are inert by default. The app manager opts in.
    property bool persistenceEnabled: false
    property url layoutLocation: ""
    property rect boundsOverride: Qt.rect(0, 0, 0, 0)
    readonly property rect layoutBounds: boundsOverride.width > 0 && boundsOverride.height > 0 ? boundsOverride
        : (screen ? Qt.rect(screen.virtualX, screen.virtualY, screen.width, screen.height) : Qt.rect(0, 0, 800, 600))
    property rect initialGeometry: Qt.rect(80, 80, 320, 240)
    property bool layoutReady: false
    property bool adjustingGeometry: false
    property var settingsObject: null
    property rect previousBounds: Qt.rect(0, 0, 0, 0)
    signal arrangeRequested(string action)

    minimumWidth: Math.min(220, Math.max(1, layoutBounds.width))
    minimumHeight: Math.min(140, Math.max(1, layoutBounds.height))
    maximumWidth: 960
    maximumHeight: 720
    width: initialGeometry.width; height: initialGeometry.height
    x: initialGeometry.x; y: initialGeometry.y
    title: widgetTitle
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
           | (editing ? 0 : Qt.WindowDoesNotAcceptFocus)
           | (unmanagedPinned ? Qt.X11BypassWindowManagerHint : 0)
    visible: shown && (pinned || editing)
    transientParent: null
    function restoreVisibility() { visible = shown && (pinned || editing) }
    onFlagsChanged: Qt.callLater(restoreVisibility)
    onShownChanged: restoreVisibility()
    onPinnedChanged: restoreVisibility()
    onEditingChanged: restoreVisibility()
    onClosing: function(close) { close.accepted = false; commitGeometry(); shown = false }

    function rectangle() { return {x: x, y: y, width: width, height: height} }
    // Where the pointer really is on screen. mapToGlobal() goes through this window's position, which lags behind
    // our own moves while dragging and made widgets rubber-band back and forth.
    function pointerAt(item, mx, my) {
        return (typeof screenPointer !== "undefined" && screenPointer) ? screenPointer.position() : item.mapToGlobal(mx, my)
    }
    function areaRecord() { return {x: layoutBounds.x, y: layoutBounds.y, width: layoutBounds.width, height: layoutBounds.height} }
    function applyGeometry(rect) {
        adjustingGeometry = true
        x = rect.x; y = rect.y; width = rect.width; height = rect.height
        adjustingGeometry = false
    }
    function constrain(snap) {
        applyGeometry(Geometry.fit(rectangle(), layoutBounds, minimumWidth, minimumHeight, maximumWidth, maximumHeight, snap))
    }
    function saveGeometry() {
        if (!layoutReady || !settingsObject) return
        settingsObject.setValue("geometry", {version: 1, rect: rectangle(), area: areaRecord()})
        settingsObject.sync()
    }
    function snapGeometry(resize) {
        applyGeometry(Geometry.fit(rectangle(), layoutBounds, minimumWidth, minimumHeight, maximumWidth, maximumHeight, true, resize))
    }
    function commitGeometry() { geometryTimer.stop(); snapGeometry(layoutAction === "resize"); saveGeometry() }
    function commitResizeGeometry() { geometryTimer.stop(); snapGeometry(true); saveGeometry() }
    function resetLayout() {
        geometryTimer.stop()
        applyGeometry(Geometry.fit(initialGeometry, layoutBounds, minimumWidth, minimumHeight, maximumWidth, maximumHeight, false))
        saveGeometry()
    }
    function initializeLayout() {
        layoutReady = false
        if (settingsObject) { settingsObject.destroy(); settingsObject = null }
        var saved = null
        if (persistenceEnabled && widgetId.length && layoutLocation.toString().length) {
            settingsObject = settingsFactory.createObject(root, {location: layoutLocation, category: "overlay-widgets-v1/" + widgetId})
            if (settingsObject) saved = settingsObject.value("geometry", null)
        }
        applyGeometry(Geometry.restore(saved, initialGeometry, layoutBounds, minimumWidth, minimumHeight, maximumWidth, maximumHeight))
        previousBounds = layoutBounds; layoutReady = true
    }
    function reflow() {
        if (!layoutReady) return
        var old = {version: 1, rect: rectangle(), area: previousBounds}
        applyGeometry(Geometry.restore(old, initialGeometry, layoutBounds, minimumWidth, minimumHeight, maximumWidth, maximumHeight))
        previousBounds = layoutBounds; saveGeometry()
    }
    function moveBy(dx, dy) { x += dx; y += dy; constrain(false) }
    function resizeBy(dw, dh) { width += dw; height += dh; constrain(false) }
    function navigateLayout(action) {
        if (!editing || !layoutAction.length) return false
        var dx = action === "left" ? -16 : action === "right" ? 16 : 0
        var dy = action === "up" ? -16 : action === "down" ? 16 : 0
        if (!dx && !dy) return false
        if (layoutAction === "resize") resizeBy(dx, dy)
        else moveBy(dx, dy)
        geometryTimer.restart(); return true
    }
    function geometryChanged() { if (layoutReady && !adjustingGeometry) geometryTimer.restart() }
    onXChanged: geometryChanged()
    onYChanged: geometryChanged()
    onWidthChanged: geometryChanged()
    onHeightChanged: geometryChanged()
    onLayoutBoundsChanged: Qt.callLater(reflow)
    onPersistenceEnabledChanged: if (layoutReady) initializeLayout()
    onLayoutLocationChanged: if (layoutReady) initializeLayout()
    Component.onCompleted: Qt.callLater(initializeLayout)
    Component.onDestruction: saveGeometry()
    property Component settingsFactory: Component { Settings {} }
    property Timer geometryTimer: Timer {
        interval: 150
        onTriggered: { root.constrain(false); root.saveGeometry() }
    }
    default property alias content: contentArea.data
    // Native transparent cards: depth is drawn with Theme-only alpha layers so
    // the software renderer and the desktop compositor use the same content.
    property Item chrome: Item {
        parent: root.contentItem
        anchors.fill: parent
        Rectangle {
            anchors.fill: parent; anchors.margins: 2; anchors.topMargin: 5
            radius: Theme.cornerRadius + 4
            color: Qt.rgba(Theme.background.r, Theme.background.g, Theme.background.b, 0.18)
        }
        Rectangle {
            anchors.fill: parent; anchors.margins: 4; anchors.topMargin: 7
            radius: Theme.cornerRadius + 2
            color: Qt.rgba(Theme.background.r, Theme.background.g, Theme.background.b, 0.30)
        }
        Rectangle {
            objectName: "widgetCardSurface"
            anchors.fill: parent; anchors.margins: 6
            radius: Theme.cornerRadius
            color: Qt.rgba(Theme.panel.r, Theme.panel.g, Theme.panel.b, 0.91)
            border.color: root.layoutSelected ? Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.55)
                                              : Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.15)
        }
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 7
            anchors.bottomMargin: 12
            spacing: 0
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 34
                color: Qt.rgba(Theme.main.r, Theme.main.g, Theme.main.b, root.layoutSelected ? 0.70 : 0.34)
                radius: Theme.cornerRadius - 2
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 4; spacing: 5
                    Text { text: "⠿"; color: Theme.glyph; font.pixelSize: 16; font.family: Theme.family }
                    Text {
                        text: root.widgetTitle; color: Theme.text; font.bold: true; font.family: Theme.family
                        font.pixelSize: 12; textFormat: Text.PlainText; Layout.fillWidth: true; elide: Text.ElideRight
                    }
                    RowLayout {
                        id: headerControls
                        objectName: "widgetHeaderControls"
                        spacing: 2
                        OverlayControl {
                            objectName: "widgetMoveButton"; text: "↔"; compact: true; implicitWidth: 24
                            enabled: root.editing
                            hint: "Move widget · drag the dotted title handle, or arrange with D-pad"
                            onClicked: root.arrangeRequested("move")
                        }
                        OverlayControl {
                            objectName: "widgetSizeButton"; text: "↘"; compact: true; implicitWidth: 24
                            enabled: root.editing
                            hint: "Resize widget · drag the lower-right grip, or arrange with D-pad"
                            onClicked: root.arrangeRequested("resize")
                        }
                        OverlayControl {
                            objectName: "widgetPinButton"; text: root.pinned ? "Pinned" : "Pin"; compact: true
                            visible: root.pinningAllowed; implicitWidth: 44
                            hint: "Keep this widget visible after closing the overlay"
                            onClicked: root.pinned = !root.pinned
                        }
                        OverlayControl {
                            objectName: "widgetCloseButton"; text: "×"; compact: true; implicitWidth: 24
                            hint: "Hide this widget"
                            onClicked: { root.commitGeometry(); root.shown = false }
                        }
                    }
                }
                MouseArea {
                    id: dragHandle
                    objectName: "widgetDragHandle"
                    anchors.fill: parent; anchors.rightMargin: headerControls.width + 12
                    cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                    property bool systemMove: false
                    property var pressPoint
                    property real startX: 0
                    property real startY: 0
                    onPressed: function(mouse) {
                        pressPoint = root.pointerAt(dragHandle, mouse.x, mouse.y); startX = root.x; startY = root.y
                        systemMove = root.useNativeSystemGestures && !root.unmanagedPinned && root.startSystemMove()
                    }
                    onPositionChanged: function(mouse) {
                        if (!pressed || systemMove || !pressPoint) return
                        var point = root.pointerAt(dragHandle, mouse.x, mouse.y)
                        root.x = startX + point.x - pressPoint.x; root.y = startY + point.y - pressPoint.y
                        root.constrain(false)
                    }
                    onReleased: root.commitGeometry()
                    onCanceled: root.commitGeometry()
                }
            }
            Item { id: contentArea; Layout.fillWidth: true; Layout.fillHeight: true; clip: true }
            Text {
                objectName: "widgetArrangeHint"
                Layout.fillWidth: true
                Layout.leftMargin: 8; Layout.rightMargin: 16
                visible: root.editing && root.layoutSelected && root.layoutAction.length > 0
                text: root.layoutAction === "resize" ? "RESIZE · D-pad / A switch / B done" : "MOVE · D-pad / A switch / B done"
                color: Theme.muted; font.family: Theme.family; font.pixelSize: 10
                textFormat: Text.PlainText; elide: Text.ElideRight
                Layout.preferredHeight: 22
                verticalAlignment: Text.AlignVCenter
            }
        }
        MouseArea {
            id: resizeHandle
            anchors.right: parent.right; anchors.bottom: parent.bottom; width: 22; height: 22
            objectName: "widgetResizeHandle"; cursorShape: Qt.SizeFDiagCursor
            property bool systemResize: false
            property var pressPoint
            property real startWidth: 0
            property real startHeight: 0
            onPressed: function(mouse) {
                pressPoint = root.pointerAt(resizeHandle, mouse.x, mouse.y); startWidth = root.width; startHeight = root.height
                systemResize = root.useNativeSystemGestures && !root.unmanagedPinned && root.startSystemResize(Qt.BottomEdge | Qt.RightEdge)
            }
            onPositionChanged: function(mouse) {
                if (!pressed || systemResize || !pressPoint) return
                var point = root.pointerAt(resizeHandle, mouse.x, mouse.y)
                root.width = startWidth + point.x - pressPoint.x; root.height = startHeight + point.y - pressPoint.y
                root.constrain(false)
            }
            onReleased: root.commitResizeGeometry()
            onCanceled: root.commitResizeGeometry()
        }
        Text {
            anchors.right: parent.right; anchors.rightMargin: 9; anchors.bottom: parent.bottom; anchors.bottomMargin: 5
            text: "◢"; color: Theme.glyph; font.pixelSize: 11
        }
    }
}
