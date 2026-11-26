import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
WorkspacePage {
    id: dashboard
    objectName: "achievementsDashboard"
    property var service: typeof platformSocial !== "undefined" ? platformSocial : null
    property string selectedGame: ""
    property string search: ""
    property var shownGames: service ? service.achievementGames.filter(function(g) { return g.provider === dashboard.service.progressProvider && (!dashboard.search || g.title.toLowerCase().indexOf(dashboard.search.toLowerCase()) >= 0) }) : []
    onOpened: if (service) service.loadProgressCache()
    background: Rectangle { color: Theme.background; border.color: Theme.line; radius: Theme.cornerRadius }
    ColumnLayout {
        anchors.fill: parent; spacing: 12
        RowLayout {
            Text { visible: !dashboard.routed; text: "Achievements"; color: Theme.text; font.bold: true; font.pixelSize: 22; Layout.fillWidth: true }
            SocialButton { text: "Reload cached progress"; enabled: dashboard.service && !dashboard.service.busy; onClicked: dashboard.service.loadProgressCache() }
            SocialButton { visible: !dashboard.routed; text: "Close"; onClicked: dashboard.close() }
        }
        Text { Layout.fillWidth: true; text: "Platform and account progress stays separate. Totals below cover synced games only, not your entire account. Steam and Xbox have sync adapters; other provider connectors are not represented as finished."; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: Theme.muted }
        Repeater {
            model: dashboard.service ? dashboard.service.achievementAccounts : []
            delegate: Text { required property var modelData; text: modelData.provider + " · " + modelData.account_id + " · " + modelData.observed_games + " synced products · " + modelData.unlocked_count + " / " + (modelData.total_count === null ? "unknown" : modelData.total_count) + " achievements"; textFormat: Text.PlainText; color: Theme.text }
        }
        RowLayout {
            CompactCombo { model: ["Steam", "Xbox"]; onActivated: { dashboard.selectedGame = ""; dashboard.service.setProgressProvider(currentIndex === 0 ? "steam" : "xbox") } }
            SocialButton { text: "Read Xbox account titles"; enabled: dashboard.service && !dashboard.service.busy; onClicked: dashboard.service.connectXboxProgress() }
            Text { text: "Uses the existing Xodus account; no new sign-in."; color: Theme.muted; textFormat: Text.PlainText }
        }
        Text { Layout.fillWidth: true; text: dashboard.service ? dashboard.service.progressStatus : ""; color: Theme.muted; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 18
            ColumnLayout {
                Layout.preferredWidth: 330; Layout.maximumWidth: 380; Layout.fillHeight: true
                Text { text: dashboard.service && dashboard.service.progressProvider === "xbox" ? "Xbox library" : "Steam library"; color: Theme.text; font.bold: true }
                TextField { Layout.fillWidth: true; placeholderText: "Find a game…"; onTextChanged: dashboard.search = text; color: Theme.text; background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: 5 } }
                ListView {
                    id: games; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    model: dashboard.shownGames
                    ScrollBar.vertical: ScrollBar {}
                    delegate: ItemDelegate {
                        required property var modelData
                        width: games.width
                        text: modelData.title; highlighted: dashboard.selectedGame === modelData.game_key
                        contentItem: Text { text: parent.text; color: Theme.text; elide: Text.ElideRight; textFormat: Text.PlainText }
                        onClicked: dashboard.selectedGame = modelData.game_key
                    }
                }
            }
            ScrollView {
                id: progressScroll; Layout.fillWidth: true; Layout.fillHeight: true; contentWidth: availableWidth; clip: true
                LiveAchievementsPanel { width: progressScroll.availableWidth; service: dashboard.service; gameKey: dashboard.visible ? dashboard.selectedGame : "" }
            }
        }
    }
}
