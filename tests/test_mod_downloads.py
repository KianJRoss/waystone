"""Offline tests for Waystone phase 2a mod file listing and safe downloads."""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from native_services import mod_catalog
from native_services.mod_downloads import (
    DownloadError,
    download,
    gamebanana_files,
    modrinth_files,
    safe_filename,
)

MIB = 1024 * 1024


# ---------------------------------------------------------------------------
# Fakes: no network and no real transport is ever used.
# ---------------------------------------------------------------------------

class FakeFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        return self.payload


class FakeResponse:
    def __init__(self, chunks, headers=None):
        self._chunks = list(chunks)
        self.headers = headers if headers is not None else {}
        self.closed = False
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        if not self._chunks:
            return b""
        chunk = self._chunks[0]
        if size is not None and size > 0 and len(chunk) > size:
            self._chunks[0] = chunk[size:]
            return chunk[:size]
        return self._chunks.pop(0)

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, chunks, headers=None):
        self.chunks = chunks
        self.headers = headers
        self.requests = []
        self.responses = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        response = FakeResponse(self.chunks, self.headers)
        self.responses.append(response)
        return response


def _boom(request, timeout=None):  # pragma: no cover - must never run
    raise AssertionError("opener must not be called")


GB_PAYLOAD = {
    "_sName": "My Mod",
    "_aFiles": [
        {
            "_idRow": 1,
            "_sFile": "old.zip",
            "_nFilesize": 100,
            "_sDownloadUrl": "https://gamebanana.com/dl/1",
            "_sMd5Checksum": "aa",
            "_sDescription": "",
            "_tsDateAdded": 1700000000,
        },
        {
            "_idRow": 2,
            "_sFile": "new.zip",
            "_nFilesize": "250",
            "_sDownloadUrl": "https://gamebanana.com/dl/2",
            "_sMd5Checksum": None,
            "_sDescription": "Newest build",
            "_tsDateAdded": 1700000500,
        },
        {
            "_idRow": 3,
            "_sFile": "middle.zip",
            "_nFilesize": 175,
            "_sDownloadUrl": "https://gamebanana.com/dl/3",
            "_sMd5Checksum": "cc",
            "_sDescription": "Middle",
            "_tsDateAdded": 1700000250,
        },
    ],
}

MR_PAYLOAD = [
    {
        "id": "ver-2",
        "name": "2.0",
        "version_number": "2.0",
        "game_versions": ["1.21", "1.20"],
        "loaders": ["fabric"],
        "date_published": "2024-02-02T00:00:00Z",
        "files": [
            {
                "url": "https://cdn.modrinth.com/a.jar",
                "filename": "a.jar",
                "size": 10,
                "primary": False,
                "hashes": {"sha1": "aaa", "sha512": "xxx"},
            },
            {
                "url": "https://cdn.modrinth.com/b.jar",
                "filename": "b.jar",
                "size": 20,
                "primary": True,
                "hashes": {"sha1": "bbb", "sha512": "yyy"},
            },
        ],
    },
    {
        "id": "ver-1",
        "name": "1.0",
        "version_number": "1.0",
        "game_versions": ["1.20"],
        "loaders": ["forge"],
        "date_published": "2023-01-01T00:00:00Z",
        "files": [
            {
                "url": "https://cdn.modrinth.com/old.jar",
                "filename": "old.jar",
                "size": 5,
                "hashes": {"sha1": "ccc"},
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# gamebanana_files
# ---------------------------------------------------------------------------

def test_gamebanana_maps_all_fields():
    fetch = FakeFetcher(GB_PAYLOAD)
    files = gamebanana_files(42, fetch=fetch)

    assert len(files) == 3
    newest = files[0]
    assert newest == {
        "file_id": "2",
        "name": "new.zip",
        "size": 250,
        "url": "https://gamebanana.com/dl/2",
        "md5": "",
        "sha1": "",
        "description": "Newest build",
        "date": datetime.fromtimestamp(1700000500, timezone.utc).isoformat(),
        "game_versions": [],
        "loaders": [],
    }
    assert fetch.urls == [
        "https://gamebanana.com/apiv11/Mod/42?_csvProperties=_aFiles,_sName"
    ]


def test_gamebanana_newest_first():
    files = gamebanana_files(42, fetch=FakeFetcher(GB_PAYLOAD))
    assert [item["file_id"] for item in files] == ["2", "3", "1"]


def test_gamebanana_missing_files_is_empty():
    fetch = FakeFetcher({"_sName": "No files"})
    assert gamebanana_files(7, fetch=fetch) == []
    assert len(fetch.urls) == 1


def test_gamebanana_non_dict_payload_is_empty():
    assert gamebanana_files(7, fetch=FakeFetcher([1, 2, 3])) == []


# ---------------------------------------------------------------------------
# modrinth_files
# ---------------------------------------------------------------------------

def test_modrinth_url_without_filters_has_no_query():
    fetch = FakeFetcher(MR_PAYLOAD)
    modrinth_files("sodium", fetch=fetch)
    assert fetch.urls == [
        "https://api.modrinth.com/v2/project/sodium/version"
    ]


def test_modrinth_url_with_loaders_only():
    fetch = FakeFetcher(MR_PAYLOAD)
    modrinth_files("sodium", loaders=["fabric", "quilt"], fetch=fetch)

    parsed = urlparse(fetch.urls[0])
    assert parsed.path == "/v2/project/sodium/version"
    query = parse_qs(parsed.query)
    assert json.loads(query["loaders"][0]) == ["fabric", "quilt"]
    assert "game_versions" not in query


def test_modrinth_url_with_loaders_and_game_versions():
    fetch = FakeFetcher(MR_PAYLOAD)
    modrinth_files("sodium", loaders=["fabric"],
                   game_versions=["1.21", "1.20"], fetch=fetch)

    query = parse_qs(urlparse(fetch.urls[0]).query)
    assert json.loads(query["loaders"][0]) == ["fabric"]
    assert json.loads(query["game_versions"][0]) == ["1.21", "1.20"]


def test_modrinth_uses_primary_file_and_keeps_api_order():
    files = modrinth_files("sodium", fetch=FakeFetcher(MR_PAYLOAD))

    assert [item["file_id"] for item in files] == ["ver-2", "ver-1"]
    first = files[0]
    assert first == {
        "file_id": "ver-2",
        "name": "b.jar",
        "size": 20,
        "url": "https://cdn.modrinth.com/b.jar",
        "md5": "",
        "sha1": "bbb",
        "description": "2.0",
        "date": "2024-02-02T00:00:00Z",
        "game_versions": ["1.21", "1.20"],
        "loaders": ["fabric"],
    }


def test_modrinth_falls_back_to_first_file_without_primary():
    files = modrinth_files("sodium", fetch=FakeFetcher(MR_PAYLOAD))
    assert files[1]["name"] == "old.jar"
    assert files[1]["sha1"] == "ccc"


def test_modrinth_accepts_list_and_skips_versions_without_files():
    payload = [
        {"id": "empty", "name": "Empty", "files": []},
        {"id": "ok", "name": "Ok", "files": [
            {"url": "https://cdn/o.jar", "filename": "o.jar", "size": 1,
             "hashes": {}},
        ]},
    ]
    files = modrinth_files("p", fetch=FakeFetcher(payload))
    assert [item["file_id"] for item in files] == ["ok"]
    assert files[0]["sha1"] == ""


def test_modrinth_non_list_payload_is_empty():
    assert modrinth_files("p", fetch=FakeFetcher({"error": "boom"})) == []


# ---------------------------------------------------------------------------
# safe_filename
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("../x", "x"),
    ("a/b", "b"),
    ("", "download"),
    (".hidden", "hidden"),
    ("日本語.zip", "___.zip"),
    ("naïve mod.jar", "na_ve mod.jar"),
    ("..", "download"),
    ("a\\b\\c.zip", "c.zip"),
    ("bad\x00name\x0a?.bin", "badname_.bin"),
    ("valid file-1.0 (1)+.jar", "valid file-1.0 (1)+.jar"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_none_is_download():
    assert safe_filename(None) == "download"


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------

def test_download_rejects_non_https():
    for url in ("http://example.com/a.zip", "ftp://example.com/a.zip",
                "file:///etc/passwd", ""):
        with pytest.raises(DownloadError):
            download(url, "/tmp/never", filename="a.zip", opener=_boom)


def test_download_writes_file_and_removes_part(tmp_path):
    payload = b"hello mod bytes"
    opener = FakeOpener([payload], headers={"Content-Length": str(len(payload))})
    result = download("https://example.com/a.zip", tmp_path,
                      filename="a.zip", opener=opener)

    assert result == tmp_path / "a.zip"
    assert result.read_bytes() == payload
    assert not (tmp_path / "a.zip.part").exists()

    request, timeout = opener.requests[0]
    assert timeout == 60
    assert request.full_url == "https://example.com/a.zip"
    assert request.get_header("User-agent") == mod_catalog.USER_AGENT
    assert opener.responses[0].closed


def test_download_size_cap_removes_part(tmp_path):
    payload = b"0123456789"
    opener = FakeOpener([payload])
    with pytest.raises(DownloadError):
        download("https://example.com/a.zip", tmp_path, filename="a.zip",
                 max_bytes=5, opener=opener)
    assert not (tmp_path / "a.zip.part").exists()
    assert not (tmp_path / "a.zip").exists()


def test_download_md5_ok(tmp_path):
    payload = b"verified payload"
    digest = hashlib.md5(payload).hexdigest()
    result = download("https://example.com/a.zip", tmp_path, filename="a.zip",
                      md5=digest.upper(), opener=FakeOpener([payload]))
    assert result.read_bytes() == payload


def test_download_md5_mismatch_deletes_file(tmp_path):
    opener = FakeOpener([b"tampered"])
    with pytest.raises(DownloadError, match="checksum mismatch"):
        download("https://example.com/a.zip", tmp_path, filename="a.zip",
                 md5="0" * 32, opener=opener)
    assert list(tmp_path.iterdir()) == []
    assert opener.responses[0].closed


def test_download_sha1_ok(tmp_path):
    payload = b"sha1 payload"
    digest = hashlib.sha1(payload).hexdigest()
    result = download("https://example.com/a.zip", tmp_path, filename="a.zip",
                      sha1=digest, opener=FakeOpener([payload]))
    assert result.read_bytes() == payload


def test_download_md5_then_sha1_mismatch_deletes_file(tmp_path):
    payload = b"sha1 payload"
    good_md5 = hashlib.md5(payload).hexdigest()
    with pytest.raises(DownloadError, match="checksum mismatch"):
        download("https://example.com/a.zip", tmp_path, filename="a.zip",
                 md5=good_md5, sha1="0" * 40, opener=FakeOpener([payload]))
    assert list(tmp_path.iterdir()) == []


def test_download_existing_matching_file_skips_opener(tmp_path):
    payload = b"already here"
    target = tmp_path / "a.zip"
    target.write_bytes(payload)
    digest = hashlib.md5(payload).hexdigest()

    result = download("https://example.com/a.zip", tmp_path, filename="a.zip",
                      md5=digest, opener=_boom)
    assert result == target
    assert target.read_bytes() == payload


def test_download_existing_matching_sha1_skips_opener(tmp_path):
    payload = b"already here"
    target = tmp_path / "a.zip"
    target.write_bytes(payload)

    result = download("https://example.com/a.zip", tmp_path, filename="a.zip",
                      sha1=hashlib.sha1(payload).hexdigest(), opener=_boom)
    assert result == target


def test_download_existing_mismatch_is_replaced(tmp_path):
    target = tmp_path / "a.zip"
    target.write_bytes(b"stale")
    payload = b"fresh"
    result = download("https://example.com/a.zip", tmp_path, filename="a.zip",
                      md5=hashlib.md5(payload).hexdigest(),
                      opener=FakeOpener([payload]))
    assert result.read_bytes() == payload


def test_download_without_checksum_redownloads(tmp_path):
    target = tmp_path / "a.zip"
    target.write_bytes(b"old")
    opener = FakeOpener([b"new"])
    download("https://example.com/a.zip", tmp_path, filename="a.zip",
             opener=opener)
    assert len(opener.requests) == 1
    assert target.read_bytes() == b"new"


def test_download_progress_reports_totals(tmp_path):
    payload = b"0123456789"
    seen = []

    def progress(written, total):
        seen.append((written, total))

    download("https://example.com/a.zip", tmp_path, filename="a.zip",
             opener=FakeOpener([b"0123", b"456789"],
                               headers={"Content-Length": str(len(payload))}),
             progress=progress)

    assert seen == [(4, 10), (10, 10)]


def test_download_progress_without_content_length_uses_zero(tmp_path):
    seen = []
    download("https://example.com/a.zip", tmp_path, filename="a.zip",
             opener=FakeOpener([b"abc"]),
             progress=lambda written, total: seen.append((written, total)))
    assert seen == [(3, 0)]


def test_download_sanitises_filename_and_creates_dest(tmp_path):
    dest = tmp_path / "nested" / "dir"
    result = download("https://example.com/a.zip", dest,
                      filename="../../evil?.zip", opener=FakeOpener([b"x"]))
    assert result == dest / "evil_.zip"
    assert result.read_bytes() == b"x"
