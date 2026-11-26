"""Native positive install evidence is distinct from imported source claims."""
import copy
import unittest
from connection_view import ConnectionView
from library_model import LibraryModel
from PySide6.QtCore import Qt

class NativeInstallProjectionTests(unittest.TestCase):
    def test_exact_positive_projection_preserves_import_and_filters(self):
        variants=[{'source':'Steam','provider_game_id':'123','source_id':'a','platform':'PC','status':'unknown'},
                  {'source':'Epic','provider_game_id':'123','source_id':'b','platform':'PC','status':'unknown'}]
        games=[{'id':'game','title':'Test','variants':variants}]
        before=copy.deepcopy(games)
        view=ConnectionView()
        record={'provider':'steam','product_id':'123','state':'installed','join_state':'matched'}
        view._data={'canonical_links':{'game':['native-a']}}
        view._index={'native-a':record}
        self.assertTrue(view.installed_for_variant('game',variants[0]))
        self.assertFalse(view.installed_for_variant('game',variants[1]))
        model=LibraryModel(games,connections=None)
        model._connections=view
        # Matches() uses the same independently validated canonical links.
        model.setFilters('','Steam','All platforms','installed')
        self.assertEqual(model.rowCount(),1)
        model.setFilters('','Epic','All platforms','installed')
        self.assertEqual(model.rowCount(),0)
        self.assertEqual(games,before)
        record['join_state']='ambiguous'
        self.assertFalse(view.installed_for_variant('game',variants[0]))
        record['join_state']='matched';record['state']='client-only'
        self.assertFalse(view.installed_for_variant('game',variants[0]))

if __name__=='__main__':unittest.main()
