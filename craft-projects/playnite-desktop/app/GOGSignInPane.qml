import QtQuick
import QtWebEngine
WebEngineView {
    id: browser
    property var service: typeof storeAccounts !== "undefined" ? storeAccounts : null
    url: service ? service.gogLoginUrl : "about:blank"
    backgroundColor: "#171719"
    profile: WebEngineProfile { offTheRecord: true; httpCacheType: WebEngineProfile.MemoryHttpCache }
    settings.localContentCanAccessFileUrls: false
    settings.localContentCanAccessRemoteUrls: false
    settings.javascriptCanOpenWindows: true
    settings.javascriptCanAccessClipboard: false
    onNavigationRequested: request => {
        const next = request.url.toString()
        if (service && next.startsWith("https://embed.gog.com/on_login_success?")) {
            service.acceptGogCallback(next)
            request.action = WebEngineNavigationRequest.IgnoreRequest
        } else if (!next.startsWith("https://")) request.action = WebEngineNavigationRequest.IgnoreRequest
    }
    onNewWindowRequested: request => {
        if (request.requestedUrl.toString().startsWith("https://")) browser.url = request.requestedUrl
    }
    onPermissionRequested: permission => permission.deny()
    onJavaScriptConsoleMessage: (level, message, lineNumber, sourceID) => { /* Never log authentication pages. */ }
}
