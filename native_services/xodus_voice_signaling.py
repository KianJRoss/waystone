"""Native Xbox Voice Signaling Service for Waystone.

Provides an asynchronous signaling facade over XodusSocialService.raw_request
implementing the exact Xbox party voice negotiation protocol from Xodus Nexus
(upstream `~/Work/nexus-party-join/src/main/xbox-party.ts` and `xbox-chat-socket.ts`).

Invariants:
- Mandatory notification handshake: requires verified chat identity (connectionId, serverNonce, xuid).
- Strict account drift verification after every await: operations abort if account changes.
- Strict boolean evaluation: voiceAllowed requires `isAllowed is True` (rejects string "false").
- Retained party ID on failed negotiation: allows caller to retry leave if cleanup fails.
- Sanitized state errors: only typed uppercase error codes stored in state, no raw exception traces.
- No bearer token or credential exposure; offer and ICE candidates are not logged.
- Explicit local microphone consent tracking (10-second window, not network auth).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import re
import time
from typing import Any, Protocol

from native_services.xodus_chat_socket import XodusChatSocket, XboxChatIdentity

logger = logging.getLogger("waystone.xodus_voice_signaling")

XUID_REGEX = re.compile(r"^[1-9][0-9]{0,19}$")
U64_MAX = 18446744073709551615
PARTY_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
INVITE_ID_REGEX = re.compile(r"^party:[A-Za-z0-9_.:-]{1,256}$")
MAX_OFFER_BYTES = 131072  # 128 KiB
MIC_CONSENT_WINDOW_SECONDS = 10.0


class XodusSignalingError(RuntimeError):
    """Base error for voice signaling operations."""


class SignalingValidationError(XodusSignalingError):
    """Raised when an invalid offer, partyId, inviteId, or XUID is supplied."""


class SignalingStateError(XodusSignalingError):
    """Raised when an operation is invalid for the current party phase or account drifts."""


class SignalingTimeoutError(XodusSignalingError):
    """Raised when Xbox party relay negotiation times out."""


@dataclass(frozen=True)
class XboxChatIdentity:
    xuid: str
    connection_id: str
    server_nonce: str


class ChatSocketProtocol(Protocol):
    """Protocol for chat notification socket providing connection identity."""

    async def connect(self) -> XboxChatIdentity:
        ...

    def close(self) -> None:
        ...


class SocialServiceProtocol(Protocol):
    """Protocol satisfied by XodusSocialService or test mocks."""

    @property
    def account(self) -> str | None:
        ...

    async def raw_request(
        self,
        args: list[str],
        input_data: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        ...


def sanitize_signaling_error(err: Any) -> str:
    if err is None:
        return "ERR_VOICE_INTERNAL"
    msg = str(err)
    if msg.startswith("ERR_"):
        return msg[:64]
    if "timeout" in msg.lower():
        return "ERR_TIMEOUT"
    if "account" in msg.lower():
        return "ERR_ACCOUNT_CHANGED"
    if "relay" in msg.lower():
        return "ERR_RELAY_DISCONNECTED"
    if "permission" in msg.lower() or "denied" in msg.lower():
        return "ERR_ACCESS_DENIED"
    if "offer" in msg.lower() or "invalid" in msg.lower():
        return "ERR_INVALID_PAYLOAD"
    return "ERR_VOICE_INTERNAL"


def validate_xuid(value: str) -> bool:
    if not isinstance(value, str) or not XUID_REGEX.fullmatch(value):
        return False
    try:
        return int(value) <= U64_MAX
    except ValueError:
        return False


def validate_party_id(value: str) -> bool:
    return isinstance(value, str) and bool(PARTY_ID_REGEX.fullmatch(value))


def _unwrap_party_domain_envelope(
    raw_response: dict[str, Any],
    account: str,
    operation: str,
) -> dict[str, Any]:
    """Strictly unwrap and validate the native Xbox party domain envelope.

    The upstream Xodus helper returns a domain envelope:
    {
        "schema": 1,
        "kind": "party",
        "account": "<bound_account>",
        "operation": "<expected_operation>",
        "result": { ... }
    }
    Validates schema == 1, kind == 'party', account == bound, operation == expected,
    and returns the inner 'result' dictionary. Rejects mismatched accounts, spoofed
    operations, or missing inner results.
    """
    if not isinstance(raw_response, dict):
        raise XodusSignalingError("ERR_INVALID_DOMAIN_ENVELOPE")

    if (type(raw_response.get('schema')) is not int or raw_response.get('schema') != 1
            or raw_response.get('kind') != 'party' or raw_response.get('account') != account
            or raw_response.get('operation') != operation
            or not isinstance(raw_response.get('result'), dict)):
        raise SignalingStateError('ERR_INVALID_PARTY_ENVELOPE')
    return raw_response['result']


def validate_invite_id(value: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("party:")
        and len(value) <= 262
        and bool(INVITE_ID_REGEX.fullmatch(value))
    )


def validate_offer(offer: str) -> None:
    if not isinstance(offer, str) or not offer:
        raise SignalingValidationError("ERR_INVALID_OFFER")
    if len(offer.encode("utf-8")) > MAX_OFFER_BYTES:
        raise SignalingValidationError("ERR_OFFER_TOO_LARGE")
    try:
        data = json.loads(offer)
        if not isinstance(data, dict):
            raise SignalingValidationError("ERR_INVALID_OFFER")
        if data.get("messageType") != "transportConnectionRequestMessage":
            raise SignalingValidationError("ERR_INVALID_OFFER_MESSAGE_TYPE")
        if not isinstance(data.get("sdp"), str) or not data["sdp"].startswith("v=0\r\n"):
            raise SignalingValidationError("ERR_INVALID_SDP")
    except json.JSONDecodeError as exc:
        raise SignalingValidationError("ERR_INVALID_OFFER_JSON") from exc


@dataclass(frozen=True)
class VoiceSignalingState:
    phase: str = "idle"  # 'idle', 'connecting', 'connected', 'leaving', 'error'
    party_id: str | None = None
    self_xuid: str | None = None
    members: tuple[dict[str, Any], ...] = ()
    error: str | None = None


class XodusVoiceSignalingService:
    """Async voice signaling facade orchestrating Xbox party WebRTC connections."""

    def __init__(
        self,
        social_service: SocialServiceProtocol,
        chat_socket: ChatSocketProtocol | None = ...,  # type: ignore[assignment]
        poll_interval: float = 1.0,
        poll_timeout: float = 30.0,
    ) -> None:
        self._social = social_service
        if chat_socket is ...:
            self._chat = XodusChatSocket(social_service)
        else:
            self._chat = chat_socket
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout
        self._state = VoiceSignalingState()
        self._bound_account: str | None = None
        self._microphone_consent_until: float = 0.0
        self._generation = 0
        self._lock = asyncio.Lock()
        self._negotiating = False
        self._cancel_event = asyncio.Event()

    @property
    def state(self) -> VoiceSignalingState:
        # Check for account drift
        current_acc = self._social.account
        if self._bound_account and self._bound_account != current_acc:
            return VoiceSignalingState(phase="idle")
        return self._state

    @property
    def phase(self) -> str:
        return self.state.phase

    @property
    def party_id(self) -> str | None:
        return self.state.party_id

    def is_microphone_allowed(self) -> bool:
        """Returns True if local user gave explicit microphone consent within the active window."""
        return time.monotonic() < self._microphone_consent_until

    def allow_xbox_microphone(self) -> float:
        """Grants explicit local user consent for hardware microphone acquisition."""
        if self.state.phase not in ("connecting", "connected"):
            raise SignalingStateError("Connect the Xbox party before granting microphone consent.")
        self._microphone_consent_until = time.monotonic() + MIC_CONSENT_WINDOW_SECONDS
        return self._microphone_consent_until

    async def start_xbox_party(self, offer: str, party_id: str | None = None) -> dict[str, Any]:
        """Initiates or joins an Xbox voice party by submitting a WebRTC SDP offer."""
        validate_offer(offer)
        if party_id is not None and not validate_party_id(party_id):
            raise SignalingValidationError("ERR_INVALID_PARTY_ID")

        async with self._lock:
            acc = self._social.account
            if not acc:
                raise SignalingStateError("ERR_SIGN_IN_REQUIRED")

            if self._negotiating or self._state.phase in ("connecting", "connected", "leaving") or self._state.party_id:
                raise SignalingStateError("ERR_PARTY_ALREADY_ACTIVE")

            if not self._chat:
                raise SignalingStateError("ERR_CHAT_SOCKET_REQUIRED")

            generation = self._generation + 1
            self._generation = generation
            self._bound_account = acc
            self._cancel_event.clear()
            self._state = VoiceSignalingState(phase="connecting")

            # Upstream Step 1: Handshake with notification socket to obtain session credentials
            identity = await self._chat.connect()

            # Account drift check after chat connect await
            if self._generation != generation or self._social.account != acc:
                self._state = VoiceSignalingState(phase="idle")
                raise SignalingStateError("ERR_ACCOUNT_CHANGED")

            if (
                not validate_xuid(identity.xuid)
                or not identity.connection_id
                or len(identity.connection_id) > 512
                or not identity.server_nonce
                or len(identity.server_nonce) > 4096
            ):
                self._state = VoiceSignalingState(phase="error", error="ERR_INVALID_CHAT_IDENTITY")
                raise SignalingStateError("ERR_INVALID_CHAT_IDENTITY")

            operation = "join" if party_id else "create"
            fields: dict[str, Any] = {
                "operation": operation,
                "offer": offer,
                "account": acc,
                "connectionId": identity.connection_id,
                "serverNonce": identity.server_nonce,
            }
            if party_id:
                fields["partyId"] = party_id

        # Release lock during HTTP negotiation/polling so concurrent leave can execute immediately
        return await self._execute_negotiation(fields, acc, generation, identity.xuid)

    async def accept_xbox_party_invite(self, offer: str, invite_id: str) -> dict[str, Any]:
        """Accepts an Xbox party invite by submitting a WebRTC SDP offer."""
        validate_offer(offer)
        if not validate_invite_id(invite_id):
            raise SignalingValidationError("ERR_INVALID_INVITE_ID")

        async with self._lock:
            acc = self._social.account
            if not acc:
                raise SignalingStateError("ERR_SIGN_IN_REQUIRED")

            if self._negotiating or self._state.phase in ("connecting", "connected", "leaving") or self._state.party_id:
                raise SignalingStateError("ERR_PARTY_ALREADY_ACTIVE")

            if not self._chat:
                raise SignalingStateError("ERR_CHAT_SOCKET_REQUIRED")

            generation = self._generation + 1
            self._generation = generation
            self._bound_account = acc
            self._cancel_event.clear()
            self._state = VoiceSignalingState(phase="connecting")

            identity = await self._chat.connect()

            if self._generation != generation or self._social.account != acc:
                self._state = VoiceSignalingState(phase="idle")
                raise SignalingStateError("ERR_ACCOUNT_CHANGED")

            if (
                not validate_xuid(identity.xuid)
                or not identity.connection_id
                or len(identity.connection_id) > 512
                or not identity.server_nonce
                or len(identity.server_nonce) > 4096
            ):
                self._state = VoiceSignalingState(phase="error", error="ERR_INVALID_CHAT_IDENTITY")
                raise SignalingStateError("ERR_INVALID_CHAT_IDENTITY")

            fields: dict[str, Any] = {
                "operation": "accept-invite",
                "offer": offer,
                "inviteId": invite_id,
                "account": acc,
                "connectionId": identity.connection_id,
                "serverNonce": identity.server_nonce,
            }

        # Release lock during HTTP negotiation/polling so concurrent leave can execute immediately
        return await self._execute_negotiation(fields, acc, generation, identity.xuid)

    async def _execute_negotiation(
        self,
        fields: dict[str, Any],
        account: str,
        generation: int,
        self_xuid: str,
    ) -> dict[str, Any]:
        allocated_party_id: str | None = None
        self._negotiating = True
        try:
            raw = await self._social.raw_request(['party'],input_data=json.dumps(fields))
            result = _unwrap_party_domain_envelope(raw,account,fields['operation'])
            candidate = result.get('id')
            allocated_party_id = candidate if validate_party_id(candidate) else None
            if self._generation != generation or self._social.account != account:
                raise SignalingStateError('ERR_ACCOUNT_CHANGED')
            return await self._poll_and_complete_answer(result,account,generation,self_xuid)
        except BaseException as exc:
            retained = allocated_party_id
            if allocated_party_id:
                try:
                    raw = await self._social.raw_request(['party'],input_data=json.dumps({
                        'operation':'leave','partyId':allocated_party_id,'account':account}))
                    result = _unwrap_party_domain_envelope(raw,account,'leave')
                    if result.get('success') is not True:
                        raise SignalingStateError('ERR_LEAVE_UNCONFIRMED')
                    retained = None
                except Exception:
                    pass
            if self._chat: self._chat.close()
            if self._generation == generation or retained:
                self._bound_account = account if retained else self._bound_account
                self._state = VoiceSignalingState(phase='error',party_id=retained,
                    error=sanitize_signaling_error(exc))
            raise
        finally:
            self._negotiating = False

    async def _poll_and_complete_answer(
        self,
        initial_res: dict[str, Any],
        account: str,
        generation: int,
        self_xuid: str,
    ) -> dict[str, Any]:
        pid = str(initial_res.get("id", ""))
        if not validate_party_id(pid):
            raise SignalingValidationError("ERR_INVALID_PARTY_ID")

        raw_members = initial_res.get("members", [])
        members: list[dict[str, Any]] = []
        if isinstance(raw_members, list):
            for m in raw_members:
                if isinstance(m, dict) and validate_xuid(str(m.get("xuid", ""))):
                    members.append({"xuid": str(m["xuid"]), "role": m.get("role")})

        transport_response = initial_res.get("transportResponse")
        deadline = time.monotonic() + self._poll_timeout

        # Upstream polling loop: poll every interval until transportResponse is ready
        while not transport_response and time.monotonic() < deadline:
            try:
                await asyncio.wait_for(self._cancel_event.wait(), timeout=self._poll_interval)
                # If cancel_event is set, we were aborted by leave
                raise SignalingStateError("ERR_PARTY_CANCELLED")
            except asyncio.TimeoutError:
                pass

            if self._generation != generation or self._social.account != account or self._cancel_event.is_set():
                raise SignalingStateError("ERR_ACCOUNT_CHANGED")

            get_fields = json.dumps({"operation": "get", "partyId": pid, "account": account})
            raw_poll_res = await self._social.raw_request(["party"], input_data=get_fields)

            if self._generation != generation or self._social.account != account:
                raise SignalingStateError("ERR_ACCOUNT_CHANGED")

            poll_res = _unwrap_party_domain_envelope(raw_poll_res, account, "get")

            if str(poll_res.get("id")) != pid:
                raise XodusSignalingError("ERR_PARTY_ID_MISMATCH")

            transport_response = poll_res.get("transportResponse")
            if isinstance(poll_res.get("members"), list):
                members = [
                    {"xuid": str(m["xuid"]), "role": m.get("role")}
                    for m in poll_res["members"]
                    if isinstance(m, dict) and validate_xuid(str(m.get("xuid", "")))
                ]

        if not transport_response or not isinstance(transport_response, str):
            raise SignalingTimeoutError("ERR_RELAY_TIMEOUT")

        try:
            answer = json.loads(transport_response)
        except json.JSONDecodeError as exc:
            raise XodusSignalingError("ERR_INVALID_TRANSPORT_RESPONSE") from exc

        if (
            not isinstance(answer, dict)
            or answer.get("status") != "success"
            or answer.get("sdpType") != "answer"
            or not isinstance(answer.get("sdp"), str)
            or not answer["sdp"].startswith("v=0\r\n")
            or not isinstance(answer.get("iceCandidates"), list)
            or len(answer["iceCandidates"]) > 128
        ):
            raise XodusSignalingError("ERR_RELAY_REJECTED")

        self._state = VoiceSignalingState(
            phase="connecting",
            party_id=pid,
            self_xuid=self_xuid,
            members=tuple(members),
        )

        return {
            "id": pid,
            "answer": {
                "status": "success",
                "sdpType": "answer",
                "sdp": answer["sdp"],
                "iceCandidates": answer["iceCandidates"],
            },
        }

    async def confirm_xbox_party(self, party_id: str) -> None:
        """Confirms that WebRTC audio and data channel successfully established."""
        if not validate_party_id(party_id):
            raise SignalingValidationError("ERR_INVALID_PARTY_ID")

        async with self._lock:
            acc = self._social.account
            if self._bound_account != acc or self._state.phase != "connecting" or self._state.party_id != party_id:
                raise SignalingStateError("ERR_PARTY_NOT_CONNECTING")
            self._state = VoiceSignalingState(
                phase="connected",
                party_id=party_id,
                self_xuid=self._state.self_xuid,
                members=self._state.members,
            )

    async def leave_xbox_party(self) -> None:
        """Invalidate immediately; retain owned ID if Xbox hasn't confirmed Leave."""
        self._cancel_event.set()
        async with self._lock:
            pid, acc = self._state.party_id, self._bound_account
            self._generation += 1
            self._microphone_consent_until = 0.0
            self._state = VoiceSignalingState(phase='leaving', party_id=pid)
            try:
                if pid:
                    if not acc or self._social.account != acc:
                        raise SignalingStateError('ERR_ACCOUNT_CHANGED')
                    raw = await self._social.raw_request(['party'], input_data=json.dumps({
                        'operation':'leave','partyId':pid,'account':acc}))
                    result = _unwrap_party_domain_envelope(raw,acc,'leave')
                    if self._social.account != acc or result.get('success') is not True:
                        raise SignalingStateError('ERR_LEAVE_UNCONFIRMED')
            except BaseException:
                self._state = VoiceSignalingState(phase='error',party_id=pid,error='ERR_LEAVE_UNCONFIRMED')
                raise
            else:
                self._state = VoiceSignalingState()
                self._bound_account = None
            finally:
                if self._chat: self._chat.close()

    async def xbox_voice_allowed(self, xuid: str) -> bool:
        """Queries Xbox privacy policy to check if communication is authorized with a player."""
        if not validate_xuid(xuid):
            raise SignalingValidationError("ERR_INVALID_XUID")

        acc = self._social.account
        if not acc:
            return False

        fields = json.dumps({"operation": "voice-policy", "xuid": xuid, "account": acc})
        try:
            raw_res = await self._social.raw_request(["party"], input_data=fields)
            res = _unwrap_party_domain_envelope(raw_res, acc, "voice-policy")
            return self._social.account == acc and res.get("isAllowed") is True
        except Exception:
            return False
