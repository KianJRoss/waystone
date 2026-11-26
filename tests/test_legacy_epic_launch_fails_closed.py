"""Legacy Epic connection launch must fail closed rather than open Heroic."""

import unittest

from native_services.launcher_connections import Connection, build_launch_intent


class TestLegacyEpicLaunchFailsClosed(unittest.TestCase):
    def test_legacy_epic_intent_never_opens_heroic(self):
        connection = Connection(
            provider="heroic-epic", product_id="ValidProduct_123",
            record_key="record", variant_key="variant", state="installed",
            title="Example", evidence=("fixture",),
            source_context={"host": "workstation"},
        )
        intent = build_launch_intent(connection)
        self.assertFalse(intent.verified_contract)
        self.assertEqual(intent.argv, ())
        self.assertIsNone(intent.uri)
        self.assertIn("Waystone direct Epic launch", intent.reason)
        self.assertNotIn("Heroic", intent.argv)
