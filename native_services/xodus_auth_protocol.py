"""Strict validator for Xodus Nexus's private external sign-in stdio frames."""
from __future__ import annotations
import json
from urllib.parse import urlsplit

PREFIX = "NEXUS_AUTH_V1 "
FRAME_LIMIT = 128 * 1024
AUTH_HOSTS = frozenset({
    "login.live.com", "account.live.com", "login.microsoftonline.com",
    "login.microsoft.com", "account.microsoft.com", "sisu.xboxlive.com",
})
HEADER_NAMES = frozenset({
    "cxh-capabilities", "cxh-correlationid", "cxh-msabinaryversion",
    "cxh-identityclientbinaryversion", "cxh-osversioninfo", "cxh-platform",
    "cxh-protocol", "cxh-source", "hostapp",
})

class XodusAuthProtocolError(ValueError): pass

def trusted_auth_url(value):
    if not isinstance(value, str) or not value or len(value) > 32768:
        return False
    try:
        parts = urlsplit(value)
        return (
            parts.scheme == "https" and parts.hostname in AUTH_HOSTS
            and parts.port in (None, 443) and not parts.username and not parts.password
            and not parts.fragment and not any(ord(c) < 32 for c in value)
        )
    except (ValueError, TypeError):
        return False

def parse_auth_frame(line):
    if not isinstance(line, str) or not line.startswith(PREFIX):
        return None
    if len(line.encode("utf-8")) > FRAME_LIMIT:
        raise XodusAuthProtocolError("Sign-in frame exceeds limit")
    try:
        frame = json.loads(line[len(PREFIX):])
    except (ValueError, TypeError):
        raise XodusAuthProtocolError("Invalid sign-in frame") from None
    if not isinstance(frame, dict):
        raise XodusAuthProtocolError("Invalid sign-in frame")
    kind = frame.get("type")
    if kind == "ready":
        if frame.get("version") != 1 or type(frame.get("version")) is not int:
            raise XodusAuthProtocolError("Unsupported sign-in protocol")
        return {"type": "ready", "version": 1}
    session = frame.get("session")
    if type(session) is not int or not 1 <= session <= 2**53 - 1:
        raise XodusAuthProtocolError("Invalid sign-in session")
    if kind == "close":
        return {"type": "close", "session": session}
    if kind == "context":
        context = frame.get("context")
        if not isinstance(context, str) or len(context) > 4096:
            raise XodusAuthProtocolError("Invalid sign-in context")
        return {"type": "context", "session": session, "context": context}
    if kind != "open" or not trusted_auth_url(frame.get("url")):
        raise XodusAuthProtocolError("Unsupported sign-in frame")
    headers = frame.get("headers")
    if not isinstance(headers, dict) or len(headers) > len(HEADER_NAMES):
        raise XodusAuthProtocolError("Invalid sign-in headers")
    clean = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise XodusAuthProtocolError("Invalid sign-in headers")
        key = name.lower()
        if key not in HEADER_NAMES or key in clean or len(value) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise XodusAuthProtocolError("Invalid sign-in headers")
        clean[key] = value
    return {"type": "open", "session": session, "url": frame["url"], "headers": clean}

class XodusAuthProtocol:
    def __init__(self):
        self.ready = False
        self.latest = 0
        self.active = None

    def accept(self, line):
        frame = parse_auth_frame(line)
        if frame is None:
            return None
        if frame["type"] == "ready":
            if self.ready:
                raise XodusAuthProtocolError("Duplicate sign-in handshake")
            self.ready = True
            return frame
        if not self.ready:
            raise XodusAuthProtocolError("Missing sign-in handshake")
        sid = frame["session"]
        if frame["type"] == "open":
            if sid <= self.latest or self.latest >= 32:
                raise XodusAuthProtocolError("Invalid sign-in session order")
            self.latest = sid
            self.active = sid
        elif sid != self.active:
            raise XodusAuthProtocolError("Unexpected sign-in session")
        elif frame["type"] == "close":
            self.active = None
        return frame

