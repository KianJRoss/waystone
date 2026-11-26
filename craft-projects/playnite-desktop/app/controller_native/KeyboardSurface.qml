import QtQuick
import QtQuick.Controls
import ".."

Item {
    id: root
    objectName: "keyboardSurface"
    implicitWidth: 940
    implicitHeight: 230
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

    // Keyboard geometry layout builder
    readonly property var rows: [
        [
            { "name": "Esc", "label": "Esc", "u": 1.0 },
            { "name": null, "label": "", "u": 0.45 },
            { "name": "F1", "label": "F1", "u": 1.0 }, { "name": "F2", "label": "F2", "u": 1.0 },
            { "name": "F3", "label": "F3", "u": 1.0 }, { "name": "F4", "label": "F4", "u": 1.0 },
            { "name": null, "label": "", "u": 0.3 },
            { "name": "F5", "label": "F5", "u": 1.0 }, { "name": "F6", "label": "F6", "u": 1.0 },
            { "name": "F7", "label": "F7", "u": 1.0 }, { "name": "F8", "label": "F8", "u": 1.0 },
            { "name": null, "label": "", "u": 0.3 },
            { "name": "F9", "label": "F9", "u": 1.0 }, { "name": "F10", "label": "F10", "u": 1.0 },
            { "name": "F11", "label": "F11", "u": 1.0 }, { "name": "F12", "label": "F12", "u": 1.0 },
            { "name": null, "label": "", "u": 0.45 },
            { "name": "Print Screen", "label": "PrtSc", "u": 1.0 },
            { "name": "Scroll Lock", "label": "ScrLk", "u": 1.0 },
            { "name": "Pause", "label": "Pause", "u": 1.0 }
        ],
        [
            { "name": "Backtick", "label": "`", "u": 1.0 },
            { "name": "1", "label": "1", "u": 1.0 }, { "name": "2", "label": "2", "u": 1.0 },
            { "name": "3", "label": "3", "u": 1.0 }, { "name": "4", "label": "4", "u": 1.0 },
            { "name": "5", "label": "5", "u": 1.0 }, { "name": "6", "label": "6", "u": 1.0 },
            { "name": "7", "label": "7", "u": 1.0 }, { "name": "8", "label": "8", "u": 1.0 },
            { "name": "9", "label": "9", "u": 1.0 }, { "name": "0", "label": "0", "u": 1.0 },
            { "name": "Minus", "label": "-", "u": 1.0 }, { "name": "Equals", "label": "=", "u": 1.0 },
            { "name": "Backspace", "label": "Backspace", "u": 2.0 },
            { "name": null, "label": "", "u": 0.45 },
            { "name": "Insert", "label": "Ins", "u": 1.0 }, { "name": "Home", "label": "Home", "u": 1.0 },
            { "name": "Page Up", "label": "PgUp", "u": 1.0 },
            { "name": null, "label": "", "u": 0.35 },
            { "name": "Num Lock", "label": "Num", "u": 1.0 },
            { "name": "Numpad Divide", "label": "/", "u": 1.0 },
            { "name": "Numpad Multiply", "label": "*", "u": 1.0 },
            { "name": "Numpad Minus", "label": "-", "u": 1.0 }
        ],
        [
            { "name": "Tab", "label": "Tab", "u": 1.5 },
            { "name": "Q", "label": "Q", "u": 1.0 }, { "name": "W", "label": "W", "u": 1.0 },
            { "name": "E", "label": "E", "u": 1.0 }, { "name": "R", "label": "R", "u": 1.0 },
            { "name": "T", "label": "T", "u": 1.0 }, { "name": "Y", "label": "Y", "u": 1.0 },
            { "name": "U", "label": "U", "u": 1.0 }, { "name": "I", "label": "I", "u": 1.0 },
            { "name": "O", "label": "O", "u": 1.0 }, { "name": "P", "label": "P", "u": 1.0 },
            { "name": "Left Bracket", "label": "[", "u": 1.0 }, { "name": "Right Bracket", "label": "]", "u": 1.0 },
            { "name": "Backslash", "label": "\\", "u": 1.5 },
            { "name": null, "label": "", "u": 0.45 },
            { "name": "Delete", "label": "Del", "u": 1.0 }, { "name": "End", "label": "End", "u": 1.0 },
            { "name": "Page Down", "label": "PgDn", "u": 1.0 },
            { "name": null, "label": "", "u": 0.35 },
            { "name": "Numpad7", "label": "7", "u": 1.0 }, { "name": "Numpad8", "label": "8", "u": 1.0 },
            { "name": "Numpad9", "label": "9", "u": 1.0 }, { "name": "Numpad Plus", "label": "+", "u": 1.0 }
        ],
        [
            { "name": "Caps Lock", "label": "Caps", "u": 1.75 },
            { "name": "A", "label": "A", "u": 1.0 }, { "name": "S", "label": "S", "u": 1.0 },
            { "name": "D", "label": "D", "u": 1.0 }, { "name": "F", "label": "F", "u": 1.0 },
            { "name": "G", "label": "G", "u": 1.0 }, { "name": "H", "label": "H", "u": 1.0 },
            { "name": "J", "label": "J", "u": 1.0 }, { "name": "K", "label": "K", "u": 1.0 },
            { "name": "L", "label": "L", "u": 1.0 }, { "name": "Semicolon", "label": ";", "u": 1.0 },
            { "name": "Apostrophe", "label": "'", "u": 1.0 }, { "name": "Enter", "label": "Enter", "u": 2.25 },
            { "name": null, "label": "", "u": 3.8 },
            { "name": "Numpad4", "label": "4", "u": 1.0 }, { "name": "Numpad5", "label": "5", "u": 1.0 },
            { "name": "Numpad6", "label": "6", "u": 1.0 }
        ],
        [
            { "name": "Left Shift", "label": "Shift", "u": 2.25 },
            { "name": "Z", "label": "Z", "u": 1.0 }, { "name": "X", "label": "X", "u": 1.0 },
            { "name": "C", "label": "C", "u": 1.0 }, { "name": "V", "label": "V", "u": 1.0 },
            { "name": "B", "label": "B", "u": 1.0 }, { "name": "N", "label": "N", "u": 1.0 },
            { "name": "M", "label": "M", "u": 1.0 }, { "name": "Comma", "label": ",", "u": 1.0 },
            { "name": "Period", "label": ".", "u": 1.0 }, { "name": "Slash", "label": "/", "u": 1.0 },
            { "name": "Right Shift", "label": "Shift", "u": 2.75 },
            { "name": null, "label": "", "u": 1.45 },
            { "name": "Up", "label": "▲", "u": 1.0 },
            { "name": null, "label": "", "u": 1.35 },
            { "name": "Numpad1", "label": "1", "u": 1.0 }, { "name": "Numpad2", "label": "2", "u": 1.0 },
            { "name": "Numpad3", "label": "3", "u": 1.0 }, { "name": "Numpad Enter", "label": "Enter", "u": 1.0 }
        ],
        [
            { "name": "Left Ctrl", "label": "Ctrl", "u": 1.3 },
            { "name": "Left Win", "label": "Win", "u": 1.2 },
            { "name": "Left Alt", "label": "Alt", "u": 1.2 },
            { "name": "Space", "label": "Space", "u": 6.25 },
            { "name": "Right Alt", "label": "Alt", "u": 1.2 },
            { "name": "Right Win", "label": "Win", "u": 1.2 },
            { "name": "Menu Key", "label": "Menu", "u": 1.2 },
            { "name": "Right Ctrl", "label": "Ctrl", "u": 1.3 },
            { "name": null, "label": "", "u": 0.45 },
            { "name": "Left", "label": "◀", "u": 1.0 },
            { "name": "Down", "label": "▼", "u": 1.0 },
            { "name": "Right", "label": "▶", "u": 1.0 },
            { "name": null, "label": "", "u": 0.35 },
            { "name": "Numpad0", "label": "0", "u": 2.0 },
            { "name": "Numpad Decimal", "label": ".", "u": 1.0 }
        ]
    ]

    function computeKeyList() {
        var list = []
        var gap = 2, unit = 38, kh = 30
        var y = 28
        for (var r = 0; r < rows.length; r++) {
            var row = rows[r]
            var x = 8
            for (var i = 0; i < row.length; i++) {
                var k = row[i]
                var w = unit * k.u
                if (k.name !== null) {
                    list.push({
                        "code": "Keyboard:" + k.name,
                        "label": k.label,
                        "x": x,
                        "y": y,
                        "width": Math.max(18, w - gap),
                        "height": kh
                    })
                }
                x += w
            }
            y += kh + (r === 0 ? 4 : 2)
        }
        return list
    }

    readonly property var keyList: computeKeyList()

    Rectangle {
        anchors.fill: parent
        color: Theme.background
    }

    Item {
        id: container
        objectName: "keyboardContainer"
        width: 940
        height: 230
        anchors.centerIn: parent
        scale: Math.max(0.1, Math.min(1.0, Math.min((root.width - 16) / 940, (root.height - 16) / 230)))
        transformOrigin: Item.Center

        Repeater {
            model: root.keyList
            delegate: Rectangle {
                id: keyRect
                x: modelData.x
                y: modelData.y
                width: modelData.width
                height: modelData.height
                radius: 4
                color: root.getColorFor(modelData.code)
                border.width: root.hoveredCode === modelData.code ? 2 : 1
                border.color: root.hoveredCode === modelData.code ? Theme.text : Theme.line

                Text {
                    anchors.centerIn: parent
                    text: modelData.label
                    color: Theme.text
                    font.family: Theme.family
                    font.pixelSize: modelData.label.length <= 1 ? 11 : (modelData.label.length <= 3 ? 9 : 8)
                }

                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    onEntered: root.hoveredCode = modelData.code
                    onExited: if (root.hoveredCode === modelData.code) root.hoveredCode = ""
                    onClicked: root.controlClicked(modelData.code, "SelectControl")
                }
            }
        }
    }
}
