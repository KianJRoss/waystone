"""Native per-game plugin features from preserved data, not a Windows plugin host.

Imported history is distinct from current running state. Duration-provider values
retain their source schema; this module never guesses units or fetches accounts.
"""
from copy import deepcopy
from math import isfinite
from .plugin_metadata import PluginMetadata

MAX_DURATION_SECONDS = 100 * 366 * 86400


def _duration(value):
    # Bound ints before isfinite: converting arbitrary-size ints to float overflows.
    return (type(value) in (int, float) and 0 <= value <= MAX_DURATION_SECONDS
            and isfinite(value))


DATASETS = ('GameActivity', 'SuccessStory', 'HowLongToBeat', 'PlayerActivities')


def game_plugin_details(archive, canonical_id, *, limit=200):
    if not isinstance(canonical_id, str) or not canonical_id or len(canonical_id) > 512:
        raise ValueError('Select an exact canonical game identity')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Expected limit 1..1000')
    result = {'canonical_id': canonical_id, 'basis': 'preserved plugin metadata',
              'live_account_sync': False, 'datasets': {}}
    repo = PluginMetadata(archive)
    try:
        for dataset in DATASETS:
            rows = list(repo.records(canonical_id=canonical_id, dataset=dataset))
            if len(rows) > 1:
                result['datasets'][dataset] = {'status': 'ambiguous', 'record_count': len(rows)}
                continue
            if not rows:
                result['datasets'][dataset] = {'status': 'absent'}
                continue
            doc = rows[0]['document']
            items = doc.get('Items')
            if not isinstance(items, list):
                result['datasets'][dataset] = {'status': 'malformed'}
                continue
            value = {'status': 'present' if items else 'empty', 'item_count': len(items),
                     'truncated': len(items) > limit, 'last_refresh': doc.get('DateLastRefresh'),
                     'items': deepcopy(items[:limit])}
            if dataset == 'GameActivity':
                valid = [x for x in items if isinstance(x, dict)
                         and _duration(x.get('ElapsedSeconds'))]
                value['recorded_seconds'] = sum(x['ElapsedSeconds'] for x in valid)
                value['valid_duration_count'] = len(valid)
                value['invalid_duration_count'] = len(items) - len(valid)
                # Imported record can be empty: zero recorded sessions != never played.
                value['current_running_state'] = 'not_observed'
            if dataset == 'SuccessStory':
                value['completion_claim'] = 'not_computed'
            if dataset == 'HowLongToBeat':
                value['selection_required'] = len(items) > 1
                value['units'] = 'preserved source schema; not normalized'
            result['datasets'][dataset] = value
    finally:
        repo.close()
    return result
