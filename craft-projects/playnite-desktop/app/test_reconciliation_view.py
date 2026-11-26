import copy,json,tempfile,unittest
from pathlib import Path
from library_model import LibraryModel,load_fixture
from native_services.catalog_reconciliation import KIND, PACKAGE_CATALOG_LIBRARY

class ReconciliationViewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.games=copy.deepcopy(load_fixture(Path(__file__).with_name('fixture-library.json')))
        for game in self.games:
            for v in game['variants']:v['source']='Unassigned'
        self.path=Path(self.temp.name)/'reconciliation.json'
    def write(self,assignments):self.path.write_text(json.dumps({'kind':KIND,'assignments':assignments}))
    def test_attribution_updates_display_and_filters_not_original(self):
        ident=self.games[0]['id'];self.write({ident:{'library':PACKAGE_CATALOG_LIBRARY,'evidence_kind':'exact_catalog_folder','package_id':'folder'}})
        m=LibraryModel(self.games);m.configure_reconciliation(self.path)
        self.assertEqual(m.overview(ident)['sources'],PACKAGE_CATALOG_LIBRARY)
        m.setFilters('',PACKAGE_CATALOG_LIBRARY,'All platforms','All states');self.assertEqual(m.rowCount(),1)
        self.assertTrue(all(v['source']=='Unassigned' for g in m._source_games for v in g['variants']))
        self.assertIn(PACKAGE_CATALOG_LIBRARY,m.library_sources())
    def test_unknown_identity_refused(self):
        self.write({'missing':{'library':'Emulation','evidence_kind':'explicit_emulator_action'}})
        with self.assertRaises(ValueError):LibraryModel(self.games).configure_reconciliation(self.path)
    def test_wrong_evidence_refused(self):
        self.write({self.games[0]['id']:{'library':PACKAGE_CATALOG_LIBRARY,'evidence_kind':'title_similarity'}})
        with self.assertRaises(ValueError):LibraryModel(self.games).configure_reconciliation(self.path)
    def test_emulator_label_does_not_change_platform(self):
        ident=self.games[0]['id'];self.write({ident:{'library':'Emulation','evidence_kind':'explicit_emulator_action'}})
        m=LibraryModel(self.games);before=m.overview(ident)['platforms'];m.configure_reconciliation(self.path)
        self.assertEqual(m.overview(ident)['sources'],'Emulation');self.assertEqual(m.overview(ident)['platforms'],before)
