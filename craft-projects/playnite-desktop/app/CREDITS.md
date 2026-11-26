# Sources and asset policy

This is a desktop-library frontend, not a game or a distribution of Playnite.
No original logos, cover artwork, screenshots, fonts, sounds or executable code are
bundled. Neutral missing-cover indicators are drawn with ordinary Qt Quick shapes.
Reference JPEGs are inspection evidence outside the application, not UI assets.

Starting frontend copied from:
`ui-prototype/launcher-refresh/`
(app.py, library_model.py, fixture-library.json, organizer_view.py, snapshot_bridge.py,
test_library_model.py, Main.qml and README.md). The shell and documentation were
rewritten here; the snapshot bridge and fixture library remain byte-for-byte intact.

Organizer engine copied unchanged from:
`modules/organizer_health.py`
SHA-256: `89ba3bc2338a7c31574f7cb5ec2917a14eb4110d147d0c7d28d09eadcc67d5d8`.

Synthetic snapshot fixture copied unchanged from:
`adapters/steam/demo-snapshot.json`.

Structural and numeric reference (read only):
`research/craft-playnite-reference/installed-theme/Default/`
Constants.xaml; Views/MainWindow.xaml, TopPanel.xaml, Library.xaml, LibraryGridView.xaml,
LibraryDetailsView.xaml, LibraryListView.xaml, ExplorerPanel.xaml, FilterPanelView.xaml,
Sidebar.xaml, DetailsViewGameOverview.xaml; DerivedStyles/GridViewItemTemplate.xaml,
DetailsViewItemTemplate.xaml. The custom_cover_background.png was inspected only to
understand the neutral fallback role and aspect ratio; it is not copied.

Qt/PySide6 was already installed. No dependencies, commercial services, generated
artwork, remote media or new font packages were added.
