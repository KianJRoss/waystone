import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: editorRoot
    objectName: "controllerMappingEditor"
    implicitWidth: 640
    implicitHeight: 460

    signal edited()

    property var bindings: []
    property int selectedIndex: -1
    property string selectedSource: ""
    property string selectedDestination: ""
    property string errorMessage: ""
    property string statusMessage: ""
    readonly property int count: bindings.length

    property var _document: ({})
    property bool _loaded: false
    property string _routesFormat: "object"

    readonly property var standardSources: [
        "Controller:A",
        "Controller:B",
        "Controller:X",
        "Controller:Y",
        "Controller:LB",
        "Controller:RB",
        "Controller:LT",
        "Controller:RT",
        "Controller:D-pad Up",
        "Controller:D-pad Down",
        "Controller:D-pad Left",
        "Controller:D-pad Right",
        "Controller:Left Stick",
        "Controller:Right Stick",
        "Controller:Menu",
        "Controller:View",
        "Controller:Home",
        "Keyboard:Space",
        "Keyboard:Return",
        "Keyboard:Escape",
        "Keyboard:Tab",
        "Keyboard:Backspace",
        "Keyboard:W",
        "Keyboard:A",
        "Keyboard:S",
        "Keyboard:D",
        "Mouse:Left",
        "Mouse:Right",
        "Mouse:Middle"
    ]

    readonly property var standardDestinations: [
        "Controller:A",
        "Controller:B",
        "Controller:X",
        "Controller:Y",
        "Controller:LB",
        "Controller:RB",
        "Controller:LT",
        "Controller:RT",
        "Controller:D-pad Up",
        "Controller:D-pad Down",
        "Controller:D-pad Left",
        "Controller:D-pad Right",
        "Controller:Left Stick",
        "Controller:Right Stick",
        "Controller:Menu",
        "Controller:View",
        "Controller:Home",
        "Desktop:Key:Space",
        "Desktop:Key:Return",
        "Desktop:Key:Escape",
        "Desktop:Key:Tab",
        "Desktop:Key:Backspace",
        "Desktop:Mouse:Left",
        "Desktop:Mouse:Right",
        "Desktop:Mouse:Middle",
        "Desktop:Wheel:Up",
        "Desktop:Wheel:Down"
    ]

    function loadProfile(rawJson) {
        if (typeof rawJson !== "string" || !rawJson.trim()) {
            errorMessage = "Profile JSON must be a non-empty string."
            return false
        }
        let parsed
        try {
            parsed = JSON.parse(rawJson)
        } catch (e) {
            errorMessage = "Invalid JSON: " + e.message
            return false
        }
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            errorMessage = "Profile must be a JSON object."
            return false
        }

        _document = parsed
        _loaded = true
        errorMessage = ""

        let nextBindings = []
        if (Array.isArray(_document.routes)) {
            _routesFormat = "array"
            for (let i = 0; i < _document.routes.length; ++i) {
                let r = _document.routes[i]
                if (r && typeof r === "object") {
                    let s = r.source !== undefined ? String(r.source) : ""
                    let d = r.destination !== undefined ? String(r.destination) : (r.output !== undefined ? String(r.output) : "")
                    if (s) nextBindings.push({ source: s, destination: d })
                }
            }
        } else if (typeof _document.routes === "object" && _document.routes !== null) {
            _routesFormat = "object"
            let keys = Object.keys(_document.routes)
            for (let j = 0; j < keys.length; ++j) {
                let k = keys[j]
                nextBindings.push({ source: k, destination: String(_document.routes[k]) })
            }
        } else {
            _routesFormat = "object"
        }

        bindings = nextBindings
        selectedIndex = -1
        selectedSource = ""
        selectedDestination = ""
        sourceField.text = ""
        destField.text = ""
        statusMessage = bindings.length + " bindings loaded."
        return true
    }

    function serializedProfile() {
        if (!_loaded && Object.keys(_document).length === 0) {
            return ""
        }
        if (_routesFormat === "array") {
            let arr = []
            for (let i = 0; i < bindings.length; ++i) {
                arr.push({ source: bindings[i].source, destination: bindings[i].destination })
            }
            _document.routes = arr
        } else {
            let obj = {}
            for (let j = 0; j < bindings.length; ++j) {
                obj[bindings[j].source] = bindings[j].destination
            }
            _document.routes = obj
        }
        return JSON.stringify(_document, null, 2)
    }

    function selectBinding(idx) {
        if (idx >= 0 && idx < bindings.length) {
            selectedIndex = idx
            selectedSource = bindings[idx].source
            selectedDestination = bindings[idx].destination
            sourceField.text = selectedSource
            destField.text = selectedDestination
        } else {
            selectedIndex = -1
            selectedSource = ""
            selectedDestination = ""
            sourceField.text = ""
            destField.text = ""
        }
    }

    function addRoute(src, dst) {
        if (typeof src !== "string" || typeof dst !== "string") {
            errorMessage = "Source and destination must be strings."
            return false
        }
        let s = src.trim()
        let d = dst.trim()
        if (!s || !d) {
            errorMessage = "Source and destination cannot be empty."
            return false
        }

        let existingIndex = -1
        for (let i = 0; i < bindings.length; ++i) {
            if (bindings[i].source === s) {
                existingIndex = i
                break
            }
        }

        let next = bindings.slice()
        if (existingIndex >= 0) {
            next[existingIndex] = { source: s, destination: d }
            selectedIndex = existingIndex
            statusMessage = "Updated route: " + s + " -> " + d
        } else {
            next.push({ source: s, destination: d })
            selectedIndex = next.length - 1
            statusMessage = "Added route: " + s + " -> " + d
        }
        bindings = next
        selectedSource = s
        selectedDestination = d
        sourceField.text = s
        destField.text = d
        errorMessage = ""
        edited()
        return true
    }

    function replaceRoute(src, dst) {
        if (typeof src !== "string" || typeof dst !== "string") {
            errorMessage = "Source and destination must be strings."
            return false
        }
        let s = src.trim()
        let d = dst.trim()
        if (!s || !d) {
            errorMessage = "Source and destination cannot be empty."
            return false
        }

        if (selectedIndex >= 0 && selectedIndex < bindings.length) {
            let next = bindings.slice()
            next[selectedIndex] = { source: s, destination: d }
            bindings = next
            selectedSource = s
            selectedDestination = d
            sourceField.text = s
            destField.text = d
            errorMessage = ""
            statusMessage = "Replaced route at #" + (selectedIndex + 1) + ": " + s + " -> " + d
            edited()
            return true
        }

        for (let i = 0; i < bindings.length; ++i) {
            if (bindings[i].source === s) {
                let next = bindings.slice()
                next[i] = { source: s, destination: d }
                bindings = next
                selectedIndex = i
                selectedSource = s
                selectedDestination = d
                sourceField.text = s
                destField.text = d
                errorMessage = ""
                statusMessage = "Replaced route: " + s + " -> " + d
                edited()
                return true
            }
        }

        errorMessage = "No matching route selected to replace."
        return false
    }

    function removeRoute(src) {
        if (typeof src !== "string") return false
        let s = src.trim()
        let targetIndex = -1
        for (let i = 0; i < bindings.length; ++i) {
            if (bindings[i].source === s) {
                targetIndex = i
                break
            }
        }
        if (targetIndex < 0) {
            errorMessage = "Route not found: " + s
            return false
        }

        let next = bindings.slice()
        next.splice(targetIndex, 1)
        bindings = next
        if (selectedIndex === targetIndex) {
            selectBinding(bindings.length ? Math.min(targetIndex, bindings.length - 1) : -1)
        } else if (selectedIndex > targetIndex) {
            selectedIndex -= 1
        }
        errorMessage = ""
        statusMessage = "Removed route: " + s
        edited()
        return true
    }

    function removeSelectedRoute() {
        if (selectedIndex >= 0 && selectedIndex < bindings.length) {
            return removeRoute(bindings[selectedIndex].source)
        }
        errorMessage = "No route selected to remove."
        return false
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "Route Mappings"
                color: Theme.text
                font.bold: true
                font.pixelSize: 14
                font.family: Theme.family
            }
            Item { Layout.fillWidth: true }
            Text {
                objectName: "mappingCountLabel"
                text: editorRoot.bindings.length + " routes"
                color: Theme.muted
                font.pixelSize: 12
                font.family: Theme.family
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.panel
            radius: Theme.cornerRadius
            border.color: Theme.line

            ListView {
                id: bindingsList
                objectName: "mappingBindingsList"
                anchors.fill: parent
                anchors.margins: 6
                clip: true
                model: editorRoot.bindings

                ScrollBar.vertical: ScrollBar {
                    implicitWidth: Theme.scrollbarWidth
                    padding: 2
                    contentItem: Rectangle {
                        radius: 2
                        color: parent.pressed ? Theme.text : parent.hovered ? Theme.glyph : Theme.main
                    }
                    background: Rectangle { color: Theme.background }
                }

                delegate: Rectangle {
                    id: rowItem
                    objectName: "mappingRow_" + index
                    width: bindingsList.width - (bindingsList.ScrollBar.vertical.visible ? Theme.scrollbarWidth + 4 : 0)
                    height: 38
                    radius: Theme.cornerRadius
                    color: editorRoot.selectedIndex === index ? Theme.main : (rowMouse.containsMouse ? Theme.background : "transparent")
                    border.width: editorRoot.selectedIndex === index ? 1 : 0
                    border.color: Theme.text

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 10
                        anchors.rightMargin: 10
                        spacing: 8

                        Rectangle {
                            implicitWidth: srcText.implicitWidth + 12
                            implicitHeight: 24
                            radius: 4
                            color: Theme.background
                            border.color: Theme.line
                            Text {
                                id: srcText
                                anchors.centerIn: parent
                                text: modelData.source
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 12
                            }
                        }

                        Text {
                            text: "→"
                            color: Theme.glyph
                            font.pixelSize: 14
                        }

                        Rectangle {
                            implicitWidth: dstText.implicitWidth + 12
                            implicitHeight: 24
                            radius: 4
                            color: Theme.background
                            border.color: Theme.line
                            Text {
                                id: dstText
                                anchors.centerIn: parent
                                text: modelData.destination
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 12
                            }
                        }

                        Item { Layout.fillWidth: true }
                    }

                    MouseArea {
                        id: rowMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: editorRoot.selectBinding(index)
                    }
                }

                Text {
                    anchors.centerIn: parent
                    visible: editorRoot.bindings.length === 0
                    text: editorRoot._loaded ? "No route mappings configured." : "No profile loaded."
                    color: Theme.muted
                    font.family: Theme.family
                    font.pixelSize: 13
                }
            }
        }

        // Edit form
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Text {
                    text: "Source:"
                    color: Theme.text
                    font.family: Theme.family
                    font.pixelSize: 13
                    Layout.preferredWidth: 80
                }

                TextField {
                    id: sourceField
                    objectName: "mappingSourceInput"
                    Layout.fillWidth: true
                    placeholderText: "Source button/key (e.g. Controller:A)"
                    text: editorRoot.selectedSource
                    onTextEdited: editorRoot.selectedSource = text
                    color: Theme.text
                    placeholderTextColor: Theme.muted
                    font.family: Theme.family
                    font.pixelSize: 13
                    background: Rectangle {
                        color: Theme.background
                        radius: Theme.cornerRadius
                        border.color: sourceField.activeFocus ? Theme.text : Theme.line
                    }
                }

                CompactCombo {
                    id: sourcePresetCombo
                    objectName: "mappingSourcePreset"
                    Layout.preferredWidth: 160
                    model: ["Source preset…"].concat(editorRoot.standardSources)
                    onActivated: function(index) {
                        if (index > 0) {
                            sourceField.text = currentText
                            editorRoot.selectedSource = currentText
                            currentIndex = 0
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Text {
                    text: "Destination:"
                    color: Theme.text
                    font.family: Theme.family
                    font.pixelSize: 13
                    Layout.preferredWidth: 80
                }

                TextField {
                    id: destField
                    objectName: "mappingDestInput"
                    Layout.fillWidth: true
                    placeholderText: "Destination (e.g. Controller:B, Desktop:Key:Space)"
                    text: editorRoot.selectedDestination
                    onTextEdited: editorRoot.selectedDestination = text
                    color: Theme.text
                    placeholderTextColor: Theme.muted
                    font.family: Theme.family
                    font.pixelSize: 13
                    background: Rectangle {
                        color: Theme.background
                        radius: Theme.cornerRadius
                        border.color: destField.activeFocus ? Theme.text : Theme.line
                    }
                }

                CompactCombo {
                    id: destPresetCombo
                    objectName: "mappingDestPreset"
                    Layout.preferredWidth: 160
                    model: ["Destination preset…"].concat(editorRoot.standardDestinations)
                    onActivated: function(index) {
                        if (index > 0) {
                            destField.text = currentText
                            editorRoot.selectedDestination = currentText
                            currentIndex = 0
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                CompactButton {
                    objectName: "addRouteButton"
                    text: "Add route"
                    implicitWidth: 100
                    enabled: sourceField.text.trim().length > 0 && destField.text.trim().length > 0
                    onClicked: editorRoot.addRoute(sourceField.text, destField.text)
                }

                CompactButton {
                    objectName: "replaceRouteButton"
                    text: "Replace route"
                    implicitWidth: 110
                    enabled: (editorRoot.selectedIndex >= 0 || sourceField.text.trim().length > 0) && destField.text.trim().length > 0
                    onClicked: editorRoot.replaceRoute(sourceField.text, destField.text)
                }

                CompactButton {
                    objectName: "removeRouteButton"
                    text: "Remove route"
                    implicitWidth: 110
                    enabled: editorRoot.selectedIndex >= 0 || sourceField.text.trim().length > 0
                    onClicked: {
                        if (editorRoot.selectedIndex >= 0) editorRoot.removeSelectedRoute()
                        else editorRoot.removeRoute(sourceField.text)
                    }
                }

                CompactButton {
                    objectName: "clearSelectionButton"
                    text: "Clear selection"
                    implicitWidth: 110
                    enabled: editorRoot.selectedIndex >= 0 || sourceField.text.length > 0 || destField.text.length > 0
                    onClicked: editorRoot.selectBinding(-1)
                }

                Item { Layout.fillWidth: true }
            }

            Text {
                objectName: "mappingErrorText"
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: "#f2a8a8"
                text: editorRoot.errorMessage
                visible: text.length > 0
                font.family: Theme.family
                font.pixelSize: 12
            }

            Text {
                objectName: "mappingStatusText"
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: Theme.muted
                text: editorRoot.statusMessage
                visible: text.length > 0 && !editorRoot.errorMessage
                font.family: Theme.family
                font.pixelSize: 12
            }
        }
    }
}
