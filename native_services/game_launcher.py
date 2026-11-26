"""User-action execution boundary for native game clients; never runs during scans."""
import os
import platform
import subprocess
from pathlib import Path
from .launcher_connections import build_launch_intent


class LaunchError(ValueError):
    pass


def launch_game(connection, *, current_host=None, spawn=None):
    """Revalidate an exact selected observation and hand off to its native client.

    Must be called by an explicit Play action, never inventory refresh. A returned
    PID means client handoff only, not proof that a game reached its main menu.
    Metadata command strings are never executed.
    """
    host = current_host if current_host is not None else platform.node()
    expected = connection.source_context.get('host', connection.source_context.get('snapshot_context', {}).get('host'))
    if host != expected:
        raise LaunchError('This installation belongs to another device')
    intent = build_launch_intent(connection)
    if not intent.verified_contract or not intent.argv:
        raise LaunchError('No supported launch profile for this installation')
    if connection.state != 'installed':
        raise LaunchError('Select an installed game, not a client-only entry')
    context = connection.source_context
    field = {'steam': 'payload_path', 'heroic-epic': 'install_path', 'xodus': 'path'}.get(connection.provider)
    payload = context.get(field) if field else None
    if not isinstance(payload, str) or not Path(payload).is_absolute() or not Path(payload).is_dir():
        raise LaunchError('Game installation directory is missing; refresh this device')
    executable = Path(intent.argv[0])
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise LaunchError('The configured native launcher is unavailable')
    try:
        process = (spawn or subprocess.Popen)(list(intent.argv), shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError as exc:
        raise LaunchError('The native launcher could not be started') from exc
    return {'status': 'handed_off', 'pid': process.pid,
            'variant_key': connection.variant_key, 'provider': connection.provider}
