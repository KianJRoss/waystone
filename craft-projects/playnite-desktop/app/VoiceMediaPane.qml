import QtQuick
import QtWebEngine
import QtWebChannel

Item {
    // This component is loaded ONLY by an explicit voice action.
    QtObject {
        id: endpoint
        WebChannel.id: "voiceHost"
        property string nonce: xboxVoiceMedia ? xboxVoiceMedia.nonce : ""
        signal command(string payload)
        signal reply(string payload)
        function receive(payload) { xboxVoiceMedia.receive(payload) }
    }
    Connections {
        target: xboxVoiceMedia
        function onCommand(payload) { endpoint.command(payload) }
        function onReply(payload) { endpoint.reply(payload) }
    }
    WebChannel { id: channel; registeredObjects: [endpoint] }
    WebEngineView {
        id: media
        anchors.fill: parent
        url: xboxVoiceMedia ? xboxVoiceMedia.pageUrl : "about:blank"
        webChannel: channel
        backgroundColor: "#111111"
        profile: WebEngineProfile { offTheRecord: true; httpCacheType: WebEngineProfile.MemoryHttpCache; persistentPermissionsPolicy: WebEngineProfile.AskEveryTime }
        settings.localContentCanAccessFileUrls: true
        settings.localContentCanAccessRemoteUrls: false
        settings.playbackRequiresUserGesture: false
        settings.javascriptCanOpenWindows: false
        settings.javascriptCanAccessClipboard: false
        onNavigationRequested: request => {
            if (request.url.toString() !== xboxVoiceMedia.pageUrl.toString()) request.action = WebEngineNavigationRequest.IgnoreRequest
        }
        onLoadingChanged: request => {
            if (request.status === WebEngineView.LoadFailedStatus && xboxVoiceMedia) xboxVoiceMedia.mediaFailed()
        }
        onRenderProcessTerminated: (terminationStatus, exitCode) => {
            if (xboxVoiceMedia) xboxVoiceMedia.mediaFailed()
        }
        onNewWindowRequested: request => { /* Never open external windows. */ }
        onPermissionRequested: permission => {
            if (permission.permissionType === WebEnginePermission.MediaAudioCapture && xboxVoiceMedia.allowMicrophone(permission.origin)) permission.grant()
            else permission.deny()
        }
        onJavaScriptConsoleMessage: (level, message, lineNumber, sourceID) => { /* Do not log SDP/ICE. */ }
    }
}
