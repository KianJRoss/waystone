"""Run pytest with Python IP sockets/DNS denied. Not child-process confinement.

Usage: python tools/run_offline_tests.py [pytest arguments]
Qt local Unix IPC remains available. A caught network attempt still fails this run.
Only fixed operation labels are printed, never destinations, URLs or payloads.
"""
import json
import socket
import sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
attempts=[]
def denied(label):
    def reject(*args,**kwargs):
        attempts.append(label)
        raise OSError('Waystone offline test: IP networking denied')
    return reject

def ip_operation(method):
    original=getattr(socket.socket,method)
    def guarded(sock,*args,**kwargs):
        if sock.family in (socket.AF_INET,socket.AF_INET6): return denied(method)()
        return original(sock,*args,**kwargs)
    return guarded

if __name__=='__main__':
    import pytest
    from contextlib import ExitStack
    with ExitStack() as stack:
        for method in ('connect','connect_ex','bind','sendto','sendmsg'):
            stack.enter_context(patch.object(socket.socket,method,ip_operation(method)))
        stack.enter_context(patch.object(socket,'getaddrinfo',denied('DNS')))
        code=pytest.main(sys.argv[1:])
    print(json.dumps({'python_network_attempts':len(attempts),'operations':sorted(set(attempts)),
                      'child_process_confinement':False}))
    sys.exit(code or (2 if attempts else 0))
