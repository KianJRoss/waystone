"""Native Xbox Chat Notification Socket for Waystone.

Implements the Xbox Live chat push transport and notification handshake from Xodus Nexus
(upstream `~/Work/nexus-party-join/src/main/xbox-chat-socket.ts`).

Invariants:
- Handshake protocol: calls `chat-auth` over social transport, initiates WebSocket connection
  to `wss://chat.xboxlive.com`, sends System WhoAmI, and receives connectionId/serverNonce.
- Complete secret isolation: authentication keys and WebSocket URLs are NEVER logged or exposed.
- Lazy networking: `websockets` library is imported lazily inside `connect()`; offline test suites
  inject a mock socket factory without external dependencies.
- Strict validation: payload bounds (< 64 KiB auth, < 256 KiB frames), decimal uint64 XUIDs,
  connectionId (<= 512 chars), serverNonce (<= 4096 chars).
- Clean lifecycle: generation-tracked state, prompt cancellation, and resource teardown.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import re
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import quote

# Isolated private logger for websockets with propagate=False and NullHandler
# Prevents any global DEBUG root logging from capturing raw AuthKey URLs or frame payloads
_ws_private_logger = logging.getLogger("waystone.xodus_chat_socket.ws_client")
_ws_private_logger.handlers = [logging.NullHandler()]
_ws_private_logger.propagate = False

logger = logging.getLogger("waystone.xodus_chat_socket")

XUID_REGEX = re.compile(r"^[1-9][0-9]{0,19}$")
U64_MAX = 18446744073709551615
MAX_AUTH_BYTES = 65536
MAX_FRAME_BYTES = 262144
HANDSHAKE_TIMEOUT_SECONDS = 20.0


class ChatSocketError(RuntimeError):
    """Base error for chat notification socket operations."""


class ChatAuthError(ChatSocketError):
    """Raised when chat-auth fails or yields an invalid schema."""


class ChatHandshakeError(ChatSocketError):
    """Raised when the WebSocket handshake times out or receives invalid frames."""


@dataclass(frozen=True)
class XboxChatIdentity:
    xuid: str
    connection_id: str
    server_nonce: str


class AsyncSocketProtocol(Protocol):
    """Protocol for an active WebSocket connection."""

    async def send(self, data: str) -> None:
        ...

    async def recv(self) -> str:
        ...

    async def close(self) -> None:
        ...


SocketFactory = Callable[[str, list[str]], Awaitable[AsyncSocketProtocol]]


class SocialTransportProtocol(Protocol):
    """Protocol satisfied by XodusSocialService."""

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


def validate_xuid(value: str) -> bool:
    if not isinstance(value, str) or not XUID_REGEX.fullmatch(value):
        return False
    try:
        return int(value) <= U64_MAX
    except ValueError:
        return False


def is_bounded_string(value: Any, max_len: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_len
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


class XodusChatSocket:
    """Manages Xbox Live chat notification socket and identity handshake."""

    def __init__(
        self,
        social_service: SocialTransportProtocol,
        socket_factory: SocketFactory | None = None,
        handshake_timeout: float = HANDSHAKE_TIMEOUT_SECONDS,
    ) -> None:
        self._social = social_service
        self._socket_factory = socket_factory
        self._handshake_timeout = handshake_timeout

        self._identity: XboxChatIdentity | None = None
        self._bound_account: str | None = None
        self._socket: AsyncSocketProtocol | None = None
        self._generation = 0
        self._sequence = 0
        self._lock = asyncio.Lock()

        self._party_changed_callbacks: list[Callable[[str, int], None]] = []
        self._party_removed_callbacks: list[Callable[[], None]] = []
        self._connection_loss_callbacks: list[Callable[[str], None]] = []
        self._listener_task: asyncio.Task[None] | None = None
        self._close_tasks: set[asyncio.Task] = set()

    @property
    def identity(self) -> XboxChatIdentity | None:
        current_acc = self._social.account
        if self._bound_account and self._bound_account != current_acc:
            return None
        return self._identity

    @property
    def is_connected(self) -> bool:
        return self.identity is not None and self._socket is not None

    def on_party_changed(self, callback: Callable[[str, int], None]) -> None:
        self._party_changed_callbacks.append(callback)

    def on_party_removed(self, callback: Callable[[], None]) -> None:
        self._party_removed_callbacks.append(callback)

    def on_connection_loss(self, callback: Callable[[str], None]) -> None:
        """Registers a callback invoked with an error code when socket is lost unexpectedly."""
        self._connection_loss_callbacks.append(callback)

    async def connect(self) -> XboxChatIdentity:
        """Authenticates and establishes the notification WebSocket, returning verified identity."""
        async with self._lock:
            current_acc = self._social.account
            if not current_acc:
                raise ChatAuthError("ERR_SIGN_IN_REQUIRED")

            # Check for existing valid identity on the same account
            if self._identity and self._bound_account == current_acc and self._socket:
                return self._identity

            self.close()

            generation = self._generation + 1
            self._generation = generation
            self._bound_account = current_acc
            self._sequence = 0

            # Step 1: Obtain short-lived authKey via chat-auth CLI command
            auth_res = await self._social.raw_request(["chat-auth"])

            # Recheck account drift after auth await
            if self._generation != generation or self._social.account != current_acc:
                raise ChatSocketError("ERR_ACCOUNT_CHANGED")

            if (
                type(auth_res.get("schema")) is not int
                or auth_res.get("schema") != 1
                or auth_res.get("kind") != "chat-auth"
                or auth_res.get("account") != current_acc
            ):
                raise ChatAuthError("ERR_INVALID_AUTH_SCHEMA")

            xuid = auth_res.get("xuid")
            if not validate_xuid(xuid):
                raise ChatAuthError("ERR_INVALID_XUID")

            auth_key = auth_res.get("authKey")
            if not is_bounded_string(auth_key, 16384):
                raise ChatAuthError("ERR_INVALID_AUTH_KEY")

            # Step 2: Establish WebSocket connection
            # NOTE: URL is kept strictly in local scope; never emitted to logs or state
            url = f"wss://chat.xboxlive.com/users/xuid({xuid})/chat/connect?AuthKey={quote(auth_key, safe='')}"

            socket: AsyncSocketProtocol
            if self._socket_factory:
                socket = await self._socket_factory(url, ["chat"])
            else:
                try:
                    import websockets
                    # Bounded timeouts, max frame size, private isolated logger, subprotocol chat
                    socket = await websockets.connect(  # type: ignore[attr-defined]
                        url,
                        subprotocols=["chat"],
                        open_timeout=self._handshake_timeout,
                        close_timeout=1.0,
                        max_size=MAX_FRAME_BYTES,
                        logger=_ws_private_logger,
                    )
                except ImportError as exc:
                    raise RuntimeError(
                        "websockets package is required for real network Xbox chat socket. "
                        "Install websockets or inject socket_factory."
                    ) from exc

            self._socket = socket
            if self._generation != generation or self._social.account != current_acc:
                await self.aclose()
                raise ChatSocketError('ERR_ACCOUNT_CHANGED')

            # Step 3: Perform WhoAmI handshake
            try:
                identity = await asyncio.wait_for(
                    self._perform_handshake(socket, xuid, generation, current_acc),
                    timeout=self._handshake_timeout,
                )
                self._identity = identity
                loop = asyncio.get_running_loop()
                self._listener_task = loop.create_task(
                    self._receive_loop(socket, xuid, generation, current_acc)
                )
                return identity
            except asyncio.TimeoutError as exc:
                await self.aclose()
                raise ChatHandshakeError("ERR_HANDSHAKE_TIMEOUT") from exc
            except BaseException:
                await self.aclose()
                raise

    async def _perform_handshake(
        self,
        socket: AsyncSocketProtocol,
        xuid: str,
        generation: int,
        account: str,
    ) -> XboxChatIdentity:
        # Send System WhoAmI
        self._sequence += 1
        who_am_i_msg = json.dumps({
            "clientSeqNum": self._sequence,
            "channel": {"type": "System"},
            "messageType": "WhoAmI",
        })
        await socket.send(who_am_i_msg)

        # Receive WhoAmI response
        raw_msg = await socket.recv()

        if self._generation != generation or self._social.account != account:
            raise ChatSocketError("ERR_ACCOUNT_CHANGED")

        if len(raw_msg.encode("utf-8")) > MAX_FRAME_BYTES:
            raise ChatHandshakeError("ERR_FRAME_TOO_LARGE")

        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError as exc:
            raise ChatHandshakeError("ERR_INVALID_FRAME_JSON") from exc

        channel = msg.get("channel", {})
        if channel.get("type") != "System" or msg.get("messageType") != "WhoAmI":
            raise ChatHandshakeError("ERR_UNEXPECTED_HANDSHAKE_MESSAGE")

        connection_id = msg.get("connectionId")
        server_nonce = msg.get("serverNonce")

        if not is_bounded_string(connection_id, 512) or not is_bounded_string(server_nonce, 4096):
            raise ChatHandshakeError("ERR_INVALID_IDENTITY_FIELDS")

        # Step 4: Send Party JoinChannel
        self._sequence += 1
        join_channel_msg = json.dumps({
            "clientSeqNum": self._sequence,
            "channel": {"type": "Party", "id": xuid},
            "messageType": "JoinChannel",
        })
        await socket.send(join_channel_msg)

        if self._generation != generation or self._social.account != account:
            raise ChatSocketError('ERR_ACCOUNT_CHANGED')
        return XboxChatIdentity(
            xuid=xuid,
            connection_id=connection_id,
            server_nonce=server_nonce,
        )

    async def _receive_loop(
        self,
        socket: AsyncSocketProtocol,
        xuid: str,
        generation: int,
        account: str,
    ) -> None:
        """Background receiver loop processing real-time notifications and detecting loss."""
        loss_reason = "ERR_SOCKET_CLOSED"
        try:
            while self._generation == generation and self._social.account == account:
                try:
                    raw_msg = await socket.recv()
                except Exception:
                    loss_reason = "ERR_SOCKET_DISCONNECTED"
                    break

                if not raw_msg:
                    # Clean EOF / connection termination
                    loss_reason = "ERR_SOCKET_CLOSED"
                    break

                if self._generation != generation or self._social.account != account:
                    loss_reason = "ERR_ACCOUNT_CHANGED"
                    break

                if len(raw_msg.encode("utf-8")) > MAX_FRAME_BYTES:
                    loss_reason = "ERR_FRAME_TOO_LARGE"
                    break

                try:
                    message = json.loads(raw_msg)
                except Exception:
                    continue

                if not isinstance(message, dict):
                    continue

                chan = message.get("channel", {})
                if (
                    isinstance(chan, dict)
                    and chan.get("type") == "Party"
                    and str(chan.get("id")) == xuid
                    and message.get("messageType") == "Party"
                ):
                    payload = message.get("payload", {})
                    if isinstance(payload, dict):
                        p_type = payload.get("type")
                        if (
                            p_type == "ShoulderTap"
                            and is_bounded_string(payload.get("partyId"), 128)
                            and isinstance(payload.get("changeNumber"), int)
                        ):
                            pid = str(payload["partyId"])
                            cnum = int(payload["changeNumber"])
                            for cb in list(self._party_changed_callbacks):
                                try:
                                    cb(pid, cnum)
                                except Exception:
                                    pass
                        elif p_type == "Kicked":
                            for cb in list(self._party_removed_callbacks):
                                try:
                                    cb()
                                except Exception:
                                    pass
        except asyncio.CancelledError:
            return
        except Exception:
            loss_reason = "ERR_SOCKET_ERROR"
        finally:
            if self._generation == generation:
                self._identity = None
                self._socket = None
                self._bound_account = None
                self._generation += 1
                try:
                    await socket.close()
                except Exception:
                    pass
                # Trigger connection loss callbacks to notify media host to stop capture
                for cb in list(self._connection_loss_callbacks):
                    try:
                        cb(loss_reason)
                    except Exception:
                        pass

    async def aclose(self) -> None:
        """Asynchronously closes the active WebSocket and clears cached identity."""
        self._generation += 1
        listener, self._listener_task = self._listener_task, None
        if listener and listener is not asyncio.current_task():
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        if self._socket:
            sock = self._socket
            self._socket = None
            try:
                await sock.close()
            except Exception:
                pass
        if self._close_tasks:
            await asyncio.gather(*list(self._close_tasks), return_exceptions=True)
        self._identity = None
        self._bound_account = None
        self._sequence = 0

    async def _quiet_close(self, sock) -> None:
        try:
            await sock.close()
        except Exception:
            pass  # Never leak raw transport exceptions/URLs through task logging.

    def close(self) -> None:
        """Closes the active WebSocket and clears cached identity."""
        self._generation += 1
        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None
        if self._socket:
            sock = self._socket
            self._socket = None
            # Schedule async socket closure without blocking synchronous close()
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._quiet_close(sock))
                self._close_tasks.add(task)
                task.add_done_callback(self._close_tasks.discard)
            except RuntimeError:
                pass
        self._identity = None
        self._bound_account = None
        self._sequence = 0


# Alias for compatibility with callers expecting XboxChatSocket
XboxChatSocket = XodusChatSocket
