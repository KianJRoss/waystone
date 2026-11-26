import QtQuick
import QtWebEngine
WebEngineView {
    id: browser
    property var service: typeof storeAccounts !== "undefined" ? storeAccounts : null
    url: service ? service.loginUrl : "about:blank"
    backgroundColor: "#171719"
    profile: WebEngineProfile { offTheRecord: true; httpCacheType: WebEngineProfile.MemoryHttpCache }
    settings.localContentCanAccessFileUrls: false
    settings.localContentCanAccessRemoteUrls: false
    settings.javascriptCanOpenWindows: true
    settings.javascriptCanAccessClipboard: false
    onNavigationRequested: request => {
        if (!request.url.toString().startsWith("https://")) request.action = WebEngineNavigationRequest.IgnoreRequest
    }
    onNewWindowRequested: request => {
        if (request.requestedUrl.toString().startsWith("https://")) browser.url = request.requestedUrl
    }
    onPermissionRequested: permission => permission.deny()
    onJavaScriptConsoleMessage: (level, message, lineNumber, sourceID) => { /* Never log authentication pages. */ }
    onLoadingChanged: request => {
        if (request.status === WebEngineView.LoadSucceededStatus && service) {
            const pageUrl = browser.url.toString()
            // Native receiver validates exact HTTPS origin/path/client before accepting anything.
            if (pageUrl.startsWith("https://www.epicgames.com/id/api/redirect?"))
                browser.runJavaScript("document.body.innerText", function(body) { service.acceptEpicPage(pageUrl, String(body)) })
        }
    }
}
