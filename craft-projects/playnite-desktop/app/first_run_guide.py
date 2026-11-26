"""Small, reopenable first-run completion marker and explicit udev rule installer for the app-owned library."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PySide6.QtCore import QObject, Property, Signal, Slot
from native_services.waystone_paths import state_home

SERVICE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_UDEV_RULE_SOURCE = SERVICE_ROOT / 'packaging' / 'arch' / '70-waystone-input.rules'
DEFAULT_UDEV_RULE_DIRS = (
    Path('/etc/udev/rules.d'),
    Path('/usr/lib/udev/rules.d'),
    Path('/lib/udev/rules.d'),
)
FALLBACK_UDEV_RULE_TEXT = (
    'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"\n'
    'KERNEL=="uhid", TAG+="uaccess", OPTIONS+="static_node=uhid"'
)


class FirstRunGuide(QObject):
    changed = Signal()

    def __init__(
        self,
        path=None,
        parent=None,
        *,
        rule_source=None,
        access_fn=None,
        runner=None,
        which_fn=None,
        rule_dest_dirs=None,
    ):
        super().__init__(parent)
        self.path = Path(path) if path else state_home() / 'waystone' / 'first-run-v1.json'
        self._rule_source = Path(rule_source) if rule_source is not None else DEFAULT_UDEV_RULE_SOURCE
        self._access_fn = access_fn or os.access
        self._runner = runner or subprocess.run
        self._which_fn = which_fn or shutil.which
        self._rule_dest_dirs = tuple(Path(d) for d in (rule_dest_dirs if rule_dest_dirs is not None else DEFAULT_UDEV_RULE_DIRS))
        self._visible = False
        self._complete = self._read_complete()
        self._message = ''

    def _read_complete(self):
        try:
            if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > 4096:
                return False
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data == {'version': 1, 'complete': True}
        except (OSError, ValueError, UnicodeError):
            return False

    @Property(bool, notify=changed)
    def visible(self): return self._visible

    @Property(bool, notify=changed)
    def completed(self): return self._complete

    @Property(str, notify=changed)
    def status(self): return self._message

    @Property(bool, notify=changed)
    def virtualPadAccessReady(self):
        try:
            return bool(
                self._access_fn('/dev/uinput', os.R_OK | os.W_OK)
                and self._access_fn('/dev/uhid', os.R_OK | os.W_OK)
            )
        except Exception:
            return False

    @Property(bool, notify=changed)
    def udevRuleInstalled(self):
        try:
            return any((d / '70-waystone-input.rules').is_file() for d in self._rule_dest_dirs)
        except Exception:
            return False

    @Property(str, constant=True)
    def udevRuleSourcePath(self):
        return str(self._rule_source)

    @Property(str, constant=True)
    def udevRuleText(self):
        try:
            if not self._rule_source.is_symlink() and self._rule_source.is_file() and self._rule_source.stat().st_size <= 4096:
                return self._rule_source.read_text(encoding='utf-8').strip()
        except OSError:
            pass
        return FALLBACK_UDEV_RULE_TEXT

    @Property(str, notify=changed)
    def virtualPadGuidance(self):
        if self.virtualPadAccessReady:
            return '• Virtual controller access (/dev/uinput and /dev/uhid) is ready on this device.'
        return (
            '• Virtual controller pads need read/write access to /dev/uinput and /dev/uhid. '
            'For AppImage or manual installs, click "Install udev rule (pkexec)" below to install '
            'packaging/arch/70-waystone-input.rules (TAG+="uaccess") to /etc/udev/rules.d/ and reload udev.'
        )

    @Slot(result=bool)
    def installUdevRule(self):
        """Explicit one-click pkexec installer for 70-waystone-input.rules; never runs silently."""
        try:
            if self._rule_source.is_symlink() or not self._rule_source.is_file() or self._rule_source.stat().st_size > 4096:
                self._message = 'Bundled udev rule (packaging/arch/70-waystone-input.rules) was not found.'
                self.changed.emit()
                return False
        except OSError:
            self._message = 'Bundled udev rule (packaging/arch/70-waystone-input.rules) could not be read.'
            self.changed.emit()
            return False

        if not self._which_fn('pkexec'):
            self._message = (
                'pkexec is not available on this system. Install packaging/arch/70-waystone-input.rules to '
                '/etc/udev/rules.d/70-waystone-input.rules and run: sudo udevadm control --reload-rules && sudo udevadm trigger'
            )
            self.changed.emit()
            return False

        # Root writes Waystone's fixed rule text, never a file path: an AppImage's files live in a folder other
        # programs of this user could swap between the check and the copy, and udev rules can run commands as root.
        cmd = [
            'pkexec',
            'sh',
            '-c',
            'umask 022 && printf "%s\\n" "$1" > /etc/udev/rules.d/70-waystone-input.rules && '
            'udevadm control --reload-rules && '
            'udevadm trigger --subsystem-match=misc --subsystem-match=uhid',
            'waystone-udev-install',
            FALLBACK_UDEV_RULE_TEXT,
        ]
        try:
            res = self._runner(cmd, capture_output=True, text=True, timeout=60, check=False)
        except Exception:
            self._message = 'pkexec failed to run. Copy packaging/arch/70-waystone-input.rules to /etc/udev/rules.d/ manually.'
            self.changed.emit()
            return False

        if getattr(res, 'returncode', 1) == 0:
            self._message = 'Installed /etc/udev/rules.d/70-waystone-input.rules and reloaded udev rules.'
            self.changed.emit()
            return True

        self._message = (
            'Udev rule installation was cancelled or denied. You can click "Install udev rule (pkexec)" again '
            'or copy packaging/arch/70-waystone-input.rules to /etc/udev/rules.d/ manually.'
        )
        self.changed.emit()
        return False

    @Slot()
    def openOnceAfterSync(self):
        if not self._complete:
            self._visible = True
            self.changed.emit()

    @Slot()
    def reopen(self):
        self._visible = True
        self.changed.emit()

    @Slot()
    def dismiss(self):
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.first-run-', dir=self.path.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump({'version': 1, 'complete': True}, stream, separators=(',', ':'))
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try: os.fsync(directory)
                finally: os.close(directory)
            except BaseException:
                try: os.close(fd)
                except OSError: pass
                Path(temporary).unlink(missing_ok=True)
                raise
            self._complete = True
            self._message = ''
        except OSError:
            self._message = 'Setup is dismissed for now, but Waystone could not save that preference. Reopen it from Settings.'
        self._visible = False
        self.changed.emit()
