import QtQuick
import QtQuick.Controls
import ".."

// CheckBox whose label uses the palette (the default style draws dark text on the dark panels).
CheckBox {
    id: box
    contentItem: Text {
        text: box.text
        color: box.enabled ? Theme.text : Theme.muted
        font.family: Theme.family
        font.pixelSize: 12
        verticalAlignment: Text.AlignVCenter
        leftPadding: box.indicator.width + box.spacing
    }
}
