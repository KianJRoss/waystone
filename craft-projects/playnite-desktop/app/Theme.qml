pragma Singleton
import QtQuick
QtObject {
    // Independently implemented Qt palette from Daze 2.1.2, Super Dario.
    readonly property color background: "#000000"
    readonly property color panel: "#1f1f1f"
    readonly property color main: "#3b3b3b"
    readonly property color hover: "#3b3b3b"
    readonly property color glyph: "#8d8d8d"
    readonly property color text: "#f2f2f2"
    readonly property color muted: "#8d8d8d"
    readonly property color line: "#3b3b3b"
    readonly property color popup: "#000000"
    readonly property int cornerRadius: 12
    readonly property int scrollbarWidth: 12
    // Daze requests Lato Light. Qt resolves this to the system sans if absent.
    readonly property string family: "Lato Light"
}
