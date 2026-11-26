"""Waystone-owned provider sessions in Linux Secret Service, never plaintext files.

Only the app's entries are accessed. Secrets use stdin/captured stdout, not argv,
environment, UI reports or exception text. A locked/missing store fails visibly;
there is deliberately no file/keyrings.alt fallback. Constructor is inert.
"""
import json
import shutil
import subprocess
from .steam_web import steam_id

class SessionStoreError(RuntimeError):
    pass

class ProviderSessionStore:
    def __init__(self, *, runner=None, executable=None, namespace='io.waystone.Library'):
        if not isinstance(namespace,str) or not namespace.startswith('io.waystone.') or len(namespace)>160 or not all(c.isalnum() or c in '.-' for c in namespace):
            raise ValueError('Invalid session store namespace')
        self._runner=runner or subprocess.run; self._executable=executable; self._namespace=namespace

    def _attributes(self, provider):
        if provider not in ('steam','xbox','itad','steamgriddb'): raise ValueError('Unsupported provider session')
        return ['application',self._namespace,'provider',provider]

    def _run(self, command, provider, payload=None):
        attributes=self._attributes(provider)
        executable=self._executable or shutil.which('secret-tool')
        if not executable: raise SessionStoreError('Desktop keyring is unavailable; sign-in could not be remembered.')
        argv=[executable,command]
        if command=='store': argv+=['--label=Waystone '+provider.title()+' sign-in']
        argv+=attributes
        try:
            result=self._runner(argv,input=payload,capture_output=True,text=True,timeout=6,check=False)
        except Exception:
            raise SessionStoreError('Desktop keyring is locked or unavailable; sign-in was not saved or forgotten.') from None
        if result.returncode==0:return result.stdout
        if command in ('lookup','clear') and result.returncode==1 and not result.stdout and not result.stderr:return None
        raise SessionStoreError('Desktop keyring refused the operation; unlock it and retry.') from None

    @staticmethod
    def _validated(provider, record):
        if type(record) is not dict:raise ValueError('Invalid saved session')
        kind=record.get('kind')
        if provider=='itad' and kind=='itad_api_key':
            if set(record)!={'kind','api_key'}:raise ValueError('Invalid ITAD credential fields')
            value=record['api_key']
            if type(value) is not str or not 8<=len(value)<=4096 or any(ord(c)<33 or ord(c)>126 for c in value):raise ValueError('Invalid ITAD API key')
        elif provider=='steamgriddb' and kind=='steamgriddb_api_key':
            if set(record)!={'kind','api_key'}:raise ValueError('Invalid SteamGridDB credential fields')
            value=record['api_key']
            if type(value) is not str or not 8<=len(value)<=4096 or any(ord(c)<33 or ord(c)>126 for c in value):raise ValueError('Invalid SteamGridDB API key')
        elif provider=='steam' and kind in ('steam_refresh','steam_web'):
            field='refresh_token' if kind=='steam_refresh' else 'api_key'
            if set(record)!={'kind','account_id',field}:raise ValueError('Invalid saved Steam session fields')
            steam_id(record['account_id'])
            value=record[field]
            if type(value) is not str or not 1<=len(value)<=16384 or any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('Invalid saved Steam credential')
        elif provider=='steam' and kind=='steam_native':
            if set(record)!={'kind'}:raise ValueError('Invalid native Steam preference')
        elif provider=='xbox' and kind=='xbox_helper':
            if set(record)!={'kind','account'} or type(record['account']) is not str or len(record['account'])>256 or any(ord(c)<32 or ord(c)==127 for c in record['account']):raise ValueError('Invalid saved Xbox selector')
        else:raise ValueError('Invalid saved session kind')
        return json.loads(json.dumps(record))

    def save(self,provider,record):
        self._attributes(provider)
        record=self._validated(provider,record)
        payload=json.dumps({'version':1,'provider':provider,'session':record},separators=(',',':'))
        self._run('store',provider,payload)

    def load(self,provider):
        raw=self._run('lookup',provider)
        if raw is None:return None
        try:
            if not isinstance(raw,str) or len(raw)>32768:raise ValueError()
            data=json.loads(raw)
            if set(data)!={'version','provider','session'} or type(data['version']) is not int or data['version']!=1 or data['provider']!=provider:raise ValueError()
            return self._validated(provider,data['session'])
        except Exception:raise SessionStoreError('Saved sign-in is invalid; sign out and sign in again.') from None

    def clear(self,provider):self._run('clear',provider)
