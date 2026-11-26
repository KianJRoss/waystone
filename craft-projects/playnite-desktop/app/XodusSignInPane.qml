import QtQuick
import QtWebEngine
import QtWebChannel
WebEngineView {
    id: browser
    property var service: typeof storeAccounts !== "undefined" ? storeAccounts : null
    property int authSession: 0
    property bool payloadSent: false
    backgroundColor: "#171719"
    profile: WebEngineProfile { id: hostedProfile }
    webChannel: WebChannel { id: authChannel }
    Component.onCompleted: {
        if (!service || !service.xboxAuthService) return
        service.xboxAuthService.attachProfile(hostedProfile)
        authChannel.registerObject("xodusBridge", service.xboxAuthService.webBridge)
        browser.userScripts.insert(WebEngine.script({
            name: "waystone-xodus-ipc",
            injectionPoint: WebEngineScript.DocumentCreation,
            worldId: WebEngineScript.MainWorld,
            runsOnSubFrames: false,
            sourceCode: "(() => {
 const pending = [];
 let post = message => pending.push(String(message));
 Object.defineProperty(window, 'external', { configurable:false, enumerable:true, writable:false, value:Object.freeze({notify:message=>post(message)}) });
 const connect = () => {
  const script = document.createElement('script');
  script.src = 'qrc:///qtwebchannel/qwebchannel.js';
  script.onload = () => new QWebChannel(qt.webChannelTransport, channel => {
   const bridge = channel.objects.xodusBridge;
   post = message => bridge.notify(String(message));
   pending.splice(0).forEach(post);
  });
  (document.head || document.documentElement).appendChild(script);
 };
 if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', connect, {once:true}); else connect();
})();"
        }))
    }
    Component.onDestruction: {
        if (service && service.xboxAuthService)
            service.xboxAuthService.detachProfile(hostedProfile)
    }
    settings.localContentCanAccessFileUrls: false
    settings.localContentCanAccessRemoteUrls: false
    settings.javascriptCanOpenWindows: false
    settings.javascriptCanAccessClipboard: false
    onNavigationRequested: request => {
        if (!service || !service.xboxAuthService || !service.xboxAuthService.trustedAuthUrl(request.url.toString()))
            request.action = WebEngineNavigationRequest.IgnoreRequest
    }
    onLoadingChanged: request => {
        if (!service || request.status !== WebEngineView.LoadSucceededStatus) return
        const current = browser.url.toString()
        if (current.startsWith("https://login.live.com/ppsecure/post.srf") && !payloadSent) {
            payloadSent = true
            browser.runJavaScript("if (globalThis.ServerData && window.external && window.external.notify) window.external.notify(JSON.stringify(globalThis.ServerData))")
        }
    }
    onPermissionRequested: permission => permission.deny()
    onJavaScriptConsoleMessage: (level, message, lineNumber, sourceID) => { /* Never log Microsoft auth pages. */ }
    Connections {
        target: browser.service ? browser.service.xboxAuthService : null
        function onPageRequested(session, url) {
            browser.authSession = session
            browser.payloadSent = false
            browser.url = url
        }
        function onPageClosed(session) {
            if (browser.authSession === session) {
                browser.stop()
                browser.url = "about:blank"
            }
        }
        function onContextRequested(session, context) {
            if (browser.authSession !== session) return
            const payload = JSON.stringify({type:"callback",value:{name:"CloudExperienceHost.getContext",
                args:["CloudExperienceHost","TokenBroker","TokenBroker",
                "{\"PrivatePropertyBag\":1,\"PasswordlessConnect\":1,\"PreferAssociate\":1,\"ChromelessUI\":0}"],context:context}})
            browser.runJavaScript('globalThis["CloudExperienceHost.Bridge.dispatchMessage"](' +
                JSON.stringify(payload) + ')')
        }
    }
}
