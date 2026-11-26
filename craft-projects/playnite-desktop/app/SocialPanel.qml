import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: panel
    objectName: "socialPanel"
    property var socialView: typeof xodusSocialView !== "undefined" ? xodusSocialView : null
    property var voiceView: typeof xboxVoiceMedia !== "undefined" ? xboxVoiceMedia : null
    property var audioRoutes: typeof voiceAudioRoutes !== "undefined" ? voiceAudioRoutes : null
    property var steamView: typeof steamFriends !== "undefined" ? steamFriends : null
    property var platformView: typeof platformSocial !== "undefined" ? platformSocial : null
    property var providerKeys: ["all", "xbox", "steam", "battlenet", "epic", "gog", "ea", "ubisoft", "playstation", "nintendo"]
    property var providerLabels: ["All platforms", "Xbox", "Steam", "Battle.net", "Epic", "GOG", "EA", "Ubisoft", "PlayStation", "Nintendo"]
    property bool compact: false
    property int selectedTab: 0
    property string friendSearch: ""
    property string friendPlatform: "all"
    signal connectionsRequested()
    property bool nativeAudio: audioRoutes && audioRoutes.available
    property bool voiceSession: voiceView && voiceView.sessionActive
    property var allFriends: {
        var rows = []
        if (socialView && socialView.connected) rows = rows.concat(socialView.friends.map(function(f) { return Object.assign({}, f, {provider: "xbox", personId: f.xuid}) }))
        if (steamView && steamView.connected) rows = rows.concat(steamView.friends.map(function(f) { return Object.assign({}, f, {provider: "steam", personId: f.steam_id}) }))
        if (platformView) rows = rows.concat(platformView.friends.map(function(f) { return Object.assign({}, f, {personId:f.row_id}) }))
        return rows
    }
    property var shownFriends: allFriends.filter(function(f) {
        return (panel.friendPlatform === "all" || f.provider === panel.friendPlatform) && (!panel.friendSearch || (f.gamertag || f.display_name || "").toLowerCase().indexOf(panel.friendSearch.toLowerCase()) >= 0)
    })
    function memberName(member) {
        var found = socialView ? socialView.friends.filter(function(f) { return f.xuid === member.xuid }) : []
        return found.length ? found[0].gamertag : "Xbox member · " + String(member.xuid || "").slice(-4)
    }
    function refreshDevices() {
        if (nativeAudio) audioRoutes.refreshDevices()
        else if (voiceView) voiceView.refreshDevices()
    }
    function inputDevices() { return nativeAudio ? audioRoutes.inputs : voiceView ? voiceView.audioInputs : [] }
    function outputDevices() { return nativeAudio ? audioRoutes.outputs : voiceView ? voiceView.audioOutputs : [] }
    function deviceIndex(rows, id) {
        for (var n = 0; n < rows.length; ++n) if (rows[n].id === id) return n
        return 0
    }
    function selectInput(id) {
        if (nativeAudio) audioRoutes.selectInput(id)
        else if (voiceView) voiceView.selectDevices(id, voiceView.outputId)
    }
    function selectOutput(id) {
        if (nativeAudio) audioRoutes.selectOutput(id)
        else if (voiceView) voiceView.selectDevices(voiceView.inputId, id)
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 12
        RowLayout {
            Layout.fillWidth: true; spacing: 12
            Rectangle {
                width: 36; height: 36; radius: 18; color: Theme.main
                Text { anchors.centerIn: parent; text: panel.socialView && panel.socialView.account ? panel.socialView.account.charAt(0).toUpperCase() : "W"; textFormat: Text.PlainText; color: Theme.text; font.bold: true; font.pixelSize: 17 }
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                // The page header already says "Social"; show whose account this is instead of repeating it.
                Text { objectName: "socialAccountName"; text: panel.socialView && panel.socialView.account ? panel.socialView.account : "Xbox account"; textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 16; font.bold: true }
                Text {
                    objectName: "connectionStatusText"; Layout.fillWidth: true; elide: Text.ElideRight
                    text: !panel.socialView ? "Xbox unavailable" : panel.socialView.connecting ? "Connecting to Xbox Live…" : panel.socialView.connected ? "Connected to Xbox Live" : (panel.platformView && panel.platformView.steamConnected ? "Steam connected · Xbox disconnected" : "Disconnected")
                    textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12
                }
            }
            SocialButton {
                objectName: "connectButton"; text: panel.socialView && panel.socialView.connected ? "Disconnect" : "Connect Xbox"
                enabled: panel.socialView && !panel.socialView.connecting
                onClicked: if (panel.socialView.connected) panel.socialView.disconnectService(); else panel.socialView.connectService()
            }
            SocialButton {
                objectName: "refreshButton"; text: "Refresh"
                enabled: panel.socialView && panel.socialView.connected && !panel.socialView.refreshing
                onClicked: { panel.socialView.refreshFriends(); panel.socialView.refreshParty() }
            }
        }
        Text {
            objectName: "xboxSessionStatus"; Layout.fillWidth: true
            text: panel.socialView && typeof panel.socialView.sessionStatus === "string" ? panel.socialView.sessionStatus : ""
            visible: !!text; textFormat: Text.PlainText; color: Theme.muted
            wrapMode: Text.WordWrap; font.pixelSize: 12
        }
        RowLayout {
            Layout.fillWidth: true
            Repeater {
                model: ["Friends", "Xbox party", "Audio"]
                SocialButton {
                    required property string modelData; required property int index
                    objectName: "socialTab_" + index; text: modelData; selected: panel.selectedTab === index
                    onClicked: { panel.selectedTab = index; if (index === 2) panel.refreshDevices() }
                }
            }
            Item { Layout.fillWidth: true }
            // Steam / store sign-ins live in Settings > Accounts; this is the shortcut there.
            SocialButton { objectName: "accountConnectionsButton"; text: "Account connections"; visible: !panel.compact; onClicked: panel.connectionsRequested() }
        }
        Text {
            Layout.fillWidth: true; visible: !!text; wrapMode: Text.WordWrap; color: "#f2a8a8"; textFormat: Text.PlainText
            text: panel.socialView ? panel.socialView.error : ""
        }
        StackLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; currentIndex: panel.selectedTab
            ColumnLayout {
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    TextField { objectName: "friendsSearch"; Layout.fillWidth: true; placeholderText: "Find a friend…"; color: Theme.text; onTextChanged: panel.friendSearch = text; background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: Theme.cornerRadius } }
                    CompactCombo { objectName: "friendPlatformFilter"; implicitWidth: 150; model: panel.providerLabels; onActivated: panel.friendPlatform = panel.providerKeys[currentIndex] }
                }
                Text { text: panel.shownFriends.length + " friends shown"; color: Theme.muted; textFormat: Text.PlainText; font.pixelSize: 12 }
                ListView {
                    id: friends; objectName: "friendsListView"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 5
                    model: panel.shownFriends; ScrollBar.vertical: ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        objectName: "friendDelegate_" + modelData.personId
                        width: friends.width; height: 62; color: Theme.panel; radius: Theme.cornerRadius
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 10
                            Rectangle { width: 8; height: 8; radius: 4; color: modelData.online === true ? "#7ccf91" : Theme.muted }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 3
                                Text { Layout.fillWidth: true; text: modelData.gamertag || modelData.display_name || "Unknown player"; textFormat: Text.PlainText; color: Theme.text; font.bold: true; elide: Text.ElideRight }
                                Text { Layout.fillWidth: true; text: (panel.providerLabels[panel.providerKeys.indexOf(modelData.provider)] || modelData.provider) + " · " + (modelData.presence || (modelData.online === true ? "Online" : modelData.online === false ? "Offline" : "Presence unavailable")); textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12; elide: Text.ElideRight }
                            }
                            SocialButton {
                                objectName: "joinFriendVoice_" + modelData.personId; text: "Join party"
                                visible: modelData.provider === "xbox" && !!modelData.joinablePartyId
                                enabled: panel.voiceView && panel.voiceView.available && !panel.voiceSession
                                onClicked: { panel.selectedTab = 1; panel.voiceView.startParty(modelData.joinablePartyId) }
                            }
                            SocialButton {
                                objectName: "inviteButton_" + modelData.personId; text: "Invite"
                                visible: modelData.provider === "xbox"
                                enabled: panel.socialView && panel.socialView.connected && !!panel.socialView.partyState.partyId
                                onClicked: panel.socialView.inviteToParty(panel.socialView.partyState.partyId, modelData.personId)
                            }
                        }
                    }
                    Text {
                        anchors.centerIn: parent; width: parent.width - 24; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; visible: friends.count === 0
                        text: panel.friendPlatform === "steam" && panel.platformView && !panel.platformView.steamConnected ? "Connect Steam in Settings → Store & Deals to load friends." : panel.friendPlatform === "steam" && (!panel.steamView || !panel.steamView.connected) && !panel.platformView ? "Steam is not linked to Waystone yet." : (!panel.socialView || !panel.socialView.connected) && (!panel.steamView || !panel.steamView.connected) && (!panel.platformView || !panel.platformView.steamConnected) ? "Connect an account to see your friends here." : "No friends match this filter."
                        textFormat: Text.PlainText; color: Theme.muted
                    }
                }
            }
            ColumnLayout {
                spacing: 12
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 100; color: Theme.panel; radius: Theme.cornerRadius
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 6
                        RowLayout {
                            Text { text: "Xbox party"; textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 18; font.bold: true; Layout.fillWidth: true }
                            Text { objectName: "partyPhaseText"; property string partyId: panel.socialView ? panel.socialView.partyState.partyId || "" : ""; text: panel.socialView ? (panel.socialView.partyState.phase || "idle").toUpperCase() : "IDLE"; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12 }
                        }
                        Text { objectName: "voiceStatusText"; Layout.fillWidth: true; wrapMode: Text.WordWrap; text: panel.voiceView ? panel.voiceView.status : panel.socialView ? panel.socialView.voiceStatusText : "Voice unavailable"; textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 13 }
                        Text { text: "Xbox handles party voice. Your other platform contacts stay in Friends."; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12 }
                    }
                }
                Flow {
                    Layout.fillWidth: true; Layout.preferredHeight: childrenRect.height; spacing: 8
                    SocialButton { objectName: "createVoicePartyButton"; text: "Create party"; enabled: panel.socialView && panel.socialView.connected && panel.voiceView && panel.voiceView.available && !panel.voiceSession; onClicked: panel.voiceView.startParty("") }
                    SocialButton { objectName: "voiceMicrophoneButton"; text: panel.voiceView && !panel.voiceView.muted ? "Mute mic" : "Unmute mic"; enabled: panel.voiceView && panel.voiceView.connected; onClicked: panel.voiceView.setMuted(!panel.voiceView.muted) }
                    SocialButton { objectName: "resumePartyAudioButton"; text: "Resume audio"; enabled: panel.voiceView && panel.voiceView.connected; onClicked: panel.voiceView.resumeAudio() }
                    SocialButton { objectName: "leavePartyButton"; text: "Leave party"; enabled: panel.voiceSession || (panel.socialView && !!panel.socialView.partyState.partyId); onClicked: { if (panel.voiceSession) panel.voiceView.leave(); else if (panel.socialView && panel.socialView.partyState.partyId) panel.socialView.leaveParty(panel.socialView.partyState.partyId) } }
                    SocialButton { text: "Audio devices"; onClicked: { panel.selectedTab = 2; panel.refreshDevices() } }
                }
                RowLayout {
                    Layout.fillWidth: true
                    TextField { id: partyId; objectName: "voicePartyIdInput"; Layout.fillWidth: true; placeholderText: "Join by party ID (optional)"; color: Theme.text; background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: Theme.cornerRadius } }
                    SocialButton { objectName: "joinVoicePartyButton"; text: "Join party"; enabled: panel.socialView && panel.socialView.connected && panel.voiceView && panel.voiceView.available && !panel.voiceSession && partyId.text.trim().length > 0; onClicked: panel.voiceView.startParty(partyId.text.trim()) }
                }
                Text { text: "Party members"; color: Theme.text; font.bold: true; textFormat: Text.PlainText }
                ListView {
                    id: members; objectName: "partyMembersList"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 5
                    model: panel.socialView ? panel.socialView.partyState.members || [] : []; ScrollBar.vertical: ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: members.width; height: 50; radius: Theme.cornerRadius; color: Theme.panel
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10
                            Text { Layout.fillWidth: true; text: panel.memberName(modelData); textFormat: Text.PlainText; color: Theme.text; elide: Text.ElideRight }
                            Text { text: modelData.role || ""; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 11 }
                            SocialButton { text: panel.voiceView && panel.voiceView.mutedMembers.indexOf(modelData.xuid) >= 0 ? "Unmute" : "Mute"; enabled: panel.voiceView && panel.voiceView.connected; onClicked: panel.voiceView.mutePeer(modelData.xuid, panel.voiceView.mutedMembers.indexOf(modelData.xuid) < 0) }
                        }
                    }
                    Text { anchors.centerIn: parent; text: "No party members to show."; visible: members.count === 0; color: Theme.muted; textFormat: Text.PlainText }
                }
            }
            ColumnLayout {
                spacing: 14
                Text { text: "Party audio"; textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 18; font.bold: true }
                Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: panel.nativeAudio ? "Route only Waystone voice audio. Other apps and system defaults are not changed." : "Audio devices are provided by the voice media session. Microphone capture starts only when you choose Unmute."; textFormat: Text.PlainText; color: Theme.muted }
                GridLayout {
                    columns: 2; Layout.fillWidth: true; columnSpacing: 14; rowSpacing: 16
                    Text { text: "Microphone"; textFormat: Text.PlainText; color: Theme.text }
                    CompactCombo { objectName: "partyInputDevice"; enabled: !(panel.nativeAudio && panel.audioRoutes.busy); Layout.fillWidth: true; textRole: "label"; model: [{id:"",label:"System default"}].concat(panel.inputDevices()); currentIndex: panel.deviceIndex(model, panel.nativeAudio ? panel.audioRoutes.inputId : panel.voiceView ? panel.voiceView.inputId : ""); onActivated: panel.selectInput(model[currentIndex].id) }
                    Text { text: "Speakers / headset"; textFormat: Text.PlainText; color: Theme.text }
                    CompactCombo { objectName: "partyOutputDevice"; enabled: !(panel.nativeAudio && panel.audioRoutes.busy); Layout.fillWidth: true; textRole: "label"; model: [{id:"",label:"System default"}].concat(panel.outputDevices()); currentIndex: panel.deviceIndex(model, panel.nativeAudio ? panel.audioRoutes.outputId : panel.voiceView ? panel.voiceView.outputId : ""); onActivated: panel.selectOutput(model[currentIndex].id) }
                }
                SocialButton { objectName: "refreshAudioDevicesButton"; text: "Refresh devices"; enabled: !(panel.nativeAudio && panel.audioRoutes.busy); onClicked: panel.refreshDevices() }
                Text { objectName: "partyAudioStatus"; Layout.fillWidth: true; wrapMode: Text.WordWrap; text: panel.nativeAudio ? panel.audioRoutes.status : panel.voiceView ? panel.voiceView.audioStatus : "Voice media is unavailable."; textFormat: Text.PlainText; color: Theme.muted }
                Item { Layout.fillHeight: true }
            }
        }
    }
}
