import unittest
from PySide6.QtCore import QCoreApplication
from plugin_features_view import PluginFeaturesView,MAX_DISPLAY_BYTES

class PluginFeaturesViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def test_selection_updates_and_clears(self):
        calls=[]
        def fetch(archive,ident,**kw):
            calls.append(ident)
            return {'canonical_id':ident,'datasets':{'GameActivity':{'status':'present','item_count':2,'recorded_seconds':3600}}}
        view=PluginFeaturesView('selected.sqlite',fetch=fetch)
        self.assertEqual(calls,[])
        view.selectGame('gc');self.assertEqual(calls,['gc'])
        self.assertEqual(view.summaries[0]['durationLabel'],'1.0 hours recorded')
        detached=view.summaries;detached[0]['status']='edited'
        self.assertEqual(view.summaries[0]['status'],'present')
        view.selectGame('');self.assertEqual(view.summaries,[])
    def test_error_clears_previous_game_and_hides_exception_path(self):
        def fetch(*a,**kw):raise OSError('/private/path')
        view=PluginFeaturesView('selected.sqlite',fetch=fetch);view.selectGame('new')
        self.assertEqual(view.summaries,[]);self.assertNotIn('/private',view.error)
    def test_no_archive_does_not_query(self):
        view=PluginFeaturesView(fetch=lambda *a,**kw:self.fail('query'))
        view.selectGame('g');self.assertFalse(view.available)
    def test_large_detail_is_bounded_without_losing_summary(self):
        view=PluginFeaturesView('selected.sqlite',fetch=lambda *a,**kw:{'datasets':{
            'HowLongToBeat':{'status':'present','item_count':1,'items':['x'*(MAX_DISPLAY_BYTES+1)]}}})
        view.selectGame('g')
        self.assertLess(len(view.detailJson.encode()),MAX_DISPLAY_BYTES)
        self.assertEqual(view.summaries[0]['items'],1)
