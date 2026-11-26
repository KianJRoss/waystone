import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Rectangle {
    id: widgetRoot
    objectName: "activeSpeakerWidget"
    color: embeddedCard ? "transparent" : Theme.panel
    radius: Theme.cornerRadius
    border.color: hasActiveFocus ? Theme.text : Theme.line
    border.width: embeddedCard ? 0 : (hasActiveFocus ? 2 : 1)

    property bool embeddedCard: false
    property var socialView: null
    property var voiceView: null
    property int selectedIndex: 0
    property bool hasActiveFocus: false

    readonly property bool inParty: !!(socialView && socialView.partyState && socialView.partyState.partyId)
    readonly property var partyMembers: inParty ? (socialView.partyState.members || []) : []
    readonly property int memberCount: partyMembers.length
    readonly property bool voiceConnected: !!(voiceView && voiceView.available && voiceView.connected)

    function memberName(member) {
        if (!member) return "Unknown"
        if (member.gamertag) return member.gamertag
        if (member.displayName) return member.displayName
        if (socialView && socialView.friends) {
            for (var i = 0; i < socialView.friends.length; ++i) {
                if (socialView.friends[i].xuid === member.xuid) return socialView.friends[i].gamertag
            }
        }
        var xuidStr = String(member.xuid || "")
        return "Member " + (xuidStr.length > 4 ? xuidStr.slice(-4) : xuidStr)
    }

    function isMemberSpeaking(xuid) {
        if (!voiceConnected || !xuid || !voiceView.speakingAvailable) return false
        if (!voiceView || typeof voiceView.speakingMembers === "undefined" || !voiceView.speakingMembers) return false
        return voiceView.speakingMembers.indexOf(xuid) >= 0
    }

    function isMemberMuted(xuid) {
        if (!voiceConnected || !xuid || !voiceView || !voiceView.mutedMembers) return false
        return voiceView.mutedMembers.indexOf(xuid) >= 0
    }

    function memberVoiceStatus(xuid) {
        if (!voiceConnected) return "Voice unavailable"
        if (isMemberMuted(xuid)) return "Muted"
        if (!voiceView.speakingAvailable) return "Activity unavailable"
        if (isMemberSpeaking(xuid)) return "Speaking"
        return "Quiet"
    }

    function toggleMuteSelected() {
        if (!voiceConnected || selectedIndex < 0 || selectedIndex >= memberCount) return
        var member = partyMembers[selectedIndex]
        if (!member || !member.xuid) return
        var muted = isMemberMuted(member.xuid)
        voiceView.mutePeer(member.xuid, !muted)
    }

    function navigate(action) {
        if (!inParty || memberCount === 0) return false
        if (action === "up") {
            selectedIndex = Math.max(0, selectedIndex - 1)
            membersView.positionViewAtIndex(selectedIndex, ListView.Contain)
            return true
        } else if (action === "down") {
            selectedIndex = Math.min(memberCount - 1, selectedIndex + 1)
            membersView.positionViewAtIndex(selectedIndex, ListView.Contain)
            return true
        } else if (action === "accept") {
            toggleMuteSelected()
            return true
        }
        return false
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: widgetRoot.embeddedCard ? 8 : 16
        spacing: 12

        // Header
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                Layout.fillWidth: true
                elide: Text.ElideRight
                text: widgetRoot.embeddedCard ? (widgetRoot.memberCount + " members") : "PARTY & VOICE"
                color: Theme.text
                font.bold: true
                font.pixelSize: 14; font.family: Theme.family
                font.letterSpacing: 1.0
                textFormat: Text.PlainText
            }


            // Voice Status Pill
            Rectangle {
                implicitHeight: 22
                implicitWidth: statusText.implicitWidth + 14
                radius: 11
                color: widgetRoot.voiceConnected ? Theme.main : Theme.background
                border.color: widgetRoot.voiceConnected ? Theme.glyph : Theme.line
                border.width: 1

                Text {
                    id: statusText
                    anchors.centerIn: parent
                    text: widgetRoot.voiceConnected ? "Voice active" : (widgetRoot.inParty ? "Voice unavailable" : "No party")
                    color: widgetRoot.voiceConnected ? Theme.text : Theme.muted
                    font.pixelSize: 11; font.family: Theme.family
                    font.bold: true
                    textFormat: Text.PlainText
                }
            }
        }

        // Party ID or Not In Party text
        Text {
            Layout.fillWidth: true
            text: widgetRoot.inParty ? ("Party: " + socialView.partyState.partyId) : "Not currently connected to a party."
            color: Theme.muted
            font.pixelSize: 12; font.family: Theme.family
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: Theme.line
        }

        // Members List
        ListView {
            id: membersView
            objectName: "overlayMembersList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 6
            ScrollBar.vertical: OverlayScrollBar { id: membersBar }
            model: widgetRoot.partyMembers

            delegate: Rectangle {
                id: delegateRoot
                required property var modelData
                required property int index

                width: membersView.width - (membersBar.visible ? 10 : 0)
                height: 48
                radius: Theme.cornerRadius
                color: (widgetRoot.hasActiveFocus && widgetRoot.selectedIndex === index) ? Theme.hover : Theme.background
                border.width: (widgetRoot.hasActiveFocus && widgetRoot.selectedIndex === index) ? 2 : 1
                border.color: (widgetRoot.hasActiveFocus && widgetRoot.selectedIndex === index) ? Theme.text : Theme.line

                readonly property bool speaking: widgetRoot.isMemberSpeaking(modelData.xuid)
                readonly property bool muted: widgetRoot.isMemberMuted(modelData.xuid)
                readonly property string voiceStatus: widgetRoot.memberVoiceStatus(modelData.xuid)

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 10

                    // Speaker indicator dot/ring
                    Rectangle {
                        implicitWidth: 16
                        implicitHeight: 16
                        radius: 8
                        color: delegateRoot.speaking ? Theme.text : (delegateRoot.muted ? Theme.main : Theme.glyph)
                        border.color: delegateRoot.speaking ? Theme.text : Theme.line
                        border.width: delegateRoot.speaking ? 2 : 0

                        SequentialAnimation on opacity {
                            running: delegateRoot.speaking
                            loops: Animation.Infinite
                            PropertyAnimation { to: 0.5; duration: 400 }
                            PropertyAnimation { to: 1.0; duration: 400 }
                        }
                    }

                    // Member Name
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: widgetRoot.memberName(modelData)
                            color: Theme.text
                            font.bold: true
                            font.pixelSize: 13; font.family: Theme.family
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                        Text {
                            text: delegateRoot.voiceStatus
                            color: delegateRoot.speaking ? Theme.text : Theme.muted
                            font.pixelSize: 11; font.family: Theme.family
                            textFormat: Text.PlainText
                        }
                    }

                    // Mute / Unmute Button
                    SocialButton {
                        text: delegateRoot.muted ? "Unmute" : "Mute"
                        implicitHeight: 28
                        visible: widgetRoot.voiceConnected
                        onClicked: {
                            widgetRoot.selectedIndex = index
                            widgetRoot.toggleMuteSelected()
                        }
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    z: -1
                    onClicked: {
                        widgetRoot.selectedIndex = index
                        widgetRoot.hasActiveFocus = true
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                visible: membersView.count === 0
                text: widgetRoot.inParty ? "No other members in party" : "Join or create a party to chat"
                color: Theme.muted
                font.pixelSize: 13; font.family: Theme.family
                textFormat: Text.PlainText
            }
        }
    }
}
