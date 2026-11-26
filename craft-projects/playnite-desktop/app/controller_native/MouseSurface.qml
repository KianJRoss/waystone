import QtQuick
import QtQuick.Controls
import ".."

Item {
    id: root
    objectName: "mouseSurface"
    implicitWidth: 170
    implicitHeight: 300
    clip: true

    property var service: null
    property string hoveredCode: ""
    property string selectedSource: ""
    property string selectedDestination: ""
    property var routes: ({})

    signal controlClicked(string code, string kind)

    function getColorFor(code) {
        if (code === root.selectedSource) return "#e49b2f" // Semantic source orange
        if (code === root.selectedDestination) return "#2fbf71" // Semantic target green
        if (root.routes && root.routes[code]) return Theme.main
        if (code === root.hoveredCode) return Theme.hover
        return Theme.panel
    }

    readonly property var mouseParts: [
        { "name": "Left", "label": "L", "x": 25, "y": 24, "w": 57, "h": 82, "r": 6 },
        { "name": "Right", "label": "R", "x": 88, "y": 24, "w": 57, "h": 82, "r": 6 },
        { "name": "Wheel Up", "label": "^", "x": 73, "y": 28, "w": 24, "h": 22, "r": 4 },
        { "name": "Middle", "label": "M", "x": 72, "y": 54, "w": 26, "h": 26, "r": 4 },
        { "name": "Wheel Down", "label": "v", "x": 73, "y": 83, "w": 24, "h": 22, "r": 4 },
        { "name": "X2", "label": "X2", "x": 10, "y": 120, "w": 22, "h": 32, "r": 4 },
        { "name": "X1", "label": "X1", "x": 10, "y": 156, "w": 22, "h": 32, "r": 4 },
        { "name": "Movement", "label": "AIM\nMOVE", "x": 38, "y": 146, "w": 94, "h": 100, "r": 28 }
    ]

    Rectangle {
        anchors.fill: parent
        color: Theme.background
    }

    Item {
        id: container
        objectName: "mouseContainer"
        width: 170
        height: 300
        anchors.centerIn: parent
        scale: Math.max(0.1, Math.min(1.0, Math.min((root.width - 16) / 170, (root.height - 16) / 300)))
        transformOrigin: Item.Center

        // Outer mouse silhouette
        Rectangle {
            x: 15; y: 15; width: 140; height: 270
            radius: 52
            color: Theme.panel
            border.color: Theme.line
            border.width: 2
        }

        Repeater {
            model: root.mouseParts
            delegate: Rectangle {
                id: partRect
                x: modelData.x
                y: modelData.y
                width: modelData.w
                height: modelData.h
                radius: modelData.r
                property string code: "Mouse:" + modelData.name
                color: root.getColorFor(code)
                border.width: root.hoveredCode === code ? 2 : 1
                border.color: root.hoveredCode === code ? Theme.text : Theme.line

                Text {
                    anchors.centerIn: parent
                    text: modelData.label
                    color: modelData.name === "Movement" ? Theme.glyph : Theme.text
                    font.family: Theme.family
                    font.pixelSize: modelData.name === "Movement" ? 10 : 9
                    horizontalAlignment: Text.AlignHCenter
                }

                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    onEntered: root.hoveredCode = partRect.code
                    onExited: if (root.hoveredCode === partRect.code) root.hoveredCode = ""
                    onClicked: root.controlClicked(partRect.code, "SelectControl")
                }
            }
        }
    }
}
