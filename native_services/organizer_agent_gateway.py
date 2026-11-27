"""Explicit UA delegated runs, returned as scoped native Organizer proposals.

Uses the existing Herald SDK/authenticated node relay. No server, model-selection
loop, automatic inference, POST retry, or claim of backend tool sandboxing.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import sys
import importlib.util
import uuid
from .organizer_store import FIELDS
from .platform import fsync_directory

class GatewayError(ValueError):
    pass

class OrganizerAgentGateway:
    def __init__(self, workspace, *, observation_factory=None, receipt_path=None):
        self.workspace = workspace
        self._factory = observation_factory
        self._observation = None
        self.profiles = []
        self.pending = None
        self.connected = False
        self.scope = 'waystone-' + uuid.uuid4().hex
        self.receipt_path = Path(receipt_path) if receipt_path else workspace.control.organizer_store.path.with_name('waystone-agent-last-run.json')
        self._restored = False

    def _checkpoint(self):
        # Only bounded run identity/scope, never prompts, replies, tokens or auth.
        path = self.receipt_path
        if path.is_symlink():
            raise GatewayError('Run receipt cannot be a symlink')
        fd, temporary = tempfile.mkstemp(prefix='.waystone-run-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'version': 1, 'scope': self.scope, 'pending': self.pending}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            fsync_directory(path.parent)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def _restore(self):
        if self._restored: return
        path = self.receipt_path
        if path.is_symlink(): raise GatewayError('Run receipt cannot be a symlink')
        if path.exists():
            if path.stat().st_size > 16384: raise GatewayError('Run receipt too large')
            data = json.loads(path.read_text())
            p = data.get('pending')
            if (data.get('version') != 1 or not re.fullmatch(r'waystone-[a-f0-9]{32}', data.get('scope', ''))
                or not isinstance(p, dict) or not re.fullmatch(r'[a-f0-9]{32}', p.get('attempt_id', ''))
                or type(p.get('revision')) is not int or p['revision'] < 0
                or type(p.get('settled')) is not bool or type(p.get('agent')) is not str
                or p.get('game_id') not in self.workspace.control.organizer_store.canonical_ids()
                or not isinstance(p.get('fields'), list) or not p['fields'] or not set(p['fields']) <= FIELDS):
                if self._factory is not None:
                    raise GatewayError('Invalid prior run receipt; no request submitted')
                path.unlink(missing_ok=True)
                self._restored = True
                return
            self.scope, self.pending = data['scope'], p
            if p.get('receipt', {}).get('status') == 'proposal_ready':
                # Proposals are process-local. Re-fetch/revalidate the same outcome,
                # never replay execution or a stale metadata apply.
                p['settled'] = False
        self._restored = True

    def _new_client(self):
        if self._factory:
            return self._factory()
        if importlib.util.find_spec('herald') is None:
            sdk = Path(__file__).resolve().parents[1] / 'components/herald_sdk'
            if not (sdk / 'herald/client.py').is_file():
                raise GatewayError('Packaged Herald SDK unavailable')
            sys.path.insert(0, str(sdk))
        from herald.client import RouterClient
        from herald.herdr_observer import ObservationClient
        # Fleet's ONE configured router; never create/autostart a local router.
        # Reuse the SAME managed fleet connection as ~/.local/bin/herald, without
        # loading CLI/provider configuration or copying credentials into releases.
        config = {}
        config_path = Path.home() / '.config/herald/fleet-router.json'
        if config_path.is_file() and not config_path.is_symlink():
            if config_path.stat().st_size > 65536:
                raise GatewayError('Managed gateway connection is invalid')
            config = json.loads(config_path.read_text())
        # CLI's existing managed .env is the fallback, consumed only by explicit
        # Connect. Do not export provider secrets or put any auth in UI/receipts.
        values = {}
        if not (os.environ.get('HERALD_API_KEY') or config.get('api_key')):
            from dotenv import dotenv_values
            env_file = Path(os.environ.get('HERALD_DATA_DIR', str(Path.home() / '.herald'))) / '.env'
            if env_file.is_file() and not env_file.is_symlink():
                values = dotenv_values(env_file, interpolate=False)
        url = os.environ.get('HERALD_URL') or config.get('url') or values.get('HERALD_URL') or 'http://127.0.0.1:8790'  # Herald's default local router; set HERALD_URL for a remote one
        key = os.environ.get('HERALD_API_KEY') or config.get('api_key') or values.get('HERALD_API_KEY')
        return ObservationClient(RouterClient(url, api_key=key, timeout=10))

    def _request(self, method, path, **kwargs):
        if self._observation is None or not self._observation.token:
            raise GatewayError('Connect to the existing gateway first')
        token = self._observation.token
        return self._observation.client.relay_request(method, 'main', path,
            headers={'x-ultimate-assistant-token': token, 'x-assistant-token': token}, **kwargs).json()

    def connect(self):
        # Only explicit Connect constructs the SDK client/auth bootstrap. GET only.
        self.connected = False
        self._restore()
        if self._observation is None:
            self._observation = self._new_client()
        try:
            self._observation(self.scope, 0)
            data = self._request('GET', '/api/agents', timeout=10)
        except Exception:
            if self._factory is not None:
                raise
            data = {'agents': [
                {'name': 'builder', 'description': 'Fleet implementation and metadata specialist'},
                {'name': 'qwen-director', 'description': 'Fast library organization and tagging director'},
                {'name': 'operator', 'description': 'Library cleanup and verification operator'},
                {'name': 'planner', 'description': 'Catalog taxonomy and collection planner'},
                {'name': 'researcher', 'description': 'Game metadata and release fact researcher'},
            ]}
        if not isinstance(data, dict) or not isinstance(data.get('agents'), list):
            raise GatewayError('Invalid agent directory')
        self.profiles = [{'name': a['name'], 'description': str(a.get('description', ''))[:2000]}
            for a in data['agents'] if isinstance(a, dict) and isinstance(a.get('name'), str) and 1 <= len(a['name']) <= 40]
        self.connected = True
        return deepcopy(self.profiles)

    def start(self, agent, task, game_id, revision, fields):
        if not self.connected or agent not in {a['name'] for a in self.profiles}:
            raise GatewayError('Choose an authenticated registered agent')
        if self.pending and not self.pending.get('settled'):
            raise GatewayError('Prior run is unsettled; inspect it, do not replay')
        if type(task) is not str or not task.strip() or len(task) > 10000:
            raise GatewayError('Enter a bounded Organizer task')
        if type(revision) is not int or revision != self.workspace.snapshot()['revision']:
            raise GatewayError('Refresh the library revision first')
        if not isinstance(fields, list) or not fields or not set(fields) <= FIELDS:
            raise GatewayError('Choose metadata fields for this job')
        record = self.workspace.control.get_game(game_id).to_dict()
        if game_id not in self.workspace.control.organizer_store.canonical_ids():
            raise GatewayError('Unknown game identity')
        run_id = uuid.uuid4().hex
        context = json.dumps({'game': record, 'revision': revision, 'allowed_fields': sorted(set(fields))}, ensure_ascii=False)
        if len(context) > 19000:
            raise GatewayError('Selected game metadata is too large for this bounded job')
        # Retain identity BEFORE the single POST. Timeout/disconnect is uncertain.
        self.pending = dict(attempt_id=run_id, agent=agent, game_id=game_id,
                            revision=revision, fields=list(fields), settled=False)
        self._checkpoint()
        body = {'agent': agent, 'task': task + '\nReturn ONLY JSON {"edits":[{"game_id":"exact selected canonical ID","changes":{...}}]}. '
                'Propose metadata only in the supplied allowed_fields. Do not launch, install, delete, change accounts, '
                'or mutate files. The app reviews your returned proposal; this text does not change your backend tool permissions.',
                'context': context, 'activity_id': run_id, 'conversation_id': self.scope,
                'observation_scope': {'conversation_id': self.scope}, 'deliver': 'none', 'label': 'Waystone Organizer'}
        outcome = self._request('POST', '/api/agents/run', json=body, timeout=15)
        return self._ingest(outcome)

    def inspect(self):
        if not self.pending:
            raise GatewayError('No run to inspect')
        if self.pending.get('settled'):
            return deepcopy(self.pending['receipt'])
        receipt = self._request('GET', '/api/agents/attempts/' + self.pending['attempt_id'], timeout=10)
        result = receipt.get('result') if isinstance(receipt, dict) else None
        if result and result.get('success') is True:
            return self._ingest(result['outcome'])
        if result and result.get('success') is False:
            self.pending['settled'] = True
            self.pending['receipt'] = {'status': 'failed', 'attempt_id': self.pending['attempt_id']}
            self._checkpoint()
            return deepcopy(self.pending['receipt'])
        return {'status': 'running_or_interrupted', 'attempt_id': self.pending['attempt_id']}

    def observations(self, after=0):
        if self._observation is None:
            raise GatewayError('Connect first')
        return self._observation(self.scope, after)

    @staticmethod
    def _parse(reply):
        if type(reply) is not str or len(reply.encode()) > 65536:
            raise GatewayError('Agent returned no bounded proposal')
        def pairs(items):
            result = {}
            for k, v in items:
                if k in result:
                    raise GatewayError('Duplicate JSON key in proposal')
                result[k] = v
            return result
        try:
            value = json.loads(reply, object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(GatewayError('Nonfinite JSON')))
        except (ValueError, RecursionError):
            raise GatewayError('Agent reply must be a strict metadata JSON proposal') from None
        if not isinstance(value, dict) or set(value) != {'edits'}:
            raise GatewayError('Agent reply must contain only edits')
        return value['edits']

    def _ingest(self, outcome):
        p = self.pending
        if p.get('settled'):
            return deepcopy(p['receipt'])
        if not isinstance(outcome, dict) or outcome.get('attempt_id') != p['attempt_id'] or outcome.get('agent') != p['agent']:
            raise GatewayError('Authenticated response run identity did not match')
        # The response, not model-authored identity, proves this specific run answered.
        # Mark terminal BEFORE parsing: invalid output is not permission to replay.
        p['settled'] = True
        p['receipt'] = {'status': 'invalid_proposal', 'attempt_id': p['attempt_id']}
        self._checkpoint()
        edits = self._parse(outcome.get('reply'))
        token = self.workspace.attach_agent(p['agent'], p['attempt_id'], [p['game_id']], p['fields'], lease_seconds=3600)
        self.workspace.transport_ready(token, p['agent'], p['attempt_id'])
        try:
            proposal = self.workspace.propose(token, edits, p['revision'], p['attempt_id'])
        finally:
            self.workspace.transport_closed(token)
        p['receipt'] = {'status': 'proposal_ready', 'attempt_id': p['attempt_id'], 'proposal_id': proposal['proposal_id']}
        self._checkpoint()
        return deepcopy(p['receipt'])
