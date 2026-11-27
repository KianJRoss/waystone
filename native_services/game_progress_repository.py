"""Detached read-only game progress projection; exact IDs, no auth/network/store writes.

Reuses existing source normalizers and PluginMetadata. AchievementStore is deliberately
not used: its key omits provider_product_id, so same-account variants would collapse.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from itertools import islice
from pathlib import Path
import json, math, os, stat, sqlite3, time
from .achievement_sources import normalize_imported, normalize_steam
from .achievements import VALID_PROVIDERS
from .plugin_metadata import PluginMetadata

MAX_DB_BYTES=256*1024*1024
MAX_ITEMS=200
TIME_FIELDS=('MainStoryClassic','MainExtraClassic','CompletionistClassic','SoloClassic','CoOpClassic','VsClassic')
HLTB_CLASSIC_SECONDS_SCHEMA='playnite-hltb-3.11.2-classic-seconds-v1'
HLTB_SCHEMA_PROVENANCE={
    'schema_id':HLTB_CLASSIC_SECONDS_SCHEMA,'provider_version':'3.11.2',
    'plugin_commit':'bea054caaafe345c3aad785711911261043d2964',
    'converter_commit':'3b2ef4bde1a17712c5711f51cb67d32966a963c8',
    'definition':'source/Models/HltbData.cs',
    'converter':'CommonPluginsShared/Converters/PlayTimeToStringConverterWithZero.cs',
    'units':'seconds','zero_semantics':'unavailable in ClassicFormat',
    'generation_equality_verified':False,'live_refresh_verified':False}

def _verified_classic_values(data):
    values=[];invalid=[]
    for field in TIME_FIELDS:
        if field not in data:continue
        raw=data[field]
        if type(raw) is int and 0<=raw<=100*366*86400:
            values.append({'field':field,'raw_value':raw,'value':raw if raw else None,
                           'units':'seconds','status':'available' if raw else 'unavailable'})
        else:
            invalid.append(field)
            # Original full raw JSON remains preserved in the native copied archive.
            bounded=raw if type(raw) in (str,int,bool,type(None)) else None
            if type(raw) is float and math.isfinite(raw) and abs(raw)<=10**12:bounded=raw
            if isinstance(bounded,str):bounded=_text(bounded,128)
            if type(bounded) is int and not -(10**12)<=bounded<=10**12:bounded=None
            values.append({'field':field,'raw_value':bounded,'value':None,'units':'seconds','status':'malformed'})
    return values,invalid

def _id(value):
    if type(value) is not str or not value or len(value)>512 or any(not c.isprintable() for c in value):
        raise ValueError('Exact bounded identity required')
    return value

def _text(value,limit=4096):
    return ''.join(c for c in value if c.isprintable())[:limit] if isinstance(value,str) else ''

def _scope(value):return tuple(_id(value[k]) for k in ('game_key','provider','product_id','account_id'))

def _freshness(value,now):
    if value is None:return {'observed_at':None,'freshness':'unknown'}
    try:
        if type(value) is not str:raise ValueError()
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError()
        parsed=parsed.astimezone(timezone.utc)
    except (ValueError,TypeError,OverflowError):return {'observed_at':None,'freshness':'invalid'}
    age=now-parsed
    state='future' if age < -timedelta(minutes=5) else 'stale' if age>timedelta(days=30) else 'recent'
    return {'observed_at':parsed.isoformat(),'freshness':state}

class GameProgressRepository:
    def __init__(self,plugin_db=None,*,bindings=(),observations=(),now=None,hltb_schema=None):
        # Deliberately no filesystem access or loading global account/store config here.
        self._plugin_db=Path(plugin_db).absolute() if plugin_db is not None else None
        self._bindings=deepcopy(tuple(bindings));self._observations=deepcopy(tuple(observations))
        if len(self._bindings)>50000 or len(self._observations)>50000:raise ValueError('Bounded observations required')
        if hltb_schema not in (None,HLTB_CLASSIC_SECONDS_SCHEMA):raise ValueError('Unsupported completion-time schema binding')
        self._hltb_schema=hltb_schema
        self._now=now
        if now is not None and (not isinstance(now,datetime) or now.tzinfo is None):raise ValueError('Aware UTC reference time required')
        self._scopes_by_game={};self._observations_by_scope={};self._times_by_game={}
        if any(not isinstance(o,dict) for o in self._observations):raise ValueError('Typed observations required')
        for binding in self._bindings:
            scope=_scope(binding)
            if scope[1] not in VALID_PROVIDERS:raise ValueError('Unsupported provider binding')
            self._scopes_by_game.setdefault(scope[0],set()).add(scope)
            if len(self._scopes_by_game[scope[0]])>64:raise ValueError('Too many sources for one game')
        for obs in self._observations:
            if obs.get('kind')=='completion_time':
                try:key=_id(obs.get('game_key'))
                except ValueError:continue
                self._times_by_game.setdefault(key,[]).append(obs)
                if len(self._times_by_game[key])>200:raise ValueError('Too many completion observations for one game')
            elif obs.get('kind') in ('steam_response','imported_achievements'):
                try:key=_scope(obs)
                except (ValueError,KeyError,TypeError):continue
                self._observations_by_scope.setdefault(key,[]).append(obs)

    def get_game(self,game_key):
        game_key=_id(game_key)
        now=(self._now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        result={'game_key':game_key,'achievements':[],'completion_times':[],'notices':[], 'live_sync':False}
        scopes=sorted(self._scopes_by_game.get(game_key,()))
        for scope in scopes:
            _,provider,product,account=scope
            row={'provider':provider,'product_id':product,'account_id':account,'source':'caller supplied cache',
                 'status':'unavailable','total_count':None,'unlocked_count':None,'completion_percent':None,
                 'observed_count':0,'observed_unlocked_count':0,'items':[], 'live_connection_verified':False,
                 'observed_at':None,'freshness':'unknown'}
            observations=[];seen=set()
            for obs in self._observations_by_scope.get(scope,()):
                if obs.get('kind') not in ('steam_response','imported_achievements'):continue
                try:
                    if _scope(obs)!=scope:continue
                    encoded=json.dumps(obs,sort_keys=True,allow_nan=False)
                except (ValueError,TypeError,KeyError):continue
                if encoded not in seen:observations.append(obs);seen.add(encoded)
            if len(observations)>1:
                row.update(status='ambiguous',snapshot_count=len(observations))
            elif observations:
                obs=observations[0]
                try:
                    if obs['kind']=='steam_response':
                        if provider!='steam':raise ValueError()
                        feed=normalize_steam(obs['payload'],game_id=game_key,app_id=product,account_id=account,schema=obs.get('schema'))
                    else:
                        records=obs['records']
                        if any('canonical_game_id' in r and r['canonical_game_id']!=game_key for r in records if isinstance(r,dict)):raise ValueError()
                        feed=normalize_imported(provider,records,game_id=game_key,product_id=product,account_id=account)
                    row.update(status=feed['status'],total_count=feed['total_count'],unlocked_count=feed['unlocked_count'],
                               completion_percent=feed['completion_percent'],source=_text(obs.get('source','caller supplied cache'),256),
                               observed_count=feed.get('observed_count',len(feed['achievements'])),
                               observed_unlocked_count=feed.get('observed_unlocked_count',sum(x['unlocked'] is True for x in feed['achievements'])),
                               items=[{'achievement_id':_text(x['achievement_id'],512),'label':_text(x['label'],512),
                                       'unlocked':x['unlocked'],'unlocked_at':_freshness(x.get('unlocked_at'),now)['observed_at']}
                                      for x in feed['achievements'][:MAX_ITEMS]],truncated=len(feed['achievements'])>MAX_ITEMS)
                    row.update(_freshness(obs.get('observed_at'),now))
                except (ValueError,TypeError,KeyError,AttributeError):row.update(status='malformed')
            result['achievements'].append(row)
        if not scopes:result['notices'].append('No verified provider/product/account binding. Imported history is not an account connection.')
        self._plugin_rows(result,game_key,now)
        self._typed_times(result,game_key,now)
        return deepcopy(result)

    def _plugin_rows(self,result,game_key,now):
        if self._plugin_db is None:
            result['notices'].append('No copied plugin metadata archive selected.');return
        fd=None;repo=None
        try:
            fd=os.open(self._plugin_db,os.O_RDONLY | getattr(os, "O_BINARY", 0)|getattr(os, "O_NOFOLLOW", 0)|getattr(os, "O_NONBLOCK", 0)|getattr(os, "O_CLOEXEC", 0))
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_DB_BYTES:raise ValueError()
            # Pin the explicitly selected inode; never follow a path from imported metadata.
            repo=PluginMetadata('/proc/self/fd/'+str(fd))
            deadline=time.monotonic()+2.5
            repo.db.set_progress_handler(lambda: int(time.monotonic()>deadline),10000)
            for dataset in ('SuccessStory','HowLongToBeat'):
                rows=list(islice(repo.records(canonical_id=game_key,dataset=dataset),2))
                if not rows:continue
                base={'source':dataset+' (preserved import)','provider':None,'product_id':None,'account_id':None,
                      'live_connection_verified':False,'observed_at':None,'freshness':'unknown'}
                if len(rows)>1:
                    base['status']='ambiguous'
                    (result['achievements'] if dataset=='SuccessStory' else result['completion_times']).append(base);continue
                doc=rows[0]['document'];items=doc.get('Items')
                base.update(_freshness(doc.get('DateLastRefresh'),now))
                if not isinstance(items,list):
                    base['status']='malformed'
                elif dataset=='SuccessStory':
                    base.update(status='empty' if not items else 'unbound_import',observed_count=len(items),items=[],
                                total_count=None,unlocked_count=None,completion_percent=None)
                elif not items:base.update(status='empty',values=[])
                else:
                    for item in items[:MAX_ITEMS]:
                        entry=deepcopy(base)
                        if not isinstance(item,dict) or not isinstance(item.get('GameHltbData'),dict):
                            entry.update(status='malformed',values=[])
                        else:
                            data=item['GameHltbData'];values=[]
                            verified=self._hltb_schema==HLTB_CLASSIC_SECONDS_SCHEMA and item.get('IsVndb') is False
                            if verified:
                                values,invalid=_verified_classic_values(data)
                                available=any(v['status']=='available' for v in values)
                                status='imported' if available else 'malformed' if invalid else 'unavailable'
                                entry['schema_provenance']={**HLTB_SCHEMA_PROVENANCE,'record_sha256':_text(rows[0].get('sha256'),64)}
                                entry['invalid_fields']=invalid
                            else:
                                status='imported_unverified_units'
                                for key in TIME_FIELDS:
                                    value=data.get(key)
                                    if type(value) in (int,float) and 0<=value<=10**12 and math.isfinite(value):
                                        values.append({'field':key,'value':value,'units':'unverified source units'})
                            entry.update(status='selection_required' if len(items)>1 else status,
                                         candidate_id=_text(item.get('Id'),512),values=values,selection_required=len(items)>1)
                        result['completion_times'].append(entry)
                    continue
                (result['achievements'] if dataset=='SuccessStory' else result['completion_times']).append(base)
        except (OSError,ValueError,TypeError,KeyError,AttributeError,json.JSONDecodeError) as exc:
            result['notices'].append('Selected copied plugin metadata is unavailable or invalid; no live fallback was attempted.')
        except sqlite3.Error:
            result['notices'].append('Selected copied plugin metadata schema is unavailable; no store was created.')
        finally:
            if repo is not None:repo.close()
            if fd is not None:os.close(fd)

    def _typed_times(self,result,game_key,now):
        observations=[];seen=set()
        for obs in self._times_by_game.get(game_key,()):
            try:key=json.dumps(obs,sort_keys=True,allow_nan=False)
            except (ValueError,TypeError):key=id(obs)
            if key not in seen:observations.append(obs);seen.add(key)
        for obs in observations:
            if obs.get('kind')!='completion_time' or obs.get('game_key')!=game_key:continue
            entry={'source':_text(obs.get('source','explicit observation'),256),'status':'malformed',
                   'live_connection_verified':False,'values':[],**_freshness(obs.get('observed_at'),now)}
            values=obs.get('values')
            if obs.get('schema')=='waystone-completion-seconds-v1' and isinstance(values,dict):
                valid=True
                for key,value in values.items():
                    if key not in ('main_story','main_extra','completionist') or (value is not None and
                        (type(value) not in (int,float) or not 0<=value<=100*366*86400 or not math.isfinite(value))):
                        valid=False;break
                    if value is not None:entry['values'].append({'field':key,'value':value,'units':'seconds'})
                if valid:
                    entry['status']='selection_required' if entry['values'] and len(observations)>1 else 'available' if entry['values'] else 'unavailable'
                    entry['selection_required']=bool(entry['values']) and len(observations)>1
                else:entry['values']=[]
            result['completion_times'].append(entry)
