import json
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from first_run_guide import FirstRunGuide


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
