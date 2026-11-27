"""Pytest plugin that makes POSIX-only stdlib imports unavailable during collection.

Loaded only by the Linux CI compatibility-simulation step. We deliberately keep
the host OS identity unchanged: production platform branches accept explicit
system injection, while the blocked-module test catches accidental imports.
"""
import sys
import os
import socket
import builtins
from pathlib import Path
from types import ModuleType
import fcntl as _linux_fcntl
import pytest


POSIX_ONLY_MODULES = ('fcntl', 'pwd', 'grp', 'termios', 'resource')


def pytest_configure(config):
    for module_name in POSIX_ONLY_MODULES:
        sys.modules[module_name] = None
    # The MCP SDK's native stdio implementations use OS-specific pipes. Stub
    # only those transport modules so its platform-neutral server API can be
    # collected; the real transport check runs separately on the native runner.
    server_stdio = ModuleType('mcp.server.stdio')
    client_stdio = ModuleType('mcp.client.stdio')

    async def unsupported_stdio(*_args, **_kwargs):
        raise RuntimeError('native stdio transport is exercised on a native runner')

    server_stdio.stdio_server = unsupported_stdio
    client_stdio.stdio_client = unsupported_stdio
    client_stdio.StdioServerParameters = type('StdioServerParameters', (), {})
    sys.modules['mcp.server.stdio'] = server_stdio
    sys.modules['mcp.client.stdio'] = client_stdio

    # Route durable stores through the Windows byte-lock seam. This thread-level
    # stand-in validates callers on Linux; windows-latest runs production msvcrt.
    import native_services.file_locking as file_locking

    def simulated_windows_lock(fd, action):
        # Use the Linux kernel lock syscall only as the simulation backend. The
        # application still takes the Windows branch and imports no POSIX module.
        operation = {'lock': _linux_fcntl.LOCK_EX,
                     'try_lock': _linux_fcntl.LOCK_EX | _linux_fcntl.LOCK_NB,
                     'unlock': _linux_fcntl.LOCK_UN}[action]
        _linux_fcntl.flock(fd, operation)

    file_locking.is_windows = lambda _system=None: True
    file_locking._windows_lock = simulated_windows_lock


def pytest_collection_modifyitems(items):
    reason = 'native stdio is exercised outside the POSIX-import compatibility simulation'
    for item in items:
        if item.name == 'test_real_stdio_transport_exposes_actionable_stale_revision_conflict':
            item.add_marker(pytest.mark.skip(reason=reason))


class _MissingWindowsAPI:
    """Module proxy modelling stdlib APIs absent from regular Windows Python."""
    def __init__(self, wrapped, absent):
        self._wrapped = wrapped
        self._absent = absent

    def __getattr__(self, name):
        if name in self._absent:
            raise AttributeError(f"module '{self._wrapped.__name__}' has no attribute '{name}' (Windows simulation)")
        return getattr(self._wrapped, name)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Run app modules with known POSIX-only stdlib attributes hidden.

    This catches runtime-only portability bugs (which import checks miss) without
    deleting attributes from pytest's own process or mutating the host OS identity.
    """
    if item.get_closest_marker('windows_api_probe') is None:
        yield
        return
    app_roots = (Path(__file__).resolve().parents[1] / 'native_services',
                 Path(__file__).resolve().parents[1] / 'craft-projects/playnite-desktop/app')
    restorations = []
    original_import = builtins.__import__
    socket_proxy = _MissingWindowsAPI(socket, {'AF_UNIX'})
    def windows_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'socket':
            return socket_proxy
        return original_import(name, globals, locals, fromlist, level)
    builtins.__import__ = windows_import
    for module in tuple(sys.modules.values()):
        filename = getattr(module, '__file__', None)
        namespace = getattr(module, '__dict__', None)
        if not filename or namespace is None:
            continue
        try:
            path = Path(filename).resolve()
        except OSError:
            continue
        if not any(path.is_relative_to(root) for root in app_roots):
            continue
        for attr, wrapped, absent in (
            ('os', os, {'fchmod', 'getuid', 'O_CLOEXEC', 'O_DIRECTORY'}),
            ('socket', socket, {'AF_UNIX'}),
        ):
            if namespace.get(attr) is wrapped:
                restorations.append((namespace, attr, wrapped))
                namespace[attr] = _MissingWindowsAPI(wrapped, absent)
    try:
        yield
    finally:
        builtins.__import__ = original_import
        for namespace, attr, wrapped in restorations:
            namespace[attr] = wrapped
