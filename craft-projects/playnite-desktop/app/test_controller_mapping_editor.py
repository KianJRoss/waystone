import os
import json
from pathlib import Path
import unittest

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
os.environ['QT_QUICK_CONTROLS_STYLE'] = 'Basic'

from PySide6.QtCore import QUrl, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

HERE = Path(__file__).resolve().parent

def get_bindings(item):
    val = item.property("bindings")
    return val.toVariant() if hasattr(val, "toVariant") else val

class ControllerMappingEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(['offline-test'])

    def setUp(self):
        self.engine = QQmlApplicationEngine()
        self.engine.addImportPath(str(HERE))
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(HERE / 'ControllerMappingEditor.qml')))
        if self.component.isError():
            self.fail(f"Component errors: {self.component.errors()}")
        self.item = self.component.create()
        self.assertIsNotNone(self.item)

    def tearDown(self):
        if hasattr(self, 'item') and self.item:
            self.item.deleteLater()
        if hasattr(self, 'engine') and self.engine:
            self.engine.deleteLater()
        self.app.processEvents()

    def test_constructor_silence(self):
        """Constructor is silent: empty bindings, no errors, no network/file ops."""
        self.assertEqual(self.item.property("count"), 0)
        self.assertEqual(get_bindings(self.item), [])
        self.assertEqual(self.item.property("selectedIndex"), -1)
        self.assertEqual(self.item.property("errorMessage"), "")
        self.assertEqual(self.item.serializedProfile(), "")

    def test_load_profile_object_schema(self):
        """Loads UC profile schema: routes object source -> destination."""
        profile = {
            "name": "Xbox Default",
            "routes": {
                "Controller:A": "Controller:B",
                "Controller:D-pad Up": "Controller:D-pad Down"
            }
        }
        res = self.item.loadProfile(json.dumps(profile))
        self.assertTrue(res)
        self.assertEqual(self.item.property("count"), 2)
        bindings = get_bindings(self.item)
        self.assertEqual(len(bindings), 2)
        sources = {b["source"]: b["destination"] for b in bindings}
        self.assertEqual(sources["Controller:A"], "Controller:B")
        self.assertEqual(sources["Controller:D-pad Up"], "Controller:D-pad Down")
        self.assertEqual(self.item.property("errorMessage"), "")

    def test_add_replace_remove_roundtrip_and_edited_signal(self):
        """Add, replace, and remove operations update bindings and emit edited()."""
        edited_count = 0
        def on_edited():
            nonlocal edited_count
            edited_count += 1
        self.item.edited.connect(on_edited)

        # 1. Add route
        ok = self.item.addRoute("Controller:X", "Controller:Y")
        self.assertTrue(ok)
        self.assertEqual(edited_count, 1)
        self.assertEqual(self.item.property("count"), 1)
        self.assertEqual(self.item.property("selectedSource"), "Controller:X")
        self.assertEqual(self.item.property("selectedDestination"), "Controller:Y")

        # 2. Replace route
        ok = self.item.replaceRoute("Controller:X", "Desktop:Key:Space")
        self.assertTrue(ok)
        self.assertEqual(edited_count, 2)
        self.assertEqual(self.item.property("count"), 1)
        bindings = get_bindings(self.item)
        self.assertEqual(bindings[0]["destination"], "Desktop:Key:Space")

        # 3. Add second route
        self.item.addRoute("Controller:LB", "Controller:RB")
        self.assertEqual(edited_count, 3)
        self.assertEqual(self.item.property("count"), 2)

        # 4. Remove route by source
        ok = self.item.removeRoute("Controller:X")
        self.assertTrue(ok)
        self.assertEqual(edited_count, 4)
        self.assertEqual(self.item.property("count"), 1)
        self.assertEqual(get_bindings(self.item)[0]["source"], "Controller:LB")

        # 5. Remove selected route
        self.item.selectBinding(0)
        ok = self.item.removeSelectedRoute()
        self.assertTrue(ok)
        self.assertEqual(edited_count, 5)
        self.assertEqual(self.item.property("count"), 0)

    def test_preserves_unknown_keys_and_typed_routes(self):
        """serializedProfile() preserves ALL unknown keys, typed_routes, macros, etc."""
        original = {
            "name": "Complex Profile",
            "version": 42,
            "custom_metadata": {"author": "Tester", "tags": ["native", "fast"]},
            "routes": {
                "Controller:A": "Controller:B"
            },
            "typed_routes": [
                {"id": 1, "type": "axis_to_button", "axis": "RT", "threshold": 0.5}
            ],
            "route_conditions": {
                "Controller:A": {"layer": "Shift", "chord": ["Controller:LB"]}
            },
            "route_activators": {
                "Controller:A": {"type": "hold", "threshold_ms": 300}
            },
            "route_effects": {
                "Controller:A": {"type": "turbo", "rate_hz": 15}
            },
            "macros": {
                "QuickCombo": {"repeat_count": 2, "steps": [{"type": "press", "target": "Controller:B"}]}
            },
            "stick_processing": {
                "left": {"enabled": True, "inner_deadzone": 0.1}
            }
        }
        res = self.item.loadProfile(json.dumps(original))
        self.assertTrue(res)

        # Add a new route
        self.item.addRoute("Controller:View", "Controller:Menu")
        # Remove original route
        self.item.removeRoute("Controller:A")

        serialized_raw = self.item.serializedProfile()
        serialized = json.loads(serialized_raw)

        # Verify modified routes
        self.assertEqual(serialized["routes"], {"Controller:View": "Controller:Menu"})

        # Verify all untouched schema keys are perfectly preserved
        self.assertEqual(serialized["name"], "Complex Profile")
        self.assertEqual(serialized["version"], 42)
        self.assertEqual(serialized["custom_metadata"], original["custom_metadata"])
        self.assertEqual(serialized["typed_routes"], original["typed_routes"])
        self.assertEqual(serialized["route_conditions"], original["route_conditions"])
        self.assertEqual(serialized["route_activators"], original["route_activators"])
        self.assertEqual(serialized["route_effects"], original["route_effects"])
        self.assertEqual(serialized["macros"], original["macros"])
        self.assertEqual(serialized["stick_processing"], original["stick_processing"])

    def test_invalid_json_fails_with_error_and_preserves_previous_document(self):
        """Invalid JSON fails with error and preserves previously loaded document."""
        initial = {
            "name": "Doc 1",
            "routes": {"Controller:A": "Controller:B"}
        }
        ok = self.item.loadProfile(json.dumps(initial))
        self.assertTrue(ok)
        self.assertEqual(self.item.property("count"), 1)

        # Attempt to load invalid JSON
        bad_json = '{"name": "Bad Doc", "routes": broken syntax'
        res = self.item.loadProfile(bad_json)
        self.assertFalse(res)
        self.assertTrue(len(self.item.property("errorMessage")) > 0)

        # Confirm document 1 is preserved intact
        self.assertEqual(self.item.property("count"), 1)
        serialized = json.loads(self.item.serializedProfile())
        self.assertEqual(serialized["name"], "Doc 1")
        self.assertEqual(serialized["routes"], {"Controller:A": "Controller:B"})

        # Attempt to load non-object JSON (array)
        res_arr = self.item.loadProfile('["not", "an", "object"]')
        self.assertFalse(res_arr)
        self.assertIn("object", self.item.property("errorMessage").lower())
        self.assertEqual(self.item.property("count"), 1)

    def test_empty_source_or_destination_rejected(self):
        """Adding or replacing with empty values fails gracefully."""
        ok = self.item.addRoute("", "Controller:A")
        self.assertFalse(ok)
        self.assertIn("empty", self.item.property("errorMessage").lower())

        ok = self.item.addRoute("Controller:A", "  ")
        self.assertFalse(ok)
        self.assertIn("empty", self.item.property("errorMessage").lower())

    def test_array_routes_schema_roundtrip(self):
        """If profile uses array format for routes, it is preserved on serialization."""
        profile = {
            "name": "Array Routes Profile",
            "routes": [
                {"source": "Controller:A", "destination": "Controller:B"},
                {"source": "Controller:X", "destination": "Desktop:Key:Space"}
            ]
        }
        ok = self.item.loadProfile(json.dumps(profile))
        self.assertTrue(ok)
        self.assertEqual(self.item.property("count"), 2)

        self.item.addRoute("Controller:Y", "Controller:Z")
        self.assertEqual(self.item.property("count"), 3)

        serialized = json.loads(self.item.serializedProfile())
        self.assertIsInstance(serialized["routes"], list)
        self.assertEqual(len(serialized["routes"]), 3)
        self.assertEqual(serialized["routes"][2], {"source": "Controller:Y", "destination": "Controller:Z"})

    def test_ui_components_and_selection_state(self):
        """Visual controls, text fields, and selection update correctly."""
        profile = {
            "routes": {
                "Controller:LB": "Controller:RB",
                "Controller:LT": "Controller:RT"
            }
        }
        self.item.loadProfile(json.dumps(profile))

        # Select binding by index
        self.item.selectBinding(0)
        self.assertEqual(self.item.property("selectedIndex"), 0)
        self.assertEqual(self.item.property("selectedSource"), "Controller:LB")
        self.assertEqual(self.item.property("selectedDestination"), "Controller:RB")

        # Clear selection
        self.item.selectBinding(-1)
        self.assertEqual(self.item.property("selectedIndex"), -1)
        self.assertEqual(self.item.property("selectedSource"), "")
        self.assertEqual(self.item.property("selectedDestination"), "")

        # Find child components
        source_input = self.item.findChild(QQuickItem, "mappingSourceInput")
        dest_input = self.item.findChild(QQuickItem, "mappingDestInput")
        add_btn = self.item.findChild(QQuickItem, "addRouteButton")
        replace_btn = self.item.findChild(QQuickItem, "replaceRouteButton")
        remove_btn = self.item.findChild(QQuickItem, "removeRouteButton")

        self.assertIsNotNone(source_input)
        self.assertIsNotNone(dest_input)
        self.assertIsNotNone(add_btn)
        self.assertIsNotNone(replace_btn)
        self.assertIsNotNone(remove_btn)
        self.assertFalse(add_btn.property("enabled"))


if __name__ == '__main__':
    unittest.main(verbosity=2)
