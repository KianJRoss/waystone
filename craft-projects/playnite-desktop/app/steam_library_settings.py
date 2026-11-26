"""Optional Steam-owned-library account settings; credentials stay in keyring."""
from PySide6.QtCore import QObject, Property, Signal, Slot, QThread
from native_services.steam_web import SteamWebAdapter, SteamWebError, steam_account_input


class _SaveSteamAccount(QThread):
    result = Signal(dict)

    def __init__(self, value, key, store, parent=None, *, adapter_factory=None, resolver=None):
        super().__init__(parent)
        self.value, self.key, self.store = value, key, store
        self.adapter_factory = adapter_factory or SteamWebAdapter
        self.resolver = resolver or SteamWebAdapter.resolve_vanity

    def run(self):
        frame = {'ok': False, 'message': 'Steam could not verify this account. Check the profile, API key, and profile privacy.'}
        try:
            kind, account = steam_account_input(self.value)
            if kind == 'vanity':
                account = self.resolver(account, self.key)
            adapter = self.adapter_factory(account, self.key)
            snapshot = adapter.fetch_owned_games()
            if not snapshot.get('complete'):
                raise SteamWebError('Incomplete library')
            # Save only after the key/profile successfully produced an explicit list.
            self.store.save('steam', {'kind': 'steam_web', 'account_id': account, 'api_key': self.key})
            frame = {'ok': True, 'account_id': account, 'count': len(snapshot['games'])}
        except Exception:
            # No request URL, key, provider body, or exception text is surfaced.
            pass
        finally:
            self.value = ''
            self.key = ''
            self.store = None
            self.adapter_factory = None
            self.resolver = None
        self.result.emit(frame)


class SteamLibrarySettings(QObject):
    changed = Signal()

    def __init__(self, session_store=None, parent=None, *, adapter_factory=None, resolver=None):
        super().__init__(parent)
        self._store = session_store
        self._adapter_factory = adapter_factory
        self._resolver = resolver
        self._busy = False
        self._configured = False
        self._steam_id = ''
        self._message = 'Optional Steam Web API account. Without it, Waystone shows local Steam installs only.'
        self._worker = None

    @Property(bool, notify=changed)
    def busy(self): return self._busy

    @Property(bool, notify=changed)
    def configured(self): return self._configured

    @Property(str, notify=changed)
    def steamId(self): return self._steam_id

    @Property(str, notify=changed)
    def message(self): return self._message

    @Slot(result=bool)
    def checkSaved(self):
        if self._busy: return False
        try:
            record = self._store.load('steam') if self._store else None
            self._configured = bool(record and record.get('kind') == 'steam_web')
            self._steam_id = record.get('account_id', '') if self._configured else ''
            self._message = (f'Steam library account saved · {self._steam_id}' if self._configured else
                             'No Steam Web API library account saved; local installs remain available.')
        except Exception:
            self._configured = False; self._steam_id = ''
            self._message = 'Desktop keyring is locked or unavailable; no account credentials were displayed.'
        self.changed.emit()
        return self._configured

    @Slot(str, str, result=bool)
    def saveAccount(self, profile, key):
        if self._busy or self._store is None:
            self._message = 'Unlock the desktop keyring before saving a Steam account.'
            self.changed.emit(); return False
        profile = profile.strip() if isinstance(profile, str) else ''
        key = key.strip() if isinstance(key, str) else ''
        if len(key) != 32 or any(c not in '0123456789abcdefABCDEF' for c in key):
            self._message = 'Enter an existing 32-character Steam Web API key.'
            self.changed.emit(); return False
        try:
            steam_account_input(profile)
        except SteamWebError as exc:
            self._message = str(exc); self.changed.emit(); return False
        if self._worker is not None:
            if self._worker.isRunning(): return False
            self._worker.deleteLater(); self._worker = None
        self._busy = True
        self._message = 'Checking Steam profile and game-library visibility…'
        worker = _SaveSteamAccount(profile, key, self._store, self,
                                   adapter_factory=self._adapter_factory, resolver=self._resolver)
        worker.result.connect(self._complete)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()
        self.changed.emit()
        return True

    @Slot()
    def _worker_finished(self):
        worker = self._worker
        self._worker = None
        if worker is not None: worker.deleteLater()

    @Slot(dict)
    def _complete(self, frame):
        self._busy = False
        if frame.get('ok'):
            self._configured = True; self._steam_id = frame['account_id']
            self._message = f"Steam Web API connected · {frame['count']} games visible. Key stored in the desktop keyring."
        else:
            self._message = frame.get('message', 'Steam library could not be saved; existing settings are unchanged.')
        self.changed.emit()

    @Slot(result=bool)
    def forget(self):
        if self._busy or self._store is None: return False
        try: self._store.clear('steam')
        except Exception:
            self._message = 'Could not remove the Steam key from the desktop keyring.'
            self.changed.emit(); return False
        self._configured = False; self._steam_id = ''
        self._message = 'Steam Web API key forgotten. Installed-game detection is unchanged.'
        self.changed.emit(); return True
