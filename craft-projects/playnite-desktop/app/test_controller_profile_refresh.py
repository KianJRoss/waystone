"""Regression: reading capabilities must never trigger another automatic refresh."""
import time
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from app import create_session
from native_services.controller_command_protocol import PROFILE_OPERATIONS

def test_profile_read_is_not_a_commit_and_mutation_refreshes_once():
    app=QGuiApplication.instance() or QGuiApplication([])
    engine,model,root,warnings=create_session(app,headless=True)
    class Client:
        names=['Default'];revision='a'*64;cap_reads=0
        def request(self,op,**fields):
            if op=='profile_command_capabilities':
                self.cap_reads+=1
                return dict(schema_version=1,operations=sorted(PROFILE_OPERATIONS))
            if op=='profiles':return dict(names=list(self.names),active_profile='Default',revision=self.revision)
            if op=='status':return dict(running=True,devices=[])
            if op=='profile_get':return dict(name='Default',revision=self.revision,profile={'routes':{}})
            if op=='profile_create':
                self.names=self.names+[fields['name']];self.revision='b'*64
                return dict(name=fields['name'],active_profile='Default',revision=self.revision,committed=True)
            raise AssertionError(op)
    c=Client();profiles=engine.controller_profile_commands;profiles._client=c;engine.unified_controller._client=c
    try:
        def settle():
            end=time.monotonic()+3
            while profiles.busy and time.monotonic()<end:QTest.qWait(10)
            assert not profiles.busy
        profiles.refreshProfiles();settle();QTest.qWait(100)
        assert not profiles.busy and c.cap_reads==1
        profiles.createProfile('Next');settle();QTest.qWait(100);settle()
        assert not profiles.busy and c.cap_reads==2 and 'Next' in profiles.profiles
        QTest.qWait(150)
        assert c.cap_reads==2 and not profiles.busy
    finally:
        profiles.shutdown();engine.controller_device_commands.shutdown();engine.controller_commands.shutdown()
        engine.unified_controller.shutdown();engine.controller_live.shutdown();root.close()
        engine.voice_media.shutdown();engine.social_view.shutdown();engine.deleteLater();QTest.qWait(30)
