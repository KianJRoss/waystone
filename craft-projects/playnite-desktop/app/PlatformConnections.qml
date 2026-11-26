import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: panel
    objectName: "platformConnectionsScroll"
    property var service: typeof platformSocial !== "undefined" ? platformSocial : null
    property int qrRevision: 0
    Connections { target: panel.service; function onChanged() { panel.qrRevision += 1 } }
    onVisibleChanged: if (!visible && service && service.qrActive) service.cancelSteamSignIn()
    function scrollToSteamLibrarySection() {
        const position = storeAccountsPanel.mapToItem(connectionsColumn, 0, 0)
        panel.contentItem.contentY = Math.max(0, position.y)
    }
    contentWidth: availableWidth
    clip: true
    ColumnLayout {
        id: connectionsColumn
        width: panel.availableWidth; spacing: 12
        Text { objectName: "gamingPlatformsHeader"; text: "Gaming platforms"; color: Theme.text; font.bold: true; font.pixelSize: 19 }
        Text { Layout.fillWidth: true; text: panel.service ? panel.service.status : "Platform service unavailable"; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
        SocialButton { objectName: "steamQrSignIn"; text: "Sign in with Steam Guard"; enabled: panel.service && !panel.service.busy && !panel.service.qrActive; onClicked: panel.service.beginSteamSignIn() }
        Text { Layout.fillWidth: true; text: "Recommended: approve the Waystone QR code in your Steam mobile app. Friends and achievements use that same session; no developer API key or password copying."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
        Image { objectName: "steamSignInQrImage"; visible: panel.service && panel.service.qrActive; Layout.preferredWidth: 360; Layout.preferredHeight: 360; cache: false; smooth: false; source: panel.service && panel.service.qrActive ? "image://steamqr/current?" + panel.qrRevision : "" }
        SocialButton { visible: panel.service && panel.service.qrActive; text: "Cancel sign-in"; onClicked: panel.service.cancelSteamSignIn() }
        Timer { interval: 5000; repeat: true; running: panel.visible && panel.service && panel.service.qrActive; onTriggered: if (!panel.service.busy) panel.service.pollSteamSignIn() }
        RowLayout {
            SocialButton { objectName: "connectNativeSteam"; text: "Connect running Steam"; enabled: panel.service && !panel.service.busy; onClicked: panel.service.connectSteam() }
            SocialButton { objectName: "refreshSteamFriends"; text: "Refresh Steam"; enabled: panel.service && !panel.service.busy && !!panel.service.steamAccount; onClicked: panel.service.refreshProvider("steam") }
            SocialButton { text: "Sign out of Steam"; enabled: !!panel.service; onClicked: panel.service.disconnectSteam() }
        }
        Text { objectName: "steamSessionStatus"; Layout.fillWidth: true; text: panel.service && typeof panel.service.sessionStatus === "string" ? panel.service.sessionStatus : ""; visible: !!text; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
        RowLayout {
            SocialButton { text: "Retry saved sign-in"; enabled: panel.service && !panel.service.busy; onClicked: panel.service.restoreSession() }
            SocialButton { text: "Save sign-in"; enabled: panel.service && !panel.service.busy && panel.service.achievementAccess; onClicked: panel.service.rememberSteamSession() }
        }
        Text { Layout.fillWidth: true; text: "Native Steam connection reads your already signed-in Steam client, without copying login cookies or passwords. Steam must be running; Waystone does not start it automatically."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
        GroupBox {
            title: "Optional existing Steam Web API key · friends and achievements"
            Layout.fillWidth: true
            ColumnLayout {
                anchors.fill: parent
                Text { Layout.fillWidth: true; text: "Use an existing key and your SteamID64. Sign-in is remembered in your desktop keyring when available, never in plaintext app files or logs. Profile privacy still applies. No new key/account is created."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
                TextField { id: owner; objectName: "steamAccountInput"; Layout.fillWidth: true; placeholderText: "SteamID64"; text: panel.service ? panel.service.steamAccount : ""; color: Theme.text; background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: 5 } }
                TextField { id: key; objectName: "steamApiKeyInput"; Layout.fillWidth: true; placeholderText: "Existing Steam Web API key"; echoMode: TextInput.Password; maximumLength: 32; color: Theme.text; background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: 5 } }
                SocialButton { objectName: "connectSteamApi"; text: "Connect Steam Web API"; enabled: panel.service && !panel.service.busy && key.text.length === 32 && owner.text.length === 17; onClicked: { panel.service.connectSteamWeb(owner.text.trim(), key.text.trim()); key.clear() } }
            }
        }
        Repeater {
            model: panel.service ? panel.service.providers : []
            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true; implicitHeight: body.implicitHeight + 20; color: Theme.panel; radius: 8
                ColumnLayout {
                    id: body; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                    Text { text: modelData.label; color: Theme.text; font.bold: true; textFormat: Text.PlainText }
                    Text {
                        Layout.fillWidth: true; color: Theme.muted; wrapMode: Text.WordWrap; textFormat: Text.PlainText
                        text: modelData.provider === "xbox" ? "Xbox connection and party controls are above; Xbox is never treated as Steam." : modelData.status === "integration_pending" ? "Connector not implemented yet. This platform is not silently counted as connected." : modelData.status + (modelData.account_id ? " · " + modelData.account_id : "") + (modelData.error ? " · " + modelData.error : "")
                    }
                }
            }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.muted; opacity: 0.3 }
        StoreAccountsPanel { id: storeAccountsPanel; objectName: "storeAccountsPanel" }
    }
}
