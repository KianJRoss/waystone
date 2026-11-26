"""Real QML macro rename data-preservation probe, no service/profile/device access."""
from pathlib import Path
import sys,json
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine,QQmlComponent
app=QGuiApplication([]);engine=QQmlEngine();root=Path(__file__).resolve().parents[1]
c=QQmlComponent(engine,QUrl.fromLocalFile(str(root/'craft-projects/playnite-desktop/app/controller_native/MacrosTab.qml')))
o=c.create();assert o,c.errors()
p={'macros':{'Old':{'repeat_count':1,'cancel_on_release':True,'steps':[{'type':'delay','duration_ms':1,'future_step':7}],'future_macro':99}},'routes':{'Controller:A':'Macro:Old','Controller:B':'Macro:Old'}}
w=engine.newQObject(o);loaded=w.property('loadFromProfile').callWithInstance(w,[engine.toScriptValue(p)]);assert not loaded.isError(),loaded.toString()
o.setProperty('macroName','Renamed');result=w.property('validateAndApply').callWithInstance(w,[])
raw=o.property('profileData');out=raw.toVariant() if hasattr(raw,'toVariant') else raw
print(json.dumps({'valid':result.toVariant(),'unknown_macro_retained':out['macros'].get('Renamed',{}).get('future_macro')==99,'all_bound_routes_retargeted':all(out['routes'].get(k)=='Macro:Renamed' for k in p['routes']),'renamed_route_keys':list(out['routes'])},indent=2))
