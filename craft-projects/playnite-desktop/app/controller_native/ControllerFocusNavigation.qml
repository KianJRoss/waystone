import QtQuick
import QtQuick.Controls
import QtQuick.Templates as T
import ".."

Item {
    id: root

    // Scope Item: Interactive controls are strictly bounded to this Item or its descendants
    property Item scope: null

    // Whether navigation processing is active
    property bool active: false
    signal backRequested()

    // Currently focused Item within the scope
    property Item currentFocusedItem: null

    // Modal trap Item: When set and visible, navigation is strictly trapped inside this container
    property Item modalTrap: null

    // Action telemetry
    property string lastHandledAction: ""

    // Section controller: e.g. TabBar or section switcher callback
    property var sectionSwitcher: null // e.g. function(direction) -> -1 (previous) or +1 (next)

    // Visual focus ring indicator overlay
    Rectangle {
        id: focusIndicator
        z: 10000
        visible: root.active && root.currentFocusedItem !== null && root.currentFocusedItem.visible && root.currentFocusedItem.enabled
        color: "transparent"
        border.color: Theme.text
        border.width: 2
        radius: Theme.cornerRadius || 4

        // Position overlay over currentFocusedItem mapped to root coordinates
        function updatePosition() {
            if (!root.currentFocusedItem || !root.currentFocusedItem.visible) {
                return
            }
            var target = root.currentFocusedItem
            var pt = target.mapToItem(root, 0, 0)
            if (pt) {
                focusIndicator.x = pt.x - 3
                focusIndicator.y = pt.y - 3
                focusIndicator.width = target.width + 6
                focusIndicator.height = target.height + 6
            }
        }

        Connections {
            target: root.currentFocusedItem
            function onXChanged() { focusIndicator.updatePosition() }
            function onYChanged() { focusIndicator.updatePosition() }
            function onWidthChanged() { focusIndicator.updatePosition() }
            function onHeightChanged() { focusIndicator.updatePosition() }
            function onVisibleChanged() { focusIndicator.updatePosition() }
        }
    }

    Timer {
        interval: 50; repeat: true; running: root.active && root.currentFocusedItem !== null
        onTriggered: focusIndicator.updatePosition()
    }
    onActiveChanged: if (!active) currentFocusedItem = null

    onCurrentFocusedItemChanged: {
        focusIndicator.updatePosition()
    }

    // Public API: dispatch high-level action
    function action(actionName) {
        if (!root.active || !root.scope || !root.scope.visible || !root.scope.enabled) return false
        root.lastHandledAction = actionName

        var act = String(actionName).toLowerCase()

        if (act === "previous_section" || act === "next_section") {
            return handleSectionSwitch(act === "previous_section" ? -1 : 1)
        }

        if (act === "accept") {
            return handleAccept()
        }

        if (act === "back") {
            return handleBack()
        }

        if (act === "up" || act === "down" || act === "left" || act === "right") {
            return handleDirection(act)
        }

        return false
    }

    // Helper: Collect all visible, enabled, interactive elements inside container
    function collectInteractive(container, list) {
        if (!container || !container.visible || container.opacity <= 0) return
        if (container.enabled === false) return

        // QML inheritance checks, not property guessing: every Text has activeFocus
        // and every AbstractButton has checked/toggled, even a plain push button.
        var atomic = (container instanceof T.AbstractButton || container instanceof Slider
                      || container instanceof ComboBox || container instanceof SpinBox
                      || container instanceof TextInput || container instanceof TextEdit)
        if (container !== root && container !== focusIndicator && atomic) {
            if (container.width > 0 && container.height > 0
                && !((container instanceof TextInput || container instanceof TextEdit) && container.readOnly))
                list.push(container)
            return // Never include style/content subitems as separate controls.
        }

        if (container.children) {
            for (var i = 0; i < container.children.length; i++) {
                var child = container.children[i]
                if (child && child !== focusIndicator) {
                    collectInteractive(child, list)
                }
            }
        }
    }

    function getEligibleControls() {
        var activeContainer = (root.modalTrap && root.modalTrap.visible) ? root.modalTrap : root.scope
        if (!activeContainer) return []
        var items = []
        collectInteractive(activeContainer, items)
        return items
    }

    function focusFirst() {
        if (!root.active) return false
        var eligible = getEligibleControls()
        if (eligible.length > 0) {
            setFocusedItem(eligible[0])
            return true
        }
        return false
    }

    function setFocusedItem(item) {
        if (!root.active) return false
        if (item !== null && getEligibleControls().indexOf(item) < 0) return false
        root.currentFocusedItem = item
        if (item) {
            item.forceActiveFocus()
            focusIndicator.updatePosition()
        }
    }

    function handleDirection(dir) {
        var current = root.currentFocusedItem
        var eligible = getEligibleControls()
        if (eligible.length === 0) return false

        // If nothing is focused, or current is no longer eligible, focus first
        if (!current || eligible.indexOf(current) === -1) {
            setFocusedItem(eligible[0])
            return true
        }

        // Special handling for Slider: left/right adjusts value boundedly
        var isSlider = (current instanceof Slider || current instanceof SpinBox)
        if (isSlider && (dir === "left" || dir === "right")) {
            var step = (typeof current.stepSize === "number" && current.stepSize > 0)
                ? current.stepSize
                : (current.to - current.from) * 0.05
            var delta = (dir === "right" ? step : -step)
            var newVal = Math.max(Math.min(current.from, current.to), Math.min(Math.max(current.from, current.to), current.value + delta))
            current.value = newVal
            if (current instanceof Slider && typeof current.moved === "function") current.moved()
            if (current instanceof SpinBox && typeof current.valueModified === "function") current.valueModified()
            return true
        }

        if (current instanceof ComboBox && (dir === "left" || dir === "right" || current.popup.visible)) {
            var before = current.currentIndex
            var next = before + ((dir === "left" || dir === "up") ? -1 : 1)
            if (next >= 0 && next < current.count) {
                current.currentIndex = next
                current.activated(next)
            }
            return true
        }

        // Spatial 2D search for the best next neighbor
        var activeContainer = (root.modalTrap && root.modalTrap.visible) ? root.modalTrap : root.scope
        var currentCenter = getItemCenter(current, activeContainer)

        var bestCandidate = null
        var bestScore = 999999999

        for (var i = 0; i < eligible.length; i++) {
            var candidate = eligible[i]
            if (candidate === current) continue

            var candCenter = getItemCenter(candidate, activeContainer)
            var dx = candCenter.x - currentCenter.x
            var dy = candCenter.y - currentCenter.y

            var isDirectionMatch = false
            if (dir === "up" && dy < -4) isDirectionMatch = true
            else if (dir === "down" && dy > 4) isDirectionMatch = true
            else if (dir === "left" && dx < -4) isDirectionMatch = true
            else if (dir === "right" && dx > 4) isDirectionMatch = true

            if (!isDirectionMatch) continue

            // Weighted Manhattan score prioritizing primary axis
            var primaryDist = (dir === "up" || dir === "down") ? Math.abs(dy) : Math.abs(dx)
            var secondaryDist = (dir === "up" || dir === "down") ? Math.abs(dx) : Math.abs(dy)
            var score = primaryDist + (secondaryDist * 2.2)

            if (score < bestScore) {
                bestScore = score
                bestCandidate = candidate
            }
        }

        if (bestCandidate) {
            setFocusedItem(bestCandidate)
            return true
        } else {
            // Sequential fallback if no geometric neighbor in that exact direction
            var curIdx = eligible.indexOf(current)
            if (dir === "down" || dir === "right") {
                var nextIdx = (curIdx + 1) % eligible.length
                setFocusedItem(eligible[nextIdx])
                return true
            } else if (dir === "up" || dir === "left") {
                var prevIdx = (curIdx - 1 + eligible.length) % eligible.length
                setFocusedItem(eligible[prevIdx])
                return true
            }
        }

        return false
    }

    function getItemCenter(item, rootContainer) {
        var pt = item.mapToItem(rootContainer, item.width / 2, item.height / 2)
        return pt ? pt : { x: 0, y: 0 }
    }

    function handleAccept() {
        var current = root.currentFocusedItem
        if (getEligibleControls().indexOf(current) < 0) return false
        // click() provides normal pressed/released/clicked AND toggled semantics;
        // toggle() alone does not activate plain buttons or checkbox handlers.
        if (current instanceof T.AbstractButton) {
            current.click()
            return true
        }
        if (current instanceof TextInput || current instanceof TextEdit) {
            current.forceActiveFocus() // Never synthesize Return or submit a form.
            return true
        }
        if (current instanceof ComboBox) {
            if (current.popup.visible) current.popup.close()
            else current.popup.open()
            return true
        }
        return false
    }

    function handleBack() {
        var current = root.currentFocusedItem
        if (getEligibleControls().indexOf(current) >= 0
            && current instanceof ComboBox && current.popup.visible) {
            current.popup.close()
            return true
        }
        // Cancellation belongs to the dialog owner: don't break a visible binding
        // or imply a profile was deleted/saved by hiding an arbitrary Item.
        root.backRequested()
        return true
    }

    function handleSectionSwitch(delta) {
        if (root.modalTrap && root.modalTrap.visible) return false
        if (typeof root.sectionSwitcher === "function") {
            root.sectionSwitcher(delta)
            // After switching section, focus the first interactive item in new tab
            focusFirst()
            return true
        }
        return false
    }
}
