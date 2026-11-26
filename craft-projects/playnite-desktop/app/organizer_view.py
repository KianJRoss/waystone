"""Presentation adapter for the native, deterministic Organizer review. No writes or AI."""
import json

BANNER = (
    'BUILT-IN ORGANIZER — read-only preview\n'
    'Custom Library Organizer AI: metadata-health and duplicate-proposal preview only.\n'
    'Not full plugin parity: no AI analysis, bulk operations, undo, merge or deletion.\n'
    'Source coverage: Steam manifests do not supply platform, tags or artwork; missing '
    'imported metadata is not evidence of corruption or a broken game. Unknown stays unknown.\n'
    'Candidates are suggestions, not confirmed duplicates. Source IDs/installations are retained.\n'
    'No network calls, source writes or game launching. Review covers the full loaded catalog, not library filters.\n'
)

def organizer_preview(games, report_builder=None):
    """Render a bundled review facade result as plain text, never rich text.

    An optional report_builder follows review_catalog's catalog-object contract.
    """
    if report_builder is None:
        try:
            from modules.library_review import review_catalog
            report_builder = review_catalog
        except ImportError:
            return BANNER + '\nUNAVAILABLE — review facade/health engine is not installed. No checks have run.'
    try:
        review = report_builder({'games': games})
        if not isinstance(review, dict) or not isinstance(review.get('health_report'), dict):
            raise ValueError('Review must contain a health report object')
        report = review['health_report']
        coverage = review.get('coverage', {})
        gaps = review.get('metadata_gaps', [])
        gap_counts = {state: sum(gap.get('state') == state for gap in gaps)
                      for state in ('not_imported', 'unknown', 'invalid')}
        # Plain text only: metadata/IDs cannot become rich text or executable actions.
        # Main.qml already binds this string to a read-only TextEdit.PlainText area.
        lines = [BANNER, 'REVIEW INPUT & COVERAGE',
                 f"Input: {coverage.get('input_state', 'unknown')}; diagnostics: {len(review.get('input_issues', []))}",
                 f"Coverage: {coverage.get('scope', 'unknown')}; reviewed records: {coverage.get('reviewed_records', 'unknown')}",
                 f"Source scan performed: {json.dumps(coverage.get('source_scan_performed'))}; source completeness: {coverage.get('source_completeness', 'unknown')}",
                 f"Provenance verification: {coverage.get('provenance_verification', 'unknown')} (claims only)",
                 'Metadata-gap states: ' + '; '.join(f'{state}={count}' for state, count in gap_counts.items()),
                 f"Duplicate proposals (review only): {len(review.get('duplicate_proposals', []))}",
                 '', 'HEALTH REPORT', f"Records reviewed: {report.get('entry_count', 'unknown')}",
                 'Reported observations (coverage gaps are informational, not damage):']
        for key, count in sorted(report.get('issue_totals', {}).items()):
            label = 'Not imported / unknown metadata' if key == 'missing_metadata' else key.replace('_', ' ')
            lines.append(f"  {label}: {count}")
        candidates = report.get('duplicate_candidates', [])
        lines += [f"Candidate duplicate groups: {len(candidates)}", '']
        for candidate in candidates:
            lines.append('CANDIDATE — ' + ', '.join(candidate.get('titles', [])))
            lines.append('Original IDs: ' + ', '.join(candidate.get('ids', [])))
            lines.append(candidate.get('note', 'No merge/delete action.'))
        lines += ['', 'RECORD REVIEW — original IDs and source identities']
        records = {record['record_index']: record for record in review.get('records', [])}
        for entry in report.get('entries', []):
            lines += ['', f"{entry.get('title', '(unknown)')}  [{entry.get('id', '?')}]"]
            record = records.get(entry.get('index'), {})
            variants = {variant['variant_index']: variant for variant in record.get('variants', [])}
            if record.get('provenance'):
                lines.append('  Unverified record claims: ' + json.dumps(record['provenance'], ensure_ascii=False, sort_keys=True))
            for variant in entry.get('variants', []):
                lines.append('  Source: ' + json.dumps(variant, ensure_ascii=False, sort_keys=True))
                detail = variants.get(variant.get('variant_index'), {})
                if detail.get('identity'):
                    lines.append('  Original identity: ' + json.dumps(detail['identity'], ensure_ascii=False, sort_keys=True))
                if detail.get('provenance'):
                    lines.append('  Unverified import claims: ' + json.dumps(detail['provenance'], ensure_ascii=False, sort_keys=True))
            for issue in entry.get('issues', []):
                label = 'Not imported / unknown' if issue.get('type') == 'missing_metadata' else issue.get('type', 'observation').replace('_', ' ')
                lines.append(f"  {label} — {issue.get('field', '')}: {issue.get('message', '')}")
        return '\n'.join(lines)
    except Exception as exc:
        return BANNER + '\nUNAVAILABLE — review failed (' + type(exc).__name__ + '). No successful report.'
