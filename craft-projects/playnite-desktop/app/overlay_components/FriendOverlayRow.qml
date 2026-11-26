import QtQuick
import QtQuick.Layouts
import ".."

Rectangle {
    id: row
    objectName: "overlayFriendRow"
    property var friend: null
    property bool selected: false
    readonly property string displayName: friend && friend.gamertag ? String(friend.gamertag)
        : (friend && friend.xuid ? "User " + String(friend.xuid).slice(-4) : "Friend")
    // Only explicit booleans from the social adapter qualify as presence evidence.
    // Membership in the friends list does not imply online. No remote avatar load.
    readonly property bool presenceKnown: !!friend && typeof friend.online === "boolean"
    readonly property bool online: presenceKnown && friend.online === true
    readonly property string presenceText: !presenceKnown ? "Presence unavailable" : !online ? "Offline"
        : (typeof friend.presence === "string" && friend.presence.trim().length ? friend.presence : "Online")
    readonly property string initials: {
        var words = displayName.trim().split(/\s+/)
        return words.length > 1 ? (words[0].charAt(0) + words[words.length - 1].charAt(0)).toUpperCase()
                                : displayName.slice(0, 2).toUpperCase()
    }
    signal selectedByPointer()
    height: 58
    radius: Theme.cornerRadius / 2
    color: selected ? Qt.rgba(Theme.main.r, Theme.main.g, Theme.main.b, 0.80)
                    : Qt.rgba(Theme.main.r, Theme.main.g, Theme.main.b, 0.25)
    border.width: selected ? 1 : 0
    border.color: Theme.line
    Accessible.name: displayName + ", " + presenceText
    RowLayout {
        anchors.fill: parent; anchors.margins: 8; spacing: 10
        Rectangle {
            Layout.preferredWidth: 34; Layout.preferredHeight: 34; radius: 17
            color: Qt.rgba(Theme.main.r, Theme.main.g, Theme.main.b, 0.80)
            Text {
                anchors.centerIn: parent; text: row.initials; color: Theme.text
                font.family: Theme.family; font.pixelSize: 12; textFormat: Text.PlainText
            }
            Rectangle {
                objectName: "overlayFriendStatusDot"
                anchors.right: parent.right; anchors.bottom: parent.bottom
                width: 9; height: 9; radius: 5
                color: row.online ? Theme.text : Theme.panel
                border.color: row.presenceKnown ? Theme.glyph : Theme.line
                border.width: 1
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 3
            Text {
                Layout.fillWidth: true; text: row.displayName; color: Theme.text
                font.pixelSize: 12; font.family: Theme.family; textFormat: Text.PlainText; elide: Text.ElideRight
            }
            Text {
                Layout.fillWidth: true; text: row.presenceText; color: Theme.muted
                font.pixelSize: 10; font.family: Theme.family; textFormat: Text.PlainText; elide: Text.ElideRight
            }
        }
    }
    MouseArea { anchors.fill: parent; onClicked: row.selectedByPointer() }
}
