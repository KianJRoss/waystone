import QtQuick
import QtQuick.Controls
import ".."

Item {
    id: root
    implicitWidth: 260
    implicitHeight: 200

    property bool stickEnabled: true
    property real innerDeadzone: 0.08
    property real outerDeadzone: 1.0
    property real antiDeadzone: 0.0
    property real curve: 1.0
    property bool liveAvailable: false
    property real liveNormX: 0.0
    property real liveNormY: 0.0
    property bool processedAvailable: false
    property real processedNormX: 0.0
    property real processedNormY: 0.0

    // Canvas drawing response curve & stick bounds
    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.clearRect(0, 0, width, height)

            // 1. Circle stick perimeter on left side
            var radius = Math.max(1, Math.min(height / 2 - 18, width * 0.22))
            var cx = radius + 14
            var cy = height / 2

            // Outer perimeter
            ctx.strokeStyle = Theme.line
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.arc(cx, cy, radius, 0, 2 * Math.PI)
            ctx.stroke()

            // Outer saturation radius and anti-deadzone minimum-output guide.
            ctx.strokeStyle = "#8c72d9"
            ctx.lineWidth = 1.5
            ctx.beginPath()
            ctx.arc(cx, cy, radius * Math.max(0.5, Math.min(1.0, root.outerDeadzone)), 0, 2 * Math.PI)
            ctx.stroke()

            // Inner deadzone circle
            if (root.stickEnabled && root.innerDeadzone > 0) {
                ctx.strokeStyle = "#e49b2f" // Semantic orange
                ctx.lineWidth = 1.5
                ctx.beginPath()
                ctx.arc(cx, cy, radius * Math.min(1.0, root.innerDeadzone), 0, 2 * Math.PI)
                ctx.stroke()
            }
            if (root.stickEnabled && root.antiDeadzone > 0) {
                ctx.strokeStyle = "#4bc3c1"
                ctx.lineWidth = 1
                ctx.setLineDash([3, 3])
                ctx.beginPath()
                ctx.arc(cx, cy, radius * Math.min(0.5, root.antiDeadzone), 0, 2 * Math.PI)
                ctx.stroke()
                ctx.setLineDash([])
            }

            // Crosshair lines
            ctx.strokeStyle = Theme.line
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(cx - radius, cy)
            ctx.lineTo(cx + radius, cy)
            ctx.moveTo(cx, cy - radius)
            ctx.lineTo(cx, cy + radius)
            ctx.stroke()

            // Live dot on perimeter circle
            if (root.liveAvailable) {
                var dotX = cx + Math.max(-1.0, Math.min(1.0, root.liveNormX)) * radius
                var dotY = cy - Math.max(-1.0, Math.min(1.0, root.liveNormY)) * radius // Y-up inversion
                ctx.fillStyle = "#e49b2f"
                ctx.strokeStyle = Theme.text
                ctx.lineWidth = 1.5
                ctx.beginPath()
                ctx.arc(dotX, dotY, 5, 0, 2 * Math.PI)
                ctx.fill()
                ctx.stroke()
            }
            if (root.processedAvailable) {
                var processedX = cx + Math.max(-1.0, Math.min(1.0, root.processedNormX)) * radius
                var processedY = cy - Math.max(-1.0, Math.min(1.0, root.processedNormY)) * radius
                ctx.fillStyle = "#2fbf71"
                ctx.strokeStyle = Theme.text
                ctx.lineWidth = 1.5
                ctx.beginPath()
                ctx.arc(processedX, processedY, 4, 0, 2 * Math.PI)
                ctx.fill()
                ctx.stroke()
            }

            // 2. Response curve plot on right side
            var rw = Math.max(1, width * 0.44)
            var rh = Math.max(1, height - 44)
            var rx = width * 0.52
            var ry = 22
            var rbottom = ry + rh

            // Axes
            ctx.strokeStyle = Theme.line
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(rx, ry)
            ctx.lineTo(rx, rbottom)
            ctx.lineTo(rx + rw, rbottom)
            ctx.stroke()

            if (root.stickEnabled) {
                // Enabled response curve
                ctx.strokeStyle = "#2fbf71" // Semantic green curve
                ctx.lineWidth = 2
                ctx.beginPath()

                var lastX = rx
                var lastY = rbottom
                ctx.moveTo(lastX, lastY)

                var inner = Math.max(0.0, Math.min(1.0, root.innerDeadzone))
                var outer = Math.max(inner + 0.01, Math.min(1.0, root.outerDeadzone))
                var anti = Math.max(0.0, Math.min(1.0, root.antiDeadzone))
                var exp = Math.max(0.1, root.curve)
                var denom = Math.max(0.01, outer - inner)

                for (var i = 1; i <= 64; i++) {
                    var input = i / 64.0
                    var output = 0.0
                    if (input > inner) {
                        var frac = Math.min(1.0, (input - inner) / denom)
                        output = anti + (1.0 - anti) * Math.pow(frac, exp)
                    }
                    output = Math.max(0.0, Math.min(1.0, output))
                    var px = rx + input * rw
                    var py = rbottom - output * rh
                    ctx.lineTo(px, py)
                }
                ctx.stroke()
            } else {
                // When !Enabled, NativeProfileRuntime.ProcessStick returns input unchanged.
                // Draw linear identity diagonal from bottom-left to top-right.
                ctx.strokeStyle = Theme.muted
                ctx.lineWidth = 1.5
                ctx.beginPath()
                ctx.moveTo(rx, rbottom)
                ctx.lineTo(rx + rw, ry)
                ctx.stroke()
            }
        }
    }

    onStickEnabledChanged: canvas.requestPaint()
    onInnerDeadzoneChanged: canvas.requestPaint()
    onOuterDeadzoneChanged: canvas.requestPaint()
    onAntiDeadzoneChanged: canvas.requestPaint()
    onCurveChanged: canvas.requestPaint()
    onLiveAvailableChanged: canvas.requestPaint()
    onLiveNormXChanged: canvas.requestPaint()
    onLiveNormYChanged: canvas.requestPaint()
    onProcessedAvailableChanged: canvas.requestPaint()
    onProcessedNormXChanged: canvas.requestPaint()
    onProcessedNormYChanged: canvas.requestPaint()

    Text {
        anchors.right: parent.right
        anchors.top: parent.top
        text: "● raw  ● processed"
        color: Theme.muted
        font.pixelSize: 9
        font.family: Theme.family
        ToolTip.visible: hovered
        ToolTip.text: "Inner/outer/anti-deadzone guides; raw and processed stick positions"
        HoverHandler { id: legendHover }
        property bool hovered: legendHover.hovered
    }

    // Unavailable live position badge - hidden when live telemetry is valid
    Rectangle {
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.margins: 4
        implicitWidth: noticeText.implicitWidth + 8
        implicitHeight: 18
        radius: 3
        color: Theme.panel
        border.color: Theme.line
        visible: !root.liveAvailable
        Text {
            id: noticeText
            anchors.centerIn: parent
            text: "Live dot: unavailable offline"
            color: Theme.muted
            font.pixelSize: 9
            font.family: Theme.family
        }
    }
}
