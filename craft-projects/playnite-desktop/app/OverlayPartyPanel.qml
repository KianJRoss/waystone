import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "overlay_components"

Item {
    id: panel
    objectName: "overlayPartyPanel"
    property var socialView: null
    property var voiceView: null
    property var discordView: null
    property var audioRoutes: null
    property bool hasActiveFocus: false
    property int selectedIndex: -1
    property int selectedControl: 0
    property bool deafened: false
    property var mutedBeforeDeafen: []
    readonly property var members: socialView && socialView.partyState ? (socialView.partyState.members || []) : []
    readonly property bool voiceConnected: !!(voiceView && voiceView.connected)

    function memberVolume(id) { return voiceView && voiceView.peerVolumes && voiceView.peerVolumes[id] !== undefined ? Number(voiceView.peerVolumes[id]) : 100 }
    function isSpeaking(id) { return voiceConnected && voiceView.speakingAvailable && voiceView.speakingMembers.indexOf(id) >= 0 }
    function isMuted(id) { return voiceConnected && voiceView.mutedMembers.indexOf(id) >= 0 }
    function setVolume(id, value) { if (voiceView && voiceView.setPeerVolume) voiceView.setPeerVolume(id, Math.max(0, Math.min(200, value))) }
    function refreshDevices() { if (audioRoutes && audioRoutes.available) audioRoutes.refreshDevices(); else if (voiceView) voiceView.refreshDevices() }
    function inputs() { return audioRoutes && audioRoutes.available ? audioRoutes.inputs : voiceView ? voiceView.audioInputs : [] }
    function outputs() { return audioRoutes && audioRoutes.available ? audioRoutes.outputs : voiceView ? voiceView.audioOutputs : [] }
    function inputId() { return audioRoutes && audioRoutes.available ? audioRoutes.inputId : voiceView ? voiceView.inputId : "" }
    function outputId() { return audioRoutes && audioRoutes.available ? audioRoutes.outputId : voiceView ? voiceView.outputId : "" }
    function chooseInput(id) { if (audioRoutes && audioRoutes.available) audioRoutes.selectInput(id); else if (voiceView) voiceView.selectDevices(id, voiceView.outputId) }
    function chooseOutput(id) { if (audioRoutes && audioRoutes.available) audioRoutes.selectOutput(id); else if (voiceView) voiceView.selectDevices(voiceView.inputId, id) }
    function indexOfDevice(rows, id) { rows = rows || []; for (var i=0;i<rows.length;i++) if (rows[i] && rows[i].id === id) return i; return 0 }
    function toggleDeafen() {
        if (!voiceConnected) return
        if (!deafened) {
            mutedBeforeDeafen = voiceView.mutedMembers.slice(0)
            for (var i=0;i<members.length;i++) voiceView.mutePeer(members[i].xuid, true)
            deafened = true
        } else {
            for (var j=0;j<members.length;j++) voiceView.mutePeer(members[j].xuid, mutedBeforeDeafen.indexOf(members[j].xuid) >= 0)
            deafened = false
        }
    }
    function createOrJoin() {
        if (!voiceView || !voiceView.available || !socialView || !socialView.connected || voiceView.sessionActive) return
        voiceView.startParty(socialView.partyState ? (socialView.partyState.partyId || "") : "")
    }
    function leaveParty() {
        if (voiceView && voiceView.sessionActive) voiceView.leave()
        else if (socialView && socialView.partyState && socialView.partyState.partyId) socialView.leaveParty(socialView.partyState.partyId)
    }
    function activateControl() {
        if (selectedControl === 0) {
            if (voiceView && voiceView.sessionActive) voiceView.leave()
            else createOrJoin()
        } else if (selectedControl === 1 && voiceConnected) voiceView.setMuted(!voiceView.muted)
        else if (selectedControl === 2) toggleDeafen()
    }
    function navigate(action) {
        if (action === "up") { selectedIndex = selectedIndex <= 0 ? -1 : selectedIndex - 1; if (selectedIndex >= 0) memberList.positionViewAtIndex(selectedIndex, ListView.Contain); return true }
        if (action === "down") { selectedIndex = selectedIndex < 0 ? (members.length ? 0 : -1) : selectedIndex >= members.length - 1 ? -1 : selectedIndex + 1; if (selectedIndex >= 0) memberList.positionViewAtIndex(selectedIndex, ListView.Contain); return true }
        if (action === "left" || action === "right") {
            if (selectedIndex < 0) selectedControl = (selectedControl + (action === "right" ? 1 : 2)) % 3
            else if (voiceConnected && selectedIndex < members.length) {
                var m = members[selectedIndex]
                setVolume(m.xuid, memberVolume(m.xuid) + (action === "right" ? 10 : -10))
            }
            return true
        }
        if (action === "accept") {
            if (selectedIndex < 0) activateControl()
            else if (members.length && selectedIndex < members.length && voiceConnected) {
                var chosen = members[selectedIndex]; voiceView.mutePeer(chosen.xuid, !isMuted(chosen.xuid))
            }
            return true
        }
        return false
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 8; spacing: 5
        RowLayout {
            Layout.fillWidth: true
            Text { text: "Party"; color: Theme.text; font.bold: true; font.pixelSize: 14; textFormat: Text.PlainText; Layout.fillWidth: true }
            Text { text: panel.voiceConnected ? "LIVE" : "OFF"; color: panel.voiceConnected ? Theme.text : Theme.muted; font.pixelSize: 10; textFormat: Text.PlainText }
            OverlayControl { objectName: "overlayOpenDiscord"; width: 58; implicitWidth: 58; text: "Discord"; compact: true; enabled: !!(panel.discordView && panel.discordView.hasClient); onClicked: if (panel.discordView) panel.discordView.openDiscord() }
            OverlayControl { objectName: "overlayPartyCreate"; width: 44; implicitWidth: 44; text: panel.socialView && panel.socialView.partyState && panel.socialView.partyState.partyId ? "Join" : "New"; compact: true; visible: !panel.voiceView || !panel.voiceView.sessionActive; highlighted: panel.hasActiveFocus && panel.selectedIndex < 0 && panel.selectedControl === 0; enabled: !!(panel.socialView && panel.socialView.connected && panel.voiceView && panel.voiceView.available); onClicked: panel.createOrJoin() }
            OverlayControl { objectName: "overlayPartyLeave"; width: 44; implicitWidth: 44; text: "Exit"; hint: "Leave party"; compact: true; visible: !!(panel.voiceView && panel.voiceView.sessionActive) || !!(panel.socialView && panel.socialView.partyState && panel.socialView.partyState.partyId); enabled: visible; highlighted: panel.hasActiveFocus && panel.selectedIndex < 0 && panel.selectedControl === 0 && !!(panel.voiceView && panel.voiceView.sessionActive); onClicked: panel.leaveParty() }
        }
        Text { Layout.fillWidth: true; text: panel.voiceView && panel.voiceView.status ? panel.voiceView.status : "Voice unavailable"; color: Theme.muted; font.pixelSize: 10; elide: Text.ElideRight; textFormat: Text.PlainText }
        ListView {
            id: memberList; objectName: "overlayPartyMembers"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 3; model: panel.members
            ScrollBar.vertical: OverlayScrollBar {}
            delegate: Rectangle {
                required property var modelData; required property int index
                width: memberList.width - 8; height: 58; radius: Theme.cornerRadius / 2
                color: panel.hasActiveFocus && panel.selectedIndex === index ? Theme.hover : Theme.panel
                border.color: panel.hasActiveFocus && panel.selectedIndex === index ? Theme.text : Theme.line; border.width: 1
                RowLayout {
                    anchors.fill: parent; anchors.margins: 5; spacing: 4
                    Rectangle { width: 8; height: 8; radius: 4; color: panel.isSpeaking(modelData.xuid) ? Theme.text : Theme.muted }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 1
                        Text { Layout.fillWidth: true; text: modelData.gamertag || ("Member " + String(modelData.xuid || "").slice(-4)); color: Theme.text; font.pixelSize: 10; font.bold: true; elide: Text.ElideRight; textFormat: Text.PlainText }
                        Text { Layout.fillWidth: true; text: panel.isSpeaking(modelData.xuid) ? "Speaking" : panel.isMuted(modelData.xuid) ? "Muted" : panel.voiceView && panel.voiceView.speakingAvailable ? "Quiet" : "Activity unavailable"; color: Theme.muted; font.pixelSize: 9; textFormat: Text.PlainText }
                    }
                    Slider { objectName: "peerVolume_" + modelData.xuid; Layout.preferredWidth: 82; from: 0; to: 200; stepSize: 5; value: panel.memberVolume(modelData.xuid); enabled: panel.voiceConnected; Accessible.name: "Volume for " + (modelData.gamertag || modelData.xuid); onMoved: panel.setVolume(modelData.xuid, value); onValueChanged: if (pressed) panel.setVolume(modelData.xuid, value) }
                    OverlayControl { objectName: "peerMute_" + modelData.xuid; compact: true; implicitWidth: 30; Accessible.name: panel.isMuted(modelData.xuid) ? "Unmute " + (modelData.gamertag || modelData.xuid) : "Mute " + (modelData.gamertag || modelData.xuid); text: panel.isMuted(modelData.xuid) ? "U" : "M"; enabled: panel.voiceConnected; onClicked: panel.voiceView.mutePeer(modelData.xuid, !panel.isMuted(modelData.xuid)) }
                }
                MouseArea { anchors.fill: parent; z: -1; onClicked: panel.selectedIndex = index }
            }
            Text { anchors.centerIn: parent; width: parent.width - 12; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; text: panel.members.length ? "" : "Create or join a party to manage voice"; color: Theme.muted; font.pixelSize: 10; textFormat: Text.PlainText }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 4
            OverlayControl { objectName: "overlayMicToggle"; width: 48; implicitWidth: 48; compact: true; text: panel.voiceView && !panel.voiceView.muted ? "Mic ✓" : "Mic ×"; hint: panel.voiceView && !panel.voiceView.muted ? "Mute microphone" : "Unmute microphone"; enabled: panel.voiceConnected; highlighted: panel.hasActiveFocus && panel.selectedIndex < 0 && panel.selectedControl === 1; onClicked: panel.voiceView.setMuted(!panel.voiceView.muted) }
            OverlayControl { objectName: "overlayDeafenToggle"; width: 48; implicitWidth: 48; compact: true; text: panel.deafened ? "Undo" : "Deaf"; hint: panel.deafened ? "Restore previous peer mutes" : "Mute all party members"; enabled: panel.voiceConnected; highlighted: panel.hasActiveFocus && panel.selectedIndex < 0 && panel.selectedControl === 2; onClicked: panel.toggleDeafen() }
            OverlayControl { objectName: "overlayRefreshDevices"; width: 40; implicitWidth: 40; compact: true; text: "IO"; hint: "Refresh audio devices"; enabled: !(panel.audioRoutes && panel.audioRoutes.busy); onClicked: panel.refreshDevices() }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 4
            Text { text: "In"; color: Theme.muted; font.pixelSize: 9; textFormat: Text.PlainText }
            CompactCombo { objectName: "overlayInputDevice"; Layout.fillWidth: true; implicitWidth: 70; textRole: "label"; model: [{id:"",label:"Default"}].concat(panel.inputs()); currentIndex: panel.indexOfDevice(model, panel.inputId()); enabled: !(panel.audioRoutes && panel.audioRoutes.busy); onActivated: panel.chooseInput(model[currentIndex].id) }
            Text { text: "Out"; color: Theme.muted; font.pixelSize: 9; textFormat: Text.PlainText }
            CompactCombo { objectName: "overlayOutputDevice"; Layout.fillWidth: true; implicitWidth: 70; textRole: "label"; model: [{id:"",label:"Default"}].concat(panel.outputs()); currentIndex: panel.indexOfDevice(model, panel.outputId()); enabled: !(panel.audioRoutes && panel.audioRoutes.busy); onActivated: panel.chooseOutput(model[currentIndex].id) }
        }
        Text { objectName: "overlayPartyNavigationHint"; Layout.fillWidth: true; text: "D-pad: member · ←/→: volume · A: mute · LB/RB: widget"; color: Theme.muted; font.pixelSize: 9; elide: Text.ElideRight; textFormat: Text.PlainText }
    }
}
