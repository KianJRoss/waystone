"""App-owned provider/account/product scoped progress cache. No auth material."""
from pathlib import Path
from contextlib import closing
import json,os,sqlite3,stat
APP_ID=1465077825
FIELDS={'game_key','provider','product_id','account_id','status','source','observed_at','items','total_count','unlocked_count','completion_percent','live_connection_verified','account_ownership_verified'}
class ProgressObservations:
    def __init__(self,path):self.path=Path(path) if path else None
    def _validate(self):
        p=self.path
        if p is None:raise ValueError('No app-owned progress cache configured')
        if p.is_symlink() or not p.is_file():raise ValueError('Progress cache is not a regular app file')
        s=p.stat()
        if not stat.S_ISREG(s.st_mode) or s.st_size>64*1024*1024 or s.st_mode&0o077:raise ValueError('Progress cache must be private and bounded')
    def put(self,value):
        if self.path is None:return
        if not isinstance(value,dict) or not set(value)<=FIELDS:raise ValueError('Unexpected progress fields')
        keys=tuple(value.get(k) for k in ('provider','account_id','game_key','product_id'))
        if any(type(k) is not str or not k or len(k)>512 for k in keys):raise ValueError('Exact progress identities required')
        raw=json.dumps(value,allow_nan=False)
        if len(raw.encode())>4*1024*1024:raise ValueError('Observation too large')
        if not self.path.exists():
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
            with closing(sqlite3.connect(self.path)) as db,db:
                db.execute(f'PRAGMA application_id={APP_ID}')
                db.execute('CREATE TABLE observations(provider TEXT,account_id TEXT,game_key TEXT,product_id TEXT,payload TEXT,PRIMARY KEY(provider,account_id,game_key,product_id))')
        self._validate()
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True)) as db,db:
            try: tag=db.execute('PRAGMA application_id').fetchone()[0]
            except sqlite3.DatabaseError:raise ValueError('Not a Waystone progress cache') from None
            if tag!=APP_ID:raise ValueError('Not a Waystone progress cache')
            db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)',(*keys,raw))
    def list(self):
        if self.path is None or not self.path.exists():return []
        self._validate()
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            try: tag=db.execute('PRAGMA application_id').fetchone()[0]
            except sqlite3.DatabaseError:raise ValueError('Not a Waystone progress cache') from None
            if tag!=APP_ID:raise ValueError('Not a Waystone progress cache')
            rows=db.execute('SELECT provider,account_id,game_key,product_id,payload FROM observations ORDER BY provider,account_id,game_key,product_id LIMIT 5001').fetchall()
            if len(rows)>5000:raise ValueError('Too many cached progress records')
            result=[]
            for row in rows:
                value=json.loads(row[4])
                if not isinstance(value,dict) or not set(value)<=FIELDS:raise ValueError('Invalid cached observations')
                if tuple(value.get(k) for k in ('provider','account_id','game_key','product_id'))!=row[:4]:raise ValueError('Cached identity mismatch')
                items=value.get('items')
                if not isinstance(items,list) or len(items)>20000:raise ValueError('Invalid cached items')
                if any(not isinstance(v,dict) or type(v.get('label')) is not str or (v.get('unlocked') is not None and type(v['unlocked']) is not bool) for v in items):raise ValueError('Malformed cached achievement')
                if any(value.get(k) is not None and (type(value[k]) is not int or not 0<=value[k]<=20000) for k in ('total_count','unlocked_count')):raise ValueError('Invalid cached counts')
                result.append(value)
            return result
