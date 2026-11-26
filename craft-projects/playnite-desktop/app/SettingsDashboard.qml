import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Settings holds only things that have no other home. Anything you can do from Library, Controllers,
// Emulation, Saves or Mods lives there and is NOT repeated here (Kian 2026-11-22).
WorkspacePage {
    id: dashboard
    objectName: "settingsDashboard"
    routed: true
    pageId: "settings"
    title: "Settings"
    background: Rectangle { color: Theme.background }

    property int selectedCategory: 0
    function scrollToSteamLibrarySection() { platformAccountsPanel.scrollToSteamLibrarySection() }
    function checkAccountKeyrings() {
        if (typeof steamLibrarySettings !== "undefined" && steamLibrarySettings) steamLibrarySettings.checkSaved()
        if (typeof ribbonArtworkSettings !== "undefined" && ribbonArtworkSettings) ribbonArtworkSettings.checkKeyring()
    }
    onVisibleChanged: if (visible && selectedCategory === 0) checkAccountKeyrings()
    onSelectedCategoryChanged: if (visible && selectedCategory === 0) checkAccountKeyrings()
    readonly property var categories: [
        { name: "Accounts", desc: "Store and platform sign-ins" },
        { name: "Storage", desc: "Install folder and installed-games refresh" },
        { name: "Store", desc: "Price comparison filters" },
        { name: "Games", desc: "Which monitor games open on" },
        { name: "General", desc: "Sounds and how Waystone feels" },
        { name: "Advanced", desc: "Catalog and connection inspectors, diagnostics" }
    ]

    component Card: Rectangle {
        default property alias content: cardCol.data
        property string heading: ""
        property string note: ""
        Layout.fillWidth: true
        implicitHeight: cardCol.implicitHeight + 24
        color: Theme.background; radius: Theme.cornerRadius; border.color: Theme.line; border.width: 1
        ColumnLayout {
            id: cardCol; anchors.fill: parent; anchors.margins: 14; spacing: 10
            Text { text: heading; visible: heading.length > 0; color: Theme.text; font.family: Theme.family; font.bold: true; font.pixelSize: 15 }
            Text { text: note; visible: note.length > 0; Layout.fillWidth: true; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap }
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 16

        Rectangle {
            Layout.preferredWidth: 220
            Layout.fillHeight: true
            color: Theme.panel; radius: Theme.cornerRadius; border.color: Theme.line; border.width: 1
            ListView {
                id: categoryList
                objectName: "settingsCategories"
                anchors.fill: parent; anchors.margins: 8
                spacing: 4; clip: true
                model: dashboard.categories
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    width: categoryList.width; height: 50
                    radius: Theme.cornerRadius
                    color: dashboard.selectedCategory === index ? Theme.main : (catMouse.containsMouse ? Theme.hover : "transparent")
                    MouseArea { id: catMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: dashboard.selectedCategory = index }
                    ColumnLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 2
                        Text { text: modelData.name; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; font.bold: dashboard.selectedCategory === index }
                        Text { text: modelData.desc; color: Theme.muted; font.family: Theme.family; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.panel; radius: Theme.cornerRadius; border.color: Theme.line; border.width: 1
            ScrollView {
                id: settingsScroll
                anchors.fill: parent; anchors.margins: 18
                clip: true
                contentWidth: availableWidth
                StackLayout {
                    width: settingsScroll.availableWidth
                    currentIndex: dashboard.selectedCategory

                    // Accounts
                    ColumnLayout {
                        spacing: 14
                        Card {
                            objectName: "nexusAccountCard"
                            heading: "Nexus Mods"
                            note: "Lets Waystone list and download Nexus mods. Get your personal API key at nexusmods.com > Settings > API Keys (bottom of the page). It is checked with Nexus and kept in your system keyring."
                            Component.onCompleted: if (typeof modBrowser !== "undefined" && modBrowser) modBrowser.refreshNexusAccount()
                            Text {
                                objectName: "nexusAccountStatus"
                                text: (typeof modBrowser !== "undefined" && modBrowser) ? (modBrowser.nexusAccount || "Checking…") : "Mods unavailable"
                                color: Theme.text; font.family: Theme.family; font.pixelSize: 13
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                TextField {
                                    id: nexusKeyField
                                    objectName: "nexusKeyField"
                                    Layout.fillWidth: true
                                    echoMode: TextInput.Password
                                    placeholderText: "Paste your Nexus API key"
                                    placeholderTextColor: Theme.muted
                                    color: Theme.text
                                    background: Rectangle { color: Theme.hover; radius: 6; border.color: Theme.line }
                                }
                                CompactButton {
                                    implicitWidth: 90; text: "Save key"
                                    enabled: nexusKeyField.text.length > 20
                                    onClicked: { modBrowser.setNexusKey(nexusKeyField.text); nexusKeyField.text = "" }
                                }
                                CompactButton { implicitWidth: 120; text: "Get a key…"; onClicked: Qt.openUrlExternally("https://www.nexusmods.com/settings/api-keys") }
                                CompactButton { implicitWidth: 80; text: "Remove"; onClicked: modBrowser.removeNexusKey() }
                            }
                        }
                        Card {
                            heading: "Platform accounts"
                            note: "Sign in to stores and platforms for library sync, installs, friends and achievements."
                            PlatformConnections { id: platformAccountsPanel; Layout.fillWidth: true; Layout.preferredHeight: 560 }
                        }
                    }

                    // Storage
                    ColumnLayout {
                        spacing: 14
                        Card {
                            heading: "Install folder"
                            note: "New installs go here unless you pick another drive in the install prompt."
                            Text {
                                objectName: "settingsInstallRoot"
                                Layout.fillWidth: true
                                text: (typeof gameInstalls !== "undefined" && gameInstalls && gameInstalls.defaultRoot) ? String(gameInstalls.defaultRoot) : "Not set yet"
                                color: Theme.text; font.family: "monospace"; font.pixelSize: 13; elide: Text.ElideMiddle
                            }
                        }
                        Card {
                            heading: "Installed games"
                            note: "Re-check which games are installed on this PC (Steam, Heroic/Legendary, GOG, Amazon, Xbox receipts). Runs only when you click."
                            CompactButton {
                                objectName: "settingsRefreshInstalled"
                                implicitWidth: 200
                                text: (typeof nativeRefresh !== "undefined" && nativeRefresh && nativeRefresh.busy) ? "Refreshing…" : "Refresh installed games"
                                enabled: typeof nativeRefresh !== "undefined" && nativeRefresh && !nativeRefresh.busy
                                onClicked: nativeRefresh.startRefresh()
                            }
                        }
                    }

                    // Store
                    ColumnLayout {
                        spacing: 14
                        Card {
                            heading: "Price comparison"
                            CheckBox {
                                objectName: "settingsExcludeCdKeys"
                                text: "Official stores only (hide grey-market key resellers)"
                                palette.windowText: Theme.text
                                checked: Boolean(typeof priceComparison !== "undefined" && priceComparison && priceComparison.excludeCdKeys)
                                onToggled: if (priceComparison) priceComparison.setExcludeCdKeys(checked)
                            }
                        }
                    }

                    // Games
                    ColumnLayout {
                        spacing: 14
                        Card {
                            id: monitorCard
                            objectName: "settingsGameMonitorCard"
                            heading: "Game monitor"
                            note: "Games open on this monitor. You can pick a different one for a single game next to its Play button."
                            readonly property bool available: typeof launchView !== "undefined" && launchView
                            readonly property var options: {
                                var list = [{ label: "Wherever the game opens", description: "" }]
                                if (!available) return list
                                var found = false
                                var choices = launchView.monitorChoices
                                for (var i = 0; i < choices.length; ++i) {
                                    list.push(choices[i])
                                    if (choices[i].description === launchView.defaultMonitor) found = true
                                }
                                if (launchView.defaultMonitor && !found)
                                    list.push({ label: launchView.defaultMonitorLabel, description: launchView.defaultMonitor })
                                return list
                            }
                            Component.onCompleted: if (available) launchView.refreshMonitors()
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                CompactCombo {
                                    objectName: "settingsGameMonitor"
                                    Layout.preferredWidth: 360
                                    model: monitorCard.options
                                    textRole: "label"
                                    enabled: monitorCard.available
                                    currentIndex: {
                                        if (!monitorCard.available) return 0
                                        for (var i = 0; i < monitorCard.options.length; ++i)
                                            if (monitorCard.options[i].description === launchView.defaultMonitor) return i
                                        return 0
                                    }
                                    onActivated: function(index) { launchView.setDefaultMonitor(monitorCard.options[index].description) }
                                }
                                CompactButton {
                                    implicitWidth: 150; text: "Refresh monitors"
                                    enabled: monitorCard.available
                                    onClicked: launchView.refreshMonitors()
                                }
                            }
                        }
                        Card {
                            id: protonCard
                            objectName: "settingsProtonCard"
                            heading: "Proton"
                            note: "Proton runs Windows games on Linux. Install the latest GE-Proton, move games over to it, and remove versions nothing uses."
                            readonly property bool available: typeof protonView !== "undefined" && protonView
                            Component.onCompleted: if (available) { protonView.refresh(); protonView.checkLatest() }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Text {
                                    Layout.fillWidth: true
                                    text: !protonCard.available ? "" : (protonView.latest.tag
                                          ? ("Latest GE-Proton: " + protonView.latest.tag + (protonView.latest.installed ? " · installed" : " · about " + protonView.latest.sizeMb + " MB download"))
                                          : "Latest GE-Proton: not checked yet")
                                    color: Theme.text; font.family: Theme.family; font.pixelSize: 13; elide: Text.ElideRight
                                }
                                CompactButton {
                                    objectName: "settingsProtonCheck"
                                    text: "Check for updates"
                                    enabled: protonCard.available && !protonView.busy
                                    onClicked: protonView.checkLatest()
                                }
                                CompactButton {
                                    objectName: "settingsProtonInstall"
                                    text: "Install"
                                    visible: protonCard.available && !!protonView.latest.tag && !protonView.latest.installed && protonView.busy !== "install"
                                    enabled: protonCard.available && !protonView.busy
                                    onClicked: protonView.installLatest()
                                }
                                CompactButton {
                                    text: "Cancel"
                                    visible: protonCard.available && protonView.busy === "install"
                                    onClicked: protonView.cancel()
                                }
                            }
                            ProgressBar {
                                Layout.fillWidth: true
                                visible: protonCard.available && protonView.busy === "install"
                                value: protonCard.available ? protonView.progress : 0
                            }
                            Text {
                                Layout.fillWidth: true
                                visible: protonCard.available && protonView.status.length > 0
                                text: protonCard.available ? protonView.status : ""
                                color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap
                            }
                            Repeater {
                                model: protonCard.available ? protonView.runtimes : []
                                RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true; spacing: 8
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 1
                                        Text { text: modelData.name + (modelData.newestInstalled ? "  (newest)" : ""); color: Theme.text; font.family: Theme.family; font.pixelSize: 13; font.bold: modelData.newestInstalled }
                                        Text {
                                            text: modelData.used
                                                  ? ("Used by " + [modelData.steam ? "Steam " + modelData.steam : "", modelData.heroicCount ? "Heroic " + modelData.heroicCount : "", modelData.waystoneCount ? "Waystone " + modelData.waystoneCount : ""].filter(function(x) { return x }).join(" · "))
                                                  : (modelData.kind === "steam" ? "Steam's own Proton · not used by your games" : "Not used by any game")
                                            color: Theme.muted; font.family: Theme.family; font.pixelSize: 12
                                        }
                                    }
                                    CompactButton {
                                        text: protonCard.available ? "Move games to " + protonView.newestInstalled : ""
                                        visible: protonCard.available && modelData.kind === "ge" && modelData.used && !modelData.newestInstalled && protonView.newestInstalled.length > 0
                                        enabled: protonCard.available && !protonView.busy
                                        onClicked: protonView.switchGames(modelData.name, protonView.newestInstalled)
                                    }
                                    CompactButton {
                                        text: "Remove"
                                        visible: modelData.kind !== "steam" && !modelData.used
                                        enabled: protonCard.available && !protonView.busy
                                        onClicked: protonView.remove(modelData.name)
                                    }
                                }
                            }
                        }
                    }

                    // General
                    ColumnLayout {
                        spacing: 14
                        Card {
                            heading: "Library setup"
                            note: "Review launcher counts and finish optional account, artwork, ROM-folder or controller setup. This guide can be opened again at any time."
                            CompactButton {
                                objectName: "reopenFirstRunGuide"
                                implicitWidth: 220
                                text: "Open library setup guide"
                                visible: typeof firstRunGuide !== "undefined" && firstRunGuide !== null
                                enabled: visible
                                onClicked: firstRunGuide.reopen()
                            }
                        }
                        Card {
                            id: soundsCard
                            objectName: "settingsSoundsCard"
                            heading: "Sounds"
                            note: "Small sounds for moving, selecting, going back, opening menus and launching games. They stay quiet while a game is running, except in the overlay."
                            readonly property bool available: typeof uiSounds !== "undefined" && uiSounds
                            CheckBox {
                                objectName: "settingsSoundsEnabled"
                                text: "Play interface sounds"
                                palette.windowText: Theme.text
                                enabled: soundsCard.available
                                checked: soundsCard.available && uiSounds.enabled
                                onToggled: uiSounds.setEnabled(checked)
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Text { text: "Sound pack"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13; Layout.preferredWidth: 90 }
                                CompactCombo {
                                    objectName: "settingsSoundPack"
                                    Layout.preferredWidth: 220
                                    enabled: soundsCard.available
                                    model: soundsCard.available ? uiSounds.packs : []
                                    textRole: "name"
                                    currentIndex: {
                                        if (!soundsCard.available) return -1
                                        var packs = uiSounds.packs
                                        for (var i = 0; i < packs.length; ++i) if (packs[i].id === uiSounds.pack) return i
                                        return -1
                                    }
                                    onActivated: function(index) {
                                        var id = uiSounds.packs[index].id
                                        uiSounds.setPack(id)
                                        uiSounds.preview(id, "select")
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: {
                                        if (!soundsCard.available) return ""
                                        var packs = uiSounds.packs
                                        for (var i = 0; i < packs.length; ++i) if (packs[i].id === uiSounds.pack) return packs[i].description
                                        return ""
                                    }
                                    color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Text { text: "Volume"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13; Layout.preferredWidth: 90 }
                                Slider {
                                    objectName: "settingsSoundVolume"
                                    Layout.preferredWidth: 220
                                    from: 0; to: 100; stepSize: 5
                                    enabled: soundsCard.available
                                    value: soundsCard.available ? uiSounds.volume : 70
                                    onMoved: uiSounds.setVolume(Math.round(value))
                                    onPressedChanged: if (!pressed && soundsCard.available) uiSounds.preview(uiSounds.pack, "move")
                                }
                                Text { text: soundsCard.available ? uiSounds.volume + "%" : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: 13 }
                            }
                            Flow {
                                Layout.fillWidth: true; spacing: 6
                                Repeater {
                                    model: [["move", "Move"], ["select", "Select"], ["back", "Back"], ["open", "Open"], ["launch", "Launch"], ["error", "Error"], ["notify", "Notify"]]
                                    CompactButton {
                                        required property var modelData
                                        implicitWidth: 84; text: modelData[1]
                                        enabled: soundsCard.available
                                        onClicked: uiSounds.preview(uiSounds.pack, modelData[0])
                                    }
                                }
                            }
                        }
                        Card {
                            id: assistantAnimationCard
                            objectName: "settingsAssistantAnimationCard"
                            heading: "Show what the assistant is doing"
                            note: "When an agent asks Waystone to open a game, animate the library search and selection before launching. Any input skips the animation and launches immediately."
                            readonly property bool available: typeof assistantPreferences !== "undefined" && assistantPreferences
                            CheckBox {
                                objectName: "settingsAssistantAnimationEnabled"
                                text: "Animate assistant actions"
                                palette.windowText: Theme.text
                                enabled: assistantAnimationCard.available
                                checked: assistantAnimationCard.available && assistantPreferences.showAnimation
                                onToggled: assistantPreferences.setShowAnimation(checked)
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                enabled: assistantAnimationCard.available && assistantPreferences.showAnimation
                                Text { text: "Pace"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 13; Layout.preferredWidth: 90 }
                                Text { text: "Quick"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                                Slider {
                                    id: assistantAnimationPace
                                    objectName: "settingsAssistantAnimationPace"
                                    Layout.preferredWidth: 220
                                    from: 0.5; to: 3.0; stepSize: 0.1
                                    value: assistantAnimationCard.available ? assistantPreferences.pace : 1.0
                                    onMoved: assistantPreferences.setPace(Math.round(value * 10) / 10)
                                }
                                Text { text: "Very slow"; color: Theme.muted; font.family: Theme.family; font.pixelSize: 12 }
                                Text {
                                    // A typical scroll at this pace (the min..max of the director's per-row timing).
                                    text: assistantAnimationPace.value.toFixed(1) + "×  ·  about " + (1.8 * assistantAnimationPace.value).toFixed(1) + "–" + (5 * assistantAnimationPace.value).toFixed(0) + " s"
                                    color: Theme.text; font.family: Theme.family; font.pixelSize: 12
                                }
                            }
                            Text {
                                visible: assistantAnimationCard.available && assistantPreferences.lastError.length > 0
                                text: assistantAnimationCard.available && assistantPreferences.lastError.length > 0 ? "Settings could not be saved; the previous value is still active." : ""
                                color: Theme.muted; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WordWrap
                            }
                        }
                    }

                    // Advanced
                    ColumnLayout {
                        spacing: 14
                        Card {
                            heading: "Inspectors"
                            note: "Read-only views of the underlying catalogs and provider connections, for troubleshooting links and imports."
                            Flow {
                                Layout.fillWidth: true; spacing: 8
                                CompactButton { objectName: "settingsNativeCatalogs"; implicitWidth: 160; text: "Native catalogs"; onClicked: managementOverview.openSection("catalogs") }
                                CompactButton { objectName: "settingsLinuxConnections"; implicitWidth: 160; text: "Linux connections"; onClicked: managementOverview.openSection("connections") }
                                CompactButton { objectName: "settingsSnapshotInfo"; implicitWidth: 160; text: "Snapshot info"; onClicked: snapshotDialog.open() }
                            }
                        }
                        Card {
                            heading: "Stick deadband test"
                            note: "Live stick telemetry for tuning deadzones on the connected controller."
                            CompactButton {
                                objectName: "settingsDeadband"
                                implicitWidth: 180; text: "Open deadband test"
                                enabled: typeof controllerCommands !== "undefined" && controllerCommands
                                onClicked: controllerCommands.openDeadband()
                            }
                        }
                    }
                }
            }
        }
    }
}
