"""Finite, offline interaction checks against real Qt Quick controls and model state."""
import copy
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


def exercise_ui(app, root, model, warnings):
    steps = []
    original = copy.deepcopy(model.games)

    def check(name, passed, detail):
        steps.append({'step': name, 'passed': bool(passed), 'detail': detail})

    def settle():
        app.processEvents()
        QTest.qWait(35)
        app.processEvents()

    def item(name):
        obj = root.findChild(QQuickItem, name)
        if obj is None:
            raise AssertionError('Missing Qt control: ' + name)
        return obj

    def click(name):
        obj = item(name)
        point = obj.mapToScene(QPointF(obj.width()/2, obj.height()/2)).toPoint()
        QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, point)
        settle()

    root.setFlag(Qt.WindowDoesNotAcceptFocus, False)
    root.requestActivate()
    settle()
    root.setProperty('organizerMode', False)
    root.setProperty('filterVisible', False)
    root.setProperty('inspectorVisible', False)
    root.clearFilters()
    root.setView('grid')
    settle()
    check('initial layout', model.rowCount() == len(model.games) and item('coverGrid').isVisible(), f'{model.rowCount()} stable records in portrait grid')

    # Search via actual text field, not a hand-built search implementation.
    search = item('searchField')
    search.forceActiveFocus()
    query = model.games[0]['title'].split()[-1].casefold() if model.games else 'no-game'
    
    for char in query:
        QTest.keyClick(root, Qt.Key(ord(char.upper())), Qt.ShiftModifier if char.isupper() else Qt.NoModifier)
    settle()
    expected = sum(query.casefold() in g['title'].casefold() for g in model.games)
    check('search input', model.rowCount() == expected and root.property('query').casefold() == query.casefold(), f'Keyboard search returned {model.rowCount()} of {len(model.games)} query={root.property("query")!r} expected={query!r} text={search.property("text")!r}')
    root.setProperty('query', 'no-such-title-offline-7491')
    settle()
    check('empty state', model.rowCount() == 0 and root.property('selectedId') == '', 'Zero results clears stale selection')
    root.clearFilters()
    settle()

    click('detailsViewButton')
    check('details view', root.property('viewMode') == 'details' and item('compactGameList').isVisible() and item('gameDetails').isVisible(), 'Compact game list and selected metadata pane are drawn')
    if model.rowCount() > 1:
        item('compactGameList').forceActiveFocus()
        first = root.property('selectedId')
        QTest.keyClick(root, Qt.Key_Down)
        settle()
        check('keyboard selection', root.property('selectedId') != first and model.rowOf(root.property('selectedId')) >= 0, 'Down arrow selects a different stable identity')
    click('listViewButton')
    check('column list', root.property('viewMode') == 'list' and item('columnView').isVisible(), 'Sortable column list is drawn')
    chosen = root.property('selectedId')
    # Click the actual Name header; this and the toolbar share the same sort state.
    table = item('columnView')
    header_point = table.mapToScene(QPointF(70, 16)).toPoint()
    old_direction = root.property('descending')
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, header_point)
    settle()
    check('column header sorting', root.property('descending') != old_direction, 'Clicking the Name header changes the real row order')
    click('sortDirection')
    # Local corrections are the visible sort key; immutable imported titles may
    # intentionally differ (e.g. the corrected GameCube Animal Crossing record).
    actual = [model._display_title(g).casefold() for g in model.visible]
    check('sort and selection', actual == sorted(actual, reverse=root.property('descending')) and root.property('selectedId') == chosen, 'Sort direction updates rows while selection follows the stable ID')

    # Drag the live explorer splitter, then collapse/reopen it through toolbar controls.
    root.setProperty('explorerVisible', True)
    settle()
    explorer = item('explorerPanel')
    old_width = explorer.width()
    edge = explorer.mapToScene(QPointF(old_width + 2, explorer.height()/2)).toPoint()
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, edge)
    QTest.mouseMove(root, edge + QPointF(48, 0).toPoint(), 60)
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, edge + QPointF(48, 0).toPoint())
    settle()
    check('resizable explorer', explorer.width() > old_width + 20, f'Explorer splitter changed width from {old_width:.0f} to {explorer.width():.0f} px')
    click('filterToggle')
    hidden = not explorer.isVisible()
    click('filterToggle')
    check('collapsible filters', hidden and explorer.isVisible(), 'The single Filters panel closes and reopens without losing filters')
    root.setProperty('status', 'installed')
    settle()
    expected_installed = sum(any(v['status'] == 'installed' for v in model._effective_variants(g)) for g in model.games)
    check('installed filter control', root.property('status') == 'installed' and model.rowCount() == expected_installed, f'Installed filter returns {model.rowCount()}; declared-installed never counts as validated installed')
    root.setProperty('status', 'declared-installed')
    settle()
    expected_declared = sum(any(v['status'] == 'declared-installed' for v in g['variants']) for g in model.games)
    check('declaration separation', model.rowCount() == expected_declared, f'{expected_declared} declared-install records remain separately selectable')
    click('clearFiltersButton')
    check('clear filters control', model.rowCount() == len(model.games) and root.property('status') == 'All states', 'Clear resets all filters')
    click('organizerButton')
    for _ in range(10):
        if 'HEALTH REPORT' in item('organizerReport').property('text'):
            break
        settle()
    check('organizer', root.property('organizerMode') and 'HEALTH REPORT' in item('organizerReport').property('text'), 'Local Organizer health report is available without project-root imports')
    root.setProperty('organizerMode', False)

    # Direct key events sent to this offscreen window only; never grabs desktop focus.
    root.setFlag(Qt.WindowDoesNotAcceptFocus, False)
    root.requestActivate()
    settle()
    before = root.property('explorerVisible')
    QTest.keyClick(root, Qt.Key_E, Qt.ControlModifier)
    settle()
    check('Ctrl+E', root.property('explorerVisible') != before, 'Explorer visibility toggles')
    root.setView('details')
    before_filter = root.property('filterVisible')
    before_selection = root.property('selectedId')
    QTest.keyClick(root, Qt.Key_G, Qt.ControlModifier)
    settle()
    toggled_filter = root.property('filterVisible') != before_filter
    QTest.keyClick(root, Qt.Key_G, Qt.ControlModifier)
    settle()
    check('Ctrl+G', toggled_filter and root.property('filterVisible') == before_filter
          and root.property('viewMode') == 'details' and root.property('selectedId') == before_selection,
          'Filter visibility toggles both ways without changing view or selection')
    QTest.keyClick(root, Qt.Key_F, Qt.ControlModifier)
    settle()
    check('Ctrl+F', search.hasActiveFocus(), 'Search shortcut focuses the actual search field')
    root.setFlag(Qt.WindowDoesNotAcceptFocus, True)
    check('read-only invariants', model.games == original, 'All identities, variants and provenance unchanged after interaction')
    check('qml diagnostics', not warnings, 'No Qt Quick warnings' if not warnings else '; '.join(warnings))
    return {'version': 1, 'kind': 'read-only-library-ui', 'steps': steps}
