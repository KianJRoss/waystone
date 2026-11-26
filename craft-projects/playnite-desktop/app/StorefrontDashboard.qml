import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

WorkspacePage {
    id: page
    objectName: "storefrontWorkspacePage"
    property var viewModel: typeof priceComparison !== "undefined" ? priceComparison : null
    property string selectedGameId: ""
    property string selectedCardTitle: ""
    property string selectedCardCover: ""
    property string selectedCardShop: ""
    property string selectedCardBadge: ""
    property int coverWidth: 136
    title: "Store & Deals"
    pageId: "store"
    padding: 10
    signal accountsRequested()

    function initStoreFeed() {
        if (!page.visible || !page.viewModel) return
        page.viewModel.loadStoreCatalog(page.viewModel.storeSort || "-trending", false)
        var items = page.viewModel.storeCatalogItems || []
        if (!page.selectedGameId && items.length > 0 && !(typeof headlessCheck !== "undefined" && headlessCheck)) {
            var first = items[0]
            selectStoreCard(first)
        }
    }

    function selectStoreCard(item) {
        if (!item) return
        page.selectedGameId = item.gameId || ""
        page.selectedCardTitle = item.title || ""
        page.selectedCardCover = item.coverUrl || ""
        page.selectedCardShop = item.shopName || ""
        page.selectedCardBadge = item.priceBadge || ""
        if (page.viewModel) {
            page.viewModel.selectStoreGame(item.gameId || "", item.title || "")
        }
    }

    onVisibleChanged: if (visible) initStoreFeed()
    Component.onCompleted: if (visible) initStoreFeed()

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 50
            color: Theme.panel
            radius: Theme.cornerRadius
            border.color: Theme.line
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                spacing: 8
                CompactButton { objectName: "storeBack"; text: "‹ Back"; onClicked: page.backRequested() }
                Text { text: "Store Browser"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                TextField {
                    id: gameSearch
                    objectName: "storeGameSearch"
                    Layout.preferredWidth: 260
                    Layout.fillWidth: true
                    Layout.minimumWidth: 100
                    implicitHeight: 32
                    placeholderText: "Filter store games or search any title…"
                    color: Theme.text
                    placeholderTextColor: Theme.muted
                    selectByMouse: true
                    background: Rectangle { color: Theme.background; radius: Theme.cornerRadius; border.color: gameSearch.activeFocus ? Theme.text : Theme.line }
                    onTextEdited: if (page.viewModel) page.viewModel.setStoreFilter(text)
                    onAccepted: if (page.viewModel && text.trim().length) page.viewModel.compareTitle(text.trim())
                }
                CompactButton {
                    objectName: "storeSearchButton"
                    text: page.viewModel && page.viewModel.busy ? "Searching…" : "Find prices"
                    enabled: !!page.viewModel && !page.viewModel.busy && gameSearch.text.trim().length > 0
                    onClicked: page.viewModel.compareTitle(gameSearch.text.trim())
                }
                CompactButton {
                    objectName: "storeRefreshButton"
                    text: "Refresh"
                    enabled: !!page.viewModel && !page.viewModel.busy && (gameSearch.text.trim().length > 0 || page.selectedCardTitle.length > 0)
                    hint: "Bypass the short in-memory price cache"
                    onClicked: page.viewModel.refreshPrices(gameSearch.text.trim().length > 0 ? gameSearch.text.trim() : page.selectedCardTitle)
                }
                Text { text: "Market"; color: Theme.muted; font.pixelSize: 12 }
                ComboBox {
                    objectName: "storeMarketSelector"
                    implicitWidth: 135
                    model: page.viewModel ? page.viewModel.marketOptions : []
                    textRole: "name"
                    currentIndex: {
                        if (!page.viewModel) return 0
                        var options = page.viewModel.marketOptions
                        for (var i = 0; i < options.length; ++i) if (options[i].code === page.viewModel.marketCountry) return i
                        return 0
                    }
                    enabled: !!page.viewModel && !page.viewModel.busy
                    onActivated: function(index) { if (page.viewModel) page.viewModel.setMarketCountry(page.viewModel.marketOptions[index].code) }
                }
                CheckBox {
                    objectName: "storeVoucherToggle"
                    text: "Vouchers"
                    leftPadding: 24
                    checked: !!page.viewModel && page.viewModel.includeVouchers
                    enabled: !!page.viewModel && !page.viewModel.busy
                    onToggled: if (page.viewModel) page.viewModel.setIncludeVouchers(checked)
                }
                CompactButton {
                    objectName: "storeAccountsLink"
                    text: "Accounts / API key"
                    hint: "Open platform connections and Store Accounts"
                    onClicked: page.accountsRequested()
                }
            }
        }

        SplitView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal
            handle: Rectangle { implicitWidth: 5; color: SplitHandle.hovered || SplitHandle.pressed ? Theme.hover : Theme.main }

            Rectangle {
                SplitView.fillWidth: true
                SplitView.minimumWidth: 420
                color: Theme.background
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 8
                        Layout.rightMargin: 8
                        spacing: 6
                        CompactButton {
                            text: "Trending"
                            selected: page.viewModel && page.viewModel.storeSort === "-trending"
                            onClicked: if (page.viewModel) page.viewModel.loadStoreCatalog("-trending", false)
                        }
                        CompactButton {
                            text: "Biggest Discounts"
                            selected: page.viewModel && page.viewModel.storeSort === "-cut"
                            onClicked: if (page.viewModel) page.viewModel.loadStoreCatalog("-cut", false)
                        }
                        CompactButton {
                            text: "Hot Sellers"
                            selected: page.viewModel && page.viewModel.storeSort === "-hot"
                            onClicked: if (page.viewModel) page.viewModel.loadStoreCatalog("-hot", false)
                        }
                        CompactButton {
                            text: "New & Recent"
                            selected: page.viewModel && page.viewModel.storeSort === "-time"
                            onClicked: if (page.viewModel) page.viewModel.loadStoreCatalog("-time", false)
                        }
                        CompactButton {
                            objectName: "storeSecondaryCatalogTab"
                            visible: !!(page.viewModel && page.viewModel.secondaryTabAvailable)
                            text: page.viewModel ? page.viewModel.secondaryTabLabel : ""
                            selected: !!(page.viewModel && page.viewModel.secondaryTabKey && page.viewModel.storeSort === page.viewModel.secondaryTabKey)
                            hint: page.viewModel ? page.viewModel.secondaryTabHint : ""
                            onClicked: if (page.viewModel && page.viewModel.secondaryTabKey) page.viewModel.loadStoreCatalog(page.viewModel.secondaryTabKey, false)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 8
                        Layout.rightMargin: 8
                        spacing: 6
                        CheckBox {
                            objectName: "storeExcludeOwnedToggle"
                            text: "Hide bought (" + (page.viewModel ? page.viewModel.ownedLibraryCount : 0) + ")"
                            leftPadding: 24
                            checked: !page.viewModel || page.viewModel.excludeOwned
                            onToggled: if (page.viewModel) page.viewModel.setExcludeOwned(checked)
                        }
                        CheckBox {
                            objectName: "storeExcludeSecondaryToggle"
                            visible: !!(page.viewModel && page.viewModel.secondaryTabAvailable)
                            text: (page.viewModel ? page.viewModel.secondaryFilterLabel : "") + " (" + (page.viewModel ? page.viewModel.secondaryLibraryCount : 0) + ")"
                            leftPadding: 24
                            checked: !page.viewModel || page.viewModel.excludeSecondary
                            enabled: !page.viewModel || !page.viewModel.secondaryTabKey || page.viewModel.storeSort !== page.viewModel.secondaryTabKey
                            onToggled: if (page.viewModel) page.viewModel.setExcludeSecondary(checked)
                        }
                        CheckBox {
                            objectName: "storeExcludeCdKeysToggle"
                            text: "Official stores only"
                            leftPadding: 24
                            checked: page.viewModel ? page.viewModel.excludeCdKeys : false
                            onToggled: if (page.viewModel) page.viewModel.setExcludeCdKeys(checked)
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: (page.viewModel ? page.viewModel.storeCatalogItems.length : 0) + " store games shown"
                            color: Theme.muted
                            font.pixelSize: 12
                        }
                        CompactButton {
                            objectName: "storeLoadMoreButton"
                            text: "Load more games"
                            enabled: !!page.viewModel
                            onClicked: if (page.viewModel) page.viewModel.loadStoreCatalog(page.viewModel.storeSort, true)
                        }
                    }

                    GridView {
                        id: storeGrid
                        objectName: "storeCatalogGrid"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.margins: 6
                        clip: true
                        model: page.viewModel ? page.viewModel.storeCatalogItems : []
                        cellWidth: page.coverWidth + 14
                        cellHeight: Math.round(page.coverWidth * 620 / 456) + 64
                        keyNavigationEnabled: true
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        delegate: Item {
                            id: cardDelegate
                            required property var modelData
                            width: storeGrid.cellWidth
                            height: storeGrid.cellHeight
                            property bool isSelected: page.selectedGameId === (modelData.gameId || "")

                            Rectangle {
                                anchors.fill: parent
                                anchors.margins: 3
                                color: cardDelegate.isSelected ? Theme.main : (cardMouse.containsMouse ? Theme.panel : "transparent")
                                radius: Theme.cornerRadius
                                border.width: cardDelegate.isSelected || cardMouse.containsMouse ? 1 : 0
                                border.color: cardDelegate.isSelected ? Theme.glyph : Theme.line
                            }
                            MissingCover {
                                objectName: "storeGridArtwork"
                                x: 7; y: 6
                                width: page.coverWidth
                                height: Math.round(page.coverWidth * 620 / 456)
                                artworkSource: modelData.coverUrl || ""
                                loadEnabled: storeGrid.visible && !(typeof headlessCheck !== "undefined" && headlessCheck) && cardDelegate.y + cardDelegate.height > storeGrid.contentY && cardDelegate.y < storeGrid.contentY + storeGrid.height
                            }
                            Rectangle {
                                visible: !!(modelData.priceBadge && modelData.priceBadge.length > 0)
                                anchors.right: parent.right
                                anchors.rightMargin: 10
                                y: 10
                                height: 22
                                width: Math.min(page.coverWidth - 8, badgeText.implicitWidth + 12)
                                radius: 4
                                color: "#1b4d2e"
                                border.color: "#43b581"
                                border.width: 1
                                Text {
                                    id: badgeText
                                    anchors.centerIn: parent
                                    width: parent.width - 8
                                    text: modelData.priceBadge || ""
                                    color: "#e8fff0"
                                    font.pixelSize: 11
                                    font.bold: true
                                    elide: Text.ElideRight
                                    horizontalAlignment: Text.AlignHCenter
                                    textFormat: Text.PlainText
                                }
                            }
                            Text {
                                x: 7
                                y: parent.height - 54
                                width: page.coverWidth
                                height: 17
                                text: modelData.title || ""
                                textFormat: Text.PlainText
                                color: Theme.text
                                font.family: Theme.family
                                font.pixelSize: 13
                                font.bold: true
                                elide: Text.ElideRight
                                horizontalAlignment: Text.AlignHCenter
                            }
                            Text {
                                x: 7
                                y: parent.height - 36
                                width: page.coverWidth
                                height: 16
                                text: (modelData.priceBadge || "") + (modelData.regularPrice ? ("  (Reg " + modelData.regularPrice + ")") : "")
                                textFormat: Text.PlainText
                                color: "#6ee7a8"
                                font.family: Theme.family
                                font.pixelSize: 11
                                font.bold: true
                                elide: Text.ElideRight
                                horizontalAlignment: Text.AlignHCenter
                            }
                            Text {
                                x: 7
                                y: parent.height - 19
                                width: page.coverWidth
                                height: 15
                                text: (modelData.shopName || "Store") + (modelData.historyLow ? (" · Low: " + modelData.historyLow) : "")
                                textFormat: Text.PlainText
                                color: Theme.muted
                                font.family: Theme.family
                                font.pixelSize: 10
                                elide: Text.ElideRight
                                horizontalAlignment: Text.AlignHCenter
                            }
                            MouseArea {
                                id: cardMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked: page.selectStoreCard(modelData)
                            }
                        }
                    }
                }
            }

            Rectangle {
                SplitView.preferredWidth: 420
                SplitView.minimumWidth: 320
                color: Theme.panel
                ScrollView {
                    id: storeResultsScroll
                    objectName: "storeResultsScrollView"
                    anchors.fill: parent
                    anchors.margins: 12
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    ColumnLayout {
                        objectName: "storeResultsColumn"
                        width: storeResultsScroll.availableWidth
                        spacing: 10

                        Text {
                            text: page.selectedCardTitle.length > 0 ? page.selectedCardTitle : (gameSearch.text.trim().length > 0 ? gameSearch.text.trim() : "Select a store game card to compare all offers")
                            color: Theme.text
                            font.pixelSize: 19
                            font.bold: true
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            textFormat: Text.PlainText
                        }
                        Text {
                            visible: page.selectedCardShop.length > 0
                            text: "Featured deal: " + page.selectedCardBadge + " on " + page.selectedCardShop
                            color: "#6ee7a8"
                            font.pixelSize: 13
                            font.bold: true
                            Layout.fillWidth: true
                            textFormat: Text.PlainText
                        }
                        Text {
                            objectName: "storeSearchStatus"
                            Layout.fillWidth: true
                            text: page.viewModel ? page.viewModel.message : "Store comparison is unavailable."
                            color: Theme.muted
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

                        Text {
                            visible: !!page.viewModel && page.viewModel.lowestOffers.length > 0
                            text: "Best current deal"
                            color: Theme.text
                            font.bold: true
                        }
                        Repeater {
                            model: page.viewModel ? page.viewModel.lowestOffers : []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: 54
                                radius: 6
                                color: "#153822"
                                border.color: "#43b581"
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2
                                        Text {
                                            Layout.fillWidth: true
                                            text: (modelData.deal.shop && modelData.deal.shop.name ? modelData.deal.shop.name : "Store")
                                                  + " · " + modelData.deal.price.amount + " " + modelData.currency
                                                  + (modelData.deal.cut ? (" (-" + modelData.deal.cut + "%)") : "")
                                            color: "#e8fff0"
                                            font.bold: true
                                            textFormat: Text.PlainText
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            visible: !!modelData.history_low && modelData.history_low.amount !== undefined
                                            text: modelData.history_low && modelData.history_low.amount !== undefined
                                                  ? ("Historical low: " + modelData.history_low.amount + " " + modelData.currency)
                                                  : ""
                                            color: Theme.muted
                                            font.pixelSize: 11
                                            textFormat: Text.PlainText
                                        }
                                    }
                                    CompactButton {
                                        text: "Open store"
                                        enabled: !!modelData.deal.url
                                        onClicked: if (page.viewModel) page.viewModel.openOfferUrl(modelData.deal.url)
                                    }
                                }
                            }
                        }

                        Rectangle {
                            objectName: "storeCdKeyDisclaimerBanner"
                            visible: !!page.viewModel && (page.viewModel.hasCdKeyOffers || page.viewModel.comparisonOffers.length > 0)
                            Layout.fillWidth: true
                            implicitHeight: cdKeyBannerText.implicitHeight + 16
                            radius: 6
                            color: "#2b1d07"
                            border.color: "#d97706"
                            border.width: 1
                            Text {
                                id: cdKeyBannerText
                                anchors.fill: parent
                                anchors.margins: 8
                                text: page.viewModel ? page.viewModel.cdKeyDisclaimer : ""
                                color: "#fcd34d"
                                font.pixelSize: 11
                                wrapMode: Text.WordWrap
                                textFormat: Text.PlainText
                            }
                        }

                        Text {
                            visible: !!page.viewModel && page.viewModel.comparisonOffers.length > 0
                            text: "All store & CD-key offers — ITAD API + Multi-Store Scraper (" + (page.viewModel ? page.viewModel.comparisonOffers.length : 0) + ")"
                            color: Theme.text
                            font.bold: true
                        }
                        Repeater {
                            model: page.viewModel ? page.viewModel.comparisonOffers : []
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: offerCol.implicitHeight + 14
                                radius: 4
                                color: Theme.background
                                border.width: modelData.isCdKeySite ? 1 : 0
                                border.color: modelData.storeCategory === "cdkey_marketplace" ? "#ef4444" : (modelData.isCdKeySite ? "#d97706" : Theme.line)
                                ColumnLayout {
                                    id: offerCol
                                    anchors.fill: parent
                                    anchors.margins: 7
                                    spacing: 3
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 6
                                        Text {
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            text: (modelData.shopName || "Store") + " · "
                                                  + (modelData.amount !== undefined ? modelData.amount : "?")
                                                  + " " + (modelData.currency || "")
                                                  + (modelData.cut ? (" (-" + modelData.cut + "%)") : "")
                                                  + (modelData.regularAmount !== undefined && modelData.regularAmount !== "" ? ("  [Reg: " + modelData.regularAmount + "]") : "")
                                            color: Theme.text
                                            font.bold: true
                                            textFormat: Text.PlainText
                                            elide: Text.ElideRight
                                        }
                                        Rectangle {
                                            radius: 3
                                            color: modelData.storeCategory === "cdkey_marketplace" ? "#451a1a" : (modelData.isCdKeySite ? "#3b270c" : "#143523")
                                            border.color: modelData.storeCategory === "cdkey_marketplace" ? "#f87171" : (modelData.isCdKeySite ? "#f59e0b" : "#43b581")
                                            border.width: 1
                                            implicitWidth: kindBadge.implicitWidth + 10
                                            implicitHeight: 18
                                            Text {
                                                id: kindBadge
                                                anchors.centerIn: parent
                                                text: modelData.cdKeyBadge || "STORE"
                                                color: modelData.storeCategory === "cdkey_marketplace" ? "#fca5a5" : (modelData.isCdKeySite ? "#fde68a" : "#bbf7d0")
                                                font.pixelSize: 10
                                                font.bold: true
                                                textFormat: Text.PlainText
                                            }
                                        }
                                        CompactButton {
                                            text: "View offer"
                                            enabled: !!modelData.url
                                            onClicked: if (page.viewModel) page.viewModel.openOfferUrl(modelData.url)
                                        }
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: "Source: " + (modelData.sourceOrigin || "ITAD API")
                                        color: Theme.muted
                                        font.pixelSize: 10
                                        textFormat: Text.PlainText
                                    }
                                    Text {
                                        visible: !!modelData.isCdKeySite && !!(modelData.cdKeyDisclaimer && modelData.cdKeyDisclaimer.length > 0)
                                        Layout.fillWidth: true
                                        text: modelData.cdKeyDisclaimer || ""
                                        color: modelData.storeCategory === "cdkey_marketplace" ? "#fca5a5" : "#fcd34d"
                                        font.pixelSize: 10
                                        wrapMode: Text.WordWrap
                                        textFormat: Text.PlainText
                                    }
                                }
                            }
                        }

                        Text {
                            visible: !!page.viewModel && page.viewModel.candidates.length > 0
                            text: "Matching game editions"
                            color: Theme.text
                            font.bold: true
                        }
                        Repeater {
                            model: page.viewModel ? page.viewModel.candidates : []
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 6
                                Text {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    text: modelData.title || modelData.id || ""
                                    color: Theme.text
                                    textFormat: Text.PlainText
                                    elide: Text.ElideRight
                                }
                                CompactButton {
                                    text: "Compare"
                                    enabled: !!page.viewModel && !page.viewModel.busy && !!modelData.id
                                    onClicked: page.viewModel.selectCandidate(modelData.id)
                                }
                                CompactButton {
                                    text: "Refresh"
                                    enabled: !!page.viewModel && !page.viewModel.busy && !!modelData.id
                                    onClicked: page.viewModel.refreshCandidate(modelData.id)
                                }
                            }
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                        Text { text: "Check a public retailer page"; color: Theme.text; font.bold: true }
                        RowLayout {
                            Layout.fillWidth: true
                            TextField {
                                id: publicOfferUrl
                                objectName: "storePublicOfferUrl"
                                Layout.fillWidth: true
                                placeholderText: "https://enabled-retailer.example/game"
                                inputMethodHints: Qt.ImhUrlCharactersOnly
                                selectByMouse: true
                            }
                            CompactButton {
                                objectName: "storePublicPageCheck"
                                text: page.viewModel && page.viewModel.busy ? "Checking…" : "Check page"
                                enabled: !!page.viewModel && !page.viewModel.busy && page.viewModel.scrapeAvailable && publicOfferUrl.text.trim().length > 0
                                hint: "Read one enabled unauthenticated public page; store terms and robots.txt apply"
                                onClicked: page.viewModel.scrapePublicPage(publicOfferUrl.text.trim(), gameSearch.text.trim())
                            }
                            CompactButton {
                                objectName: "storeScraperSettings"
                                text: "Manage sources"
                                onClicked: page.accountsRequested()
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            visible: !!page.viewModel && page.viewModel.scrapedTitle.length > 0
                            text: page.viewModel && page.viewModel.scrapedTitle.length > 0
                                  ? "Page title: " + page.viewModel.scrapedTitle
                                  : ""
                            color: Theme.muted
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                        }
                        Text {
                            objectName: "storeScrapedTitleMismatch"
                            Layout.fillWidth: true
                            visible: !!page.viewModel && page.viewModel.scrapedTitle.length > 0
                                     && gameSearch.text.trim().length > 0
                                     && page.viewModel.scrapedTitle.trim().toLowerCase() !== gameSearch.text.trim().toLowerCase()
                            text: "Warning: the public page title differs from your search. Do not assume it is the same game, edition, or region."
                            color: "#ff7a7a"
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                        }
                        Repeater {
                            model: page.viewModel ? page.viewModel.scrapedOffers : []
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
                                    color: Theme.text
                                    textFormat: Text.PlainText
                                    elide: Text.ElideRight
                                }
                                CompactButton {
                                    text: "Open page"
                                    enabled: !!modelData.product_page_url
                                    onClicked: if (page.viewModel) page.viewModel.openOfferUrl(modelData.product_page_url)
                                }
                            }
                        }
                        Item { Layout.fillHeight: true; Layout.minimumHeight: 8 }
                        Text {
                            Layout.fillWidth: true
                            text: "ITAD supplies these store offers and links. Confirm edition, seller, region, final price and eligibility at checkout."
                            color: Theme.muted
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                            font.pixelSize: 11
                        }
                    }
                }
            }
        }
    }
}
