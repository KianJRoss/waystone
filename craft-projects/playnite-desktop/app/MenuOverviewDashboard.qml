import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

WorkspacePage {
    id: dashboard
    objectName: "menuOverviewDashboard"
    property string section: "connections"
    property var overview: typeof menuOverviews !== "undefined" ? menuOverviews : null
    property string query: ""
    property var catalogGroups: overview ? overview.catalogs : []
    property var systemCards: catalogGroups.filter(function(card) { return card.key.indexOf("emulation:") === 0 })
    property var catalogCards: {
        var groups = catalogGroups.filter(function(card) { return card.key.indexOf("emulation:") !== 0 })
        if (systemCards.length) {
            var known = systemCards.every(function(card) { return card.count !== null && card.count !== undefined })
            var total = systemCards.reduce(function(sum, card) { return sum + (card.count || 0) }, 0)
            groups.push({key: "emulation", label: "Emulation catalogs", count: known ? total : null,
                summary: systemCards.length + " systems • browse by system, then game • catalog records, not owned or installed games"})
        }
        return groups
    }
    property var allCards: !overview ? [] : section === "connections" ? overview.connections : section === "roms" ? overview.roms : section === "catalog-systems" ? systemCards : catalogCards
    property var cards: allCards.filter(function(card) { return !dashboard.query || (card.label || "").toLowerCase().indexOf(dashboard.query.toLowerCase()) >= 0 })
    signal drillRequested(string section, string key, string label)
    signal actionRequested(string section)
    title: section === "connections" ? "Linux connections" : section === "roms" ? "ROM library" : "Native catalogs"
    background: Rectangle { color: Theme.popup; border.color: Theme.line; radius: Theme.cornerRadius }
    function openSection(value) {
        section = value
        groupSearch.text = ""
        if (overview) overview.refresh()
        open()
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 14
        Text {
            objectName: "overviewBreadcrumb"
            text: dashboard.section === "catalog-systems" ? "Native catalogs   /   Emulation   /   Systems" : "Overview   /   " + (dashboard.section === "connections" ? "Providers" : dashboard.section === "roms" ? "Systems" : "Catalogs")
            textFormat: Text.PlainText; color: Theme.text; font.family: Theme.family; font.pixelSize: 16; font.bold: true
        }
        Text {
            Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText; color: Theme.muted
            text: dashboard.section === "connections" ? "Choose a provider to inspect its games and exact library links. These counts describe the selected native inventory, not authenticated account status."
                : dashboard.section === "roms" ? "Your local ROMs, grouped by system. Choose a system to browse its games. Emulator settings apply across the library. Opening this overview does not scan files."
                : "Choose a catalog or system to browse its records. Store catalog entries are not necessarily owned or installed games."
        }
        CompactButton {
            objectName: "catalogSystemsBack"; visible: dashboard.section === "catalog-systems"
            implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
            text: "‹ All catalogs"; onClicked: dashboard.openSection("catalogs")
        }
        RowLayout {
            Layout.fillWidth: true
            CompactButton {
                implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
                objectName: "overviewPrimaryAction"
                text: dashboard.section === "connections" ? "Refresh installed inventory" : dashboard.section === "roms" ? "Scan configured ROM roots" : "Downloads & installs"
                enabled: dashboard.section === "connections" ? (typeof nativeRefresh !== "undefined" && nativeRefresh && !nativeRefresh.busy) : dashboard.section === "roms" ? (typeof launchView !== "undefined" && launchView && launchView.romRoots.length > 0 && !launchView.romScannerBusy) : true
                onClicked: dashboard.actionRequested(dashboard.section)
            }
            CompactButton {
                implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
                objectName: "overviewManageAccounts"; text: "Manage accounts"
                visible: dashboard.section === "connections"
                onClicked: dashboard.actionRequested("accounts")
            }
            CompactButton {
                implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
                objectName: "overviewEmulatorSettings"; text: "Emulators & profiles"
                visible: dashboard.section === "roms"
                onClicked: dashboard.actionRequested("profiles")
            }
            Item { Layout.fillWidth: true }
            Text {
                objectName: "overviewGroupCount"
                text: dashboard.cards.filter(function(c) { return c.key !== "none" }).length + (dashboard.section === "connections" ? " providers" : (dashboard.section === "roms" || dashboard.section === "catalog-systems") ? " systems" : " catalogs")
                textFormat: Text.PlainText; color: Theme.muted
            }
        }
        TextField {
            id: groupSearch; objectName: "overviewGroupSearch"; Layout.fillWidth: true
            placeholderText: dashboard.section === "connections" ? "Find a provider…" : "Find a catalog or system…"
            color: Theme.text; onTextChanged: dashboard.query = text
            background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: Theme.cornerRadius }
        }
        GridView {
            id: groups; objectName: "overviewCards"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; cellWidth: Math.floor(width / (width >= 650 ? 2 : 1)); cellHeight: 148
            model: dashboard.cards
            ScrollBar.vertical: ScrollBar {}
            delegate: ItemDelegate {
                required property var modelData
                objectName: "overviewCard_" + modelData.key
                enabled: modelData.key !== "none"
                width: groups.cellWidth - 10; height: groups.cellHeight - 10
                background: Rectangle { color: parent.hovered ? Theme.hover : Theme.panel; border.color: Theme.line; radius: Theme.cornerRadius }
                contentItem: ColumnLayout {
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        Text { Layout.fillWidth: true; text: modelData.label; textFormat: Text.PlainText; color: Theme.text; font.bold: true; font.pixelSize: 16; elide: Text.ElideRight }
                        Text { text: modelData.count === null || modelData.count === undefined ? "—" : String(modelData.count); textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 24 }
                    }
                    Text { Layout.fillWidth: true; Layout.fillHeight: true; text: modelData.summary || ""; textFormat: Text.PlainText; color: Theme.muted; wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight; font.pixelSize: 12 }
                    Text { text: modelData.key === "none" ? "Configure inventory above" : "Browse  ›"; textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 12 }
                }
                onClicked: dashboard.drillRequested(dashboard.section, modelData.key, modelData.label)
            }
            Text { anchors.centerIn: parent; visible: dashboard.cards.length === 0; text: dashboard.query ? "No provider or system matches that search." : dashboard.section === "roms" ? "No cached ROM inventory yet. Configure roots or scan them above." : "No inventory is configured for this overview."; textFormat: Text.PlainText; color: Theme.muted; wrapMode: Text.WordWrap; width: Math.min(parent.width - 30, 550); horizontalAlignment: Text.AlignHCenter }
        }
    }
}
