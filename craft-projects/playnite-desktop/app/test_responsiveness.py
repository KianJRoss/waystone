"""W1 regression contracts; fixture-only, offscreen, no controller/device access."""
import copy
import threading
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtTest import QTest
from library_model import LibraryModel, COLLECTION_FIELDS, collection_values_for_variant
from organizer_report import OrganizerReport
from organizer_view import organizer_preview
from platform_social_view import social_catalog_snapshot

_APP = None

def application():
    global _APP
    _APP = QGuiApplication.instance() or QGuiApplication([])
    return _APP


def games():
    return [{'id': ident, 'title': 'Same title', 'variants': [{
        'source': 'Steam', 'source_id': ident, 'provider_game_id': product,
        'platform': 'Linux', 'status': 'unknown', 'recipe': [],
        'metadata': {'genres': [genre], 'favorite': favorite, 'playtime_seconds': seconds}}]}
        for ident, product, genre, favorite, seconds in
        [('a', '10', 'Action', True, 0), ('b', '20', 'RPG', False, 4000)]]


def test_facets_scan_once_across_searches_and_return_detached_results():
    application()
    original = games(); before = copy.deepcopy(original)
    model = LibraryModel(original)
    with patch('library_model.collection_values_for_variant', wraps=collection_values_for_variant) as call:
        assert model.collectionValues('genres') == ['Action', 'RPG']
        count = call.call_count
        for field in COLLECTION_FIELDS:
            model.collectionValues(field)
        model.setFilters('Same', 'All sources', 'All platforms', 'All states')
        assert model.collectionValues('genres') == ['Action', 'RPG']
        assert call.call_count == count == len(original)*len(COLLECTION_FIELDS)
        labels = model.collectionValues('genres'); labels.append('not in metadata')
        assert model.collectionValues('genres') == ['Action', 'RPG']
    assert model.rowCount() == 2 and original == before
    assert model.collectionValues('favorite') == ['Favorite', 'Not favorite']
    assert model.collectionValues('playtime_bucket') == ['0 minutes', '1–10 hours']


def test_facets_invalidate_on_organizer_commit_undo_and_identity_links(tmp_path):
    application(); model = LibraryModel(games())
    model.configure_organizer(tmp_path/'organizer.sqlite3')
    assert model.collectionValues('genres') == ['Action', 'RPG']
    model.previewMetadata('a', '{"genres":["Puzzle"]}')
    model.applyOrganizer('a')
    assert model.collectionValues('genres') == ['Puzzle', 'RPG']
    model.undoOrganizer()
    assert model.collectionValues('genres') == ['Action', 'RPG']
    model.setIdentityLinks({'b': 'a'})
    assert model.rowCount() == 1
    assert model.collectionValues('genres') == ['Action', 'RPG']
    model.setIdentityLinks({})
    assert model.rowCount() == 2 and model.collectionValues('genres') == ['Action', 'RPG']


def test_unchanged_receipts_do_not_reset_model_or_refresh_projections(tmp_path):
    from test_receipt_library_flow import session
    from native_services.install_receipts import InstallReceiptRegistry
    from test_game_install_controller import close_session
    app = application()
    engine, model, root, warnings = session(app, InstallReceiptRegistry(tmp_path/'receipts.json'))
    try:
        resets = []; model.modelReset.connect(lambda: resets.append(True))
        with patch.object(engine.local_library, 'refreshFrom') as refresh:
            assert engine.refresh_receipts() is True
            assert engine.refresh_receipts() is True
            assert not refresh.called and not resets
    finally:
        close_session(app, engine, root)


@pytest.mark.parametrize('expression', ['"undefined"', '"null"', '""'])
def test_missing_cover_never_resolves_missing_value_as_filename(expression):
    application(); engine = QQmlEngine(); warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    component = QQmlComponent(engine)
    base = Path(__file__).parent/'w1-cover-test.qml'
    component.setData(('import QtQuick\nMissingCover { width: 100; height: 150; artworkSource: '+expression+' }').encode(), QUrl.fromLocalFile(str(base)))
    item = component.create()
    assert item is not None, [e.toString() for e in component.errors()]
    try:
        QTest.qWait(5)
        assert item.property('safeArtworkSource').toString() == ''
        assert item.property('placeholderVisible') is True
        assert warnings == []
    finally:
        item.deleteLater(); engine.deleteLater(); QTest.qWait(5)


def test_organizer_review_is_lazy_background_single_shot_and_exact():
    application(); catalog = games(); original = copy.deepcopy(catalog)
    entered = threading.Event(); release = threading.Event(); worker_threads = []
    expected = organizer_preview(catalog)
    def builder(data):
        worker_threads.append(threading.get_ident()); entered.set()
        assert release.wait(5)
        return organizer_preview(data)
    view = OrganizerReport(catalog, builder=builder)
    try:
        assert not entered.is_set()
        view.start(); view.start()
        assert entered.wait(2)
        ticks = []; QTimer.singleShot(0, lambda: ticks.append(True)); QTest.qWait(20)
        assert ticks and worker_threads == [worker_threads[0]]
        assert worker_threads[0] != threading.get_ident()
        release.set()
        for _ in range(200):
            QTest.qWait(5)
            if view._job is None: break
        assert view._job is None and view.text == expected and catalog == original
        view.start(); assert len(worker_threads) == 1
    finally:
        release.set(); view.shutdown()


def test_social_snapshot_retains_all_exact_identities_without_heavy_metadata_copy():
    catalog = games()
    class NoCopy:
        def __deepcopy__(self, memo): raise AssertionError('Unrelated metadata was copied')
    catalog[0]['variants'][0]['metadata']['large_unrelated_payload'] = NoCopy()
    snapshot = social_catalog_snapshot(catalog)
    assert [g['id'] for g in snapshot] == ['a', 'b']
    assert [g['variants'][0]['provider_game_id'] for g in snapshot] == ['10', '20']
    catalog[0]['variants'][0]['provider_game_id'] = 'changed'
    catalog[0]['title'] = 'changed'
    assert snapshot[0]['variants'][0]['provider_game_id'] == '10'
    assert snapshot[0]['title'] == 'Same title'


def test_editor_draft_notifications_do_not_rebuild_store_ownership(tmp_path):
    from app import create_session
    from test_game_install_controller import close_session
    from price_comparison_view import PriceComparisonView
    app = application()
    with patch.object(PriceComparisonView, 'setLibraryOwnership') as sync:
        engine, model, root, warnings = create_session(app, headless=True)
        try:
            assert sync.call_count == 1
            for _ in range(5):
                model.discardOrganizerPreview()
            assert sync.call_count == 1
            # Committed revision / exact grouping changes still invalidate.
            model._organizer_revision += 1
            model.organizerChanged.emit()
            assert sync.call_count == 2
            first, second = [g['id'] for g in model._source_games[:2]]
            engine.catalog_link_view._store = Mock()
            engine.catalog_link_view._store.resolution.return_value = {second: first}
            engine.catalog_link_view._store.audit.return_value = []
            engine.catalog_link_view._store.snapshot.return_value = {'revision': 0}
            engine.catalog_link_view.changed.emit()
            assert sync.call_count == 3
        finally:
            close_session(app, engine, root)


def test_session_starts_historical_review_only_when_organizer_is_opened():
    from app import create_session
    from organizer_report import _Review
    from test_game_install_controller import close_session
    app = application()
    def fixture_review(job): job.text = 'Historical fixture report'
    with patch.object(_Review, 'run', fixture_review):
        engine, model, root, warnings = create_session(app, headless=True)
        try:
            assert not engine.organizer_report._started
            root.setProperty('organizerMode', True)
            for _ in range(100):
                QTest.qWait(5)
                if engine.organizer_report._job is None: break
            assert engine.organizer_report._started
            assert engine.organizer_report.text == 'Historical fixture report'
            assert engine.rootContext().contextProperty('organizerNotice') == 'Historical fixture report'
        finally:
            close_session(app, engine, root)
