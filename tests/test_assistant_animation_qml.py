from __future__ import annotations

from pathlib import Path
import unittest

from PySide6.QtCore import QEvent, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtTest import QTest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/fixtures/assistant_director_harness.qml"
SCREENSHOTS = ROOT / "research/w14"


class AssistantAnimationQmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(["waystone-assistant-animation-tests"])

    def make_window(self):
        self.engine = QQmlEngine()
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(HARNESS)))
        if self.component.isError():
            self.fail("QML component failed to load: " + "\n".join(error.toString() for error in self.component.errors()))
        window = self.component.create()
        if window is None:
            self.fail("QML harness failed to create: " + "\n".join(error.toString() for error in self.component.errors()))
        window.show()
        QTest.qWait(60)
        return window

    def tearDown(self):
        if getattr(self, "window", None) is not None:
            self.window.close()
            self.window.deleteLater()
            self.window = None
        if getattr(self, "engine", None) is not None:
            self.engine.deleteLater()
            self.engine = None
        self.app.processEvents()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @staticmethod
    def value(window, name):
        value = window.property(name)
        return value.toVariant() if hasattr(value, "toVariant") else value

    def _screenshot(self, name: str):
        # Give Qt's offscreen renderer a frame after the QML state mutation.
        QTest.qWait(40)
        self.app.processEvents()
        image = self.app.primaryScreen().grabWindow(int(self.window.winId())).toImage()
        self.assertFalse(image.isNull(), f"offscreen screenshot {name} was empty")
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        destination = SCREENSHOTS / name
        self.assertTrue(image.save(str(destination)), f"could not save {destination}")
        return image

    def test_fake_clock_sequence_filters_screenshots_and_beats(self):
        self.window = self.make_window()
        self.window.setProperty("requestId", "game-90")
        result = self.value(self.window, "lastRequest")
        self.assertEqual(result, {"status": "started", "animating": True})
        self.assertTrue(self.window.property("animationActive"))
        self.assertEqual(self.value(self.window, "played"), [])
        self.assertFalse(self.window.property("filtered"))  # temporarily exposes hidden target

        self.window.setProperty("fakeProgress", 0)  # preparing -> scroll
        self.assertEqual(self.window.property("assistantPhase"), "scrolling")
        self.window.setProperty("fakeProgress", 0.5)
        mid = self.window.property("gridY")
        self.assertGreater(mid, 100)
        self.assertLess(mid, 800)
        screenshot = self._screenshot("assistant-mid-scroll.png")
        self.assertGreater(screenshot.width(), 100)

        self.window.setProperty("fakeProgress", 1.0)  # select and show pressed Play state
        self.assertEqual(self.window.property("assistantPhase"), "pressing")
        self.assertTrue(self.window.property("playPressed"))
        self.assertEqual(self.window.property("selectedId"), "game-90")
        pressed_image = self._screenshot("assistant-play-pressed.png")
        pressed_color = pressed_image.pixelColor(510, 180)
        self.assertGreater(pressed_color.red(), 200)
        self.assertGreater(pressed_color.green(), 150)
        self.assertLess(pressed_color.blue(), 150)
        self.assertEqual(self.value(self.window, "played"), [])

        self.window.setProperty("fakeProgress", 2.0)  # 150 ms button beat -> shared launch path
        self.assertEqual(self.value(self.window, "played"), ["game-90"])
        beats = self.value(self.window, "beatLog")
        self.assertEqual(beats[0], "open")
        self.assertTrue(beats[1:-2] and all(beat == "move" for beat in beats[1:-2]))
        self.assertEqual(beats[-2:], ["select", "launch"])
        self.assertEqual(self.window.property("activePage"), "settings")
        self.assertEqual(self.window.property("query"), "saved query")
        self.assertTrue(self.window.property("filtered"))
        self.assertFalse(self.window.property("animationActive"))

    def test_user_input_cancels_and_launches_immediately(self):
        self.window = self.make_window()
        self.window.setProperty("requestId", "game-90")
        self.window.setProperty("fakeProgress", 0)
        self.window.setProperty("fakeProgress", 0.25)
        self.assertTrue(self.window.property("animationActive"))
        self.window.setProperty("cancelRequested", True)
        self.assertEqual(self.value(self.window, "played"), ["game-90"])
        self.assertFalse(self.window.property("animationActive"))
        self.assertTrue(self.window.property("filtered"))

    def test_console_mode_uses_console_grid_and_does_not_switch_to_desktop_library(self):
        self.window = self.make_window()
        self.window.enableConsole()
        self.window.setProperty("requestId", "game-90")
        self.assertEqual(self.value(self.window, "lastRequest"), {"status": "started", "animating": True})
        self.assertTrue(self.window.property("consoleMode"))
        self.assertEqual(self.value(self.window, "activePage"), "social")
        self.window.setProperty("fakeProgress", 0)
        self.assertEqual(self.window.property("assistantPhase"), "scrolling")
        self.window.setProperty("fakeProgress", 0.5)
        self.assertGreater(self.window.property("consoleGridY"), 100)
        self.window.setProperty("fakeProgress", 1)
        self.assertEqual(self.window.property("consoleSelectedIndex"), 90)
        self.assertEqual(self.value(self.window, "selectedId"), "game-90")
        self.window.setProperty("fakeProgress", 2)
        self.assertEqual(self.value(self.window, "played"), ["game-90"])
        self.assertTrue(self.window.property("consoleMode"))
        self.assertEqual(self.value(self.window, "activePage"), "social")

    def test_disabled_or_running_game_skips_ui_animation(self):
        self.window = self.make_window()
        self.window.setProperty("animationSetting", False)
        self.window.setProperty("requestId", "game-90")
        result = self.value(self.window, "lastRequest")
        self.assertEqual(result, {"status": "started", "animating": False})
        self.assertEqual(self.window.property("activePage"), "settings")
        self.assertEqual(self.value(self.window, "beatLog"), [])

        self.window.setProperty("animationSetting", True)
        self.window.setProperty("runningSetting", True)
        self.window.setProperty("requestId", "game-90")
        result = self.value(self.window, "lastRequest")
        self.assertEqual(result, {"status": "started", "animating": False})
        self.assertEqual(self.window.property("activePage"), "settings")
        self.assertEqual(self.value(self.window, "beatLog"), [])


if __name__ == "__main__":
    unittest.main()
