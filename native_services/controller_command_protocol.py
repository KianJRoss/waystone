"""Additive controller commands using the existing private transport safety helpers.
No changes to the legacy client/allowlist; no construction I/O or automatic replay.
"""
import json, math, os, secrets, socket, time
from .unified_controller_control import (
    ControllerControlClient, ControllerControlError, _validate_socket_path,
    PROTOCOL_VERSION, MAX_MESSAGE_BYTES,
)
OPERATIONS = frozenset({'command_capabilities','motion_recenter','motion_calibrate','rumble_test','rumble_stop'})
PROFILE_OPERATIONS=frozenset({'profile_command_capabilities','profile_create','profile_copy','profile_rename','profile_delete','profile_import','profile_export','profile_set_field'})
DEVICE_OPERATIONS=frozenset({'device_command_capabilities','joycon_pair','joycon_unpair','device_capture'})
ALL_OPERATIONS=OPERATIONS|PROFILE_OPERATIONS|DEVICE_OPERATIONS
MUTATIONS = (ALL_OPERATIONS - {'profile_command_capabilities','profile_export','device_command_capabilities'}) - {'command_capabilities'}

def validate(operation, fields):
    if not isinstance(operation,str) or operation not in ALL_OPERATIONS:
        raise ControllerControlError('ERR_INVALID_OPERATION')
    expected = {'heavy','light','duration_ms'} if operation == 'rumble_test' else set()
    if operation in PROFILE_OPERATIONS and operation!='profile_command_capabilities':
        expected={'name','expected_revision'}
        if operation in {'profile_copy','profile_rename'}:expected.add('source')
        if operation=='profile_import':expected.add('document')
        if operation=='profile_set_field':expected.update({'field','value'})
    if operation=='joycon_pair':expected={'left_id','right_id'}
    if operation=='joycon_unpair':expected={'pair_id'}
    if operation=='device_capture':expected={'enabled'}
    if set(fields) != expected:
        raise ControllerControlError('ERR_INVALID_REQUEST')
    if operation in DEVICE_OPERATIONS:
        for key in ('left_id','right_id','pair_id'):
            if key in fields and (not isinstance(fields[key],str) or not fields[key].strip() or len(fields[key])>512 or any(ord(c)<32 or ord(c)==127 for c in fields[key])):
                raise ControllerControlError('ERR_INVALID_REQUEST')
        if operation=='joycon_pair' and fields['left_id']==fields['right_id']:raise ControllerControlError('ERR_INVALID_DEVICE_PAIR')
        if operation=='device_capture' and type(fields['enabled']) is not bool:raise ControllerControlError('ERR_INVALID_REQUEST')
    if operation in PROFILE_OPERATIONS and operation!='profile_command_capabilities':
        import re
        for key in ('name','source'):
            if key in fields and (not isinstance(fields[key],str) or not fields[key].strip() or len(fields[key])>256 or any(ord(c)<32 or ord(c)==127 for c in fields[key])):
                raise ControllerControlError('ERR_INVALID_REQUEST')
        if not isinstance(fields['expected_revision'],str) or not re.fullmatch('[0-9a-f]{64}',fields['expected_revision']):
            raise ControllerControlError('ERR_INVALID_REQUEST')
        if operation=='profile_import' and not isinstance(fields['document'],dict):raise ControllerControlError('ERR_INVALID_REQUEST')
        if operation=='profile_set_field':
            field,value=fields['field'],fields['value']
            valid=False
            if field in ('rumble.enabled','controller_mouse.enabled'):valid=type(value) is bool
            elif field in ('rumble.left_intensity','rumble.right_intensity'):valid=type(value) in (int,float) and 0<=value<=100 and math.isfinite(value)
            elif field=='source_merge_policy':valid=type(value) is str and value in ('first_active','all_connected')
            elif field=='mouse_sensitivity':valid=type(value) is int and 300<=value<=3000
            if not valid:raise ControllerControlError('ERR_INVALID_SETTING')
    if operation == 'rumble_test':
        for key in ('heavy','light'):
            value=fields[key]
            if type(value) not in (int,float) or not 0 <= value <= 1 or not math.isfinite(value):
                raise ControllerControlError('ERR_INVALID_REQUEST')
        if type(fields['duration_ms']) is not int or not 1 <= fields['duration_ms'] <= 2000:
            raise ControllerControlError('ERR_INVALID_REQUEST')

class ControllerCommandClient(ControllerControlClient):
    def request(self, operation, **fields):
        validate(operation,fields)
        request_id=secrets.token_hex(16)
        payload=dict(version=PROTOCOL_VERSION,request_id=request_id,operation=operation,**fields)
        try:raw=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()+b'\n'
        except (TypeError,ValueError,UnicodeError) as exc:raise ControllerControlError('ERR_INVALID_REQUEST') from exc
        if len(raw)>MAX_MESSAGE_BYTES:raise ControllerControlError('ERR_INVALID_REQUEST')
        _validate_socket_path(self.socket_path)
        mutation=operation in MUTATIONS
        deadline=time.monotonic()+self._timeout
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            self._set_remaining_timeout(conn,deadline,is_mutation=mutation,phase='connect')
            try:conn.connect(os.fspath(self.socket_path))
            except OSError as exc:raise ControllerControlError('ERR_NOT_AVAILABLE') from exc
            try:
                self._set_remaining_timeout(conn,deadline,is_mutation=mutation,phase='send')
                conn.sendall(raw);conn.shutdown(socket.SHUT_WR)
            except OSError as exc:
                raise ControllerControlError('ERR_SEND_FAILED',uncertain=mutation) from exc
            reply=self._read_response(conn,is_mutation=mutation,deadline=deadline)
        return self._parse_response(reply,request_id=request_id,is_mutation=mutation)

class ControllerProfileCommandClient(ControllerCommandClient):
    def request(self,operation,**fields):
        if operation=='profiles':return ControllerControlClient.request(self,operation,**fields)
        return super().request(operation,**fields)
