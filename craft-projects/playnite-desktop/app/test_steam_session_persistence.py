"""Real asynchronous view lifecycle with synthetic auth and secret-store fixtures."""
from copy import deepcopy
from threading import Event
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtTest import QTest
from platform_social_view import PlatformSocialView
from test_platform_social_view import app, settle, Native, Web, OWNER

class Store:
    def __init__(self):self.rows={};self.calls=[];self.fail=False;self.save_enter=None;self.save_release=None
    def load(self,p):self.calls.append(('load',p));return deepcopy(self.rows.get(p))
    def save(self,p,r):
        self.calls.append(('save',p))
        if self.save_enter:self.save_enter.set();assert self.save_release.wait(3)
        if self.fail:raise RuntimeError('fixture-private-store-message')
        self.rows[p]=deepcopy(r)
    def clear(self,p):self.calls.append(('clear',p));self.rows.pop(p,None)

class Adapter(Web):
    account_id=OWNER
    def __init__(self,refresh='fixture-refresh'):super().__init__();self.refresh=refresh
    def session_record(self):return {'kind':'steam_refresh','account_id':OWNER,'refresh_token':self.refresh}

class Qr:
    challenge_url='https://s.team/q/fixture'
    def __init__(self):self.begins=0;self.restores=0;self.enter=None;self.release=None;self.fail=False
    def begin(self):self.begins+=1;return {'challenge_url':self.challenge_url,'interval':5}
    def poll(self):return Adapter()
    def cancel(self):pass
    def restore(self,owner,refresh):
        self.restores+=1
        if self.enter:self.enter.set();assert self.release.wait(3)
        if self.fail:raise RuntimeError('fixture-private-refresh-error')
        assert owner==OWNER and refresh=='fixture-refresh'
        return Adapter('fixture-rotated-refresh')

def signed_in(store,qr=None):
    app();qr=qr or Qr();v=PlatformSocialView(native=Native(),session_store=store,qr_session=qr)
    v.beginSteamSignIn();settle(v);v.pollSteamSignIn();settle(v)
    assert v.steamConnected and v.achievementAccess
    return v

def test_shutdown_restart_restores_without_new_qr_and_saves_rotation():
    store=Store();first=signed_in(store);first.shutdown()
    assert store.rows['steam']['refresh_token']=='fixture-refresh'
    qr=Qr();second=PlatformSocialView(native=Native(),session_store=store,qr_session=qr)
    try:
        before=list(store.calls);assert qr.restores==0 and not second.steamConnected
        assert store.calls==before
        second.restoreSession();settle(second)
        assert second.steamConnected and second.steamAccount==OWNER and second.achievementAccess
        assert qr.begins==0 and qr.restores==1
        assert store.rows['steam']['refresh_token']=='fixture-rotated-refresh'
    finally:second.shutdown()

def test_explicit_signout_forgets_even_when_next_view_is_disconnected():
    store=Store();v=signed_in(store)
    try:v.disconnectSteam();settle(v);assert not v.steamConnected and not v.achievementAccess and not store.rows
    finally:v.shutdown()
    store.rows['steam']={'kind':'steam_refresh','account_id':OWNER,'refresh_token':'fixture-refresh'}
    fresh=PlatformSocialView(session_store=store,qr_session=Qr())
    try:fresh.disconnectSteam();settle(fresh);assert not store.rows
    finally:fresh.shutdown()

def test_locked_store_keeps_current_login_but_reports_not_remembered_and_can_retry():
    store=Store();store.fail=True;v=signed_in(store)
    try:
        assert 'this run only' in v.sessionStatus and not store.rows
        assert 'fixture-private' not in v.sessionStatus
        store.fail=False;v.rememberSteamSession();settle(v)
        assert 'saved securely' in v.sessionStatus and 'steam' in store.rows
    finally:v.shutdown()

def test_no_saved_record_has_no_network_or_automatic_qr():
    app();store=Store();qr=Qr();native=Native();v=PlatformSocialView(native=native,session_store=store,qr_session=qr)
    try:
        assert store.calls==[]
        v.restoreSession();settle(v)
        assert qr.begins==qr.restores==native.calls==0 and not v.steamConnected
    finally:v.shutdown()

def test_expired_restore_preserves_record_and_never_leaks_secret_or_opens_qr():
    app();store=Store();store.rows['steam']={'kind':'steam_refresh','account_id':OWNER,'refresh_token':'fixture-refresh'}
    qr=Qr();qr.fail=True;v=PlatformSocialView(session_store=store,qr_session=qr)
    try:
        v.restoreSession();settle(v)
        assert not v.steamConnected and qr.begins==0 and 'steam' in store.rows
        assert 'fixture-private' not in v.sessionStatus
    finally:v.shutdown()

def test_logout_during_restore_cannot_rebind_or_preserve_remembered_login():
    app();store=Store();store.rows['steam']={'kind':'steam_refresh','account_id':OWNER,'refresh_token':'fixture-refresh'}
    qr=Qr();qr.enter=Event();qr.release=Event();v=PlatformSocialView(session_store=store,qr_session=qr)
    try:
        v.restoreSession();assert qr.enter.wait(2)
        v.disconnectSteam();qr.release.set();settle(v)
        assert not v.steamConnected and not v.achievementAccess and not store.rows
    finally:qr.release.set();v.shutdown()

def test_logout_during_keyring_write_is_followed_by_durable_forget():
    app();store=Store();store.save_enter=Event();store.save_release=Event()
    v=PlatformSocialView(native=Native(),session_store=store,qr_session=Qr())
    try:
        v.beginSteamSignIn();settle(v);v.pollSteamSignIn()
        for _ in range(200):
            QTest.qWait(5)
            if store.save_enter.is_set():break
        assert store.save_enter.is_set()
        v.disconnectSteam();store.save_release.set();settle(v)
        assert not v.steamConnected and not store.rows
        assert store.calls[-1]==('clear','steam')
    finally:store.save_release.set();v.shutdown()
