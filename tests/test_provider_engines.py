import hashlib
import os
from pathlib import Path

import pytest

from native_services import provider_engines as pe


def make_exec(path, data=b"#!/bin/sh\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o755)
    return path


def test_resolve_prefers_the_apps_own_copy_else_waystones_download(tmp_path):
    root, home = tmp_path / "app", tmp_path / "home"
    assert pe.resolve("gogdl", root=root, home=home) == pe.download_path("gogdl", home)
    make_exec(pe.bundled_path("gogdl", root))
    assert pe.resolve("gogdl", root=root, home=home) == pe.bundled_path("gogdl", root)


def test_ensure_downloads_once_and_checks_the_checksum(tmp_path, monkeypatch):
    payload = b"official build"
    monkeypatch.setitem(pe.PINS, "nile", pe.Pin("1.2.0", "https://github.com/imLinguin/nile/x", hashlib.sha256(payload).hexdigest(),
                                               len(payload), "https://github.com/imLinguin/nile"))
    calls = []
    def download(url, dest, limit):
        calls.append(url)
        Path(dest).write_bytes(payload)
    target = pe.download_path("nile", tmp_path)
    assert pe.ensure(target, home=tmp_path, download=download) == target
    assert target.read_bytes() == payload and os.access(target, os.X_OK) and len(calls) == 1
    pe.ensure(target, home=tmp_path, download=download)
    assert len(calls) == 1                                    # already there: no second download


def test_bad_checksum_installs_nothing(tmp_path):
    target = pe.download_path("legendary", tmp_path)
    with pytest.raises(pe.EngineError):
        pe.ensure(target, home=tmp_path, download=lambda url, dest, limit: Path(dest).write_bytes(b"tampered"))
    assert not target.exists() and not [p for p in target.parent.iterdir()]


def test_other_paths_are_never_written(tmp_path):
    elsewhere = tmp_path / "app/components/provider_engines/gogdl"
    assert pe.ensure(elsewhere, home=tmp_path, download=lambda *a: pytest.fail("must not download")) == elsewhere
    assert not elsewhere.exists()
