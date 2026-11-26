import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "overlay_components"

Item {
    id: panel
    objectName: "overlayFriendsPanel"
    property var socialView: null
    property var voiceView: null
    property bool hasActiveFocus: false
    property int selectedIndex: 0
    property int selectedAction: 0
    property string quickReply: ""
    readonly property var friends: {
        var source = socialView && socialView.friends ? socialView.friends.slice(0) : []
        source.sort(function(a,b) {
            var ao = a.online === true ? 0 : a.online === false ? 2 : 1
            var bo = b.online === true ? 0 : b.online === false ? 2 : 1
            if (ao !== bo) return ao - bo
            return String(a.gamertag || a.xuid || "").localeCompare(String(b.gamertag || b.xuid || ""))
        })
        return source
    }
    function actions(friend) {
        var result=[]
        if (socialView && socialView.connected && friend && friend.online === true && friend.xuid && socialView.partyState && socialView.partyState.partyId) result.push("Invite")
        if (friend && friend.joinablePartyId && voiceView && voiceView.available && !voiceView.sessionActive) result.push("Join")
        if (socialView && typeof socialView.sendMessage === "function" && friend && friend.xuid) result.push("Message")
        return result
    }
    function performAction(friend, action) {
        if (!friend || !action) return
        if (action === "Invite" && socialView && socialView.partyState.partyId) socialView.inviteToParty(socialView.partyState.partyId, friend.xuid)
        else if (action === "Join" && friend.joinablePartyId && voiceView && voiceView.available && !voiceView.sessionActive) voiceView.startParty(friend.joinablePartyId)
        else if (action === "Message") { messageTarget = friend; replyField.forceActiveFocus() }
    }
    property var messageTarget: null
    function sendReply() {
        if (!messageTarget || !quickReply.trim() || !socialView || typeof socialView.sendMessage !== "function") return
        socialView.sendMessage(messageTarget.xuid, quickReply.trim())
        quickReply = ""
    }
    function navigate(action) {
        if (action === "up" || action === "down") {
            selectedIndex = Math.max(0, Math.min(friends.length - 1, selectedIndex + (action === "down" ? 1 : -1)))
            selectedAction = 0
            friendList.positionViewAtIndex(selectedIndex, ListView.Contain)
            return true
        }
        var row = friends.length && selectedIndex < friends.length ? friends[selectedIndex] : null
        var choices = actions(row)
        if (action === "left" || action === "right") {
            if (choices.length) selectedAction = (selectedAction + (action === "right" ? 1 : choices.length - 1)) % choices.length
            return true
        }
        if (action === "accept") { if (choices.length) performAction(row, choices[selectedAction % choices.length]); return true }
        return false
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 8; spacing: 5
        RowLayout {
            Layout.fillWidth: true
            Text { text: "Friends"; color: Theme.text; font.bold: true; font.pixelSize: 14; textFormat: Text.PlainText; Layout.fillWidth: true }
            Text { text: panel.friends.length + " · online first"; color: Theme.muted; font.pixelSize: 9; textFormat: Text.PlainText }
            OverlayControl { objectName: "overlayFriendsRefresh"; text: "↻"; compact: true; hint: "Refresh friends"; enabled: !!(panel.socialView && panel.socialView.connected); onClicked: { panel.socialView.refreshFriends(); panel.socialView.refreshParty() } }
        }
        ListView {
            id: friendList; objectName: "overlayFriendsList"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 3; model: panel.friends
            ScrollBar.vertical: OverlayScrollBar {}
            delegate: Rectangle {
                required property var modelData; required property int index
                readonly property int friendIndex: index
                width: friendList.width - 8; height: 72; radius: Theme.cornerRadius / 2
                color: panel.hasActiveFocus && panel.selectedIndex === index ? Theme.hover : Theme.panel
                border.color: panel.hasActiveFocus && panel.selectedIndex === index ? Theme.text : Theme.line; border.width: 1
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 5; spacing: 1
                    RowLayout {
                        Layout.fillWidth: true; spacing: 5
                        Rectangle { width: 7; height: 7; radius: 4; color: modelData.online === true ? Theme.text : Theme.glyph }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 1
                            Text { Layout.fillWidth: true; text: modelData.gamertag || ("Player " + String(modelData.xuid || "").slice(-4)); color: Theme.text; font.bold: true; font.pixelSize: 10; elide: Text.ElideRight; textFormat: Text.PlainText }
                            Text { Layout.fillWidth: true; text: (modelData.presence || (modelData.online === true ? "Online" : modelData.online === false ? "Offline" : "Presence unavailable")) + (modelData.currentGame ? " · " + modelData.currentGame : ""); color: Theme.muted; font.pixelSize: 9; elide: Text.ElideRight; textFormat: Text.PlainText }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true; height: 26; spacing: 3
                        Repeater {
                            model: panel.actions(modelData)
                            delegate: Rectangle {
                                required property string modelData
                                objectName: "friendAction_" + String(modelData) + "_" + String(friendIndex)
                                width: 38; height: 22; radius: Theme.cornerRadius / 2
                                color: panel.hasActiveFocus && panel.selectedIndex === friendIndex && panel.actions(friendList.model[friendIndex])[panel.selectedAction] === modelData ? Theme.hover : Theme.main
                                border.color: Theme.line; border.width: 1
                                Accessible.name: modelData + " " + (friendList.model[friendIndex].gamertag || "friend")
                                Text { anchors.centerIn: parent; text: modelData === "Message" ? "Msg" : modelData === "Invite" ? "Inv" : "Join"; color: Theme.text; font.family: Theme.family; font.pixelSize: 9; textFormat: Text.PlainText }
                                MouseArea { anchors.fill: parent; onClicked: { panel.selectedIndex = friendIndex; panel.performAction(friendList.model[friendIndex], modelData) } }
                            }
                        }
                    }
                }
                MouseArea { anchors.fill: parent; z: -1; onClicked: panel.selectedIndex = index }
            }
            Text { anchors.centerIn: parent; visible: friendList.count === 0; text: panel.socialView && !panel.socialView.connected ? "Connect Xbox to load friends" : "No friends found"; color: Theme.muted; font.pixelSize: 10; textFormat: Text.PlainText }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 4
            Text { Layout.preferredWidth: 74; text: panel.messageTarget ? "To " + (panel.messageTarget.gamertag || "friend") : "Quick reply"; color: Theme.muted; font.pixelSize: 9; elide: Text.ElideRight; textFormat: Text.PlainText }
            TextField { id: replyField; objectName: "overlayFriendQuickReply"; Layout.fillWidth: true; placeholderText: "Select Message, type reply…"; text: panel.quickReply; onTextChanged: panel.quickReply = text; onAccepted: panel.sendReply(); enabled: !!panel.messageTarget; color: Theme.text; font.pixelSize: 10; background: Rectangle { color: Theme.background; border.color: Theme.line; radius: Theme.cornerRadius / 2 } }
            OverlayControl { objectName: "overlayFriendSendReply"; width: 42; implicitWidth: 42; compact: true; text: "Send"; enabled: !!panel.messageTarget && !!panel.quickReply.trim(); onClicked: panel.sendReply() }
        }
        Text { objectName: "overlayFriendsNavigationHint"; Layout.fillWidth: true; text: "D-pad: friend · ←/→: action · A: choose · B: back"; color: Theme.muted; font.pixelSize: 9; elide: Text.ElideRight; textFormat: Text.PlainText }
    }
}
