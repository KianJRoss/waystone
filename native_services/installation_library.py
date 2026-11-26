"""Provider-qualified installation choices; never infer a store ID from a title.

Historical library records are choices, not proof of current account ownership.
Account credentials and imported launch commands are never used by this projection.
"""
import json
import os
from pathlib import Path
import re
import tempfile
import shutil

try:
    from private_addons import (
        ADDON_INSTALL_ALIASES as _ADDON_ALIASES,
        ADDON_INSTALL_PROVIDERS as _ADDON_PROVIDERS,
        ADDON_INSTALLABLE_PROVIDERS as _ADDON_INSTALLABLE,
        validate_addon_product_id as _validate_addon_product,
    )
except ImportError:
    _ADDON_ALIASES = {}
    _ADDON_PROVIDERS = {}
    _ADDON_INSTALLABLE = frozenset()
    _validate_addon_product = None

PROVIDERS = {
    'steam': ('Steam', 'Steam confirms the install and manages its library folders.'),
    'epic': ('Epic Games', 'Download inside Waystone using Legendary and the existing Epic sign-in.'),
    'gog': ('GOG', 'Download inside Waystone using GOGdl and the existing GOG sign-in.'),
    'amazon': ('Amazon Games', 'Download inside Waystone using Nile and the existing Amazon sign-in.'),
    'battlenet': ('Battle.net', 'Native in-app installation is not implemented yet; existing launch support is separate.'),
    'ea': ('EA', 'Native in-app installation is not implemented yet.'),
    'ubisoft': ('Ubisoft Connect', 'Native in-app installation is not implemented yet.'),
    'xbox': ('Xbox / Microsoft Store', 'Download supported PC Store packages with the existing Xodus backend. Uses its Microsoft sign-in; unsupported console-only packages are excluded.'),
    **_ADDON_PROVIDERS,
    'emulation': ('Emulation', 'Manage existing ROMs and emulator profiles in Emulation; no automatic ROM downloads.'),
}
ALIASES = {'steam': 'steam', 'epic': 'epic', 'epic games': 'epic', 'epic games store': 'epic',
           'gog': 'gog', 'gog.com': 'gog', 'amazon': 'amazon', 'amazon games': 'amazon',
           'battle.net': 'battlenet', 'battlenet': 'battlenet', 'ea': 'ea', 'ea app': 'ea',
           'origin': 'ea', 'ubisoft': 'ubisoft', 'ubisoft connect': 'ubisoft', 'uplay': 'ubisoft',
           'xbox': 'xbox', 'microsoft store': 'xbox',
           'emulation': 'emulation', **_ADDON_ALIASES}
INSTALLABLE = frozenset({'steam', 'epic', 'gog', 'amazon', 'xbox'}) | _ADDON_INSTALLABLE


def inspect_destination(value):
    """A non-writing space/permission preflight; installation size may be unknown."""
    result = {'ready': False, 'free_bytes': None, 'message': 'Choose a writable absolute installation folder.'}
    if not isinstance(value, str) or not value or '\x00' in value or not Path(value).is_absolute():
        return result
    try:
        target = Path(value).resolve()
        existing = target
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        if not existing.is_dir() or not os.access(existing, os.W_OK | os.X_OK):
            return result
        space = shutil.disk_usage(existing)
        if space.free <= 0:
            return dict(result, free_bytes=space.free, message='The selected drive is full.')
        return {'ready': True, 'free_bytes': space.free, 'path': str(target), 'filesystem_device': existing.stat().st_dev,
                'message': f'{space.free / 2**30:.1f} GiB free. The provider checks the required download size before installation.'}
    except (OSError, ValueError, RuntimeError):
        return result


def valid_product(provider, value):
    if not isinstance(value, str) or not value or len(value) > 256:
        return False
    if provider in ('steam', 'gog'):
        return bool(re.fullmatch(r'[1-9][0-9]{0,19}', value)) and (provider != 'steam' or int(value) <= 4294967295)
    if provider=='xbox':return bool(re.fullmatch('[A-Z0-9]{12}',value))
    if provider in _ADDON_INSTALLABLE and _validate_addon_product is not None:
        return _validate_addon_product(provider, value)
    return bool(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_. -]{0,127}', value)) and '..' not in value


def catalog_install_choices(catalog):
    """Exact source/provider_game_id joins, retaining game links across store twins."""
    rows = {}
    for game in (catalog or {}).get('games', []):
        for variant in game.get('variants', []):
            provider = ALIASES.get(str(variant.get('source', '')).casefold())
            product = variant.get('provider_game_id')
            if provider not in INSTALLABLE or not valid_product(provider, product):
                continue
            key = provider + ':' + product
            if key not in rows:
                rows[key] = {'key': key, 'provider': provider, 'product_id': product,
                             'title': str(game.get('title') or product)[:512],
                             'origin': 'Imported library record; account ownership will be checked by the provider.',
                             'game_ids': [], 'is_installed': None}
            if game.get('id') and game['id'] not in rows[key]['game_ids']:
                rows[key]['game_ids'].append(game['id'])
    return list(rows.values())


def merge_cached_choices(imported, cached):
    rows = {row['key']: dict(row, game_ids=list(row['game_ids'])) for row in imported}
    for item in cached:
        if item.provider not in INSTALLABLE or not valid_product(item.provider, item.product_id):
            continue
        key = item.provider + ':' + item.product_id
        old = rows.get(key, {})
        rows[key] = {'key': key, 'provider': item.provider, 'product_id': item.product_id,
                     'title': item.title[:512], 'game_ids': list(old.get('game_ids', [])),
                     'origin': 'Cached provider library; sign-in is checked when installing.',
                     'is_installed': item.is_installed}
    return sorted(rows.values(), key=lambda row: (row['title'].casefold(), row['key']))


class InstallPreferences:
    """One app-owned destination preference, never a launcher/auth config writer."""
    def __init__(self, path=None):
        self.path = Path(path) if path else None

    def load(self):
        if self.path:
            try:
                if self.path.stat().st_size > 8192:
                    raise ValueError('oversized preference')
                value = json.loads(self.path.read_text()).get('install_root')
                if isinstance(value, str) and Path(value).is_absolute() and '\x00' not in value:
                    return value
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        if os.path.ismount('/mnt/linux-data') and os.access('/mnt/linux-data', os.W_OK):
            return '/mnt/linux-data/Games/Waystone'
        return str(Path.home() / 'Games/Waystone')

    def save(self, root):
        if not self.path:
            return
        if not isinstance(root, str) or not Path(root).is_absolute() or '\x00' in root:
            raise ValueError('An absolute install root is required')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix='.install-settings-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'install_root': root}, stream)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name): os.unlink(name)
