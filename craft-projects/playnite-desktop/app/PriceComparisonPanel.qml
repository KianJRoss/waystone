import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ColumnLayout {
    id: panel
    objectName: "priceComparisonPanel"
    property var service: typeof priceComparison !== "undefined" ? priceComparison : null
    property string gameTitle: ""
    Layout.fillWidth: true
    visible: gameTitle.length > 0
    spacing: 6
    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Text {
            text: "Compare store prices"
            color: Theme.text
            font.bold: true
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            elide: Text.ElideRight
        }
        CompactButton {
            objectName: "compareStorePrices"
            text: service && service.busy ? "Checking…" : "Check prices"
            enabled: !!service && !service.busy
            hint: "Search current store offers from IsThereAnyDeal"
            onClicked: service.compareTitle(panel.gameTitle)
        }
        CompactButton {
            objectName: "refreshStorePrices"
            text: "Refresh"
            enabled: !!service && !service.busy
            hint: "Force a fresh ITAD lookup instead of using this session's 15-minute cache"
            onClicked: service.refreshPrices(panel.gameTitle)
        }
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Text {
            text: "Price region"
            color: Theme.muted
            Layout.minimumWidth: 0
        }
        ComboBox {
            objectName: "priceMarketSelector"
            Layout.preferredWidth: 120
            model: service ? service.marketOptions : []
            textRole: "name"
            currentIndex: {
                if (!service) return 0
                var options = service.marketOptions
                for (var i = 0; i < options.length; ++i) if (options[i].code === service.marketCountry) return i
                return 0
            }
            enabled: !!service && !service.busy
            onActivated: function(index) {
                if (panel.service) panel.service.setMarketCountry(panel.service.marketOptions[index].code)
            }
        }
        CheckBox {
            objectName: "includeVoucherPrices"
            text: "Include voucher prices"
            checked: !!service && service.includeVouchers
            enabled: !!service && !service.busy
            onToggled: if (panel.service) panel.service.setIncludeVouchers(checked)
        }
    }
    Text {
        objectName: "priceComparisonStatus"
        Layout.fillWidth: true
        visible: !!service
        text: service ? service.message : "Price comparison service unavailable"
        color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap
        font.pixelSize: 11
    }
    Repeater {
        model: service ? service.candidates : []
        delegate: RowLayout {
            required property var modelData
            Layout.fillWidth: true
            spacing: 6
            Text {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: modelData.title || "Unknown title"
                color: Theme.text
                textFormat: Text.PlainText
                elide: Text.ElideRight
            }
            CompactButton {
                text: "Select"
                enabled: !!panel.service && !panel.service.busy
                onClicked: if (panel.service) panel.service.selectCandidate(modelData.id)
            }
            CompactButton {
                text: "Refresh"
                enabled: !!panel.service && !panel.service.busy
                hint: "Refresh this exact ITAD game ID"
                onClicked: if (panel.service) panel.service.refreshCandidate(modelData.id)
            }
        }
    }
    ColumnLayout {
        Layout.fillWidth: true
        visible: !!service && service.lowestOffers.length > 0
        spacing: 4
        Text { text: "Lowest current offers"; color: Theme.text; font.bold: true }
        Repeater {
            model: service ? service.lowestOffers : []
            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: 6
                Text {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: {
                        var d = modelData.deal || {}, p = d.price || {}, shop = d.shop || {}, low = modelData.history_low || {}
                        var best = (shop.name || "Store") + " · " + (p.amount !== undefined ? p.amount : "?") + " " + modelData.currency
                        return best + (low.amount !== undefined ? " · all-time low " + low.amount + " " + low.currency : "")
                    }
                    color: Theme.text; textFormat: Text.PlainText; elide: Text.ElideRight
                }
                CompactButton {
                    text: "Best offer"
                    enabled: !!(modelData.deal && modelData.deal.url)
                    hint: "Open the source-provided offer URL; verify final price, edition and region"
                    onClicked: if (panel.service && modelData.deal && modelData.deal.url) panel.service.openOfferUrl(modelData.deal.url)
                }
            }
        }
    }
    Rectangle {
        objectName: "panelCdKeyDisclaimerBanner"
        visible: !!service && service.hasCdKeyOffers
        Layout.fillWidth: true
        implicitHeight: panelCdKeyText.implicitHeight + 14
        radius: 4
        color: "#2b1d07"
        border.color: "#d97706"
        border.width: 1
        Text {
            id: panelCdKeyText
            anchors.fill: parent
            anchors.margins: 7
            text: service ? service.cdKeyDisclaimer : ""
            color: "#fcd34d"
            font.pixelSize: 10
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
        }
    }
    Repeater {
        model: service ? service.comparisonOffers : []
        delegate: RowLayout {
            required property var modelData
            Layout.fillWidth: true
            spacing: 6
            Text {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: (modelData.shopName || "Store") + " · "
                      + (modelData.amount !== undefined ? modelData.amount : "?")
                      + " " + (modelData.currency || "")
                      + " [" + (modelData.cdKeyBadge || "STORE") + " · " + (modelData.sourceOrigin || "ITAD API") + "]"
                color: modelData.isCdKeySite ? "#fcd34d" : Theme.text; textFormat: Text.PlainText; elide: Text.ElideRight
            }
            CompactButton {
                text: "View offer"
                enabled: !!modelData.url
                hint: modelData.isCdKeySite ? (modelData.cdKeyDisclaimer || "Third-party CD-key seller; verify region and DRM") : "Open this direct store offer; confirm price, edition, region, and eligibility at checkout"
                onClicked: if (panel.service && modelData.url) panel.service.openOfferUrl(modelData.url)
            }
        }
    }
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4
        Text { text: "Check a public store page"; color: Theme.text; font.bold: true }
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            TextField {
                id: publicOfferUrl
                objectName: "publicOfferUrl"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                placeholderText: "https://approved-store.example/game"
                inputMethodHints: Qt.ImhUrlCharactersOnly
            }
            CompactButton {
                objectName: "scrapePublicOfferPage"
                text: "Read page"
                enabled: !!service && !service.busy && service.scrapeAvailable && publicOfferUrl.text.trim().length > 0
                hint: "Fetch one unauthenticated public page only; terms and robots.txt are respected"
                onClicked: service.scrapePublicPage(publicOfferUrl.text.trim(), panel.gameTitle)
            }
        }
        Text {
            Layout.fillWidth: true
            visible: !!service && service.scrapedTitle.length > 0
            text: service && service.scrapedTitle.length > 0 && service.scrapedTitle.trim().toLowerCase() !== panel.gameTitle.trim().toLowerCase()
                  ? "Warning: scraped page title differs from the selected game. Do not treat this as a matched offer."
                  : "Scraped title: " + (service ? service.scrapedTitle : "")
            color: service && service.scrapedTitle.trim().toLowerCase() !== panel.gameTitle.trim().toLowerCase() ? "#ff7a7a" : Theme.muted
            textFormat: Text.PlainText; wrapMode: Text.WordWrap
        }
        Repeater {
            model: service ? service.scrapedOffers : []
            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: 6
                Text {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: (modelData.seller || modelData.source_id || "Unknown seller")
                          + " · " + modelData.price + " " + modelData.currency
                          + " · " + (modelData.verification || "unverified")
                    color: Theme.text; textFormat: Text.PlainText; elide: Text.ElideRight
                }
                CompactButton {
                    text: "Open page"
                    enabled: !!modelData.product_page_url
                    hint: "Open the page and verify price, seller, edition, and region before purchase"
                    onClicked: if (panel.service && modelData.product_page_url) panel.service.openOfferUrl(modelData.product_page_url)
                }
            }
        }
        Text {
            visible: !!service && service.scrapedOffers.length > 0
            text: "Unverified public-page data. Store class, seller legitimacy, edition, and region are not inferred. Verify at checkout."
            color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap; font.pixelSize: 10
        }
    }
    Text {
        visible: !!service && service.offers.length > 0
        text: "Prices and retailer links are supplied by IsThereAnyDeal. Verify final price, edition, region, and availability at the store."
        color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap; font.pixelSize: 10
    }
}
