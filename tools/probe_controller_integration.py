"""Synthetic end-to-end: real Qt + Python socket + .NET native parser; NO input engine."""
import os,sys,subprocess,tempfile,selectors,json
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'craft-projects/playnite-desktop/app'),str(ROOT)]
from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session
from native_services.unified_controller_control import ControllerControlClient,ControllerControlError

def wait(view):
    for _ in range(2000):
        QTest.qWait(5)
        if not view.busy:return
    raise AssertionError('Qt worker deadline exceeded')
fixture=Path(sys.argv[1]).resolve()
assert fixture.name=='UnifiedController.Waystone.Fixture.dll'
checks=[]
Path(os.environ.get('WAYSTONE_PROBE_EVIDENCE', str(ROOT/'evidence/controller-integration-20261116'))).mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='waystone-controller-e2e-') as directory:
    proc=subprocess.Popen(['/usr/bin/dotnet',str(fixture),directory],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    view=engine=window=None
    try:
        sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ)
        assert sel.select(15),'Native fixture not ready'
        endpoint=proc.stdout.readline().strip();sel.close()
        assert endpoint==str(Path(directory)/'waystone-unified-controller/control.sock')
        client=ControllerControlClient(endpoint,timeout=2)
        app=QGuiApplication.instance() or QGuiApplication([])
        engine,model,window,warnings=create_session(app,headless=True,controller_client=client)
        window.resize(1280,800);window.show();QTest.qWait(50)
        view=engine.unified_controller
        assert not view.available and not view.busy
        dialog=window.findChild(QObject,'controllerDashboard');dialog.open();wait(view);QTest.qWait(40)
        assert view.available and view.profiles==['Default','Other']
        checks.append('real Qt dialog opens explicit same-UID native fixture connection')
        view.loadProfile('Other');wait(view);QTest.qWait(30)
        mapping=dialog.findChild(QObject,'controllerMappingEditor')
        assert mapping.addRoute('Controller:A','Controller:B')
        assert dialog.property('dirty')
        raw=dialog.findChild(QObject,'controllerProfileEditor').property('text')
        assert json.loads(raw)['routes']['Controller:A']=='Controller:B'
        revision=client.request('profiles')['revision']
        view.saveProfile(raw);wait(view);QTest.qWait(40)
        assert not dialog.property('dirty')
        actual=client.request('profile_get',name='Other')
        assert actual['profile']['routes']=={'Controller:A':'Controller:B'}
        assert actual['profile']['fixture_profile_extension'] is True
        assert client.request('profiles')['active_profile']=='Default'
        checks.append('embedded mapping edit saved through native parser with readback; inactive edit does not activate')
        try:
            client.request('profile_select',name='Other',expected_revision=revision)
            raise AssertionError('stale write accepted')
        except ControllerControlError as e:assert e.code=='ERR_REVISION_CONFLICT' and not e.uncertain
        checks.append('real native stale revision rejection')
        view.useProfile();wait(view);assert view.activeProfile=='Other'
        checks.append('explicit native profile selection')
        doc=json.loads((Path(directory)/'data/profiles.json').read_text())
        assert doc['fixture_extension']=={'retained':True} and len(doc['profiles'])==2
        assert (Path(directory)/'data/profiles.json.bak').is_file()
        checks.append('native backup, unrelated profile and unknown fields preserved')
        view.cancelMacros();wait(view)
        assert not warnings,warnings
        window.grabWindow().save(str(Path(os.environ.get('WAYSTONE_PROBE_EVIDENCE', str(ROOT/'evidence/controller-integration-20261116')))/'synthetic-controller-ui.png'))
        print(json.dumps({'passed':True,'checks':checks,'qml_warnings':warnings,'physical_devices_or_runtime_started':False},indent=2))
    finally:
        if view is not None:view.shutdown()
        if engine is not None:engine.voice_media.shutdown();engine.social_view.shutdown()
        if window is not None:window.close()
        if engine is not None:engine.deleteLater();QTest.qWait(30)
        if proc.poll() is None:
            try:proc.communicate('\n',timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:proc.kill();proc.communicate()
