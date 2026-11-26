"""Read Steam's existing Linux client through versioned public ISteam interfaces.

No SteamAPI_Init, game AppID, login/cookie reads, launches, chat or mutations.
FFI is isolated in a bounded child process so unsupported ABI cannot crash Qt.
"""
from pathlib import Path
import json,os,subprocess,sys

ROOT=Path(__file__).resolve().parents[1]

def default_root():return Path.home()/'.local/share/Steam'

class SteamNativeAdapter:
    def __init__(self,root=None,*,runner=None):
        self.root=Path(root) if root else default_root();self.runner=runner or subprocess.run
    def fetch_friends(self):
        env=dict(os.environ)
        env['PYTHONDONTWRITEBYTECODE']='1'
        for key in ('SteamAppId','SteamGameId','SteamOverlayGameId'):env.pop(key,None)
        env['PYTHONPATH']=str(ROOT)
        result=self.runner([sys.executable,'-m','native_services.steam_native','--friends',str(self.root)],
            capture_output=True,text=True,timeout=12,env=env,cwd=ROOT)
        if result.returncode or len(result.stdout.encode())>1048576:
            raise ValueError('Steam native reader unavailable')
        lines=[line[len('WAYSTONE_STEAM_JSON='):] for line in result.stdout.splitlines() if line.startswith('WAYSTONE_STEAM_JSON=')]
        if len(lines)!=1:raise ValueError('Steam reader response marker missing')
        value=json.loads(lines[0])
        if not isinstance(value,dict) or value.get('provider')!='steam':raise ValueError('Invalid Steam reader response')
        return value

def _read(root):
    import ctypes as C
    import struct
    if sys.platform!='linux' or struct.calcsize('P')!=8:raise ValueError('Linux64 Steam client required')
    # Check first: this adapter NEVER starts or signs in Steam itself.
    if subprocess.run(['pgrep','-u',str(os.getuid()),'-x','steam'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2).returncode:
        return {'provider':'steam','account_id':'','status':'unavailable','reason':'steam_not_running','friends':[]}
    library=Path(root)/'linux64/steamclient.so'
    if not library.is_file():raise ValueError('Steam client library not available')
    lib=C.CDLL(str(library.resolve()))
    lib.CreateInterface.argtypes=[C.c_char_p,C.POINTER(C.c_int)];lib.CreateInterface.restype=C.c_void_p
    client=lib.CreateInterface(b'SteamClient020',None)
    if not client:raise ValueError('SteamClient020 not supported')
    def call(obj,index,restype,args=(),values=()):
        if not obj:raise ValueError('Missing native Steam interface')
        table=C.cast(obj,C.POINTER(C.POINTER(C.c_void_p))).contents
        fn=C.CFUNCTYPE(restype,C.c_void_p,*args)(table[index])
        return fn(obj,*values)
    pipe=call(client,0,C.c_int)
    if not pipe:raise ValueError('Steam IPC pipe unavailable')
    user=0
    try:
        user=call(client,2,C.c_int,(C.c_int,),(pipe,))
        if not user:raise ValueError('No signed-in global Steam user')
        account=None
        for version in (b'SteamUser021',):
            account=call(client,5,C.c_void_p,(C.c_int,C.c_int,C.c_char_p),(user,pipe,version))
            if account:break
        if not account:raise ValueError('Supported Steam user interface unavailable')
        owner=call(account,2,C.c_uint64)
        if not 76561197960265728<=owner<76561202255233024:raise ValueError('No individual Steam account')
        live=bool(call(account,1,C.c_bool))
        friends=call(client,8,C.c_void_p,(C.c_int,C.c_int,C.c_char_p),(user,pipe,b'SteamFriends017'))
        if not friends:raise ValueError('SteamFriends017 unavailable')
        count=call(friends,3,C.c_int,(C.c_int,),(4,))
        if not 0<=count<=2000:raise ValueError('Friend list exceeds bounded view')
        rows=[]
        for index in range(count):
            ident=call(friends,4,C.c_uint64,(C.c_int,C.c_int),(index,4))
            if not 76561197960265728<=ident<76561202255233024:raise ValueError('Invalid Steam friend identity')
            name=call(friends,7,C.c_char_p,(C.c_uint64,),(ident,))
            state=call(friends,6,C.c_int,(C.c_uint64,),(ident,))
            labels={0:'Offline',1:'Online',2:'Busy',3:'Away',4:'Snooze',5:'Looking to trade',6:'Looking to play'}
            rows.append({'friend_id':str(ident),'display_name':(name or b'').decode('utf-8','replace')[:256] or str(ident),
                         'presence':labels.get(state,'Unknown') if live else 'Steam offline; presence unknown',
                         'online':state!=0 if live and state in labels else None})
        return {'provider':'steam','account_id':str(owner),'status':'live' if live else 'offline','friends':rows}
    finally:
        if user:call(client,4,None,(C.c_int,C.c_int),(pipe,user))
        call(client,1,C.c_bool,(C.c_int,),(pipe,))

if __name__=='__main__':
    try:
        if len(sys.argv)!=3 or sys.argv[1]!='--friends':raise ValueError('Explicit friends request required')
        value=_read(Path(sys.argv[2]))
    except Exception:
        value={'provider':'steam','account_id':'','status':'unavailable','reason':'steam_native_unavailable','friends':[]}
    print('WAYSTONE_STEAM_JSON='+json.dumps(value,ensure_ascii=False))
