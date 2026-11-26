"""Async editable Organizer over existing native data; no implicit agent/model start."""
import json,uuid
from copy import deepcopy
from PySide6.QtCore import QObject,Property,Signal,Slot,QThread
from native_services.organizer_store import Conflict
from native_services.organizer_control import ConcurrencyConflict
from native_services.organizer_agent_workspace import WorkspaceError

try:
    from private_addons import (
        DEDUPLICATE_BUTTON_LABEL as _DEDUPLICATE_BUTTON_LABEL,
        EXTRA_PC_SOURCES as _EXTRA_PC_SOURCES,
        SECONDARY_LIBRARY_NAME as _SECONDARY_LIBRARY_NAME,
    )
except ImportError:
    _DEDUPLICATE_BUTTON_LABEL = 'Merge duplicates (across stores)'
    _EXTRA_PC_SOURCES = set()
    _SECONDARY_LIBRARY_NAME = ''

class _Work(QThread):
    def __init__(self,fn,parent):super().__init__(parent);self.fn=fn;self.result=None
    def run(self):
        try:self.result={'ok':True,'value':self.fn()}
        except (Conflict,ConcurrencyConflict):self.result={'ok':False,'conflict':True}
        except (ValueError,TypeError):self.result={'ok':False,'invalid':True}
        except Exception:self.result={'ok':False}

class OrganizerWorkspaceView(QObject):
    changed=Signal();selectedChanged=Signal();metadataCommitted=Signal();operationFinished=Signal(bool)
    def __init__(self,workspace=None,parent=None):
        super().__init__(parent);self.workspace=workspace;self._job=None;self._closed=False
        self._query='';self._selected_id='';self._selected={};self._rows=[];self._revision=0
        self._sessions=[];self._proposals=[];self._activity=[];self._audit=[];self._latest=None
        self._pending=None;self._pending_refresh=False;self._op='';self._uncertain=False
        self._status='Organizer ready to load.' if workspace else 'Select a library with native Organizer storage enabled.'
    @Property(bool,notify=changed)
    def available(self):return self.workspace is not None
    @Property(str,constant=True)
    def deduplicateButtonLabel(self):return str(_DEDUPLICATE_BUTTON_LABEL)
    @Property(bool,notify=changed)
    def busy(self):return self._job is not None
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property(bool,notify=changed)
    def uncertain(self):return self._uncertain
    @Property(int,notify=changed)
    def revision(self):return self._revision
    @Property('QVariantList',notify=changed)
    def rows(self):return deepcopy(self._rows)
    @Property('QVariantMap',notify=selectedChanged)
    def selectedGame(self):return deepcopy(self._selected)
    @Property(str,notify=selectedChanged)
    def selectedGameId(self):return self._selected_id
    @Property('QVariantList',notify=changed)
    def agents(self):return deepcopy(self._sessions)
    @Property('QVariantList',notify=changed)
    def proposals(self):return deepcopy(self._proposals)
    @Property('QVariantList',notify=changed)
    def activity(self):return deepcopy(self._activity)
    @Property('QVariantList',notify=changed)
    def audit(self):return deepcopy(self._audit)
    @Property(bool,notify=changed)
    def canUndo(self):return self._latest is not None and not self._uncertain and not self.busy
    def _read(self,selected=None):
        snapshot=self.workspace.snapshot()
        rows=[r.to_dict() for r in self.workspace.control.list_games(limit=250,query=self._query or None) if not r.is_duplicate][:100]
        key=selected or self._selected_id or (rows[0]['canonical_id'] if rows else '')
        record=self.workspace.control.get_game(key).to_dict() if key else {}
        audit=self.workspace.control.audit_log('organizer',20)
        return {'revision':snapshot['revision'],'rows':rows,'selected_id':key,'selected':record,
                'agents':self.workspace.sessions(),'proposals':self.workspace.proposals(),
                'activity':self.workspace.activity(),'audit':audit}
    def _start(self,op,fn):
        if self._closed or self.busy or not self.available:return False
        self._op=op;self._job=_Work(fn,self);self._job.finished.connect(self._finished);self._job.start();self.changed.emit();return True
    @Slot()
    def refresh(self):self._start('read',lambda:self._read())
    @Slot(str)
    def search(self,text):
        if len(text)>256:return
        self._query=text
        if self.busy:self._pending_refresh=True
        else:self.refresh()
    @Slot(str)
    def selectGame(self,key):
        if self.busy:self._pending=key;return
        self._start('read',lambda:self._read(key))
    @Slot(str)
    def saveEdits(self,raw):
        if not self.available or not self._selected_id or self.busy or self._uncertain:return
        try:
            if len(raw.encode())>65536:raise ValueError()
            changes=json.loads(raw)
            if not isinstance(changes,dict) or not changes:raise ValueError()
        except Exception:self._status='Make valid metadata changes before saving. Draft retained.';self.changed.emit();return
        key,revision,request=self._selected_id,self._revision,uuid.uuid4().hex
        self._start('write',lambda:self.workspace.edit_metadata([{'game_id':key,'changes':changes}],revision,request))
    @Slot()
    def deduplicateLibrary(self):
        if not self.available or self.busy or self._uncertain:return
        def run_dedup():
            import re, sqlite3
            ctrl=self.workspace.control
            lstore=ctrl.catalog_link_store
            if lstore is None:
                return {'merged':0,'message':'Catalog link store is not enabled for this session.'}
            PC_LIBS={'steam','epic','gog','amazon','xbox','battlenet','ea','ubisoft'} | {x.casefold() for x in _EXTRA_PC_SOURCES}
            def pbucket(r):
                p=(r.platform or '').casefold()
                libs={x.casefold() for x in r.libraries}
                if 'pc (windows)' in p or 'pc (linux)' in p or (not p and (libs & PC_LIBS)):return 'pc'
                if not p:return 'unknown'
                return p.split('/')[0].strip()
            def norm_title(raw):
                s=re.sub(r"['’]",'',str(raw or '').casefold())
                s=re.sub(r'\s+v\d[\w.()-]*','',s)
                s=re.sub(r'\s*\((?:pcvr|vr|\d{4})\)','',s)
                s=re.sub(r'[:\-\s]*(?:\((?:ultimate|complete|definitive|deluxe|game of the year|goty|enhanced|anniversary|collectors|standard)\s+edition\)|(?:ultimate|complete|definitive|deluxe|game of the year|goty|enhanced|anniversary|collectors|standard)\s+edition)\b','',s)
                s=re.sub(r'\s+vr$','',s)
                s=re.sub(r'^the\s+','',s)
                return ' '.join(re.findall(r'[a-z0-9]+',s))
            games=ctrl.list_games(limit=10000)
            by_t={}
            for g in games:
                t=norm_title(g.title)
                if t:by_t.setdefault(t,[]).append(g)
            before=lstore.resolution()
            after=dict(before)
            existing_reps=set(before.values())
            merged=0
            for t,all_rows in sorted(by_t.items()):
                if len(all_rows)<2:continue
                buckets={}
                for r in all_rows:buckets.setdefault(pbucket(r),[]).append(r)
                if 'unknown' in buckets:
                    if 'pc' in buckets:
                        buckets['pc'].extend(buckets.pop('unknown'))
                    elif len(buckets)==2:
                        unk=buckets.pop('unknown');buckets[next(iter(buckets))].extend(unk)
                for rows in buckets.values():
                    if len(rows)<2:continue
                    def score(r):
                        noisy=bool(re.search(r'\bv\d{4,}',r.title or '',re.I)) or str(r.title or '').strip().endswith('-')
                        primary=(_SECONDARY_LIBRARY_NAME not in r.libraries if _SECONDARY_LIBRARY_NAME else True) and bool(r.libraries)
                        return (primary,not noisy,r.canonical_id in existing_reps,bool(r.has_overrides),bool(r.platform),bool(r.description))
                    best=sorted(rows,key=lambda r:(score(r),-rows.index(r)),reverse=True)[0]
                    rep_id=best.canonical_id
                    if rep_id in after and after[rep_id] in {r.canonical_id for r in rows}:rep_id=after[rep_id]
                    after.pop(rep_id,None)
                    changed=False
                    for r in rows:
                        if r.canonical_id!=rep_id and after.get(r.canonical_id)!=rep_id:
                            after[r.canonical_id]=rep_id;changed=True
                    mids={r.canonical_id for r in rows if r.canonical_id!=rep_id}
                    for k,v in list(after.items()):
                        if v in mids:
                            if k==rep_id:after.pop(k,None)
                            else:after[k]=rep_id
                            changed=True
                    if changed:merged+=1
            if after!=before and set(after.keys()).isdisjoint(set(after.values())):
                bjson=json.dumps(before,sort_keys=True,ensure_ascii=False,separators=(',',':'))
                ajson=json.dumps(after,sort_keys=True,ensure_ascii=False,separators=(',',':'))
                intent=json.dumps({'kind':'apply','source':'organizer-ui-dedup','groups':merged},sort_keys=True,separators=(',',':'))
                with sqlite3.connect(lstore.path) as db,db:
                    rev=db.execute('SELECT revision FROM link_meta').fetchone()[0]
                    db.execute('DELETE FROM links')
                    db.executemany('INSERT INTO links(member_id,representative_id) VALUES(?,?)',sorted(after.items()))
                    db.execute('UPDATE link_meta SET revision=?',(rev+1,))
                    db.execute("INSERT INTO audit(request_id,intent,kind,target,revision,before_state,after_state) VALUES(?,?,'apply',NULL,?,?,?)",(f'ui-dedup-{uuid.uuid4().hex[:12]}',intent,rev+1,bjson,ajson))
            return {'merged':merged,'total_links':len(after)}
        self._start('dedup',run_dedup)
    @Slot()
    def undo(self):
        if self.canUndo:self._start('write',lambda:self.workspace.undo(self._latest,uuid.uuid4().hex))
    @Slot(str,int)
    def acceptProposal(self,proposal_id,revision):
        if not self._uncertain:self._start('write',lambda:self.workspace.accept(proposal_id,revision))
    @Slot(str)
    def rejectProposal(self,proposal_id):
        def reject():
            self.workspace.reject(proposal_id)
            return {'agents':self.workspace.sessions(),'proposals':self.workspace.proposals(),'activity':self.workspace.activity()}
        self._start('agents',reject)
    @Slot()
    def acknowledgeUncertain(self):
        if not self.busy:self._uncertain=False;self._status='Possible prior change acknowledged. Refresh audit before another edit.';self.changed.emit()
    @Slot()
    def refreshAgentActivity(self):
        def state():return {'agents':self.workspace.sessions(),'proposals':self.workspace.proposals(),'activity':self.workspace.activity()}
        self._start('agents',state)
    @Slot()
    def _finished(self):
        job=self._job
        if job is None:return
        self._job=None;result=job.result or {'ok':False};job.deleteLater()
        if self._closed:return
        if not result['ok']:
            if self._op=='write' and not result.get('conflict') and not result.get('invalid'):self._uncertain=True
            self._status='Another edit changed the revision. Draft retained; refresh before retrying.' if result.get('conflict') else 'Outcome uncertain; no retry sent. Inspect the audit.' if self._uncertain else 'Organizer operation rejected or unavailable. Draft retained.'
            self.changed.emit();self.operationFinished.emit(False)
        elif self._op=='dedup':
            info=result['value'];self._status=f"Merged {info['merged']} duplicate groups ({info['total_links']} total linked duplicate records).";self.metadataCommitted.emit();self.operationFinished.emit(True);self.changed.emit();self.refresh()
        elif self._op=='write':
            receipt=result['value'];self._revision=receipt['revision'];self._status='Saved to native Organizer; audit '+str(receipt['audit_id'])+'. Imported source unchanged.'
            self.metadataCommitted.emit();self.operationFinished.emit(True);self.changed.emit()
            self.refresh()
        else:
            value=result['value'];self._sessions=value['agents'];self._proposals=value['proposals'];self._activity=value['activity']
            if self._op=='read':
                self._revision=value['revision'];self._rows=value['rows'];self._selected_id=value['selected_id'];self._selected=value['selected'];self._audit=value['audit']
                self._latest=self._audit[0]['audit_id'] if self._audit and self._audit[0]['kind']=='apply' else None
                self._status='Editable native library · '+str(len(self._rows))+' matching records shown · revision '+str(self._revision)
                self.selectedChanged.emit()
            self.changed.emit();self.operationFinished.emit(True)
        if self._pending is not None and not self.busy:
            key=self._pending;self._pending=None;self.selectGame(key)
        elif self._pending_refresh and not self.busy:
            self._pending_refresh=False;self.refresh()
    def shutdown(self):
        self._closed=True
        if self._job:
            self._job.wait(10000)
            if self._job.isRunning():raise RuntimeError('Organizer operation still settling')
