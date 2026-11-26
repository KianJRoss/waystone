"""Evidence-based library attribution; original records/IDs are never rewritten."""
import json
from collections import defaultdict
from pathlib import Path

try:
    from private_addons import PACKAGE_CATALOG_LIBRARY
except ImportError:
    PACKAGE_CATALOG_LIBRARY = 'Catalog'

KIND = 'waystone-library-reconciliation-v1'


def build_reconciliation(rows, catalog):
    pkg_lib = PACKAGE_CATALOG_LIBRARY
    folders = {x['Folder'] for x in catalog if isinstance(x, dict) and isinstance(x.get('Folder'), str)}
    assignments = {}
    packages = defaultdict(list)
    for row in rows:
        ident = row['canonical_id']
        provider = row['provider']
        product = row.get('provider_game_id')
        meta = row.get('metadata', {})
        emulator = any(
            isinstance(a, dict) and a.get('Type') == 'Emulator'
            for a in (meta.get('GameActions') or [])
        )
        package = product in folders if isinstance(product, str) else False
        if provider == 'Unassigned':
            if package and not emulator:
                assignments[ident] = {
                    'library': pkg_lib,
                    'evidence_kind': 'exact_catalog_folder',
                    'package_id': product,
                }
            elif emulator and not package:
                assignments[ident] = {
                    'library': 'Emulation',
                    'evidence_kind': 'explicit_emulator_action',
                }
        if package and not emulator and provider in ('Unassigned', pkg_lib):
            packages[product].append(row)
    groups = []
    for product, items in packages.items():
        assigned = [x for x in items if x['provider'] == pkg_lib]
        unassigned = [x for x in items if x['provider'] == 'Unassigned']
        if len(assigned) == 1 and unassigned:
            # A package identifier selects the package, not a title/edition guess.
            groups.append({
                'canonical_ids': sorted({x['canonical_id'] for x in items}),
                'representative_id': assigned[0]['canonical_id'],
                'evidence_kind': 'exact_catalog_folder',
                'package_id': product,
            })
    return {
        'kind': KIND,
        'assignments': assignments,
        'groups': groups,
        'counts': {
            'assigned': len(assignments),
            'libraries': {
                name: sum(x['library'] == name for x in assignments.values())
                for name in (pkg_lib, 'Emulation')
            },
            'duplicate_groups': len(groups),
        },
    }


def load_assignments(path, canonical_ids):
    pkg_lib = PACKAGE_CATALOG_LIBRARY
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Select a bounded app-owned reconciliation file')
    data = json.loads(path.read_bytes())
    if data.get('kind') != KIND or not isinstance(data.get('assignments'), dict):
        raise ValueError('Invalid library reconciliation')
    ids = set(canonical_ids)
    result = {}
    for ident, item in data['assignments'].items():
        if ident not in ids or not isinstance(item, dict):
            raise ValueError('Unknown reconciliation identity')
        library = item.get('library')
        kind = item.get('evidence_kind')
        if (library, kind) not in ((pkg_lib, 'exact_catalog_folder'), ('Emulation', 'explicit_emulator_action')):
            raise ValueError('Unsupported library attribution evidence')
        if kind == 'exact_catalog_folder' and not isinstance(item.get('package_id'), str):
            raise ValueError('Package identity is required')
        result[ident] = library
    return result
