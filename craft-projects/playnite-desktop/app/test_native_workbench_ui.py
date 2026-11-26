"""Offline integration of native-first browse UI with unmatched installs."""
import os,json,hashlib,tempfile,sys,unittest
from pathlib import Path
os.environ.update(QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QT_QUICK_CONTROLS_STYLE='Basic')
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[2]))
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import QCoreApplication,QEvent,QObject
from app import create_session

class NativeWorkbenchUiTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.qt=QGuiApplication.instance() or QGuiApplication(['native-workbench-offline'])
 def test_native_browse_keeps_unmatched_installed_record(self):
  data={'schema_version':2,'catalog_kind':'playnite-display-selected-copy-v1','games':[
   {'id':'game-a','title':'Known','variants':[{'source':'Steam','source_id':'guid','provider_game_id':'42','platform':'PC','status':'unknown'}]}]}
  def record(product):
   variant=hashlib.sha256(('v'+product).encode()).hexdigest()
   return dict(provider='steam',product_id=product,record_key=hashlib.sha256(('r'+product).encode()).hexdigest(),
    variant_key=variant,state='installed',title='Local '+product,platform='PC',evidence=['fixture'],
    source_context={'host':'FixtureHost','instance':'fixture','installation_id':'fixture'},
    canonical_game_ids=['game-a'] if product=='42' else [],join_state='matched' if product=='42' else 'unmatched',
    join_evidence=[{'source':'Steam','provider_game_id':product}],
    current_claims={'installation':'installed','client_only':False,'gameplay_tested':False,'verification_scope':'metadata and directory fixture'},
    launch_intent={'provider':'steam','variant_key':variant,'dry_run':True,'execution_supported':False})
  with tempfile.TemporaryDirectory() as tmp:
   catalog=Path(tmp)/'catalog.json';catalog.write_text(json.dumps(data))
   records=[record('42'),record('99')]
   payload=dict(schema_version=1,inventory_kind='native-connections-readonly',host='FixtureHost',observed_at='2026-11-15T00:00:00Z',
    catalog_sha256=hashlib.sha256(catalog.read_bytes()).hexdigest(),execution_supported=False,connections=records,
    canonical_links={'game-a':[records[0]['variant_key']]},unmatched_native_variant_keys=[records[1]['variant_key']],ambiguous_native_variant_keys=[])
   inventory=Path(tmp)/'native.json';inventory.write_text(json.dumps(payload))
   engine,model,root,warnings=create_session(self.qt,headless=True,catalog=catalog,connections=inventory)
   try:
    view=engine.local_library
    self.assertEqual(len(view.rows),2)
    self.assertEqual({r['joinState'] for r in view.rows},{'matched','unmatched'})
    # The separate "On this device" page was removed (Kian 2026-11-22): installed games live in Library.
    self.assertIsNone(root.findChild(QObject,'nativeLibraryDialog'))
    self.assertEqual(model.totalCount,1) # native records do not rewrite migration registry
    self.assertEqual(model.overview('game-a')['state'],'Installed · native observation')
    self.assertEqual(warnings,[])
   finally:
    root.setVisible(False);engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.qt.processEvents()

if __name__=='__main__':unittest.main()
