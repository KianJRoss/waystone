import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import controller_profile_assignments as cpa


class ControllerProfileAssignmentsTests(unittest.TestCase):
    def assertNotMutated(self, before, after_copy, func, *args, **kwargs):
        # helper not required but handy; tests below do explicit checks
        pass

    def test_normalize_from_none(self):
        doc = cpa.normalize(None)
        self.assertEqual(
            doc,
            {"version": 1, "profiles": {}, "default_profile": None, "game_profiles": {}},
        )
        # also empty dict
        self.assertEqual(cpa.normalize({}), doc)

    def test_default_taken_from_active_profile(self):
        src = {
            "version": 1,
            "active_profile": "P2",
            "profiles": {"P1": {"a": 1}, "P2": {"b": 2}},
        }
        out = cpa.normalize(src)
        self.assertEqual(out["default_profile"], "P2")
        self.assertEqual(out["active_profile"], "P2")

    def test_default_falls_back_to_sorted_first(self):
        src = {
            "version": 1,
            "profiles": {"Zulu": {}, "Alpha": {}},
        }
        out = cpa.normalize(src)
        self.assertEqual(out["default_profile"], "Alpha")

    def test_stale_game_assignment_dropped(self):
        src = {
            "version": 1,
            "profiles": {"A": {}},
            "default_profile": "A",
            "game_profiles": {"g1": "A", "g2": "Missing"},
        }
        out = cpa.normalize(src)
        self.assertEqual(out["game_profiles"], {"g1": "A"})

    def test_unknown_keys_and_version_preserved(self):
        src = {
            "version": 7,
            "profiles": {"A": {}},
            "active_profile": "A",
            "virtual_gamepad_driver": "vigem",
            "extra_top": {"nested": True},
            "another": [1, 2, 3],
        }
        out = cpa.normalize(src)
        self.assertEqual(out["version"], 7)
        self.assertEqual(out["virtual_gamepad_driver"], "vigem")
        self.assertEqual(out["extra_top"], {"nested": True})
        self.assertEqual(out["another"], [1, 2, 3])

    def test_load_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "nope", "profiles.json")
            out = cpa.load(missing)
            self.assertEqual(
                out,
                {"version": 1, "profiles": {}, "default_profile": None, "game_profiles": {}},
            )

    def test_load_save_roundtrip_uses_os_replace(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sub", "profiles.json")
            doc = {
                "version": 2,
                "profiles": {"Main": {"buttons": {"A": "B"}}},
                "active_profile": "Main",
                "virtual_gamepad_driver": "x",
                "game_profiles": {"game-1": "Main"},
            }
            real_replace = os.replace
            calls = []

            def spy_replace(src, dst):
                calls.append((src, dst))
                return real_replace(src, dst)

            with mock.patch("controller_profile_assignments.os.replace", side_effect=spy_replace):
                cpa.save(path, doc)

            self.assertTrue(os.path.exists(path))
            self.assertEqual(len(calls), 1)
            self.assertEqual(os.path.abspath(calls[0][1]), os.path.abspath(path))
            # temp file should be in same directory
            self.assertEqual(os.path.dirname(os.path.abspath(calls[0][0])), os.path.dirname(os.path.abspath(path)))
            self.assertFalse(os.path.exists(calls[0][0]))

            loaded = cpa.load(path)
            self.assertEqual(loaded["version"], 2)
            self.assertEqual(loaded["default_profile"], "Main")
            self.assertEqual(loaded["active_profile"], "Main")
            self.assertEqual(loaded["profiles"]["Main"], {"buttons": {"A": "B"}})
            self.assertEqual(loaded["game_profiles"], {"game-1": "Main"})
            self.assertEqual(loaded["virtual_gamepad_driver"], "x")

    def test_resolve_with_assignment(self):
        doc = {
            "version": 1,
            "profiles": {"Default": {}, "Shooter": {}},
            "default_profile": "Default",
            "game_profiles": {"g1": "Shooter"},
        }
        self.assertEqual(cpa.resolve(doc, "g1"), "Shooter")

    def test_resolve_without_assignment(self):
        doc = {
            "version": 1,
            "profiles": {"Default": {}, "Shooter": {}},
            "default_profile": "Default",
            "game_profiles": {},
        }
        self.assertEqual(cpa.resolve(doc, "unknown"), "Default")
        self.assertEqual(cpa.resolve(doc, None), "Default")

    def test_resolve_with_neither(self):
        doc = {"version": 1, "profiles": {}, "default_profile": None, "game_profiles": {}}
        self.assertIsNone(cpa.resolve(doc, "g1"))
        self.assertIsNone(cpa.resolve(doc, None))

    def test_assign_keyerror_for_missing_profile(self):
        doc = cpa.normalize({"profiles": {"A": {}}, "active_profile": "A"})
        with self.assertRaises(KeyError):
            cpa.assign(doc, "g1", "Missing")

    def test_assign_none_removes(self):
        doc = cpa.normalize(
            {
                "profiles": {"A": {}},
                "active_profile": "A",
                "game_profiles": {"g1": "A", "g2": "A"},
            }
        )
        out = cpa.assign(doc, "g1", None)
        self.assertEqual(out["game_profiles"], {"g2": "A"})
        # removing missing is no-op
        out2 = cpa.assign(out, "missing", None)
        self.assertEqual(out2["game_profiles"], {"g2": "A"})

    def test_set_default_updates_active_profile(self):
        doc = cpa.normalize(
            {
                "profiles": {"A": {}, "B": {}},
                "active_profile": "A",
                "default_profile": "A",
            }
        )
        out = cpa.set_default(doc, "B")
        self.assertEqual(out["default_profile"], "B")
        self.assertEqual(out["active_profile"], "B")
        with self.assertRaises(KeyError):
            cpa.set_default(doc, "Missing")

    def test_create_from_base_copies_without_aliasing(self):
        base_body = {"buttons": {"A": "X"}, "nested": {"k": [1, 2]}}
        doc = cpa.normalize({"profiles": {"Base": base_body}, "active_profile": "Base"})
        out = cpa.create(doc, "Copy", base="Base")
        self.assertIn("Copy", out["profiles"])
        self.assertEqual(out["profiles"]["Copy"], base_body)
        self.assertIsNot(out["profiles"]["Copy"], out["profiles"]["Base"])
        self.assertIsNot(out["profiles"]["Copy"]["nested"], out["profiles"]["Base"]["nested"])
        # mutate copy, base unchanged
        out["profiles"]["Copy"]["buttons"]["A"] = "CHANGED"
        out["profiles"]["Copy"]["nested"]["k"].append(3)
        self.assertEqual(out["profiles"]["Base"]["buttons"]["A"], "X")
        self.assertEqual(out["profiles"]["Base"]["nested"]["k"], [1, 2])

    def test_create_duplicate_name_valueerror(self):
        doc = cpa.normalize({"profiles": {"A": {}}, "active_profile": "A"})
        with self.assertRaises(ValueError):
            cpa.create(doc, "A")
        with self.assertRaises(ValueError):
            cpa.create(doc, "")
        with self.assertRaises(ValueError):
            cpa.create(doc, "   ")

    def test_rename_rewrites_default_active_and_game_assignments(self):
        doc = cpa.normalize(
            {
                "profiles": {"Old": {"x": 1}, "Other": {}},
                "active_profile": "Old",
                "default_profile": "Old",
                "game_profiles": {"g1": "Old", "g2": "Other"},
            }
        )
        out = cpa.rename(doc, "Old", "New")
        self.assertNotIn("Old", out["profiles"])
        self.assertIn("New", out["profiles"])
        self.assertEqual(out["profiles"]["New"], {"x": 1})
        self.assertEqual(out["default_profile"], "New")
        self.assertEqual(out["active_profile"], "New")
        self.assertEqual(out["game_profiles"], {"g1": "New", "g2": "Other"})

    def test_delete_last_profile_valueerror(self):
        doc = cpa.normalize({"profiles": {"Only": {}}, "active_profile": "Only"})
        with self.assertRaises(ValueError):
            cpa.delete(doc, "Only")

    def test_delete_default_picks_sorted_first_and_drops_assignments(self):
        doc = cpa.normalize(
            {
                "profiles": {"Zulu": {}, "Alpha": {}, "Mid": {}},
                "default_profile": "Zulu",
                "active_profile": "Zulu",
                "game_profiles": {"g1": "Zulu", "g2": "Mid"},
            }
        )
        out = cpa.delete(doc, "Zulu")
        self.assertNotIn("Zulu", out["profiles"])
        self.assertEqual(out["default_profile"], "Alpha")
        self.assertEqual(out["active_profile"], "Alpha")
        self.assertEqual(out["game_profiles"], {"g2": "Mid"})

    def test_input_docs_are_never_mutated(self):
        base_doc = {
            "version": 1,
            "profiles": {"A": {"v": [1]}, "B": {"v": [2]}},
            "active_profile": "A",
            "default_profile": "A",
            "game_profiles": {"g1": "A"},
            "extra": {"k": "v"},
        }

        def check(func, *args, **kwargs):
            src = copy.deepcopy(base_doc)
            before = copy.deepcopy(src)
            func(src, *args, **kwargs)
            self.assertEqual(src, before, f"{func.__name__} mutated input")

        check(cpa.normalize)
        check(cpa.resolve, "g1")
        check(cpa.assign, "g1", "B")
        check(cpa.assign, "g1", None)
        check(cpa.set_default, "B")
        check(cpa.create, "C", "A")
        check(cpa.duplicate, "A", "D")
        check(cpa.rename, "A", "A2")
        check(cpa.delete, "B")



class DeleteActiveKeepsDefaultTests(unittest.TestCase):
    def test_deleting_active_non_default_keeps_default(self):
        doc = {"version": 1, "active_profile": "B", "default_profile": "C",
               "profiles": {"A": {}, "B": {}, "C": {}}, "game_profiles": {}}
        out = cpa.delete(doc, "B")
        self.assertEqual(out["default_profile"], "C")
        self.assertEqual(out["active_profile"], "C")

if __name__ == "__main__":
    unittest.main()
