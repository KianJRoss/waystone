"""Pure, lossless display grouping over detached native catalog records.

No filesystem/database/network access, matching by title, importer identity, or
inference of a store from a product ID. Caller-supplied links/aliases must be
previously confirmed app-owned identity evidence, never candidate suggestions.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import re

KIND = 'waystone-game-identity-groups-v1'
_BAD = {'', 'unknown', 'unassigned', 'manual', 'manual/unassigned', 'none', 'null',
        'false', 'true', 'n/a', '0', '-1', '00000000-0000-0000-0000-000000000000'}
_NATIVE_OBSERVATIONS = {'native_installation', 'native_discovery', 'native_verified_installation'}


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _id(kind, value):
    return kind + ':' + hashlib.sha256(_dump(value).encode()).hexdigest()[:24]


def _text(value):
    if not isinstance(value, str) or value != value.strip() or len(value) > 4096:
        return None
    if value.casefold() in _BAD or any(ord(c) < 32 for c in value):
        return None
    return value


def _product(value):
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)
    text = _text(value)
    if text and re.fullmatch(r'-?\d+', text) and (text.startswith('-') or not text.strip('0')):
        return None
    return text


def _variants(game):
    return [v for v in game.get('variants', []) if isinstance(v, dict)]


def _signature(game, field):
    values = set()
    for obj in [game] + _variants(game):
        value = _text(obj.get(field))
        if value:
            values.add(value.casefold())
        metadata = obj.get('metadata')
        if field == 'platform' and isinstance(metadata, dict):
            # native_catalog uses joined platform text on each variant. Prefer
            # the same variant field so PC does not become an invented platform.
            if not value:
                values.update(x.casefold() for x in metadata.get('platforms', []) if _text(x))
    return tuple(sorted(values))


def _products(game):
    result = set()
    for variant in _variants(game):
        provider = _text(variant.get('provider')) or _text(variant.get('source'))
        product = _product(variant.get('provider_game_id'))
        if provider and product:
            result.add((provider.casefold(), product))
    return result


def _installed(variant):
    observation = variant.get('observation', {})
    return (isinstance(observation, dict)
            and observation.get('kind') in _NATIVE_OBSERVATIONS
            and observation.get('installation_verified') is True
            and variant.get('status') == 'installed')


def _quality(value):
    """Count available evidence, not a claim that imported media bytes exist."""
    if isinstance(value, dict):
        return sum(_quality(v) for k, v in value.items() if k != 'provenance')
    if isinstance(value, list):
        return sum(_quality(v) for v in value)
    return int(value is not None and value != '' and value != 'unknown')


def _fill(target, source):
    # Choose a complete representative then fill gaps. Conflicting values stay
    # on source_records and variants; never combine counters or histories.
    for key, value in source.items():
        if key not in target or target[key] is None or target[key] == '':
            target[key] = deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _fill(target[key], value)


def project_game_identity_groups(catalog, *, confirmed_links=None, canonical_aliases=None):
    """Return a detached, catalog-compatible lossless grouping projection.

    ``catalog`` is native_catalog.load_native_catalog()'s JSON-compatible dict.
    ``confirmed_links`` is an existing CatalogLinkStore.resolution() mapping or
    snapshot; ``canonical_aliases`` maps exact catalog IDs to confirmed canonical
    IDs. Nothing is loaded implicitly. Invalid/unknown link endpoints fail closed.
    Product identity matches require equal full platform/edition signatures and
    identical complete product sets, preventing transitive cross-store bridges.
    Unknown/missing game IDs are retained using content-addressed record keys.
    """
    if not isinstance(catalog, dict) or not isinstance(catalog.get('games'), list):
        raise ValueError('Expected detached catalog with games list')
    if len(catalog['games']) > 10000:
        raise ValueError('At most 10000 source games')
    if any(not isinstance(g, dict) or not isinstance(g.get('variants', []), list)
           for g in catalog['games']):
        raise ValueError('Each game must be a dictionary with a variants list')
    # Sort by content, including equal-ID records, so ordering cannot pick a
    # different representative or discard repeated raw source records.
    records = sorted(deepcopy(catalog['games']), key=_dump)
    counts = defaultdict(int)
    keys = []
    by_id = defaultdict(list)
    for index, game in enumerate(records):
        digest = _id('record', game)
        counts[digest] += 1
        keys.append(digest + ':' + str(counts[digest]))
        ident = _text(game.get('id'))
        if ident:
            by_id[ident].append(index)
    parents = list(range(len(records)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    evidence = []
    def join(indexes, kind, detail):
        indexes = sorted(set(indexes))
        if len(indexes) < 2:
            return
        for i in indexes[1:]:
            parents[root(i)] = root(indexes[0])
        evidence.append({'kind': kind, 'record_keys': [keys[i] for i in indexes], **detail})

    # Already-detached equal canonical IDs are explicit identity, not titles.
    for ident, indexes in sorted(by_id.items()):
        join(indexes, 'exact_canonical_id', {'canonical_id': ident})

    aliases = canonical_aliases or {}
    if not isinstance(aliases, dict):
        raise ValueError('Canonical aliases must be an explicit mapping')
    alias_buckets = defaultdict(list)
    def resolve(ident, mapping):
        seen = set()
        while ident in mapping:
            if ident in seen:
                raise ValueError('Cyclic identity mapping')
            seen.add(ident)
            target = mapping[ident]
            if not _text(target):
                raise ValueError('Invalid identity mapping target')
            if target == ident:
                return ident
            ident = target
        return ident
    for ident in aliases:
        if ident not in by_id:
            raise ValueError('Unknown canonical alias source')
        resolve(ident, aliases)
    for ident, indexes in by_id.items():
        alias_buckets[resolve(ident, aliases)].extend(indexes)
    for ident, indexes in sorted(alias_buckets.items()):
        join(indexes, 'exact_canonical_alias', {'canonical_id': ident})

    links = confirmed_links if confirmed_links is not None else {}
    if isinstance(links, dict) and 'resolution' in links:
        links = links['resolution']
    if not isinstance(links, dict):
        raise ValueError('Confirmed links must be an existing resolution mapping')
    for member, representative in sorted(links.items()):
        if member not in by_id or representative not in by_id:
            raise ValueError('Unknown confirmed link endpoint')
        resolve(member, links)
        join(by_id[member] + by_id[representative], 'confirmed_identity_link',
             {'member_id': member, 'representative_id': representative})

    # A product set rather than any overlapping product prevents A(Steam),
    # B(Steam+Epic), C(Epic) from silently crossing storefront boundaries.
    exact = defaultdict(list)
    for i, game in enumerate(records):
        products = tuple(sorted(_products(game)))
        if products and len({p[0] for p in products}) == 1:
            exact[(products, _signature(game, 'platform'), _signature(game, 'edition'))].append(i)
    component_members = defaultdict(list)
    for i in range(len(records)):
        component_members[root(i)].append(i)
    for signature, indexes in sorted(exact.items()):
        # Do not attach a third store through a previously confirmed component.
        components = {root(i) for i in indexes}
        compatible = all(_products(records[j]) == set(signature[0])
                         and _signature(records[j], 'platform') == signature[1]
                         and _signature(records[j], 'edition') == signature[2]
                         for component in components for j in component_members[component])
        if compatible:
            join(indexes, 'exact_provider_product', {'products': [list(p) for p in signature[0]],
                 'platforms': list(signature[1]), 'editions': list(signature[2])})

    buckets = defaultdict(list)
    for i in range(len(records)):
        buckets[root(i)].append(i)
    output, groups = [], []
    record_to_group = {}
    for indexes in buckets.values():
        members = [records[i] for i in indexes]
        ids = sorted({_text(g.get('id')) for g in members} - {None})
        record_keys = sorted(keys[i] for i in indexes)
        gid = _id('identity-group', ids if ids else record_keys)
        for i in indexes:
            record_to_group[i] = gid
        ranked = sorted(members, key=lambda g: (-_quality(g), _dump(g)))
        display = deepcopy(ranked[0])
        for game in ranked[1:]:
            _fill(display, game)
        # Retain every variant including repeats and every per-game override.
        variants = [deepcopy(v) for game in members for v in game.get('variants', [])]
        variants.sort(key=lambda v: (-_quality(v), _dump(v)))
        confirmed_representatives = sorted({links[i] for i in ids if i in links} & set(ids))
        display_id = confirmed_representatives[0] if confirmed_representatives else (ids[0] if ids else gid)
        display.update({'id': display_id, 'identity_group_id': gid,
                        'member_ids': ids, 'source_records': deepcopy(members),
                        'variants': variants, 'installed': any(_installed(v) for v in variants if isinstance(v, dict)),
                        'installed_variant_count': sum(_installed(v) for v in variants if isinstance(v, dict))})
        group_evidence = [e for e in evidence if set(e['record_keys']).issubset(record_keys)]
        providers = {p for game in members for p, _ in _products(game)}
        classification = ('same_game_across_providers' if len(providers) > 1 else
                          'duplicate_source_records' if len(members) > 1 else 'single_record')
        groups.append({'id': gid, 'member_ids': ids, 'record_keys': record_keys,
                       'classification': classification, 'evidence': sorted(group_evidence, key=_dump),
                       'source_record_count': len(members), 'variant_count': len(variants)})
        output.append(display)
    titles = defaultdict(list)
    for i, game in enumerate(records):
        title = game.get('title')
        normalized = ' '.join(re.findall(r'\w+', title.casefold())) if isinstance(title, str) else ''
        if normalized:
            titles[normalized].append(i)
    candidates = []
    for title, indexes in sorted(titles.items()):
        group_ids = sorted({record_to_group[i] for i in indexes})
        if len(group_ids) > 1:
            candidates.append({'id': _id('identity-candidate', [title, group_ids]),
                'kind': 'title_similarity_only', 'normalized_title': title,
                'group_ids': group_ids, 'requires_explicit_confirmation': True,
                'auto_grouped': False, 'records': [
                    {'record_key': keys[i], 'canonical_id': records[i].get('id'),
                     'platforms': list(_signature(records[i], 'platform')),
                     'editions': list(_signature(records[i], 'edition')),
                     'provider_products': [list(p) for p in sorted(_products(records[i]))]}
                    for i in indexes]})
    result = deepcopy(catalog)
    result.update({'games': sorted(output, key=lambda g: g['identity_group_id']),
        'identity_groups': sorted(groups, key=lambda g: g['id']),
        'duplicate_candidates': candidates, 'identity_projection_kind': KIND,
        'record_mutation_performed': False, 'source_scan_performed': False,
        'source_game_count': len(records)})
    return result
