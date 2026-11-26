"""Qt QObject native controller model: typed draft editing over ControllerControlClient.

- Explicit load/refresh/save/select; no automatic profile writes.
- Preserves unknown profile fields by cloning the full profile dict and updating only typed fields.
- Revision conflict protection: save requires expected_revision and surfaces errors (no silent overwrite).
- Capability-dependent controls are unavailable when engine is not ready (no fake telemetry).
"""
from __future__ import annotations

import copy
import re
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot, QThread


class _Job(QThread):
    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn
        self.result = None

    def run(self):
        try:
            self.result = {"ok": True, "data": self.fn()}
        except Exception as e:
            code = getattr(e, "code", "ERR_CONTROLLER_UNAVAILABLE")
            if not isinstance(code, str) or not re.fullmatch(r"ERR_[A-Z0-9_]{1,60}", code):
                code = "ERR_CONTROLLER_UNAVAILABLE"
            self.result = {
                "ok": False,
                "code": code,
                "uncertain": getattr(e, "uncertain", False) is True,
                "message": str(e) if str(e) else code,
            }


class ControllerNativeModel(QObject):
    changed = Signal()
    operationFinished = Signal(bool, str)  # ok, code_or_empty

    def __init__(self, client=None, parent=None):
        super().__init__(parent)
        self._client = client
        self._job = None
        self._closed = False

        self._available = False
        self._busy = False
        self._status_text = "Refresh to connect to the existing controller engine."
        self._error_code = ""
        self._error_uncertain = False

        self._profile_names: list[str] = []
        self._active_profile_name = ""
        self._revision = ""

        self._profile_full: dict[str, Any] = {}
        self._loaded_profile_name = ""
        # Typed draft fields (explicit). We preserve all other keys in _profile_full.
        self._draft_note = ""
        self._dirty = False

    # --- client
    def _get_client(self):
        if self._client is None:
            from native_services.unified_controller_control import ControllerControlClient

            self._client = ControllerControlClient()
        return self._client

    def _request(self, operation: str, **fields: Any):
        return self._get_client().request(operation, **fields)

    def _read(self, name: str | None = None):
        status = self._request("status")
        profiles = self._request("profiles")
        selected = name or profiles.get("active_profile") or ""
        if selected:
            prof = self._request("profile_get", name=selected)
        else:
            prof = {"name": "", "profile": {}, "revision": profiles.get("revision", "")}
        return {"status": status, "profiles": profiles, "profile_payload": prof}

    def _start(self, operation: str, fn):
        if self._busy or self._closed:
            return False
        self._busy = True
        self._error_code = ""
        self._error_uncertain = False
        self._job = _Job(fn, self)
        self._job.finished.connect(lambda: self._finished(operation))
        self._job.start()
        self.changed.emit()
        return True

    def _apply_loaded(self, data: dict[str, Any]):
        status = data.get("status") or {}
        profiles = data.get("profiles") or {}
        prof_payload = data.get("profile_payload") or {}
        self._available = status.get("running") is True
        self._profile_names = list(profiles.get("names") or [])
        self._active_profile_name = str(profiles.get("active_profile") or "")
        # revision comes from profile payload when loaded, else profiles
        self._revision = str(prof_payload.get("revision") or profiles.get("revision") or "")
        self._profile_full = copy.deepcopy(prof_payload.get("profile") or {}) if isinstance(prof_payload.get("profile"), dict) else {}
        self._loaded_profile_name = str(prof_payload.get("name") or self._active_profile_name or "")
        # init typed drafts from full profile (preserve others untouched)
        note = self._profile_full.get("note")
        self._draft_note = note if isinstance(note, str) else ""
        self._dirty = False
        self._status_text = "Connected." if self._available else "Controller engine is not running."

    def _set_error(self, code: str, uncertain: bool, message: str = ""):
        self._error_code = code
        self._error_uncertain = uncertain
        self._status_text = message or code

    @Slot()
    def refresh(self):
        if self._start("refresh", lambda: self._read(None)):
            self._status_text = "Reading controller state…"
            self.changed.emit()

    @Slot(str)
    def loadProfile(self, name: str):
        if name and name not in self._profile_names and self._profile_names:
            return
        if self._start("load", lambda: self._read(name)):
            self._status_text = f"Loading profile {name}…" if name else "Loading profile…"
            self.changed.emit()

    @Slot(str)
    def setDraftNote(self, text: str):
        # typed draft edit only; no auto save
        if text != self._draft_note:
            self._draft_note = text if isinstance(text, str) else str(text)
            self._dirty = True
            self.changed.emit()

    @Slot(result=bool)
    def canSaveDraft(self) -> bool:
        # capability-dependent: must be available, not busy, have a loaded profile+revision
        return bool(self._available and not self._busy and self._loaded_profile_name and self._revision and self._dirty)

    @Slot(result=bool)
    def saveDraft(self) -> bool:
        if not self.canSaveDraft():
            self._set_error("ERR_CONTROLLER_UNAVAILABLE" if not self._available else "ERR_INVALID_REQUEST", False, "Save unavailable.")
            self.changed.emit()
            self.operationFinished.emit(False, self._error_code)
            return False
        # build profile: preserve unknown fields by copying full, then set typed keys
        new_profile = copy.deepcopy(self._profile_full) if isinstance(self._profile_full, dict) else {}
        # typed updates
        new_profile["note"] = self._draft_note
        name = self._loaded_profile_name
        rev = self._revision

        def _do_save():
            return self._request("profile_update", name=name, profile=new_profile, expected_revision=rev)

        if self._start("save", _do_save):
            self._status_text = "Saving profile…"
            self.changed.emit()
            return True
        return False

    @Slot(str, result=bool)
    def activateProfile(self, name: str) -> bool:
        if not self._available or self._busy or not name or name not in self._profile_names:
            return False
        if not self._revision:
            return False
        # revision conflict protection: use current known revision as expected (best effort) — server enforces
        def _do_select():
            return self._request("profile_select", name=name, expected_revision=self._revision)

        if self._start("activate", _do_select):
            self._status_text = f"Activating {name}…"
            self.changed.emit()
            return True
        return False

    @Slot(str)
    def cancelMacros(self):
        if not self._available or self._busy:
            return
        self._start("cancel", lambda: self._request("cancel_macros"))

    def _finished(self, operation: str):
        job = self._job
        self._job = None
        self._busy = False
        if not job or not job.result:
            self._status_text = "Controller request failed."
            self.changed.emit()
            self.operationFinished.emit(False, "ERR_CONTROLLER_UNAVAILABLE")
            return
        res = job.result
        if not res.get("ok"):
            code = res.get("code") or "ERR_CONTROLLER_UNAVAILABLE"
            uncertain = bool(res.get("uncertain"))
            self._set_error(code, uncertain, res.get("message") or code)
            self.changed.emit()
            self.operationFinished.emit(False, code)
            return
        # ok
        data = res.get("data") or {}
        try:
            if operation in ("refresh", "load"):
                self._apply_loaded(data)
            elif operation == "save":
                # server returns revision
                new_rev = str((data or {}).get("revision") or "")
                if new_rev:
                    self._revision = new_rev
                # re-sync full profile draft baseline with saved typed note preserved
                self._profile_full["note"] = self._draft_note
                self._dirty = False
                self._status_text = "Profile saved."
            elif operation == "activate":
                new_rev = str((data or {}).get("revision") or "")
                if new_rev:
                    self._revision = new_rev
                self._status_text = "Profile activated."
            elif operation == "cancel":
                self._status_text = "Macros cancelled."
        except Exception:
            self._status_text = "Controller response could not be applied."
            self.changed.emit()
            self.operationFinished.emit(False, "ERR_PROTOCOL_ERROR")
            return
        self.changed.emit()
        self.operationFinished.emit(True, "")

    # Properties
    @Property(bool, notify=changed)
    def available(self):
        return self._available

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def statusText(self):
        return self._status_text

    @Property(str, notify=changed)
    def errorCode(self):
        return self._error_code

    @Property(bool, notify=changed)
    def errorUncertain(self):
        return self._error_uncertain

    @Property(str, notify=changed)
    def revision(self):
        return self._revision

    @Property(str, notify=changed)
    def activeProfileName(self):
        return self._active_profile_name

    @Property(str, notify=changed)
    def loadedProfileName(self):
        return self._loaded_profile_name

    @Property("QStringList", notify=changed)
    def profileNames(self):
        return list(self._profile_names)

    @Property(str, notify=changed)
    def draftNote(self):
        return self._draft_note

    @Property(bool, notify=changed)
    def draftDirty(self):
        return self._dirty

    @Property("QVariantMap", notify=changed)
    def profileFull(self):
        # read-only view for UI/debug; edits go through typed setters
        return copy.deepcopy(self._profile_full) if isinstance(self._profile_full, dict) else {}
