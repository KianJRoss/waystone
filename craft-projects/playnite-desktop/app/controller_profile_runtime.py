"""Pure session-to-profile coordination over an injected, existing-engine adapter.

No Qt, process, socket, storage, timers, engine startup, or automatic retries live
here. ``resolver(game_id)`` returns a profile name (``None`` means no profile);
``resolver(None)`` resolves the default. Session IDs and canonical library IDs
must be snapshotted by the caller, not inferred from the selected UI game.

``activate(profile, request_id)`` returns exactly True if a request was accepted
for asynchronous execution, or False if nothing was sent. Acceptance is NOT
success. The adapter must call ``complete`` only after the existing native
profile_select/CAS path settles, with success requiring active-name readback.
Lost replies or failed verification require uncertain=True. A callback exception
or non-boolean return is conservatively uncertain, never silently retried.

All methods run on the caller's event thread. A dedicated protocol adapter must
not change the controller editor's selected profile or unsaved draft.
"""
from __future__ import annotations

from typing import Callable


class ControllerProfileRuntime:
    """Newest confirmed-running session wins; only actual closed removes it.

    Duplicate polls never retry failed activation. Unknown/launching observations
    retain a previously running session. A failed startup never activates; an
    anomalous failed observation of a running session is not proof of exit.
    Closed/failed-before-running IDs are tombstoned so late polls cannot revive
    an old session. IDs must be unique for the lifetime of this coordinator.
    """

    def __init__(self, resolver: Callable[[str | None], str | None],
                 activate: Callable[[str, int], bool]):
        self._resolver = resolver
        self._activate = activate
        self._identities: dict[str, str] = {}
        self._sessions: dict[str, str] = {}
        self._terminal: set[str] = set()
        self._sequence = 0
        self._pending: tuple[int, str] | None = None
        self._active_profile: str | None = None
        self._desired_profile: str | None = None
        self._blocked = False
        self._uncertain = False
        self._status = 'idle'

    @property
    def active_sessions(self) -> dict[str, str]:
        return dict(self._sessions)

    @property
    def active_profile(self) -> str | None:
        """Last confirmed profile, not an accepted/queued request."""
        return self._active_profile

    @property
    def desired_profile(self) -> str | None:
        return self._desired_profile

    @property
    def pending_request_id(self) -> int | None:
        return self._pending[0] if self._pending else None

    @property
    def pending_profile(self) -> str | None:
        return self._pending[1] if self._pending else None

    @property
    def uncertain(self) -> bool:
        return self._uncertain

    @property
    def status(self) -> str:
        return self._status

    def observe(self, session_id: str, game_id: str, state: str) -> None:
        """Consume canonical tracker observations, NOT launch handoff signals.

        running first registers a session; closed unregisters it. A startup
        failure or unrelated closed observation does not select the default.
        """
        if not isinstance(session_id, str) or not session_id:
            raise ValueError('session_id must be a non-empty string')
        if not isinstance(game_id, str) or not game_id:
            raise ValueError('game_id must be a non-empty canonical string')
        if state not in ('launching', 'running', 'unknown', 'closed', 'failed'):
            raise ValueError('unsupported tracked session state')
        previous = self._identities.get(session_id)
        if previous is not None and previous != game_id:
            raise ValueError('session identity cannot change')
        self._identities[session_id] = game_id
        if session_id in self._terminal:
            return
        if state == 'running' and session_id not in self._sessions:
            self._sessions[session_id] = game_id
            self._resolve_and_drive()
        elif state == 'closed':
            self._terminal.add(session_id)
            if session_id in self._sessions:
                del self._sessions[session_id]
                self._resolve_and_drive()
        elif state == 'failed' and session_id not in self._sessions:
            self._terminal.add(session_id)

    def reconcile(self) -> None:
        """Explicitly apply changed defaults/assignments or retry known rejection.

        Uncertain outcomes remain blocked until verified readback is supplied via
        reset_from_observation. Callers must not use timer polls as reconciliation.
        """
        if not self._uncertain:
            self._blocked = False
        self._resolve_and_drive()

    def reset_from_observation(self, active_profile: str | None) -> None:
        """Seed confirmed native readback without issuing any mutation.

        Explicitly call reconcile afterwards if the desired profile must change.
        Never call this with the value from a queued request or local JSON alone.
        """
        if self._pending is not None:
            raise RuntimeError('activation still pending')
        self._validate_profile(active_profile)
        self._active_profile = active_profile
        self._blocked = False
        self._uncertain = False
        self._status = 'observed'

    def complete(self, request_id: int, success: bool, *, uncertain: bool = False) -> bool:
        """Settle a correlated request; stale/duplicate completions are ignored.

        Only success may drive a different coalesced target. Rejection blocks
        until explicit reconciliation; uncertainty blocks until native readback.
        """
        if type(success) is not bool or type(uncertain) is not bool:
            raise ValueError('completion flags must be booleans')
        if type(request_id) is not int or not self._pending or self._pending[0] != request_id:
            return False
        _, profile = self._pending
        self._pending = None
        if uncertain:
            self._active_profile = None
            self._blocked = self._uncertain = True
            self._status = 'uncertain'
        elif not success:
            self._blocked = True
            self._status = 'rejected'
        else:
            self._active_profile = profile
            self._status = 'confirmed'
            self._drive()
        return True

    @staticmethod
    def _validate_profile(profile: str | None) -> None:
        if profile is not None and (not isinstance(profile, str) or not profile):
            raise ValueError('resolver/readback must return a non-empty profile name or None')

    def _resolve_and_drive(self) -> None:
        game_id = next(reversed(self._sessions.values()), None)
        try:
            profile = self._resolver(game_id)
            self._validate_profile(profile)
        except Exception:
            # Never apply a stale target after assignment storage/read failures.
            self._desired_profile = None
            if not self._uncertain:
                self._status = 'resolution_failed'
            return
        self._desired_profile = profile
        self._drive()

    def _drive(self) -> None:
        if self._pending or self._blocked or self._desired_profile is None:
            return
        if self._desired_profile == self._active_profile:
            return
        self._sequence += 1
        request_id, profile = self._sequence, self._desired_profile
        self._pending = (request_id, profile)
        self._status = 'pending'
        try:
            accepted = self._activate(profile, request_id)
        except Exception:
            accepted = None
        # A synchronous fake/adapter may already have completed this request.
        if self._pending != (request_id, profile):
            return
        if accepted is False:
            self.complete(request_id, False)
        elif accepted is not True:
            self.complete(request_id, False, uncertain=True)
