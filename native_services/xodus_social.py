"""Native Xodus Social, Presence and Party Integration Service for Waystone.

Provides high-level, asynchronous Python bindings to the Xodus Xbox Live social
backend helper (xodus-cli) using the exact line-delimited stdio wire protocol
established by Xodus Nexus SocialBroker:
- Line-framed JSON command transport: {"id": N, "args": [...], "input": ...}
- Line-framed JSON response handling: {"id": N, "ok": bool, "result": {...}, "stage": "..."}
- Bounded request queue (MAX_PENDING = 32)
- Strict request-to-response ID correlation
- Clean EOF / process exit teardown without replaying mutating commands
- Explicit binary path validation (absolute, executable, shell=False)
- Zero raw credential or auth token emission to logs or stderr
- Injectable transport interface for offline testing with exact representative frames
- Constructor does NOT start child processes; explicit connect() and close() lifecycle
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Protocol, Sequence

logger = logging.getLogger("waystone.xodus_social")

XUID_REGEX = re.compile(r"^[1-9][0-9]{0,19}$")
U64_MAX = 18446744073709551615
PARTY_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
STAGE_REGEX = re.compile(r"^[a-z-]{1,64}$")
MAX_PENDING_REQUESTS = 32
MAX_LINE_LENGTH = 8_000_000

# Representative error stage messages matching Xodus socialFailure()
STAGE_ERROR_MESSAGES: dict[str, str] = {
    "privacy-or-access-denied": "Xbox did not authorize this message. Check your Xbox communication and privacy permissions.",
    "people-permission-denied": "Xbox did not authorize this player action. Check your account’s social and privacy permissions.",
    "people-rate-limited": "Xbox is limiting social requests. Wait a moment before refreshing.",
    "people-relationship-unavailable": "Xbox could not establish this player’s current relationship. Refresh the profile to check again.",
    "people-self-action": "You cannot perform this relationship action on your own Xbox profile.",
}


class XodusSocialError(RuntimeError):
    """Base error for Xodus social operations."""


class SocialBusyError(XodusSocialError):
    """Raised when the social helper request queue is full."""


class SocialDisconnectedError(XodusSocialError):
    """Raised when the helper process disconnects or terminates."""


class SocialProtocolError(XodusSocialError):
    """Raised when incoming JSON frames violate the protocol."""


class SocialServiceError(XodusSocialError):
    """Raised when Xbox Live rejects a request with an explicit error stage."""

    def __init__(self, stage: str, message: str | None = None):
        user_msg = message or STAGE_ERROR_MESSAGES.get(
            stage, "Xbox social request failed. Check your connection and account permissions."
        )
        super().__init__(user_msg)
        self.stage = stage


@dataclass(frozen=True)
class XboxFriend:
    xuid: str
    gamertag: str
    online: bool
    presence: str
    favorite: bool = False
    in_party: bool = False
    avatar_url: str | None = None
    joinable_party_id: str | None = None
    current_game: str | None = None


@dataclass(frozen=True)
class XboxPlayerProfile:
    xuid: str
    gamertag: str
    gamerscore: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    location: str | None = None
    tenure: str | None = None


@dataclass(frozen=True)
class XboxPartyMember:
    xuid: str
    role: str | None = None


@dataclass(frozen=True)
class XboxPartyState:
    phase: str = "idle"  # 'idle', 'connecting', 'connected', 'leaving', 'error'
    party_id: str | None = None
    self_xuid: str | None = None
    members: tuple[XboxPartyMember, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class XboxMessage:
    id: str
    sender: str
    timestamp: str | None = None
    text: str = ""
    hidden: bool = False
    mine: bool = False


@dataclass(frozen=True)
class XboxConversation:
    id: str
    gamertag: str
    unread: bool = False
    participants: tuple[str, ...] = ()
    avatar_url: str | None = None
    last_message: XboxMessage | None = None


@dataclass(frozen=True)
class XboxInvite:
    id: str
    sender_xuid: str
    gamertag: str
    title_id: str
    timestamp: str | int
    activation_info: str | None = None


class SocialTransport(Protocol):
    """Abstract protocol for stdio line transport to xodus-cli."""

    async def open(self) -> None:
        ...

    async def write_line(self, data: str) -> None:
        ...

    async def read_line(self) -> str | None:
        ...

    async def close(self) -> None:
        ...

    @property
    def is_open(self) -> bool:
        ...


class SubprocessSocialTransport:
    """Production transport spawning `xodus-cli native-social listen`."""

    def __init__(self, binary_path: str, environment: dict[str, str] | None = None):
        p = Path(binary_path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Xodus binary not found at '{binary_path}'")
        if not os.access(p, os.X_OK):
            raise PermissionError(f"Xodus binary at '{binary_path}' is not executable")
        self.binary_path = str(p)
        self.environment = environment or dict(os.environ)
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def is_open(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def open(self) -> None:
        if self.is_open:
            return
        env = dict(self.environment)
        # Suppress noisy and credential-exposing debug logs
        env["XODUS_LOG"] = "error"
        env["RUST_LOG"] = "error"
        self._proc = await asyncio.create_subprocess_exec(
            self.binary_path,
            "native-social",
            "listen",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=MAX_LINE_LENGTH + 1,
        )

    async def write_line(self, data: str) -> None:
        if not self.is_open or self._proc is None or self._proc.stdin is None:
            raise SocialDisconnectedError("Xbox social helper is not connected.")
        try:
            self._proc.stdin.write(data.encode("utf-8"))
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise SocialDisconnectedError("Xbox social helper pipe broken.") from exc

    async def read_line(self) -> str | None:
        if not self.is_open or self._proc is None or self._proc.stdout is None:
            return None
        try:
            line_bytes = await self._proc.stdout.readline()
            if not line_bytes:
                return None
            return line_bytes.decode("utf-8")
        except (ConnectionResetError, OSError):
            return None

    async def close(self) -> None:
        if self._proc is not None:
            proc = self._proc
            self._proc = None
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, OSError):
                    pass
                finally:
                    if proc.returncode is None:
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        try:
                            await asyncio.shield(proc.wait())
                        except (asyncio.CancelledError, OSError):
                            pass


def validate_xuid(value: str) -> bool:
    """Validates that a string is a valid positive 64-bit integer XUID."""
    if not isinstance(value, str) or not XUID_REGEX.match(value):
        return False
    try:
        return int(value) <= U64_MAX
    except ValueError:
        return False


def validate_party_id(value: str) -> bool:
    """Validates that a string is a valid 1-128 char alphanumeric party ID."""
    return isinstance(value, str) and bool(PARTY_ID_REGEX.fullmatch(value))


class XodusSocialService:
    """Manages Xbox Live Social, Friends, and Party state via Xodus native-social."""

    def __init__(
        self,
        binary_path: str | None = None,
        transport: SocialTransport | None = None,
        default_timeout: float = 50.0,
    ):
        """Initializes service. Does NOT launch child process until connect() is called."""
        if transport is not None:
            self._transport = transport
        elif binary_path is not None:
            self._transport = SubprocessSocialTransport(binary_path)
        else:
            raise ValueError("Either binary_path or an explicit transport must be provided.")

        self.default_timeout = default_timeout
        self._seq = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._connected = False
        self._party_state = XboxPartyState()
        self._account: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._transport.is_open

    @property
    def party_state(self) -> XboxPartyState:
        return self._party_state

    @property
    def account(self) -> str | None:
        return self._account

    def set_account(self, account: str | None) -> None:
        """Sets the active Xbox account identifier."""
        self._account = account

    async def connect(self) -> None:
        """Starts transport and listener loop. Safe to call multiple times."""
        if self.is_connected:
            return
        await self._transport.open()
        self._connected = True
        self._reader_task = asyncio.create_task(self._listen_loop())

    async def close(self) -> None:
        """Stops transport and cancels any pending requests without replaying them."""
        self._connected = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None

        await self._transport.close()
        self._fail_all_pending("Xbox social helper disconnected. The request was not retried.")

    def _fail_all_pending(self, reason: str) -> None:
        """Fails all in-flight requests when disconnected, ensuring mutating commands are not retried."""
        pending = list(self._pending.values())
        self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(SocialDisconnectedError(reason))

    async def _listen_loop(self) -> None:
        """Continuously reads line-framed JSON from transport stdout."""
        try:
            while self._connected:
                raw_line = await self._transport.read_line()
                if raw_line is None:
                    # EOF reached
                    break
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                if len(raw_line) > MAX_LINE_LENGTH:
                    self._fail_all_pending("Xbox social response exceeded protocol frame limit.")
                    break
                try:
                    frame = json.loads(raw_line)
                except Exception:
                    self._fail_all_pending("Xbox social response frame was invalid JSON.")
                    break

                if not isinstance(frame, dict):
                    self._fail_all_pending("Xbox social response frame was not a JSON object.")
                    break

                req_id = frame.get("id")
                # Disallow bools which are instances of int in Python
                if type(req_id) is not int or req_id not in self._pending:
                    self._fail_all_pending("Xbox social response frame had invalid request ID.")
                    break

                fut = self._pending.pop(req_id)
                if fut.done():
                    continue

                ok = frame.get("ok")
                if type(ok) is not bool:
                    fut.set_exception(SocialDisconnectedError("Xbox social response frame had non-boolean ok status."))
                    self._fail_all_pending("Xbox social response frame had non-boolean ok status.")
                    break

                if ok:
                    result = frame.get("result")
                    if not isinstance(result, dict):
                        fut.set_exception(SocialDisconnectedError("Xbox social response frame missing result object."))
                        self._fail_all_pending("Xbox social response frame missing result object.")
                        break
                    fut.set_result(result)
                else:
                    stage = frame.get("stage")
                    if not isinstance(stage, str) or not STAGE_REGEX.match(stage):
                        fut.set_exception(SocialDisconnectedError("Xbox social response frame had invalid error stage."))
                        self._fail_all_pending("Xbox social response frame had invalid error stage.")
                        break
                    fut.set_exception(SocialServiceError(stage))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            self._connected = False
            self._fail_all_pending("Xbox social helper disconnected. The request was not retried.")

    async def raw_request(
        self,
        args: Sequence[str],
        input_data: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Submits a command over the framed stdio protocol with queue bounding and timeouts."""
        if not self.is_connected:
            raise SocialDisconnectedError("Xbox social is not connected. Call connect() first.")

        if len(self._pending) >= MAX_PENDING_REQUESTS:
            raise SocialBusyError("Xbox social is busy. Try again in a moment.")

        self._seq += 1
        req_id = self._seq
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut

        payload = json.dumps({
            "id": req_id,
            "args": list(args),
            "input": input_data,
        }) + "\n"

        req_timeout = timeout if timeout is not None else (
            60.0 if args and args[0] == "send-message" else self.default_timeout
        )

        try:
            await self._transport.write_line(payload)
            return await asyncio.wait_for(fut, timeout=req_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise XodusSocialError(f"Xbox social request '{args[0] if args else ''}' timed out.")
        except Exception:
            self._pending.pop(req_id, None)
            raise

    # -------------------------------------------------------------------------
    # Friends and Presence
    # -------------------------------------------------------------------------

    async def list_friends(self) -> list[XboxFriend]:
        """Retrieves user's Xbox friends list with online presence and party status."""
        res = await self.raw_request(["friends"])
        # Bind to the helper's existing authenticated identity, not a guessed
        # account label. Never display another account's response as this one.
        observed_account = res.get('account')
        if observed_account is not None:
            if not isinstance(observed_account, str) or not observed_account or len(observed_account) > 256:
                raise SocialProtocolError('Xbox social account identity was invalid.')
            if self._account is not None and self._account != observed_account:
                raise SocialServiceError('account-mismatch')
            self._account = observed_account
        raw_items = res.get("items", [])
        if not isinstance(raw_items, list):
            return []

        friends: list[XboxFriend] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            xuid = str(item.get("xuid", ""))
            gamertag = str(item.get("gamertag", ""))
            if not validate_xuid(xuid) or not gamertag:
                continue
            friends.append(XboxFriend(
                xuid=xuid,
                gamertag=gamertag,
                online=bool(item.get("online", False)),
                presence=str(item.get("presence", "")),
                favorite=bool(item.get("favorite", False)),
                in_party=bool(item.get("inParty", False)),
                avatar_url=item.get("avatarUrl"),
                joinable_party_id=item.get("joinablePartyId") if validate_party_id(item.get("joinablePartyId")) else None,
                current_game=(str(item.get("currentGame", ""))[:160] or None)
                    if isinstance(item.get("currentGame", ""), str) and not any(ord(c) < 32 or ord(c) == 127 for c in str(item.get("currentGame", ""))[:160]) else None,
            ))
        return friends

    async def get_player_profile(self, xuid: str = "me") -> XboxPlayerProfile:
        """Retrieves profile details for self or a given XUID."""
        if xuid != "me" and not validate_xuid(xuid):
            raise ValueError(f"Invalid XUID '{xuid}'")

        res = await self.raw_request(["profile", xuid])
        items = res.get("items", [])
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise SocialProtocolError("Missing profile in response")
        item = items[0]
        return XboxPlayerProfile(
            xuid=str(item.get("xuid", xuid)),
            gamertag=str(item.get("gamertag", "")),
            gamerscore=str(item.get("gamerscore")) if item.get("gamerscore") is not None else None,
            avatar_url=item.get("avatarUrl"),
            bio=item.get("bio"),
            location=item.get("location"),
            tenure=item.get("tenure"),
        )

    # -------------------------------------------------------------------------
    # Messaging & Inbox
    # -------------------------------------------------------------------------

    async def list_conversations(self) -> list[XboxConversation]:
        """Retrieves active conversation threads from Xbox inbox."""
        res = await self.raw_request(["inbox"])
        items = res.get("items", [])
        if not isinstance(items, list):
            return []

        convs: list[XboxConversation] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id", ""))
            gt = str(item.get("gamertag", ""))
            parts = item.get("participants", [])
            last_msg = None
            if isinstance(item.get("lastMessage"), dict):
                lm = item["lastMessage"]
                last_msg = XboxMessage(
                    id=str(lm.get("id", "")),
                    sender=str(lm.get("sender", "")),
                    timestamp=lm.get("timestamp"),
                    text=str(lm.get("text", "")),
                    hidden=bool(lm.get("hidden", False)),
                    mine=bool(lm.get("mine", False)),
                )
            convs.append(XboxConversation(
                id=cid,
                gamertag=gt,
                unread=bool(item.get("unread", False)),
                participants=tuple(str(p) for p in parts if validate_xuid(str(p))),
                avatar_url=item.get("avatarUrl"),
                last_message=last_msg,
            ))
        return convs

    async def get_conversation(self, xuid: str) -> list[XboxMessage]:
        """Retrieves message history for a 1:1 conversation with a specific XUID."""
        if not validate_xuid(xuid):
            raise ValueError(f"Invalid player XUID '{xuid}'")

        res = await self.raw_request(["conversation", xuid])
        items = res.get("items", [])
        if not isinstance(items, list):
            return []

        messages: list[XboxMessage] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            messages.append(XboxMessage(
                id=str(item.get("id", "")),
                sender=str(item.get("sender", "")),
                timestamp=item.get("timestamp"),
                text=str(item.get("text", "")),
                hidden=bool(item.get("hidden", False)),
                mine=bool(item.get("mine", False)),
            ))
        return messages

    async def send_message(self, xuid: str, text: str, account: str | None = None) -> str:
        """Sends a 1:1 text message (1 to 256 characters) to a friend."""
        if not validate_xuid(xuid):
            raise ValueError(f"Invalid recipient XUID '{xuid}'")
        text_clean = text.strip()
        if not text_clean or len(text_clean) > 256:
            raise ValueError("Message text must be between 1 and 256 characters.")

        acc = account or self._account
        if not acc:
            raise ValueError("Account identifier required to send Xbox messages.")

        input_payload = json.dumps({"account": acc, "xuid": xuid, "text": text_clean})
        res = await self.raw_request(["send-message"], input_data=input_payload)
        items = res.get("items", [])
        if isinstance(items, list) and items and isinstance(items[0], dict) and "id" in items[0]:
            return str(items[0]["id"])
        return ""

    # -------------------------------------------------------------------------
    # Party Operations
    # -------------------------------------------------------------------------

    async def _party_request(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Unwrap the DOMAIN envelope, distinct from native-social listen framing."""
        account = self._account
        res = await self.raw_request(["party"], input_data=json.dumps(fields))
        if (type(res.get('schema')) is not int or res.get('schema') != 1
                or res.get('kind') != 'party' or res.get('account') != account
                or self._account != account or res.get('operation') != fields.get('operation')
                or not isinstance(res.get('result'),dict)):
            raise SocialProtocolError('Xbox party response identity or operation did not match.')
        return res['result']

    async def get_active_parties(self, friend_xuid: str | None = None) -> list[str]:
        """Retrieves party IDs active on the account or for a friend."""
        if friend_xuid is not None and not validate_xuid(friend_xuid):
            raise ValueError(f"Invalid friend XUID '{friend_xuid}'")

        fields: dict[str, Any] = {"operation": "active"}
        if self._account:
            fields["account"] = self._account
        if friend_xuid:
            fields["xuid"] = friend_xuid

        res = await self._party_request(fields)
        ids = res.get("ids")
        if not isinstance(ids,list) or len(ids)>16 or not all(validate_party_id(pid) for pid in ids):
            raise SocialProtocolError('Xbox party list was malformed.')
        return list(ids)

    async def get_party(self, party_id: str) -> tuple[str, tuple[XboxPartyMember, ...], str | None]:
        """Fetches current party state and WebRTC transportResponse for a party ID."""
        if not validate_party_id(party_id):
            raise ValueError(f"Invalid party ID '{party_id}'")

        fields: dict[str, Any] = {"operation": "get", "partyId": party_id}
        if self._account:
            fields["account"] = self._account

        res = await self._party_request(fields)
        pid = res.get("id")
        raw_members = res.get("members")
        if pid != party_id or not validate_party_id(pid) or not isinstance(raw_members,list) or len(raw_members)>128:
            raise SocialProtocolError('Xbox party details did not match the requested party.')
        members: list[XboxPartyMember] = []
        if isinstance(raw_members, list):
            for m in raw_members:
                if isinstance(m, dict) and validate_xuid(str(m.get("xuid", ""))):
                    members.append(XboxPartyMember(
                        xuid=str(m["xuid"]),
                        role=str(m.get("role")) if m.get("role") is not None else None,
                    ))
        transport_response = res.get("transportResponse")
        if transport_response is not None and (not isinstance(transport_response,str) or len(transport_response.encode('utf-8'))>131072):
            raise SocialProtocolError('Xbox party transport description was malformed.')

        # Update local projected party state
        self._party_state = XboxPartyState(
            phase="connected" if pid else "idle",
            party_id=pid,
            members=tuple(members),
        )
        return pid, tuple(members), transport_response

    async def invite_to_party(self, party_id: str, xuid: str) -> None:
        """Invites a friend to an existing party."""
        if not validate_party_id(party_id):
            raise ValueError(f"Invalid party ID '{party_id}'")
        if not validate_xuid(xuid):
            raise ValueError(f"Invalid recipient XUID '{xuid}'")

        fields: dict[str, Any] = {"operation": "invite", "partyId": party_id, "xuid": xuid}
        if self._account:
            fields["account"] = self._account
        await self._party_request(fields)

    async def leave_party(self, party_id: str) -> None:
        """Leaves an active party."""
        if not validate_party_id(party_id):
            raise ValueError(f"Invalid party ID '{party_id}'")

        fields: dict[str, Any] = {"operation": "leave", "partyId": party_id}
        if self._account:
            fields["account"] = self._account
        try:
            await self._party_request(fields)
        finally:
            if self._party_state.party_id == party_id:
                self._party_state = XboxPartyState(phase="idle", party_id=None, members=())

    async def check_voice_policy(self, xuid: str) -> bool:
        """Checks if communication with this player is permitted by Xbox privacy policies."""
        if not validate_xuid(xuid):
            raise ValueError(f"Invalid player XUID '{xuid}'")

        fields: dict[str, Any] = {"operation": "voice-policy", "xuid": xuid}
        if self._account:
            fields["account"] = self._account

        res = await self._party_request(fields)
        return res.get("isAllowed") is True
