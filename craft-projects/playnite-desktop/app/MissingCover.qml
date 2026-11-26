import QtQuick
Rectangle {
    id: cover
    // This URL is supplied by the validated backend, never derived from a title.
    property url artworkSource: ""
    // Reject missing-value sentinels before Image resolves them as local files.
    // Keep the public url-typed property compatible with existing consumers.
    readonly property url safeArtworkSource: {
        if (artworkSource === undefined || artworkSource === null) return ""
        var value = String(artworkSource)
        return value === "undefined" || value === "null" ? "" : artworkSource
    }
    property bool loadEnabled: true
    readonly property int artworkStatus: raster.status
    readonly property bool artworkReady: raster.status === Image.Ready
    readonly property bool placeholderVisible: safeArtworkSource.toString().length === 0 || raster.status === Image.Error
    color: Theme.panel
    border.color: Theme.line
    border.width: 1
    clip: true
    Image {
        id: raster
        objectName: "coverImage"
        anchors.fill: parent
        source: cover.loadEnabled ? cover.safeArtworkSource : ""
        asynchronous: true
        cache: true
        retainWhileLoading: false
        sourceSize.width: Math.max(1, Math.min(512, Math.ceil(cover.width)))
        sourceSize.height: Math.max(1, Math.min(768, Math.ceil(cover.height)))
        fillMode: Image.PreserveAspectCrop
        visible: status === Image.Ready
    }
    Column {
        objectName: "missingArtworkPlaceholder"
        visible: cover.placeholderVisible
        anchors.centerIn: parent
        width: parent.width - 12
        spacing: 10
        Rectangle {
            width: 28; height: 22; anchors.horizontalCenter: parent.horizontalCenter
            color: "transparent"; border.width: 1; border.color: Theme.glyph
            Rectangle { x: 6; y: 5; width: 4; height: 4; radius: 2; color: Theme.glyph }
            Text { anchors.centerIn: parent; anchors.verticalCenterOffset: 3; text: "—"; color: Theme.glyph; font.pixelSize: 16 }
        }
        Text {
            width: parent.width
            text: "No artwork imported"
            color: Theme.muted; font.family: Theme.family; font.pixelSize: 12
            wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
    }
}
