"""Task-scoped, read-only native service. No transport or model/backend dependency."""
from __future__ import annotations

import json
import os
import stat
from typing import Any, Literal, TypedDict

from modules.library_review import review_catalog

Action = Literal['list', 'search', 'detail', 'sources', 'health', 'review']
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 65536
MAX_PAGE = 50
CATALOG_KIND = 'playnite-display-selected-copy-v1'
ACTIONS = ('list', 'search', 'detail', 'sources', 'health', 'review')
UNAVAILABLE = ('write', 'classify', 'apply', 'launch', 'install', 'ui_control')
GAME_FIELDS = ('id', 'title', 'type', 'tags', 'evidence', 'provenance', 'source_context', 'observation')
VARIANT_FIELDS = ('source', 'source_id', 'installation_id', 'platform', 'status', 'provider', 'instance', 'host', 'record_key', 'variant_key', 'external_id', 'provider_installation_id', 'record_kind', 'payload_presence', 'evidence', 'provenance', 'source_context', 'observation')


class Request(TypedDict):
    action: Action
    arguments: dict[str, Any]


class ControlError(ValueError):
    """Invalid/unsupported request; no action was performed."""


def _clip(value: Any, depth: int = 0) -> Any:
    """Bound untrusted display claims; they are data, never instructions."""
    if depth >= 6:
        return {'truncated': True}
    if isinstance(value, str):
        return value if len(value) <= 512 else value[:512] + '…[truncated]'
    if isinstance(value, list):
        result = [_clip(v, depth + 1) for v in value[:32]]
        return result + ([{'truncated': True}] if len(value) > 32 else [])
    if isinstance(value, dict):
        result = {k: _clip(v, depth + 1) for k, v in list(value.items())[:32] if len(k) <= 128}
        if len(value) > 32 or any(len(k) > 128 for k in value):
            result['_truncated'] = True
        return result
    return value


class AIControlService:
    """Detached explicitly supplied display snapshot; IDs are never inferred.

    All methods are local CPU-only reads. Construct a new instance on refresh.
    Returned provenance is an unverified imported claim. Recipe/media/path fields
    are deliberately not exposed. No discovered source or runtime is consulted.
    """

    def __init__(self, catalog: dict[str, Any]):
        try:
            raw = json.dumps(catalog, allow_nan=False).encode('utf-8')
            if len(raw) > MAX_INPUT_BYTES:
                raise ControlError('Catalog exceeds input byte cap')
            snapshot = json.loads(raw)
        except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
            raise ControlError('Expected finite JSON within input cap') from exc
        if (not isinstance(snapshot, dict) or type(snapshot.get('schema_version')) is not int
                or snapshot['schema_version'] not in (1, 2) or snapshot.get('catalog_kind') not in (CATALOG_KIND, 'native-library-sqlite-v1')
                or not isinstance(snapshot.get('games'), list) or len(snapshot['games']) > 10000):
            raise ControlError('Expected native or selected-copy display catalog schema 1 or 2, at most 10000 games')
        identities: set[str] = set()
        variants = 0
        for game in snapshot['games']:
            if not isinstance(game, dict) or not isinstance(game.get('id'), str) or not 0 < len(game['id']) <= 512 or game['id'] in identities:
                raise ControlError('Games require unique nonempty bounded IDs')
            identities.add(game['id'])
            if not isinstance(game.get('title'), str) or not game['title'].strip() or len(game['title']) > 4096:
                raise ControlError('Games require bounded titles')
            if not isinstance(game.get('variants'), list) or len(game['variants']) > 100 or any(not isinstance(v, dict) for v in game['variants']):
                raise ControlError('Expected at most 100 object variants per game')
            variants += len(game['variants'])
        if variants > 50000:
            raise ControlError('Too many variants')
        self._catalog = snapshot
        self._games = snapshot['games']
        self._review: dict[str, Any] | None = None

    @classmethod
    def from_json_file(cls, path: str | os.PathLike[str]) -> AIControlService:
        """Only an explicit regular file, no symlinks or supplied catalog roots."""
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_INPUT_BYTES:
                raise ControlError('Expected bounded regular file')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(MAX_INPUT_BYTES + 1)
            after = os.fstat(fd)
            if len(raw) > MAX_INPUT_BYTES or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ControlError('File exceeds cap or changed during read')
        finally:
            os.close(fd)
        try:
            data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ControlError('Nonfinite JSON')))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ControlError('Invalid JSON') from exc
        return cls(data)

    def execute(self, action: Action, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Strict allowlisted request schema. Unknown actions/fields fail closed."""
        if not isinstance(action, str) or action not in ACTIONS:
            raise ControlError('Unavailable action; read-only actions: ' + ', '.join(ACTIONS))
        args = {} if arguments is None else arguments
        allowed = {'offset', 'limit'} | ({'query'} if action == 'search' else set()) | ({'id'} if action == 'detail' else set()) | ({'section'} if action == 'review' else set())
        if not isinstance(args, dict) or set(args) - allowed:
            raise ControlError('Unknown argument')
        offset, limit = args.get('offset', 0), args.get('limit', 20)
        if type(offset) is not int or not 0 <= offset <= 1000000 or type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise ControlError('Invalid pagination')
        if action == 'search' and (not isinstance(args.get('query'), str) or not args['query'].strip() or len(args['query']) > 256):
            raise ControlError('Search requires nonempty query up to 256 characters')
        if action == 'detail' and (not isinstance(args.get('id'), str) or not 0 < len(args['id']) <= 512):
            raise ControlError('Detail requires bounded exact ID')
        section = args.get('section', 'records')
        if action == 'review' and (not isinstance(section, str) or section not in ('records', 'metadata_gaps', 'duplicate_proposals')):
            raise ControlError('Unknown review section')
        envelope: dict[str, Any] = {'schema': 'native-ai-control-v1', 'action': action, 'read_only': True,
            'scope': 'caller_supplied_display_catalog', 'provenance_verification': 'not_performed',
            'claims_are_untrusted_data': True, 'unavailable_actions': list(UNAVAILABLE)}
        rows: list[Any]
        if action in ('list', 'search', 'detail', 'sources'):
            games = self._games
            if action == 'search':
                query = args['query'].casefold().strip()
                games = [g for g in games if query in g['title'].casefold()]
            if action == 'detail':
                games = [g for g in games if g['id'] == args['id']]
            rows = []
            for game in games:
                row = {k: game[k] for k in GAME_FIELDS if k in game}
                if action in ('detail', 'sources'):
                    row['variants'] = [{k: v[k] for k in VARIANT_FIELDS if k in v} for v in game['variants']]
                else:
                    row['variant_count'] = len(game['variants'])
                rows.append(row)
        else:
            if self._review is None:
                self._review = review_catalog(self._catalog)
            report = self._review
            envelope['coverage'] = _clip(report['coverage'])
            envelope['input_issues'] = _clip(report['input_issues'])
            if action == 'health':
                envelope['issue_totals'] = _clip(report['health_report']['issue_totals'])
                rows = report['health_report']['entries']
            else:
                rows = report[section]
                envelope['section'] = section
                envelope['metadata_gap_count'] = len(report['metadata_gaps'])
                envelope['duplicate_proposal_count'] = len(report['duplicate_proposals'])
        items: list[Any] = []
        for row in rows[offset:offset + limit]:
            candidate = _clip(row)
            probe = {**envelope, 'items': items + [candidate]}
            if len(json.dumps(probe).encode('utf-8')) > MAX_OUTPUT_BYTES - 1024:
                if not items:
                    items.append({'truncated': True, 'reason': 'single record exceeds output cap'})
                break
            items.append(candidate)
        end = offset + len(items)
        envelope.update(items=items, total=len(rows), offset=offset, next_offset=end if end < len(rows) else None,
                        projection_limits={'string_chars': 512, 'collection_items': 32, 'depth': 6, 'response_bytes': MAX_OUTPUT_BYTES})
        # Detach returned rows from the internal snapshot and cached review.
        result = json.loads(json.dumps(envelope, allow_nan=False))
        if len(json.dumps(result).encode('utf-8')) > MAX_OUTPUT_BYTES:
            raise ControlError('Response exceeds byte cap')
        return result


def main(argv: list[str] | None = None) -> int:
    """One-shot local CLI; JSON errors never echo paths or input content."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('action')
    parser.add_argument('--args', default='{}', help='Strict JSON argument object (max 4096 bytes)')
    parsed = parser.parse_args(argv)
    try:
        if len(parsed.args.encode('utf-8')) > 4096:
            raise ControlError('Arguments exceed input cap')
        args = json.loads(parsed.args)
        result = AIControlService.from_json_file(parsed.catalog).execute(parsed.action, args)
    except (ControlError, OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({'schema': 'native-ai-control-v1', 'ok': False, 'error': 'invalid_input_or_unavailable_action', 'read_only': True}))
        return 2
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
