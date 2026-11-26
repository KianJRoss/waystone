import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

WorkspacePage {
    id: dashboard
    objectName: "socialDashboard"
    property var socialView: typeof xodusSocialView !== "undefined" ? xodusSocialView : null
    property var voiceView: typeof xboxVoiceMedia !== "undefined" ? xboxVoiceMedia : null
    title: "Friends & parties"
    background: Rectangle { color: Theme.popup; border.color: Theme.line; radius: Theme.cornerRadius }
    contentItem: SocialPanel {
        socialView: dashboard.socialView
        voiceView: dashboard.voiceView
        onConnectionsRequested: dashboard.connectionsRequested()
    }
    signal connectionsRequested()
}
