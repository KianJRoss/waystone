"""Qt presentation model and controller for Xodus Social, Friends, and Parties.

Exposes Xbox Live friends, presence, party controls, and status to QML.
Runs asynchronous operations on a dedicated, bounded background worker event loop
and delivers all state changes to the Qt main thread via signals.
- Constructor does NOT spawn any helper or network activity.
- Explicit connectService(), refreshFriends(), refreshParty(), shutdown().
- Injects custom transport for offline UI/integration testing.
- Never echoes raw auth tokens or private protocol frames to UI or error messages.
- Accurately reports voice status: "Existing Xodus voice component integration pending".
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import (
    QObject,
    Property,
    Signal,
    Slot,
    QThread,
    Qt,
    QMetaObject,
    Q_ARG,
)

SERVICE_ROOT = Path(__file__).resolve().parents[3]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from native_services.xodus_social import (
    XodusSocialService,
    SocialTransport,
    XboxFriend,
    XboxPartyMember,
    XboxPartyState,
    XodusSocialError,
)

logger = logging.getLogger("waystone.xodus_social_view")

SAFE_ERROR_PREFIXES = (
    "Xbox did not authorize",
    "Xbox is limiting social requests",
    "Xbox could not establish",
    "You cannot perform this relationship action",
    "Xbox social request failed",
    "Xbox social helper disconnected",
    "Xbox social is busy",
    "Xbox social is not connected",
    "Xodus binary not found",
    "Xodus binary at",
    "Xodus binary path is not configured",
    "Invalid recipient XUID",
    "Invalid player XUID",
    "Invalid party ID",
    "Message text must be between 1 and 256 characters",
    "Account identifier required",
    "Sign in to use Xbox voice parties",
    "Select an Xbox friend to invite",
)

VOICE_PENDING_STATUS = "Existing Xodus voice component integration pending"


class SocialWorkerThread(QThread):
    """Dedicated background thread hosting an asyncio event loop for XodusSocialService."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._ready_event = threading.Event()

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._running = True
        self._ready_event.set()
        try:
            self.loop.run_forever()
        finally:
            self._running = False
            self.loop.close()

    def wait_ready(self, timeout: float = 2.0) -> bool:
        return self._ready_event.wait(timeout)

    def schedule(self, coro: Any) -> None:
        """Schedules a coroutine onto the worker's asyncio loop."""
        if not self.loop or not self._running:
            return
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        """Cancels all pending loop tasks, gathers cancellations, and stops event loop."""
        if self.loop and self._running and self.loop.is_running():
            async def _cleanup_loop() -> None:
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for t in tasks:
                    t.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.get_running_loop().shutdown_asyncgens()
                except Exception:
                    pass

            try:
                fut = asyncio.run_coroutine_threadsafe(_cleanup_loop(), self.loop)
                fut.result(timeout=1.0)
            except Exception:
                pass

            self.loop.call_soon_threadsafe(self.loop.stop)
        self.wait()


class XodusSocialView(QObject):
    """QObject presentation model for Xbox Social & Party in Waystone."""

    voiceReply = Signal(str)
    voiceConnectionLost = Signal()
    connectionChanged = Signal()
    accountChanged = Signal()
    friendsChanged = Signal()
    partyStateChanged = Signal()
    errorChanged = Signal()
    statusChanged = Signal()
    refreshingChanged = Signal()
    operationCompleted = Signal(str, bool, str)  # operation_name, success, message

    # Internal signals used to deliver background worker results to the main thread
    _sigConnected = Signal()
    _sigDisconnected = Signal()
    _sigFriendsLoaded = Signal(list)
    _sigPartiesLoaded = Signal(list, dict)
    _sigOpResult = Signal(str, bool, str)
    _sigError = Signal(str)
    _sigSessionResult = Signal(object, bool)
    _sigSessionLoadDone = Signal(object, bool, int)
    _sigSessionStatus = Signal(str)
    sessionStatusChanged = Signal()

    def __init__(
        self,
        binary_path: Optional[str] = None,
        transport: Optional[SocialTransport] = None,
        account: Optional[str] = None,
        session_store: Any = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._binary_path = binary_path
        self._transport = transport
        self._account = account or ""
        self._session_store = session_store
        self._service: Optional[XodusSocialService] = None
        self._worker: Optional[SocialWorkerThread] = None

        self._connection_watch_task = None
        self._voice_signaling = None
        self._voice_chat = None
        self._connected = False
        self._connecting = False
        self._refreshing = False
        self._error = ""
        self._session_status = ""
        self._friends: List[Dict[str, Any]] = []
        self._active_parties: List[str] = []
        self._party_state: Dict[str, Any] = {
            "phase": "idle",
            "partyId": "",
            "members": [],
            "error": "",
        }

        self._session_generation = 0
        self._session_shutdown = False
        self._session_executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="xodus-session-store")
            if session_store is not None
            else None
        )
        self._session_futures: list[Any] = []
        self._session_futures_lock = threading.Lock()

        # Connect internal cross-thread signals to main thread handlers
        self._sigConnected.connect(self._on_worker_connected)
        self._sigDisconnected.connect(self._on_worker_disconnected)
        self._sigFriendsLoaded.connect(self._on_worker_friends_loaded)
        self._sigPartiesLoaded.connect(self._on_worker_parties_loaded)
        self._sigOpResult.connect(self._on_worker_op_result)
        self._sigError.connect(self._on_worker_error)
        self._sigSessionLoadDone.connect(self._on_session_load_done)
        self._sigSessionStatus.connect(self._on_session_status)

    # --- Properties exposed to QML ---

    @Property(bool, notify=connectionChanged)
    def connected(self) -> bool:
        return self._connected

    @Property(bool, notify=connectionChanged)
    def connecting(self) -> bool:
        return self._connecting

    @Property(bool, notify=refreshingChanged)
    def refreshing(self) -> bool:
        return self._refreshing

    @Property(str, notify=accountChanged)
    def account(self) -> str:
        return self._account

    @Property(str, notify=errorChanged)
    def error(self) -> str:
        return self._error

    @Property(str, notify=sessionStatusChanged)
    def sessionStatus(self) -> str:
        return self._session_status

    @Property("QVariantList", notify=friendsChanged)
    def friends(self) -> List[Dict[str, Any]]:
        return self._friends

    @Property("QVariantList", notify=partyStateChanged)
    def activeParties(self) -> List[str]:
        return self._active_parties

    @Property("QVariantMap", notify=partyStateChanged)
    def partyState(self) -> Dict[str, Any]:
        return self._party_state

    @Property(str, notify=statusChanged)
    def voiceStatusText(self) -> str:
        return VOICE_PENDING_STATUS

    @Slot(str)
    def _on_session_status(self, text: str) -> None:
        safe = str(text or "")
        if self._session_status != safe:
            self._session_status = safe
            self.sessionStatusChanged.emit()

    def _set_session_status(self, text: str) -> None:
        # marshal to UI thread when called from worker executor
        self._sigSessionStatus.emit(text)

    # --- Slots & Public Lifecycle API ---

    @Slot(str)
    def setAccount(self, account: str) -> None:
        """Sets the Xbox account identity explicitly."""
        cleaned = (account or "").strip()
        if (self._connected or self._connecting) and cleaned != self._account:
            self._error = 'Disconnect before switching Xbox accounts'
            self.errorChanged.emit()
            return
        if self._account != cleaned:
            self._account = cleaned
            if self._service:
                self._service.set_account(cleaned)
            self.accountChanged.emit()

    @Slot()
    def connectService(self) -> None:
        """Explicit connect; persists helper selector after success."""
        self._start_connect(persist_on_success=True)

    @Slot()
    def restoreSession(self) -> None:
        """Restores only the remembered helper selector after UI startup."""
        if self._session_store is None or self._session_shutdown:
            return
        if self._connected or self._connecting:
            return
        generation = self._session_generation

        def do_load() -> Any:
            return self._session_store.load("xbox")

        def on_done(payload: Any, ok: bool) -> None:
            self._sigSessionLoadDone.emit(payload, ok, generation)

        self._submit_session_op(do_load, on_done)

    def _start_connect(self, *, persist_on_success: bool = False, account: Optional[str] = None) -> None:
        if self._session_shutdown:
            return
        if self._connected or self._connecting:
            return

        # Fail fast on missing/invalid binary before starting worker thread
        if self._transport is None:
            if not self._binary_path:
                self._error = "Xodus binary path is not configured."
                self.errorChanged.emit()
                return
            p = Path(self._binary_path).resolve()
            if not p.is_file():
                self._error = f"Xodus binary not found at '{self._binary_path}'"
                self.errorChanged.emit()
                return
            if not os.access(p, os.X_OK):
                self._error = f"Xodus binary at '{self._binary_path}' is not executable"
                self.errorChanged.emit()
                return

        self._connecting = True
        self._error = ""
        self.connectionChanged.emit()
        self.errorChanged.emit()

        if self._worker is None:
            self._worker = SocialWorkerThread(self)
            self._worker.start()
            self._worker.wait_ready()

        if self._service is None:
            self._service = XodusSocialService(
                binary_path=self._binary_path,
                transport=self._transport,
            )
        if account:
            self._service.set_account(account)
        elif self._account:
            self._service.set_account(self._account)

        service = self._service
        persisted = persist_on_success

        async def do_connect() -> None:
            try:
                await service.connect()
                friends_raw = await service.list_friends()
                parties_raw = await service.get_active_parties()
                friends_mapped = [
                    {
                        "xuid": f.xuid,
                        "gamertag": f.gamertag,
                        "online": f.online,
                        "presence": f.presence,
                        "currentGame": getattr(f, "current_game", None) or "",
                        "favorite": f.favorite,
                        "inParty": f.in_party,
                        "avatarUrl": f.avatar_url or "",
                        "joinablePartyId": f.joinable_party_id or "",
                    }
                    for f in friends_raw
                ]
                party_state_mapped = {
                    "phase": service.party_state.phase,
                    "partyId": service.party_state.party_id or "",
                    "members": [
                        {"xuid": m.xuid, "role": m.role or ""}
                        for m in service.party_state.members
                    ],
                    "error": service.party_state.error or "",
                }
                self._sigFriendsLoaded.emit(friends_mapped)
                self._sigPartiesLoaded.emit(parties_raw, party_state_mapped)
                self._sigConnected.emit()
                if persisted and service.account:
                    self._save_persisted_session(service.account)

                async def watch_helper() -> None:
                    while service is self._service and service.is_connected:
                        await asyncio.sleep(0.5)
                    if service is self._service:
                        self.voiceConnectionLost.emit()
                        await self._close_voice()
                        self._sigDisconnected.emit()

                self._connection_watch_task = asyncio.create_task(watch_helper())
            except Exception as exc:
                sanitized_err = self._sanitize_error(exc)
                self._sigError.emit(sanitized_err)
                self._sigDisconnected.emit()

        self._worker.schedule(do_connect())

    @Slot(str)
    def requestVoiceCall(self, raw: str) -> None:
        """Private media IPC. Same social worker/account; never a second helper."""
        if not self._connected or not self._service or not self._worker or len(raw.encode('utf-8')) > 262144:
            return
        try:
            frame = json.loads(raw)
            method, args = frame['method'], frame['args']
            if not isinstance(frame,dict) or not isinstance(args,list): return
            nonce, cid = frame['nonce'], frame['callId']
            import re
            if not isinstance(nonce,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',nonce): return
            if not isinstance(cid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',cid): return
            names = {'startXboxParty':'start_xbox_party', 'acceptXboxPartyInvite':'accept_xbox_party_invite',
                     'confirmXboxParty':'confirm_xbox_party', 'leaveXboxParty':'leave_xbox_party',
                     'xboxVoiceAllowed':'xbox_voice_allowed'}
            if method not in names: return
        except (ValueError,KeyError,TypeError): return
        service = self._service
        async def run():
            reply = {'source':'xodus-voice-parent','nonce':nonce,'callId':cid}
            try:
                if self._voice_signaling is None:
                    from native_services.xodus_voice_signaling import XodusVoiceSignalingService
                    from native_services.xodus_chat_socket import XodusChatSocket
                    self._voice_chat = XodusChatSocket(service)
                    if hasattr(self._voice_chat, 'on_connection_loss'):
                        self._voice_chat.on_connection_loss(lambda code: self.voiceConnectionLost.emit())
                    self._voice_chat.on_party_removed(lambda: self.voiceConnectionLost.emit())
                    self._voice_signaling = XodusVoiceSignalingService(service, chat_socket=self._voice_chat)
                result = await getattr(self._voice_signaling,names[method])(*args)
                if service is not self._service or not self._connected:
                    raise RuntimeError('disconnected')
                reply['result'] = result
                if method in {'startXboxParty','acceptXboxPartyInvite','confirmXboxParty','leaveXboxParty'}:
                    state = self._voice_signaling.state
                    self._sigPartiesLoaded.emit([state.party_id] if state.party_id else [], {
                        'phase':state.phase,'partyId':state.party_id or '',
                        'members':list(state.members),'error':''})
            except Exception:
                # No raw helper errors, SDP, URLs or credentials cross into presentation.
                reply['error'] = 'ERR_VOICE_SIGNALING_FAILED'
            encoded = json.dumps(reply)
            if len(encoded.encode('utf-8')) > 262144:
                encoded = json.dumps({'source':'xodus-voice-parent','nonce':nonce,'callId':cid,'error':'ERR_REPLY_TOO_LARGE'})
            self.voiceReply.emit(encoded)
        self._worker.schedule(run())

    async def _close_voice(self):
        watch, self._connection_watch_task = self._connection_watch_task, None
        if watch is not None and watch is not asyncio.current_task():
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
        if self._voice_chat is not None and hasattr(self._voice_chat,'aclose'):
            try:
                await asyncio.wait_for(self._voice_chat.aclose(), timeout=1.5)
            except Exception:
                pass
        if self._voice_signaling is not None:
            try:
                await asyncio.wait_for(self._voice_signaling.leave_xbox_party(), timeout=1.0)
            except Exception:
                pass
        if self._voice_chat is not None:
            self._voice_chat.close()
        self._voice_signaling = self._voice_chat = None

    @Slot()
    def disconnectService(self) -> None:
        """Explicit disconnect clears persisted selector even if disconnected."""
        self._session_generation += 1
        self._clear_persisted_session()
        if not self._connected and not self._connecting and self._service is None:
            return
        if self._service and self._worker and self._worker.loop and self._worker.loop.is_running():
            service = self._service

            async def do_close() -> None:
                try:
                    await self._close_voice()
                    await service.close()
                finally:
                    self._sigDisconnected.emit()

            self._worker.schedule(do_close())
        else:
            self._on_worker_disconnected()

    @Slot()
    def refreshFriends(self) -> None:
        """Refreshes friends list on demand."""
        if not self._connected or not self._service or not self._worker:
            return
        self._refreshing = True
        self.refreshingChanged.emit()

        service = self._service

        async def do_refresh() -> None:
            try:
                friends_raw = await service.list_friends()
                friends_mapped = [
                    {
                        "xuid": f.xuid,
                        "gamertag": f.gamertag,
                        "online": f.online,
                        "presence": f.presence,
                        "currentGame": getattr(f, "current_game", None) or "",
                        "favorite": f.favorite,
                        "inParty": f.in_party,
                        "avatarUrl": f.avatar_url or "",
                        "joinablePartyId": f.joinable_party_id or "",
                    }
                    for f in friends_raw
                ]
                self._sigFriendsLoaded.emit(friends_mapped)
                self._sigOpResult.emit("refreshFriends", True, "")
            except Exception as exc:
                sanitized = self._sanitize_error(exc)
                self._sigError.emit(sanitized)
                self._sigOpResult.emit("refreshFriends", False, sanitized)

        self._worker.schedule(do_refresh())

    @Slot()
    def refreshParty(self) -> None:
        """Refreshes active parties and current party members on demand."""
        if not self._connected or not self._service or not self._worker:
            return

        service = self._service

        async def do_refresh_party() -> None:
            try:
                active_ids = await service.get_active_parties()
                party_state_mapped = {
                    "phase": service.party_state.phase,
                    "partyId": service.party_state.party_id or "",
                    "members": [
                        {"xuid": m.xuid, "role": m.role or ""}
                        for m in service.party_state.members
                    ],
                    "error": service.party_state.error or "",
                }
                # If there is an active party known, refresh its members
                target_id = service.party_state.party_id or (active_ids[0] if active_ids else None)
                if target_id:
                    pid, members, _ = await service.get_party(target_id)
                    party_state_mapped = {
                        "phase": "connected",
                        "partyId": pid,
                        "members": [{"xuid": m.xuid, "role": m.role or ""} for m in members],
                        "error": "",
                    }
                self._sigPartiesLoaded.emit(active_ids, party_state_mapped)
                self._sigOpResult.emit("refreshParty", True, "")
            except Exception as exc:
                sanitized = self._sanitize_error(exc)
                self._sigError.emit(sanitized)
                self._sigOpResult.emit("refreshParty", False, sanitized)

        self._worker.schedule(do_refresh_party())

    @Slot(str)
    def leaveParty(self, party_id: str) -> None:
        """Leaves the specified party."""
        pid = (party_id or "").strip()
        if not pid or not self._connected or not self._service or not self._worker:
            return

        service = self._service

        async def do_leave() -> None:
            try:
                await service.leave_party(pid)
                active_ids = await service.get_active_parties()
                party_state_mapped = {
                    "phase": "idle",
                    "partyId": "",
                    "members": [],
                    "error": "",
                }
                self._sigPartiesLoaded.emit(active_ids, party_state_mapped)
                self._sigOpResult.emit("leaveParty", True, "Left Xbox party.")
            except Exception as exc:
                sanitized = self._sanitize_error(exc)
                self._sigError.emit(sanitized)
                self._sigOpResult.emit("leaveParty", False, sanitized)

        self._worker.schedule(do_leave())

    @Slot(str, str)
    def inviteToParty(self, party_id: str, xuid: str) -> None:
        """Invites a friend to a party."""
        pid = (party_id or "").strip()
        target_xuid = (xuid or "").strip()
        if not pid or not target_xuid or not self._connected or not self._service or not self._worker:
            return

        service = self._service

        async def do_invite() -> None:
            try:
                await service.invite_to_party(pid, target_xuid)
                self._sigOpResult.emit("inviteToParty", True, f"Invitation sent to {target_xuid}.")
            except Exception as exc:
                sanitized = self._sanitize_error(exc)
                self._sigError.emit(sanitized)
                self._sigOpResult.emit("inviteToParty", False, sanitized)

        self._worker.schedule(do_invite())

    @Slot(str, str)
    def sendMessage(self, xuid: str, text: str) -> None:
        """Sends a 1:1 chat message."""
        target_xuid = (xuid or "").strip()
        msg_text = (text or "").strip()
        if not target_xuid or not msg_text or not self._connected or not self._service or not self._worker:
            return

        service = self._service

        async def do_send() -> None:
            try:
                msg_id = await service.send_message(target_xuid, msg_text)
                self._sigOpResult.emit("sendMessage", True, f"Message sent ({msg_id}).")
            except Exception as exc:
                sanitized = self._sanitize_error(exc)
                self._sigError.emit(sanitized)
                self._sigOpResult.emit("sendMessage", False, sanitized)

        self._worker.schedule(do_send())

    @Slot()
    def close(self) -> None:
        """Alias for shutdown(). Releases worker and child transport cleanly."""
        self.shutdown()


    # --- Session persistence helpers ---

    def _extract_session_selector(self, payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        if payload.get("kind") != "xbox_helper":
            return None
        acct = payload.get("account")
        if isinstance(acct, str) and acct.strip():
            return acct.strip()
        return None

    def _submit_session_op(self, fn, on_done=None) -> None:
        if self._session_executor is None:
            if on_done:
                try:
                    on_done(None, False)
                except Exception:
                    pass
            return
        if self._session_shutdown:
            if on_done:
                try:
                    on_done(None, False)
                except Exception:
                    pass
            return

        def run() -> None:
            try:
                res = fn()
                if on_done:
                    on_done(res, True)
            except Exception:
                if on_done:
                    try:
                        on_done(None, False)
                    except Exception:
                        pass

        try:
            fut = self._session_executor.submit(run)
        except Exception:
            if on_done:
                try:
                    on_done(None, False)
                except Exception:
                    pass
            return
        with self._session_futures_lock:
            self._session_futures.append(fut)

        def _cleanup(_fut: Any) -> None:
            with self._session_futures_lock:
                try:
                    self._session_futures.remove(_fut)
                except ValueError:
                    pass
        try:
            fut.add_done_callback(_cleanup)
        except Exception:
            pass

    @Slot(object, bool, int)
    def _on_session_load_done(self, payload: Any, ok: bool, generation: int) -> None:
        if self._session_shutdown or generation != self._session_generation:
            return
        if not ok:
            self._on_session_status("Saved Xbox sign-in could not be restored.")
            return
        if self._connected or self._connecting:
            return
        selector = self._extract_session_selector(payload)
        if not selector:
            return
        self._start_connect(persist_on_success=False, account=selector)

    def _save_persisted_session(self, account: str) -> None:
        if self._session_store is None or self._session_shutdown:
            return
        acct = (account or "").strip()
        if not acct:
            return

        def do_save() -> None:
            self._session_store.save("xbox", {"kind": "xbox_helper", "account": acct})

        def on_done(_res: Any, ok: bool) -> None:
            if ok:
                self._set_session_status("Xbox sign-in saved for this device.")
            else:
                self._set_session_status("Xbox sign-in could not be saved.")

        self._submit_session_op(do_save, on_done)

    def _clear_persisted_session(self) -> None:
        if self._session_store is None:
            return

        def do_clear() -> None:
            self._session_store.clear("xbox")

        def on_done(_res: Any, ok: bool) -> None:
            if ok:
                self._set_session_status("Saved Xbox sign-in removed.")
            else:
                # Must not claim "forgotten"/removed when clear failed.
                self._set_session_status("Saved Xbox sign-in could not be removed.")

        self._submit_session_op(do_clear, on_done)

    @Slot()
    def shutdown(self) -> None:
        """Cleanly terminates the worker thread and shuts down service transport."""
        self._session_shutdown = True
        self._session_generation += 1
        # Retain/join pending session store ops
        if self._session_executor is not None:
            with self._session_futures_lock:
                futs = list(self._session_futures)
            for f in futs:
                try:
                    f.result(timeout=6.0)
                except Exception:
                    pass
            try:
                self._session_executor.shutdown(wait=True, cancel_futures=False)
            except Exception:
                try:
                    self._session_executor.shutdown(wait=True)
                except Exception:
                    pass
            self._session_executor = None
            with self._session_futures_lock:
                self._session_futures.clear()

        if self._worker and self._worker.isRunning():
            if self._service and self._worker.loop and self._worker.loop.is_running():
                service = self._service
                async def close_all():
                    await self._close_voice()
                    await service.close()
                fut = asyncio.run_coroutine_threadsafe(close_all(), self._worker.loop)
                try:
                    # Bound chat close + party leave + helper termination/reap.
                    fut.result(timeout=4.5)
                except Exception:
                    pass
            self._worker.stop()
            self._worker = None

        self._service = None
        self._connected = False
        self._connecting = False
        self._refreshing = False
        self.connectionChanged.emit()
        self.refreshingChanged.emit()

    # --- Private Main-Thread Slots for Cross-Thread Delivery ---

    def _on_worker_connected(self) -> None:
        self._connected = True
        self._connecting = False
        self._error = ""
        if self._service and self._service.account:
            self._account=self._service.account
            self.accountChanged.emit()
        self.connectionChanged.emit()
        self.errorChanged.emit()

    def _on_worker_disconnected(self) -> None:
        self._connected = False
        self._connecting = False
        self._refreshing = False
        self._friends = []
        self._active_parties = []
        self._party_state = {'phase': 'idle', 'partyId': '', 'members': [], 'error': ''}
        self.friendsChanged.emit()
        self.partyStateChanged.emit()
        self.connectionChanged.emit()
        self.refreshingChanged.emit()

    def _on_worker_friends_loaded(self, friends: list) -> None:
        self._friends = friends
        self._refreshing = False
        self.friendsChanged.emit()
        self.refreshingChanged.emit()

    def _on_worker_parties_loaded(self, parties: list, state: dict) -> None:
        self._active_parties = parties
        self._party_state = state
        self.partyStateChanged.emit()

    def _on_worker_op_result(self, op: str, success: bool, msg: str) -> None:
        self.operationCompleted.emit(op, success, msg)

    def _on_worker_error(self, err: str) -> None:
        self._error = err
        self._connecting = False
        self._refreshing = False
        self.errorChanged.emit()
        self.connectionChanged.emit()
        self.refreshingChanged.emit()

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        """Ensures only strictly vetted, allowlisted safe error messages are emitted."""
        msg = str(exc).strip()
        # Explicit check for credential/token markers
        if any(marker in msg.lower() for marker in ("authkey", "bearer ", "token", "password", "sessionnonce")):
            return "Xbox social communication error occurred."
        for prefix in SAFE_ERROR_PREFIXES:
            if msg.startswith(prefix):
                return msg
        return "Xbox social service operation failed."
