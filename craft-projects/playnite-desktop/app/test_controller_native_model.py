import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from controller_native_model import ControllerNativeModel


class FakeClient:
    def __init__(self):
        self.calls = []
        self.active = "Default"
        self.revision = "a" * 64
        self.profile = {"routes": {"A": 1}, "unknown_keep": {"x": True}, "note": "old"}

    def request(self, operation, **fields):
        self.calls.append((operation, dict(fields)))
        if operation == "status":
            return {"running": True}
        if operation == "profiles":
            return {"names": ["Default", "Other"], "active_profile": self.active, "revision": self.revision}
        if operation == "profile_get":
            name = fields.get("name")
            return {"name": name, "profile": dict(self.profile), "revision": self.revision}
        if operation == "profile_update":
            # simulate revision conflict if mismatched
            if fields.get("expected_revision") != self.revision:
                from native_services.unified_controller_control import ControllerControlError

                raise ControllerControlError("ERR_REVISION_CONFLICT", uncertain=False)
            self.profile = dict(fields.get("profile") or {})
            self.revision = "b" * 64
            return {"revision": self.revision}
        if operation == "profile_select":
            if fields.get("expected_revision") != self.revision:
                from native_services.unified_controller_control import ControllerControlError

                raise ControllerControlError("ERR_REVISION_CONFLICT", uncertain=False)
            self.active = fields.get("name")
            self.revision = "c" * 64
            return {"revision": self.revision}
        if operation == "cancel_macros":
            return {"accepted": True}
        raise AssertionError(operation)


def settle(model):
    for _ in range(300):
        QTest.qWait(5)
        if not model.busy:
            return
    raise AssertionError("model busy leaked")


def test_refresh_load_typed_edit_explicit_save_preserves_unknown_and_revision():
    app = QGuiApplication.instance() or QGuiApplication([])
    fc = FakeClient()
    m = ControllerNativeModel(client=fc)
    assert m.available is False
    m.refresh()
    settle(m)
    assert m.available is True
    assert m.profileNames == ["Default", "Other"]
    assert m.revision == "a" * 64
    assert m.loadedProfileName == "Default"
    assert m.draftNote == "old"
    assert m.draftDirty is False

    # typed edit only, no auto save
    m.setDraftNote("new-note")
    assert m.draftDirty is True
    assert any(c[0] == "profile_update" for c in fc.calls) is False

    # save explicit
    ok = m.saveDraft()
    assert ok is True
    settle(m)
    assert m.errorCode == ""
    assert m.draftDirty is False
    assert fc.profile.get("note") == "new-note"
    assert fc.profile.get("unknown_keep") == {"x": True}  # preserved
    assert fc.profile.get("routes") == {"A": 1}  # preserved
    assert m.revision == "b" * 64


def test_revision_conflict_surfaces_error_no_silent_overwrite():
    app = QGuiApplication.instance() or QGuiApplication([])
    fc = FakeClient()
    m = ControllerNativeModel(client=fc)
    m.refresh()
    settle(m)
    m.setDraftNote("changed")
    # force stale revision
    fc.revision = "d" * 64
    ok = m.saveDraft()
    assert ok is True
    settle(m)
    assert m.errorCode == "ERR_REVISION_CONFLICT"
    # profile not overwritten to changed note in that failed attempt
    assert fc.profile.get("note") == "old"


def test_no_auto_profile_writes_on_load():
    app = QGuiApplication.instance() or QGuiApplication([])
    fc = FakeClient()
    m = ControllerNativeModel(client=fc)
    m.refresh()
    settle(m)
    updates = [c for c in fc.calls if c[0] == "profile_update"]
    assert updates == []
