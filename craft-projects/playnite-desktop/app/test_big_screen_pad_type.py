"""Big Screen's Virtual pad type row edits the controller profile being edited (Kian 2026-11-25). Fakes only."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtGui import QGuiApplication

from big_screen_view import BigScreenView


class FakeControllerView(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__()
        self.pad, self.saved, self.ready, self.pending = "", [], True, False
    @Property(str, notify=changed)
    def editingProfile(self): return "Couch"
    @Property(str, notify=changed)
    def padType(self): return self.pad
    @Property(bool, notify=changed)
    def available(self): return self.ready
    @Property(bool, notify=changed)
    def busy(self): return self.pending
    @Slot(str)
    def setPadType(self, value):
        self.saved.append(value)
        self.pad = value
        self.changed.emit()


def test_cycle_saves_the_next_type_into_the_profile_and_label_names_it(tmp_path):
    QGuiApplication.instance() or QGuiApplication([])
    uc = FakeControllerView()
    view = BigScreenView(settings_path=tmp_path / "s.json", persist_settings=False, unified_controller=uc)
    assert view.virtualPadLabel == "Default type · Couch"
    assert view.cycleVirtualPadType() == "Off · real controller · Couch" and uc.saved == ["passthrough"]
    assert view.virtualPadLabel == "Off · real controller · Couch"
    assert view.cycleVirtualPadType() == "Xbox 360 · Couch" and uc.saved[-1] == "xbox360"
    uc.pad = "dualsense"
    assert view.cycleVirtualPadType() == "Default type · Couch" and uc.saved[-1] == ""
    uc.ready = False
    assert view.cycleVirtualPadType() == "Controller engine unavailable" and len(uc.saved) == 3
