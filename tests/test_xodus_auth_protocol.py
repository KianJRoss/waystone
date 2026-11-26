import json
import pytest
from native_services.xodus_auth_protocol import (
    PREFIX, XodusAuthProtocol, XodusAuthProtocolError, parse_auth_frame, trusted_auth_url,
)

def frame(value): return PREFIX + json.dumps(value)

def test_auth_frame_allows_only_trusted_https_targets_and_allowlisted_headers():
    assert trusted_auth_url("https://login.live.com/ppsecure/InlineLogin.srf")
    assert not trusted_auth_url("http://login.live.com/")
    assert not trusted_auth_url("https://login.live.com.evil.test/")
    assert not trusted_auth_url("https://user:pass@login.live.com/")
    assert not trusted_auth_url("https://login.live.com:444/")
    out=parse_auth_frame(frame({"type":"open","session":1,"url":"https://login.live.com/","headers":{"HostApp":"CloudExperienceHost"}}))
    assert out["headers"]=={"hostapp":"CloudExperienceHost"}
    for url,headers in [
        ("https://evil.test/","{}"),
        ("https://login.live.com/","bad"),
    ]:
        with pytest.raises(XodusAuthProtocolError):
            parse_auth_frame(frame({"type":"open","session":1,"url":url,"headers":headers if isinstance(headers,dict) else {"Authorization":"secret"}}))

def test_auth_protocol_requires_handshake_and_monotonic_sessions():
    p=XodusAuthProtocol()
    opened={"type":"open","session":1,"url":"https://login.live.com/","headers":{}}
    with pytest.raises(XodusAuthProtocolError):p.accept(frame(opened))
    assert p.accept(frame({"type":"ready","version":1}))["type"]=="ready"
    assert p.accept(frame(opened))["type"]=="open"
    assert p.accept(frame({"type":"close","session":1}))["type"]=="close"
    assert p.accept(frame({"type":"open","session":2,"url":"https://account.live.com/","headers":{}}))["session"]==2
    with pytest.raises(XodusAuthProtocolError):p.accept(frame({"type":"close","session":1}))

def test_auth_protocol_rejects_duplicate_ready_unknown_header_and_oversized_payload():
    p=XodusAuthProtocol();ready=frame({"type":"ready","version":1})
    p.accept(ready)
    with pytest.raises(XodusAuthProtocolError):p.accept(ready)
    for bad in [
        {"type":"open","session":1,"url":"https://login.live.com/","headers":{"Authorization":"secret"}},
        {"type":"context","session":1,"context":"x"*4097},
    ]:
        with pytest.raises(XodusAuthProtocolError):parse_auth_frame(frame(bad))
    with pytest.raises(XodusAuthProtocolError):parse_auth_frame(PREFIX + "x"*(128*1024))

