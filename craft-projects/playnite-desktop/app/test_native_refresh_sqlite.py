"""Offscreen controller regressions; fake collector only, no game/store calls."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication
from native_refresh_view import NativeRefreshView, _source_digest, _Rejected, CATALOG_LIMIT


@pytest.fixture(scope='module')
def core_app():
    return QCoreApplication.instance() or QCoreApplication([])


def snapshot(digest):
    return {'schema_version':1,'inventory_kind':'native-connections-readonly',
        'catalog_sha256':digest,'execution_supported':False,
        'observed_at':'2026-11-15T12:00:00+00:00',
        'source_identity':{'kind':'native-library-sqlite-v1','database_sha256':digest}}


def test_native_route_above_json_cap_and_public_signals(tmp_path,core_app):
    db=tmp_path/'native.sqlite3'
    with db.open('wb') as f:f.truncate(CATALOG_LIMIT+1)
    calls=[]
    def runner(argv,**kwargs):
        calls.append(argv)
        assert '--library-db' in argv and '--catalog' not in argv
        assert kwargs['shell'] is False
        digest,_=_source_digest(db,True)
        Path(argv[argv.index('--output')+1]).write_text(json.dumps(snapshot(digest)))
        return SimpleNamespace(returncode=0)
    view=NativeRefreshView(None,tmp_path/'output',tmp_path,runner=runner,library_db=db)
    signals=[];view.refreshed.connect(signals.append)
    assert view.refresh();assert view.wait(5000)
    assert not view.error and not view.busy and len(signals)==1 and len(calls)==1
    view.close()


@pytest.mark.parametrize('mutation',['generation','wrong-binding'])
def test_native_refresh_rejects_changed_generation_or_hash(tmp_path,core_app,mutation):
    db=tmp_path/'native.sqlite3';db.write_bytes(b'selected native bytes')
    def runner(argv,**kwargs):
        digest,_=_source_digest(db,True)
        data=snapshot(digest)
        if mutation=='generation':db.write_bytes(b'changed native bytes!')
        else:data['source_identity']['database_sha256']='wrong'
        Path(argv[argv.index('--output')+1]).write_text(json.dumps(data))
        return SimpleNamespace(returncode=0)
    view=NativeRefreshView(None,tmp_path/'output',tmp_path,runner=runner,library_db=db)
    signals=[];view.refreshed.connect(signals.append)
    assert view.refresh();assert view.wait(5000)
    assert view.error and not signals
    view.close()


@pytest.mark.parametrize('kind',['fifo','symlink'])
def test_native_refresh_unsafe_path_never_runs_collector(tmp_path,core_app,kind):
    db=tmp_path/'unsafe.sqlite3'
    if kind=='fifo':os.mkfifo(db)
    else:
        target=tmp_path/'target';target.write_bytes(b'bytes');db.symlink_to(target)
    def forbidden(*args,**kwargs):raise AssertionError('collector called for unsafe source')
    view=NativeRefreshView(None,tmp_path/'output',tmp_path,runner=forbidden,library_db=db)
    assert view.refresh();assert view.wait(3000)
    assert view.error
    view.close()
