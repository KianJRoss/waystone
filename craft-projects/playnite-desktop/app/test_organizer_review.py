"""Offline bundled-facade/Organizer regressions; no outer-project imports."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import modules.library_review as bundled_review
import modules.organizer_health as bundled_health
from organizer_view import BANNER, organizer_preview
from snapshot_bridge import snapshot_games


HERE = Path(__file__).resolve().parent


def demo_games():
    snapshot = json.loads((HERE / 'fixtures' / 'demo-snapshot.json').read_text())
    return snapshot, snapshot_games(snapshot)


class OrganizerReviewTests(unittest.TestCase):
    def test_facade_and_original_engine_are_bundled_imports(self):
        for module in (bundled_review, bundled_health):
            self.assertEqual(Path(module.__file__).resolve().parent, HERE / 'modules')

    def test_default_render_uses_facade_and_its_original_health_payload(self):
        _, games = demo_games()
        captured = []
        original_builder = bundled_review.review_catalog

        def capture(catalog):
            self.assertIs(catalog['games'], games)
            review = original_builder(catalog)
            captured.append(review)
            return review

        # Capture default dispatch without replacing the engine or importing a root module.
        with patch.object(bundled_review, 'review_catalog', side_effect=capture) as builder:
            text = organizer_preview(games)
        builder.assert_called_once_with({'games': games})
        self.assertEqual(captured[0]['health_report'], bundled_health.health_report(games))
        self.assertIn('HEALTH REPORT', text)
        self.assertIn(f"Records reviewed: {len(games)}", text)
        self.assertIn('Input: accepted; diagnostics: 0', text)
        self.assertIn('Coverage: caller_supplied_catalog', text)

    def test_actual_demo_render_is_immutable_and_preserves_unverified_claims(self):
        snapshot, games = demo_games()
        before = copy.deepcopy(games)
        text = organizer_preview(games)
        self.assertEqual(games, before)
        self.assertIn('Unverified import claims:', text)
        for game in games:
            for variant in game['variants']:
                for key in ('source_context', 'observation', 'evidence'):
                    self.assertIn(json.dumps(variant[key], ensure_ascii=False, sort_keys=True), text)
                self.assertIn(variant['provider_installation_id'], text)
                self.assertIn(variant['source_id'], text)
                self.assertIn(variant['installation_id'], text)
        self.assertIn(snapshot['source']['host'], text)
        self.assertIn(snapshot['source']['instance'], text)
        self.assertIn(snapshot['observed_at'], text)
        self.assertIn('source completeness: unknown', text)
        self.assertIn('Provenance verification: not_performed (claims only)', text)
        self.assertIn('Source scan performed: false', text)

    def test_gap_state_counts_come_from_facade_not_legacy_issue_totals(self):
        games = [{'id': 'a', 'title': '', 'type': 8, 'tags': [], 'variants': [{}]}]
        review = bundled_review.review_catalog({'games': games})
        counts = {state: sum(gap['state'] == state for gap in review['metadata_gaps'])
                  for state in ('not_imported', 'unknown', 'invalid')}
        self.assertEqual(counts, {'not_imported': 6, 'unknown': 2, 'invalid': 1})
        text = organizer_preview(games)
        self.assertIn('Metadata-gap states: not_imported=6; unknown=2; invalid=1', text)
        self.assertIn('Not imported / unknown metadata:', text)

    def test_duplicate_proposal_count_preserves_legacy_candidate_output(self):
        variant = {'source': 'Steam', 'source_id': '42', 'installation_id': 'disk-A', 'platform': 'unknown'}
        second = {**variant, 'installation_id': 'disk-B'}
        games = [{'id': 'a', 'title': 'Same Game', 'variants': [variant, second]},
                 {'id': 'b', 'title': ' same   game ', 'variants': []}]
        before = copy.deepcopy(games)
        text = organizer_preview(games)
        self.assertIn('Duplicate proposals (review only): 2', text)
        self.assertIn('Candidate duplicate groups: 1', text)
        self.assertIn('CANDIDATE —', text)
        self.assertIn('Original IDs: a, b', text)
        self.assertIn('disk-A', text)
        self.assertIn('disk-B', text)
        self.assertIn('Not full plugin parity: no AI analysis, bulk operations, undo, merge or deletion.', text)
        self.assertIn('Candidates are suggestions, not confirmed duplicates.', text)
        self.assertEqual(games, before)

    def test_unknown_partial_claims_never_become_verified_installations(self):
        snapshot, _ = demo_games()
        snapshot.update(complete=False, availability='partial')
        games = snapshot_games(snapshot)
        before = copy.deepcopy(games)
        text = organizer_preview(games)
        self.assertIn('Unknown stays unknown.', text)
        self.assertIn('unknown (not inferred)', text)
        self.assertIn('"availability": "partial"', text)
        self.assertIn('"complete": false', text)
        self.assertIn('source completeness: unknown', text)
        self.assertIn('"status": "declared-installed"', text)
        self.assertNotIn('"status": "installed"', text)
        self.assertEqual(games, before)

    def test_bundled_claims_are_detached_in_direct_and_proposal_records(self):
        _, games = demo_games()
        before = copy.deepcopy(games)
        review = bundled_review.review_catalog({'games': games})
        proposal = next(p for p in review['duplicate_proposals'] if p['kind'] == 'repeated_source_record')
        record = proposal['records'][0]
        direct = review['records'][record['record_index']]['variants'][0]['provenance']
        proposed = record['variants'][0]['provenance']
        self.assertEqual(direct, proposed)
        direct['source_context']['host'] = 'output-only'
        proposed['observation']['complete'] = False
        direct['evidence']['field_locators'].append('output-only')
        self.assertEqual(games, before)
        self.assertNotEqual(direct['source_context'], proposed['source_context'])
        self.assertNotEqual(direct['observation'], proposed['observation'])

    def test_malformed_input_is_visible_not_a_successful_empty_catalog(self):
        text = organizer_preview(None)
        self.assertIn('Input: invalid; diagnostics: 1', text)
        self.assertIn('Records reviewed: 0', text)
        self.assertIn('source completeness: unknown', text)
        self.assertNotIn('UNAVAILABLE', text)
        malformed = organizer_preview([None, {'title': 7, 'variants': [None]}])
        self.assertIn('malformed record', malformed)
        self.assertIn('malformed variant', malformed)

    def test_failed_builder_does_not_expose_exception_message_as_markup(self):
        def broken(catalog):
            raise ValueError('<img src="https://invalid.test/secret">')
        text = organizer_preview([], broken)
        self.assertIn('UNAVAILABLE — review failed (ValueError)', text)
        self.assertNotIn('https://invalid.test', text)
        self.assertNotIn('HEALTH REPORT', text)

    def test_absent_facade_is_unavailable_without_legacy_fallback_claims(self):
        with patch.dict(sys.modules, {'modules.library_review': None}):
            text = organizer_preview([])
        self.assertIn('UNAVAILABLE', text)
        self.assertIn('No checks have run.', text)
        self.assertNotIn('Input: accepted', text)

    def test_invalid_builder_contract_is_unavailable(self):
        for value in (None, [], {}, {'health_report': []}):
            with self.subTest(value=value):
                self.assertIn('UNAVAILABLE', organizer_preview([], lambda catalog: value))

    def test_untrusted_markup_stays_literal_in_existing_plain_text_sink(self):
        title = '<b>Literal</b><img src="https://invalid.test/image">'
        games = [{'id': '<a href="file:///never">id</a>', 'title': title,
                  'variants': [{'source': '<i>store</i>', 'source_id': '42',
                                'observation': {'at': '<script>never()</script>'}}]}]
        text = organizer_preview(games)
        self.assertIsInstance(text, str)
        self.assertIn(title, text)
        self.assertIn('<script>never()</script>', text)
        # Imported-baseline evidence moved into its own tab; keep that sink plain/read-only.
        qml = (HERE / 'OrganizerWorkspace.qml').read_text()
        sink_start = next(i for i, line in enumerate(qml.splitlines()) if 'objectName: "organizerReport"' in line)
        sink = '\n'.join(qml.splitlines()[sink_start:sink_start + 5])
        self.assertIn('textFormat: TextEdit.PlainText', sink)
        self.assertIn('readOnly: true', sink)

    def test_render_is_deterministic_and_performs_no_io(self):
        _, games = demo_games()
        before = copy.deepcopy(games)
        expected = organizer_preview(games)
        with patch('builtins.open', side_effect=AssertionError('filesystem access')), \
             patch('subprocess.Popen', side_effect=AssertionError('process')), \
             patch('socket.socket', side_effect=AssertionError('network')):
            self.assertEqual(organizer_preview(games), expected)
        self.assertEqual(games, before)

    def test_minimal_standalone_bundle_needs_no_outer_project(self):
        with tempfile.TemporaryDirectory(prefix='organizer-standalone-') as directory:
            bundle = Path(directory)
            (bundle / 'modules').mkdir()
            for relative in ('organizer_view.py', 'modules/__init__.py',
                             'modules/library_review.py', 'modules/organizer_health.py'):
                shutil.copyfile(HERE / relative, bundle / relative)
            code = (
                'import pathlib, sys\n'
                f'sys.path.insert(0, {str(bundle)!r})\n'
                'import modules.library_review as review\n'
                'import modules.organizer_health as health\n'
                'from organizer_view import organizer_preview\n'
                f'base = pathlib.Path({str(bundle)!r})\n'
                'assert pathlib.Path(review.__file__).parent == base / "modules"\n'
                'assert pathlib.Path(health.__file__).parent == base / "modules"\n'
                'text = organizer_preview([])\n'
                'assert "Input: accepted" in text and "HEALTH REPORT" in text\n'
                'assert "UNAVAILABLE" not in text\n'
                'print("standalone-ok")\n'
            )
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code], cwd=bundle,
                                    text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'standalone-ok')


if __name__ == '__main__':
    unittest.main()
