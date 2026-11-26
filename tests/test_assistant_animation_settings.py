from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from native_services.assistant_animation_settings import AssistantAnimationSettings


class AssistantAnimationSettingsTests(unittest.TestCase):
    def test_defaults_and_persists_under_ui_while_preserving_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "waystone" / "settings.json"
            settings = AssistantAnimationSettings(path)
            self.assertTrue(settings.showAnimation)
            self.assertEqual(settings.speed, "Normal")
            self.assertTrue(settings.setShowAnimation(False))
            self.assertTrue(settings.setSpeed("Cinematic"))
            doc = json.loads(path.read_text())
            self.assertEqual(doc, {"ui": {"assistant_animation": False, "assistant_speed": "Cinematic"}})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_existing_keys_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"theme": {"name": "Daze"}, "ui": {"sounds": False, "custom": 7}}))
            settings = AssistantAnimationSettings(path)
            self.assertTrue(settings.setSpeed("Quick"))
            self.assertTrue(settings.setShowAnimation(False))
            self.assertEqual(json.loads(path.read_text()), {
                "theme": {"name": "Daze"},
                "ui": {"sounds": False, "custom": 7, "assistant_speed": "Quick", "assistant_animation": False},
            })

    def test_invalid_speed_and_malformed_document_do_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{not valid json")
            original = path.read_bytes()
            settings = AssistantAnimationSettings(path)
            self.assertEqual(settings.speed, "Normal")
            self.assertFalse(settings.setSpeed("very-fast"))
            self.assertFalse(settings.setShowAnimation(False))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(settings.lastError, "JSONDecodeError")

    def test_does_not_follow_settings_symlink_on_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            target.write_text("{}")
            path = root / "settings.json"
            path.symlink_to(target)
            settings = AssistantAnimationSettings(path)
            self.assertFalse(settings.setSpeed("Quick"))
            self.assertEqual(target.read_text(), "{}")


if __name__ == "__main__":
    unittest.main()


def test_pace_slider_persists_clamps_and_migrates_presets(tmp_path):
    import json
    from native_services.assistant_animation_settings import AssistantAnimationSettings
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"ui": {"assistant_speed": "Cinematic"}, "general": {"x": 1}}))
    settings = AssistantAnimationSettings(path)
    assert settings.pace == 1.8                      # old preset carried over
    assert settings.setPace(2.34)
    assert settings.pace == 2.3
    assert settings.setPace(9)
    assert settings.pace == 3.0
    saved = json.loads(path.read_text())
    assert saved["ui"]["assistant_pace"] == 3.0 and saved["general"] == {"x": 1}
    assert AssistantAnimationSettings(path).pace == 3.0
