import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ColumnLayout {
    id: panel
    property var service: typeof platformSocial !== "undefined" ? platformSocial : null
    property string gameKey: ""
    objectName: "liveAchievementsPanel"
    function selectCurrent() { if (visible && service) service.selectGame(gameKey) }
    onGameKeyChanged: selectCurrent()
    onVisibleChanged: selectCurrent()
    Component.onCompleted: selectCurrent()
    spacing: 8
    Text { text: panel.service && panel.service.progressProvider === "xbox" ? "Xbox achievements" : "Steam achievements"; color: Theme.text; font.bold: true; font.pixelSize: 16 }
    Text { Layout.fillWidth: true; text: panel.service ? panel.service.progressStatus : "Provider progress unavailable"; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: Theme.muted }
    RowLayout {
        Layout.fillWidth: true
        CompactCombo { id: product; objectName: "achievementSteamProduct"; Layout.fillWidth: true; model: panel.service && panel.service.gameKey === panel.gameKey ? panel.service.progressProducts : [] }
        SocialButton { objectName: "syncSteamAchievements"; text: "Sync achievements"; enabled: panel.service && !panel.service.busy && product.count > 0; onClicked: panel.service.syncAchievements(product.currentText) }
    }
    Text { Layout.fillWidth: true; visible: product.count === 0; text: "No exact provider product is linked to this game. Achievement IDs are never guessed from its title."; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: Theme.muted }
    Text { Layout.fillWidth: true; visible: panel.service && panel.service.progressProvider === "steam" && !panel.service.achievementAccess && product.count > 0; text: "For live sync, sign in with Steam Guard under Friends → Platforms. A developer API key is not required."; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: Theme.muted }
    ListView {
        objectName: "liveAchievementList"; Layout.fillWidth: true; Layout.preferredHeight: Math.min(320, count * 32); clip: true
        model: panel.service && panel.service.gameKey === panel.gameKey ? panel.service.achievements : []
        ScrollBar.vertical: ScrollBar {}
        delegate: Text { required property var modelData; width: ListView.view.width; height: Math.max(32, implicitHeight); text: (modelData.unlocked === true ? "Unlocked · " : modelData.unlocked === false ? "Locked · " : "Unknown · ") + modelData.label; color: Theme.text; textFormat: Text.PlainText; wrapMode: Text.WordWrap }
    }
}
