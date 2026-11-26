import QtQuick
import QtWebEngine
WebEngineView {
    id: browser
    property var service: typeof storeAccounts !== "undefined" ? storeAccounts : null
    url: service ? service.amazonLoginUrl : "about:blank"
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
    onJavaScriptConsoleMessage: (level, message, lineNumber, sourceID) => { /* Do not log account pages. */ }
    onUrlChanged: {
        if (service && browser.url.toString().indexOf("openid.oa2.authorization_code=") >= 0)
            service.acceptAmazonCallback(browser.url.toString())
    }
}
