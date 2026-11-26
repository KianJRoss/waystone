"""Read-only native launcher observations and NON-EXECUTING reviewed contracts.

No subprocess, shell, client activation, title matching or Windows backend bridge.
Installed means metadata plus payload directory presence, NOT runnable/integrity verified.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from .catalog_providers import _read, UnsupportedCatalog

try:
    from private_addons import DEFAULT_TARGET_HOST as _DEFAULT_TARGET_HOST
except ImportError:
    _DEFAULT_TARGET_HOST = None


@dataclass(frozen=True)
class Connection:
    provider: str
    product_id: str
    record_key: str
    variant_key: str
    state: str
    title: str
    evidence: tuple[str, ...]
    source_context: dict
    platform: str | None = None


@dataclass(frozen=True)
class LaunchIntent:
    provider: str
    variant_key: str
    verified_contract: bool
    argv: tuple[str, ...] = ()
    uri: str | None = None
    reason: str = ''
    dry_run: bool = True
    execution_supported: bool = False


def _key(parts):
    return sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _connection(provider, product_id, title, state, evidence, context):
    key = _key([provider, context['instance'], product_id])
    variant = _key([key, context['host'], context['installation_id']])
    return Connection(provider, product_id, key, variant, state, title,
                      tuple(evidence), context)


def steam_connections(snapshot):
    """Reuse adapters.steam.scan output, including its exact canonical keys."""
    result = []
    for row in snapshot['records']:
        installed = (row['installation_state'] == 'declared-installed'
                     and row['payload_presence'] == 'directory-present')
        context = dict(row)
        context['snapshot_context'] = {k: v for k, v in snapshot.items() if k != 'records'}
        result.append(Connection('steam', row['external_id'], row['record_key'],
                                 row['variant_key'], 'installed' if installed else 'unverified',
                                 row['title'], (row['evidence']['locator'],), context))
    return tuple(result)


def _metadata(path, basename):
    if Path(path).name != basename:
        raise UnsupportedCatalog('Select the explicit supported metadata file')
    text, locator, digest = _read(path)
    return json.loads(text), locator, digest


def heroic_epic_connections(path, *, host, instance, installation_id):
    """Only Legendary installed.json; never Heroic accounts, config or auth stores."""
    data, locator, digest = _metadata(path, 'installed.json')
    if not isinstance(data, dict):
        raise UnsupportedCatalog('Unsupported Legendary installed schema')
    result = []
    for product_id, row in data.items():
        if not isinstance(row, dict) or row.get('app_name') != product_id:
            raise UnsupportedCatalog('Legendary identity mismatch')
        context = {k: row.get(k) for k in ('app_name', 'install_path', 'platform')}
        context.update(host=host, instance=instance, installation_id=installation_id,
                       metadata_sha256=digest, locator=locator)
        payload = row.get('install_path')
        present = isinstance(payload, str) and Path(payload).is_absolute() and Path(payload).is_dir()
        result.append(_connection('heroic-epic', product_id, row.get('title', product_id),
                                  'installed' if present else 'unverified', (locator,), context))
    return tuple(result)


def xodus_connections(path, *, host, instance, installation_id):
    data, locator, digest = _metadata(path, 'launcher-state.json')
    if not isinstance(data, dict) or not isinstance(data.get('games'), list):
        raise UnsupportedCatalog('Unsupported Xodus state schema')
    result = []
    for row in data['games']:
        if not isinstance(row, dict) or not re.fullmatch('[a-f0-9]{16}', str(row.get('id', ''))):
            raise UnsupportedCatalog('Invalid Xodus identity')
        # Deliberately exclude settings, downloads, launchTools and arbitrary commands.
        context = {k: row.get(k) for k in ('id', 'storeId', 'offerProductId', 'path', 'installState')}
        context.update(host=host, instance=instance, installation_id=installation_id,
                       metadata_sha256=digest, locator=locator)
        payload = row.get('path')
        present = isinstance(payload, str) and Path(payload).is_absolute() and Path(payload).is_dir()
        ready = present and row.get('installState') == 'ready'
        result.append(_connection('xodus', row['id'], row.get('title', row['id']),
                                  'installed' if ready else 'unverified', (locator,), context))
    return tuple(result)


def battlenet_client_connection(*, host, database_id, configpath, executable_present,
                                evidence, instance='lutris'):
    """Call with selected read-only Lutris DB/config observations, never full config."""
    if database_id != 2 or configpath != 'kian-battlenet':
        raise UnsupportedCatalog('Unreviewed Battle.net registration')
    return _connection('lutris-battlenet', '2', 'Battle.net',
                       'client-only' if executable_present else 'unverified', evidence,
                       dict(host=host, instance=instance, installation_id=configpath,
                            database_id=database_id, configpath=configpath))


def build_launch_intent(connection):
    """Independent pinned native recipes. NEVER executes; no metadata command use.

    Contract verification is source review, not game viability or authorization.
    Rechecking native client and payload paths at explicit future launch is required.
    """
    c = connection
    def disabled(reason):
        return LaunchIntent(c.provider, c.variant_key, False, reason=reason)
    host = c.source_context.get('host', c.source_context.get('snapshot_context', {}).get('host'))
    allowed_hosts = {'workstation', 'localhost'}
    if _DEFAULT_TARGET_HOST:
        allowed_hosts.add(_DEFAULT_TARGET_HOST)
    if host not in allowed_hosts:
        return disabled('Pinned contracts reviewed only for approved host')
    if not c.evidence or c.state not in ('installed', 'client-only'):
        return disabled('No installed/client evidence; launch disabled')
    if c.provider == 'steam' and re.fullmatch('[1-9][0-9]{0,15}', c.product_id):
        uri = 'steam://rungameid/' + c.product_id
        return LaunchIntent(c.provider, c.variant_key, True,
                            (str(Path.home() / '.local/bin/steam'), uri), uri)
    if c.provider == 'heroic-epic':
        # Epic must use the Waystone-owned Legendary planner in LaunchView. This
        # legacy connection adapter must never reopen Heroic as an implicit fallback.
        return disabled('Use Waystone direct Epic launch; Heroic fallback is disabled')
    if c.provider == 'xodus' and re.fullmatch('[a-f0-9]{16}', c.product_id):
        return LaunchIntent(c.provider, c.variant_key, True,
                            (str(Path.home() / '.local/bin/xodus-nexus'), '--launch-game', c.product_id))
    if (c.provider == 'lutris-battlenet' and c.product_id == '2'
            and c.source_context.get('configpath') == 'kian-battlenet'):
        uri = 'lutris:rungameid/2'
        return LaunchIntent(c.provider, c.variant_key, True, ('/usr/bin/lutris', uri), uri,
                            'Client only; no Battle.net game install inventory verified')
    return disabled('Unknown or invalid provider-specific identity; launch disabled')
