"""Qt bridge for the existing additive profile-assignment document model.

No engine startup or writes on construction/read/selection. Explicit mutations
reload current bytes, detect stale UI revisions, and save via the shared model.
Atomic replacement is not claimed to be cross-process CAS against engine writers.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from PySide6.QtCore import QObject, Property, Signal, Slot
import controller_profile_assignments as model


class ControllerProfilesView(QObject):
    changed = Signal()
    documentChanged = Signal()
    operationFinished = Signal(str, bool)

    def __init__(self, path=None, parent=None):
        super().__init__(parent)
        self._path = Path(path) if path is not None else None
        self._doc = model.normalize(None)
        self._fingerprint = None
        self._available = False
        self._game_id = ''
        self._runtime_game_id = ''
        self._status = 'Controller profile storage is not configured.'
        self._error = ''
        self._runtime_status = 'Engine profile has not been observed.'
        self._engine_blocked = False
        self._legacy_default = None
        self._metadata_persisted = False
        if self._path is not None:
            self.refresh()

    def _raw(self):
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return None
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('Profile document exceeds the safe read limit.')
        return raw

    @staticmethod
    def _hash(raw):
        return hashlib.sha256(raw).hexdigest() if raw is not None else None

    def _read_document(self):
        before = self._raw()
        parsed = {}
        if before is not None:
            parsed = json.loads(before)
            if not isinstance(parsed, dict) or not isinstance(parsed.get('profiles', {}), dict):
                raise ValueError('Profile document must contain a profiles object.')
            if not all(isinstance(k, str) and k for k in parsed.get('profiles', {})):
                raise ValueError('Invalid profile names in document.')
        doc = model.load(self._path)
        if before != self._raw():
            raise ValueError('Profiles changed while reading; refresh again.')
        valid_default = isinstance(parsed.get('default_profile'), str) and parsed['default_profile'] in doc.get('profiles', {})
        if not valid_default and self._legacy_default in doc.get('profiles', {}):
            doc['default_profile'] = self._legacy_default
        self._legacy_default = doc.get('default_profile')
        self._metadata_persisted = valid_default and isinstance(parsed.get('game_profiles'), dict)
        return doc, self._hash(before)

    @Property(bool, notify=changed)
    def available(self): return self._available

    @Property('QStringList', notify=changed)
    def profiles(self): return list(self._doc.get('profiles', {}))

    @Property(str, notify=changed)
    def defaultProfile(self): return self._doc.get('default_profile') or ''

    @Property(str, notify=changed)
    def activeProfile(self): return self._doc.get('active_profile') or ''

    @Property('QVariantMap', notify=changed)
    def gameProfiles(self): return dict(self._doc.get('game_profiles', {}))

    @Property(str, notify=changed)
    def selectedGameId(self): return self._game_id

    @Property(str, notify=changed)
    def gameProfile(self): return self.assignmentForGame(self._game_id)

    @Property(str, notify=changed)
    def resolvedProfile(self): return self.resolveProfile(self._game_id)

    @Property(str, notify=changed)
    def runtimeGameId(self): return self._runtime_game_id

    @Property(bool, notify=changed)
    def engineMutationBlocked(self): return self._engine_blocked

    @Slot(bool)
    def setEngineMutationBlocked(self, blocked):
        if self._engine_blocked != blocked:
            self._engine_blocked = blocked
            self.changed.emit()

    def ensureRuntimeMetadata(self):
        # Explicit runtime-use migration, not constructor/read I/O: persist the
        # initially inferred default before profile_select can change active.
        if not self._available: return False
        if self._metadata_persisted: return True
        return self._change('metadata', lambda doc: doc)

    @Property(str, notify=changed)
    def runtimeStatus(self): return self._runtime_status

    @Slot(str)
    def setRuntimeStatus(self, status):
        if self._runtime_status != status:
            self._runtime_status = status
            self.changed.emit()

    @Property(str, notify=changed)
    def status(self): return self._status

    @Property(str, notify=changed)
    def error(self): return self._error

    @Slot(str)
    def selectGame(self, game_id):
        if self._game_id != game_id:
            self._game_id = game_id
            self.changed.emit()

    @Slot(str)
    def setRuntimeGame(self, game_id):
        # Runtime observation only: no persistence or activation.
        if self._runtime_game_id != game_id:
            self._runtime_game_id = game_id
            self.changed.emit()

    @Slot(str, result=str)
    def assignmentForGame(self, game_id):
        return self._doc.get('game_profiles', {}).get(game_id, '') if game_id else ''

    @Slot(str, result=str)
    def resolveProfile(self, game_id):
        return model.resolve(self._doc, game_id or None) or ''

    @Slot(result=bool)
    def refresh(self):
        if self._path is None: return False
        try:
            doc, fingerprint = self._read_document()
        except (OSError, ValueError, TypeError):
            self._available = False
            self._error = 'Controller profile document could not be read safely. Nothing was changed.'
            self._status = self._error
            self.changed.emit()
            return False
        self._doc, self._fingerprint = doc, fingerprint
        self._available = True
        self._error = ''
        self._status = 'Controller profiles loaded. Changes are saved only on explicit actions.'
        self.changed.emit()
        return True

    def _change(self, operation, mutate):
        if not self._available or self._path is None:
            return False
        if self._engine_blocked:
            self._error = 'Native profile operation pending or uncertain. Refresh in Controllers before changing profiles.'
            self._status = self._error
            self.changed.emit(); self.operationFinished.emit(operation, False)
            return False
        saved = False
        try:
            doc, fingerprint = self._read_document()
            if fingerprint != self._fingerprint:
                self.refresh()
                raise ValueError('Profiles changed elsewhere. Review the refreshed list and retry explicitly.')
            updated = mutate(doc)
            # Intent changes must NOT implicitly select an engine profile via
            # its file watcher. Only explicit rename/delete of the active name
            # can require the model's valid-name fallback here; pending or
            # uncertain native operations block all such lifecycle mutations.
            active_before = doc.get('active_profile')
            if active_before in updated.get('profiles', {}):
                updated['active_profile'] = active_before
            if self._hash(self._raw()) != fingerprint:
                raise ValueError('Profiles changed before save. Refresh and retry explicitly.')
            model.save(self._path, updated)
            saved = True
            readback, new_fingerprint = self._read_document()
            if readback != model.normalize(updated):
                raise ValueError('Saved profiles could not be read back unchanged.')
            self._doc, self._fingerprint = readback, new_fingerprint
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if saved:
                self._available = False
                self._error = 'Save may already be committed. Refresh to verify before another change.'
            else:
                self._error = str(exc) if isinstance(exc, (ValueError, KeyError)) else 'Could not save controller profiles.'
            self._status = self._error
            self.changed.emit(); self.operationFinished.emit(operation, False)
            return False
        self._error = ''
        self._status = 'Controller profile changes saved.'
        self.changed.emit(); self.documentChanged.emit(); self.operationFinished.emit(operation, True)
        return True

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Profile name must not be empty.')
        if len(name.strip()) > 128 or any(ord(c) < 32 for c in name):
            raise ValueError('Profile name must be at most 128 characters without control characters.')
        return name.strip()

    @Slot(str, result=bool)
    def createProfile(self, name):
        return self._change('create', lambda doc: model.create(doc, self._name(name), base=doc.get('default_profile')))

    @Slot(str, str, result=bool)
    def duplicateProfile(self, source, name):
        return self._change('duplicate', lambda doc: model.duplicate(doc, source, self._name(name)))

    @Slot(str, str, result=bool)
    def renameProfile(self, source, name):
        return self._change('rename', lambda doc: model.rename(doc, source, self._name(name)))

    @Slot(str, result=bool)
    def deleteProfile(self, name):
        return self._change('delete', lambda doc: model.delete(doc, name))

    @Slot(str, result=bool)
    def setDefaultProfile(self, name):
        return self._change('default', lambda doc: model.set_default(doc, name))

    @Slot(str, str, result=bool)
    def assignGame(self, game_id, name):
        def mutate(doc):
            if not isinstance(game_id, str) or not game_id or len(game_id) > 1024:
                raise ValueError('A stable game ID is required for a per-game assignment.')
            return model.assign(doc, game_id, name or None)
        return self._change('assign', mutate)
