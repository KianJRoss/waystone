import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ColumnLayout {
    id: panel
    property var service: typeof storeAccounts !== "undefined" ? storeAccounts : null
    property var priceService: typeof priceComparison !== "undefined" ? priceComparison : null
    property var steamLibrary: typeof steamLibrarySettings !== "undefined" ? steamLibrarySettings : null
    Layout.fillWidth: true
    spacing: 10
    Text { text: "Steam library"; color: Theme.text; font.bold: true; font.pixelSize: 18 }
    Text { Layout.fillWidth: true; text: "Optional: add your Steam Web API key and SteamID64/profile URL/vanity name to show owned games that are not installed here. Waystone verifies the list, then keeps the key only in your desktop keyring. Your Steam game-detail privacy must allow the API to return your library. Without a key, installed-game detection still works."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    RowLayout {
        Layout.fillWidth: true
        TextField {
            id: steamProfile; objectName: "steamLibraryProfile"
            Layout.fillWidth: true
            placeholderText: steamLibrary && steamLibrary.configured ? "Saved SteamID64: " + steamLibrary.steamId : "SteamID64, steamcommunity.com profile URL, or vanity name"
            color: Theme.text; background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: 5 }
        }
        TextField {
            id: steamLibraryKey; objectName: "steamLibraryApiKey"
            Layout.fillWidth: true; echoMode: TextInput.Password; maximumLength: 32; passwordMaskDelay: 0
            placeholderText: "Existing Steam Web API key"; color: Theme.text
            background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: 5 }
        }
        SocialButton {
            objectName: "saveSteamLibraryAccount"; text: steamLibrary && steamLibrary.busy ? "Checking…" : "Save & check"
            enabled: steamLibrary && !steamLibrary.busy && steamProfile.text.trim().length > 0 && steamLibraryKey.text.length === 32
            onClicked: { if (steamLibrary.saveAccount(steamProfile.text.trim(), steamLibraryKey.text.trim())) steamLibraryKey.clear() }
        }
        SocialButton { objectName: "forgetSteamLibraryAccount"; text: "Forget"; enabled: steamLibrary && steamLibrary.configured && !steamLibrary.busy; onClicked: steamLibrary.forget() }
    }
    Text { objectName: "steamLibraryStatus"; Layout.fillWidth: true; text: steamLibrary ? steamLibrary.message : "Steam library account settings unavailable"; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
    Text { text: "Epic Games"; color: Theme.text; font.bold: true; font.pixelSize: 18 }
    Text { Layout.fillWidth: true; text: panel.service ? panel.service.message : "Store account service unavailable"; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    Text { Layout.fillWidth: true; visible: !!text; text: panel.service && panel.service.epicAccount.display_name ? panel.service.epicAccount.display_name + " · " + panel.service.epicAccount.status : ""; color: Theme.text; textFormat: Text.PlainText }
    RowLayout {
        SocialButton { objectName: "epicSignIn"; text: "Sign in to Epic"; enabled: panel.service && panel.service.available && !panel.service.busy; onClicked: panel.service.beginEpicSignIn() }
        SocialButton { objectName: "epicRefresh"; text: "Refresh Epic library"; enabled: panel.service && panel.service.available && !panel.service.busy && !!panel.service.epicAccount.account_id; onClicked: panel.service.refreshEpic() }
        SocialButton { objectName: "epicSignOut"; text: "Sign out"; enabled: panel.service && panel.service.available && !panel.service.busy && !!panel.service.epicAccount.account_id; onClicked: panel.service.signOutEpic() }
    }
    SocialButton { text: "Cancel sign-in"; visible: panel.service && panel.service.signingIn; onClicked: panel.service.cancelSignIn() }
    Loader { objectName: "epicBrowserLoader"; Layout.fillWidth: true; Layout.preferredHeight: active ? 580 : 0; active: panel.service && panel.service.signingIn; source: active ? "EpicSignInPane.qml" : "" }
    Text { visible: panel.service && panel.service.gameCount > 0; text: panel.service ? panel.service.gameCount + " games available from your Epic account" : ""; color: Theme.muted }
    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
    Text { text: "GOG"; color: Theme.text; font.bold: true; font.pixelSize: 18 }
    Text { Layout.fillWidth: true; text: panel.service ? panel.service.gogMessage : "GOG account service unavailable"; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    Text { Layout.fillWidth: true; visible: panel.service && !!panel.service.gogAccount.account_id; text: panel.service && panel.service.gogAccount.display_name ? panel.service.gogAccount.display_name + " · connected to Waystone" : ""; color: Theme.text; textFormat: Text.PlainText }
    RowLayout {
        SocialButton { objectName: "gogSignIn"; text: "Sign in to GOG"; enabled: panel.service && panel.service.gogAvailable && !panel.service.busy; onClicked: panel.service.beginGogSignIn() }
        SocialButton { objectName: "gogRefresh"; text: "Refresh GOG library"; enabled: panel.service && panel.service.gogAvailable && !panel.service.busy && !!panel.service.gogAccount.account_id; onClicked: panel.service.refreshGogLibrary() }
        SocialButton { objectName: "gogSignOut"; text: "Sign out"; enabled: panel.service && panel.service.gogAvailable && !panel.service.busy && !!panel.service.gogAccount.account_id; onClicked: panel.service.signOutGog() }
    }
    SocialButton { text: "Cancel GOG sign-in"; visible: panel.service && panel.service.gogSigningIn; onClicked: panel.service.cancelSignIn() }
    Loader { objectName: "gogBrowserLoader"; Layout.fillWidth: true; Layout.preferredHeight: active ? 580 : 0; active: panel.service && panel.service.gogSigningIn; source: active ? "GOGSignInPane.qml" : "" }
    Text { visible: panel.service && panel.service.gogGameCount > 0; text: panel.service ? panel.service.gogGameCount + " owned games available from your GOG account" : ""; color: Theme.muted }
    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
    Text { text: "Amazon Games"; color: Theme.text; font.bold: true; font.pixelSize: 18 }
    Text { Layout.fillWidth: true; text: panel.service ? panel.service.amazonMessage : "Amazon account service unavailable"; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    Text { Layout.fillWidth: true; visible: panel.service && !!panel.service.amazonAccount.account_id; text: panel.service && panel.service.amazonAccount.display_name ? panel.service.amazonAccount.display_name + " · connected to Waystone" : ""; color: Theme.text; textFormat: Text.PlainText }
    RowLayout {
        SocialButton { objectName: "amazonSignIn"; text: "Sign in to Amazon"; enabled: panel.service && panel.service.amazonAvailable && !panel.service.busy; onClicked: panel.service.beginAmazonSignIn() }
        SocialButton { objectName: "amazonRefresh"; text: "Refresh Amazon library"; visible: panel.service && !!panel.service.amazonAccount.account_id; enabled: panel.service && panel.service.amazonAvailable && !panel.service.busy; onClicked: panel.service.refreshAmazonLibrary() }
        SocialButton { objectName: "amazonSignOut"; text: "Sign out"; enabled: panel.service && panel.service.amazonAvailable && !panel.service.busy && !!panel.service.amazonAccount.account_id; onClicked: panel.service.signOutAmazon() }
    }
    SocialButton { text: "Cancel Amazon sign-in"; visible: panel.service && panel.service.amazonSigningIn; onClicked: panel.service.cancelSignIn() }
    Loader { objectName: "amazonBrowserLoader"; Layout.fillWidth: true; Layout.preferredHeight: active ? 580 : 0; active: panel.service && panel.service.amazonSigningIn; source: active ? "NileSignInPane.qml" : "" }

    Text { text: "Xbox / Microsoft Store"; color: Theme.text; font.bold: true; font.pixelSize: 18 }
    Text { Layout.fillWidth: true; text: panel.service ? panel.service.xboxMessage : "Xodus backend unavailable"; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    RowLayout {
        SocialButton { objectName: "xboxConnect"; text: "Connect Xbox account"; enabled: panel.service && panel.service.xboxAvailable && !panel.service.xboxSigningIn; onClicked: panel.service.beginXboxSignIn() }
        SocialButton { objectName: "xboxRefresh"; text: "Refresh Xbox library"; enabled: panel.service && panel.service.xboxAvailable && !panel.service.xboxSigningIn; onClicked: panel.service.refreshXboxLibrary() }
        SocialButton { objectName: "xboxCancel"; text: "Cancel"; visible: panel.service && panel.service.xboxSigningIn; onClicked: panel.service.xboxAuthService.cancel() }
    }
    Loader {
        objectName: "xboxBrowserLoader"
        Layout.fillWidth: true
        Layout.preferredHeight: active ? 580 : 0
        active: panel.service && panel.service.xboxSigningIn
        source: active ? "XodusSignInPane.qml" : ""
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
    Text { text: "Store price comparison (personal use)"; color: Theme.text; font.bold: true; font.pixelSize: 18 }
    Text { Layout.fillWidth: true; text: "Connect your IsThereAnyDeal API key. It is stored in the desktop keyring; it is never written to Waystone files or logs."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    RowLayout {
        Layout.fillWidth: true
        TextField { id: itadApiKey; objectName: "itadApiKey"; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: priceService && priceService.keyConfigured ? "API key saved in desktop keyring" : "ITAD key (or check prices to use a saved key)"; passwordMaskDelay: 0 }
        SocialButton { objectName: "saveItadApiKey"; text: "Save key"; enabled: priceService && !priceService.busy && itadApiKey.text.trim().length >= 8; onClicked: { if (priceService.saveApiKey(itadApiKey.text.trim())) itadApiKey.clear() } }
        SocialButton { objectName: "clearItadApiKey"; text: "Forget"; enabled: priceService && priceService.keyConfigured && !priceService.busy; onClicked: priceService.clearApiKey() }
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
    Text { text: "Public product-page scraper"; color: Theme.text; font.bold: true; font.pixelSize: 18 }
    Text { Layout.fillWidth: true; text: "Waystone can read one public product page at a time, without account cookies. It checks robots.txt, refuses redirects, and rate-limits requests. Only enable stores whose terms allow this."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    RowLayout {
        Layout.fillWidth: true
        TextField { id: scraperHost; objectName: "scraperHost"; Layout.fillWidth: true; placeholderText: "store.example (hostname only)" }
        SocialButton { objectName: "enableScraperHost"; text: "Enable host"; enabled: panel.priceService && scraperHost.text.trim().length > 0; onClicked: { if (panel.priceService.addScraperHost(scraperHost.text, scraperTerms.checked)) scraperHost.clear() } }
    }
    CheckBox { id: scraperTerms; objectName: "scraperTermsConfirmed"; leftPadding: 24; text: "I reviewed this retailer's terms and robots.txt and permit public-page checks"; checked: false }
    Repeater {
        model: panel.priceService ? panel.priceService.scraperHosts : []
        delegate: RowLayout {
            required property string modelData
            Layout.fillWidth: true
            Text { Layout.fillWidth: true; text: "Enabled: " + modelData; color: Theme.muted; textFormat: Text.PlainText; elide: Text.ElideMiddle }
            SocialButton {
                objectName: "removeScraperHost"
                text: "Remove"
                enabled: panel.priceService && !panel.priceService.busy
                onClicked: panel.priceService.removeScraperHost(modelData)
            }
        }
    }
    Text { Layout.fillWidth: true; text: panel.priceService ? panel.priceService.message : "Price comparison service unavailable"; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
    Text { text: "Ribbon artwork (SteamGridDB)"; color: Theme.text; font.bold: true; font.pixelSize: 18 }
    Text { Layout.fillWidth: true; text: "Optional fallback for missing hero banners and transparent logos. Get your personal key from SteamGridDB Preferences → API. It is stored only in the desktop keyring; Waystone searches only games currently shown and silently skips this source when no key is saved."; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    RowLayout {
        Layout.fillWidth: true
        TextField { id: sgdbApiKey; objectName: "steamGridDbApiKey"; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: typeof ribbonArtworkSettings !== "undefined" && ribbonArtworkSettings && ribbonArtworkSettings.keyConfigured ? "Key saved in desktop keyring" : "Optional SteamGridDB API key"; passwordMaskDelay: 0 }
        SocialButton { objectName: "saveSteamGridDbApiKey"; text: "Save key"; enabled: typeof ribbonArtworkSettings !== "undefined" && ribbonArtworkSettings && sgdbApiKey.text.trim().length >= 8; onClicked: { if (ribbonArtworkSettings.saveApiKey(sgdbApiKey.text.trim())) sgdbApiKey.clear() } }
        SocialButton { objectName: "checkSteamGridDbApiKey"; text: "Check saved key"; enabled: typeof ribbonArtworkSettings !== "undefined" && !!ribbonArtworkSettings; onClicked: ribbonArtworkSettings.checkKeyring() }
        SocialButton { objectName: "clearSteamGridDbApiKey"; text: "Forget"; enabled: typeof ribbonArtworkSettings !== "undefined" && ribbonArtworkSettings && ribbonArtworkSettings.keyConfigured; onClicked: ribbonArtworkSettings.clearApiKey() }
    }
    Text { Layout.fillWidth: true; text: typeof ribbonArtworkSettings !== "undefined" && ribbonArtworkSettings ? ribbonArtworkSettings.message : "Optional artwork settings unavailable"; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
}
