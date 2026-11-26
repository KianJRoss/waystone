"""Optional SteamGridDB API key controls. The credential is never exposed to QML."""
from PySide6.QtCore import QObject, Property, Signal, Slot


class RibbonArtworkSettings(QObject):
    changed = Signal()

    def __init__(self, session_store, parent=None):
        super().__init__(parent)
        self._store = session_store
        self._configured = False
        self._keyring_available = None
        self._message = "SteamGridDB is optional; without a key Waystone silently uses store artwork only."

    def _refresh(self):
        try:
            record = self._store.load("steamgriddb") if self._store is not None else None
            self._configured = bool(record and record.get("kind") == "steamgriddb_api_key")
            self._keyring_available = self._store is not None
        except Exception:
            self._configured = False
            self._keyring_available = False

    @Property(bool, notify=changed)
    def keyConfigured(self):
        return self._configured

    @Slot(result=bool)
    def checkKeyring(self):
        self._refresh()
        self._message = ("Desktop keyring is unavailable; optional artwork fallback remains disabled."
                         if not self._keyring_available else
                         "SteamGridDB key found in the desktop keyring." if self._configured else
                         "No SteamGridDB key found; the source remains optional and silent.")
        self.changed.emit()
        return self._configured

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Slot(str, result=bool)
    def saveApiKey(self, value):
        value = value.strip() if isinstance(value, str) else ""
        if self._store is None or not 8 <= len(value) <= 4096:
            self._message = "Enter a valid SteamGridDB API key; the key is not stored in Waystone files."
            self.changed.emit()
            return False
        try:
            self._store.save("steamgriddb", {"kind": "steamgriddb_api_key", "api_key": value})
        except Exception:
            self._message = "Could not save the SteamGridDB key. Unlock the desktop keyring and retry."
            self.changed.emit()
            return False
        self._configured = True
        self._keyring_available = True
        self._message = "SteamGridDB key stored in the desktop keyring; fetched only for visible games."
        self.changed.emit()
        return True

    @Slot(result=bool)
    def clearApiKey(self):
        if self._store is None:
            return False
        try:
            self._store.clear("steamgriddb")
        except Exception:
            self._message = "Could not remove the key. Unlock the desktop keyring and retry."
            self.changed.emit()
            return False
        self._configured = False
        self._keyring_available = True
        self._message = "SteamGridDB key removed from the desktop keyring."
        self.changed.emit()
        return True
