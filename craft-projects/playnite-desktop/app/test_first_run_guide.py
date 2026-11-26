import json
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from first_run_guide import FirstRunGuide


from first_run_guide import FALLBACK_UDEV_RULE_TEXT

class FirstRunGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_shows_once_after_sync_reopens_and_saves_private_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state/first-run.json'
            guide = FirstRunGuide(path)
            self.assertFalse(guide.visible); self.assertFalse(guide.completed)
            guide.openOnceAfterSync()
            self.assertTrue(guide.visible)
            guide.dismiss()
            self.assertFalse(guide.visible); self.assertTrue(guide.completed)
            self.assertEqual(json.loads(path.read_text()), {'version': 1, 'complete': True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            guide.openOnceAfterSync()
            self.assertFalse(guide.visible)
            guide.reopen()
            self.assertTrue(guide.visible)

    def test_invalid_or_symlink_marker_is_not_trusted_as_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'marker.json'
            path.write_text('{"version":999,"complete":true}')
            self.assertFalse(FirstRunGuide(path).completed)
            target = Path(temp) / 'real.json'; target.write_text('{"version":1,"complete":true}')
            path.unlink(); path.symlink_to(target)
            self.assertFalse(FirstRunGuide(path).completed)

    def test_udev_rule_guidance_and_explicit_pkexec_install(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            marker = tmp_path / 'state/first-run.json'
            rule_src = tmp_path / '70-waystone-input.rules'
            rule_src.write_text(
                'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"\n'
                'KERNEL=="uhid", TAG+="uaccess", OPTIONS+="static_node=uhid"\n',
                encoding='utf-8',
            )
            calls = []
            accessible = {'ok': False}

            def fake_access(dev_path, mode):
                return accessible['ok'] and dev_path in ('/dev/uinput', '/dev/uhid')

            def fake_runner(cmd, **kwargs):
                calls.append(cmd)
                accessible['ok'] = True
                return SimpleNamespace(returncode=0, stdout='', stderr='')

            guide = FirstRunGuide(
                marker,
                rule_source=rule_src,
                access_fn=fake_access,
                runner=fake_runner,
                which_fn=lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None,
                rule_dest_dirs=(tmp_path / 'etc-rules',),
            )
            # Never runs pkexec automatically on init or openOnceAfterSync
            guide.openOnceAfterSync()
            self.assertEqual(calls, [])
            self.assertFalse(guide.virtualPadAccessReady)
            self.assertIn('70-waystone-input.rules', guide.virtualPadGuidance)
            self.assertIn('TAG+="uaccess"', guide.udevRuleText)

            # Explicit user click triggers pkexec install
            self.assertTrue(guide.installUdevRule())
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], 'pkexec')
            self.assertEqual(calls[0][-1], FALLBACK_UDEV_RULE_TEXT)  # fixed rule text, never a file path
            self.assertTrue(guide.virtualPadAccessReady)
            self.assertIn('ready on this device', guide.virtualPadGuidance)
            self.assertIn('Installed /etc/udev/rules.d/70-waystone-input.rules', guide.status)

    def test_udev_rule_install_handles_missing_pkexec_or_cancelled_auth(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            rule_src = tmp_path / '70-waystone-input.rules'
            rule_src.write_text('KERNEL=="uinput", TAG+="uaccess"\n', encoding='utf-8')

            no_pkexec = FirstRunGuide(
                tmp_path / 'm1.json',
                rule_source=rule_src,
                access_fn=lambda *_: False,
                which_fn=lambda *_: None,
            )
            self.assertFalse(no_pkexec.installUdevRule())
            self.assertIn('pkexec is not available', no_pkexec.status)

            cancelled = FirstRunGuide(
                tmp_path / 'm2.json',
                rule_source=rule_src,
                access_fn=lambda *_: False,
                which_fn=lambda *_: '/usr/bin/pkexec',
                runner=lambda *a, **k: SimpleNamespace(returncode=126, stdout='', stderr='dismissed'),
            )
            self.assertFalse(cancelled.installUdevRule())
            self.assertIn('cancelled or denied', cancelled.status)


def test_udev_install_hands_root_fixed_rule_text_not_a_path(tmp_path):
    """Root must never copy a user-writable file (AppImage mounts): the command carries the fixed rule text."""
    import first_run_guide as frg
    bundled = [l for l in frg.DEFAULT_UDEV_RULE_SOURCE.read_text().splitlines() if l.strip() and not l.startswith('#')]
    assert frg.FALLBACK_UDEV_RULE_TEXT.splitlines() == bundled
    assert 'RUN' not in frg.FALLBACK_UDEV_RULE_TEXT
    calls = []
    class Result: returncode = 0
    guide = frg.FirstRunGuide(tmp_path / 'marker.json', runner=lambda cmd, **kw: calls.append(cmd) or Result(),
                              which_fn=lambda name: '/usr/bin/pkexec')
    assert guide.installUdevRule() is True
    cmd = calls[0]
    assert cmd[0] == 'pkexec' and cmd[-1] == frg.FALLBACK_UDEV_RULE_TEXT
    assert not any(str(frg.DEFAULT_UDEV_RULE_SOURCE) == part for part in cmd)
