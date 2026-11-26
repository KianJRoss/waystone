"""Daze-derived native theme contracts; no Windows runtime or remote resources."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
os.environ['QT_QUICK_CONTROLS_STYLE'] = 'Basic'
import re
import unittest
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlExpression
from PySide6.QtQuick import QQuickItem
from app import create_session

HERE = Path(__file__).parent

class DazeThemeTests(unittest.TestCase):
    def test_source_palette_is_neutral_and_daze_constants(self):
        for name in ('Theme', 'Main', 'DetailsPane', 'CompactButton', 'CompactCombo', 'MissingCover', 'PlainToolTip'):
            for value in re.findall(r'#[0-9a-fA-F]{6}\b', (HERE / (name + '.qml')).read_text()):
                self.assertEqual(len(set((value[1:3].lower(), value[3:5].lower(), value[5:7].lower()))), 1, (name, value))
        theme = (HERE / 'Theme.qml').read_text()
        for color in ('#000000', '#f2f2f2', '#3b3b3b', '#1f1f1f', '#8d8d8d'):
            self.assertIn(color, theme)
        self.assertIn('cornerRadius: 12', theme)
        self.assertIn('scrollbarWidth: 12', theme)
        self.assertIn('Lato Light', theme)

    def test_runtime_palette_shapes_and_native_panels(self):
        app = QGuiApplication.instance() or QGuiApplication(['daze-test'])
        engine, model, root, warnings = create_session(app, headless=True)
        try:
            root.setVisible(True)
            app.processEvents()
            self.assertEqual(root.title(), 'Waystone')
            self.assertEqual(root.color().name(), '#000000')
            for name in ('searchField', 'sortCombo', 'organizerButton', 'detailsScroll'):
                self.assertIsNotNone(root.findChild(QQuickItem, name), name)
            for name in ('sortCombo', 'organizerButton'):
                item = root.findChild(QQuickItem, name)
                expr = QQmlExpression(engine.rootContext(), item, 'background.radius')
                value = expr.evaluate()
                self.assertFalse(expr.hasError(), expr.error().toString())
                self.assertEqual(value[0] if isinstance(value, tuple) else value, 12)
            for mode in ('details', 'list', 'grid'):
                root.setView(mode)
                app.processEvents()
            root.setView('details')
            app.processEvents()
            scroll = root.findChild(QQuickItem, 'detailsScroll')
            bar = root.findChild(QQuickItem, 'detailsDazeScrollBar')
            self.assertEqual(bar.width(), 12)
            self.assertAlmostEqual(bar.x() + bar.width(), scroll.width())
            self.assertGreater(bar.height(), 100)
            root.setProperty('organizerMode', True)
            app.processEvents()
            self.assertEqual(root.property('organizerMode'), True)
            self.assertFalse(warnings, warnings)
        finally:
            root.setVisible(False)
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()

if __name__ == '__main__':
    unittest.main()
