"""Qt adapter for exact-game plugin history; no account/network or game execution.

Attach as context property pluginFeaturesView, call selectGame(canonicalId) when
selection changes. Fields are detached scalar summaries, not raw plugin HTML.
"""
import json
from pathlib import Path
import sys
from PySide6.QtCore import QObject, Property, Signal, Slot
ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from native_services.plugin_features import game_plugin_details

MAX_DISPLAY_BYTES=65536


class PluginFeaturesView(QObject):
    changed=Signal()

    def __init__(self, archive=None, parent=None, *, fetch=None):
        super().__init__(parent)
        self._archive=archive
        self._fetch=fetch or game_plugin_details
        self._selected=''
        self._data={}
        self._summary=[]
        self._error=''

    @Property(str,notify=changed)
    def selectedGame(self):return self._selected

    @Property(str,notify=changed)
    def error(self):return self._error

    @Property(bool,notify=changed)
    def available(self):return self._archive is not None

    @Property('QVariantList',notify=changed)
    def summaries(self):return [dict(row) for row in self._summary]

    @Property(str,notify=changed)
    def detailJson(self):
        raw=json.dumps(self._data,ensure_ascii=True,allow_nan=False)
        return raw if len(raw.encode())<=MAX_DISPLAY_BYTES else json.dumps({
            'canonical_id':self._selected,'notice':'Imported detail exceeds display limit; summaries remain available'})

    @Slot(str)
    def selectGame(self,canonical_id):
        self._selected=canonical_id if isinstance(canonical_id,str) else ''
        self._data={};self._summary=[];self._error=''
        if not self._selected or self._archive is None:
            self.changed.emit();return
        try:
            result=self._fetch(self._archive,self._selected,limit=50)
            # Reject malformed/non-JSON display payloads atomically, not stale game data.
            json.dumps(result,allow_nan=False)
            for name,data in result['datasets'].items():
                row={'dataset':name,'status':data['status'],
                     'items':data.get('item_count',0),'basis':'Imported plugin history'}
                if name=='GameActivity' and 'recorded_seconds' in data:
                    row['recordedSeconds']=data['recorded_seconds']
                    row['durationLabel']=f"{data['recorded_seconds']/3600:.1f} hours recorded"
                if name=='SuccessStory':row['notice']='No achievement completion percentage inferred'
                if name=='HowLongToBeat':row['notice']='Choose a match' if data.get('selection_required') else 'Original provider values; not live sync'
                self._summary.append(row)
            self._data=result
        except Exception:
            self._data={};self._summary=[]
            self._error='Plugin history could not be loaded for this game'
        self.changed.emit()
