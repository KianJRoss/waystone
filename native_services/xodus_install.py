"""Native Xbox Store installs using the existing Xodus streaming backend.

Contracts independently implemented from Nexus install-plan/download-progress/store
and verified against the installed 0.2.14 CLI help. No GUI launcher, credential
copying, sign-in command, license export or game execution is involved.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path, PureWindowsPath
import re
import signal
import time
import xml.etree.ElementTree as ET
from native_services import file_locking as fcntl

from .provider_jobs import ProviderJobProgress
from .installation_library import inspect_destination

PRODUCT = re.compile(r'[A-Z0-9]{12}')
CONTENT = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}')
PHASES = {'initializing':'Preparing Xbox installation', 'metadata':'Reading Store package metadata',
          'downloading':'Downloading and extracting Xbox files', 'finalizing':'Finalizing Xbox installation',
          'complete':'Xbox download finished; checking installed files', 'failed':'Xbox installation failed'}


class XodusInstallError(ValueError): pass


def _id(value, pattern):
    if not isinstance(value,str) or not pattern.fullmatch(value):
        raise XodusInstallError('Invalid Xbox Store product or package identity.')
    return value


def _count(value, optional=False):
    return (optional and value is None) or (type(value) is int and 0 <= value <= 2**53-1)


def parse_packages(raw, product, market='US'):
    _id(product,PRODUCT)
    if not re.fullmatch('[A-Z]{2}',market):raise XodusInstallError('Invalid Store market.')
    if not isinstance(raw,str) or len(raw.encode())>256000:raise XodusInstallError('Store package response is too large.')
    try:data=json.loads(raw)
    except (ValueError,TypeError):raise XodusInstallError('Invalid Store package response.') from None
    if (not isinstance(data,dict) or type(data.get('schema')) is not int or data['schema']!=1
            or data.get('rootProductId')!=product or data.get('market')!=market
            or data.get('status') not in ('ready','choice','unavailable')
            or not isinstance(data.get('choices'),list) or len(data['choices'])>128):
        raise XodusInstallError('Store package response does not match the requested product.')
    choices=[];seen=set()
    for row in data['choices']:
        if not isinstance(row,dict):raise XodusInstallError('Invalid Store package choice.')
        item=_id(row.get('productId'),PRODUCT);content=_id(row.get('contentId'),CONTENT)
        if content in seen:raise XodusInstallError('Duplicate Store package choice.')
        seen.add(content)
        for field in ('title','platform','packageFormat','kind'):
            if not isinstance(row.get(field),str) or len(row[field])>500 or any(ord(c)<32 for c in row[field]):
                raise XodusInstallError('Invalid Store package fields.')
        if not row['title'] or type(row.get('supported')) is not bool:
            raise XodusInstallError('Invalid Store package support claim.')
        for field in ('downloadBytes','installBytes'):
            if not _count(row.get(field),optional=True) or row.get(field)==0:
                raise XodusInstallError('Invalid Store package size.')
        if row['supported'] and (row['platform']!='Windows.Desktop' or row['packageFormat']!='MSIXVC'
                or row['kind'] not in ('Game','GameAddOn','Durable')):
            raise XodusInstallError('Unsupported package marked supported.')
        choices.append({'product_id':item,'content_id':content,'title':row['title'],
            'supported':row['supported'],'kind':row['kind'],'platform':row['platform'],
            'download_bytes':row.get('downloadBytes'),'install_bytes':row.get('installBytes')})
    supported=[c for c in choices if c['supported']]
    expected='ready' if len(supported)==1 and supported[0]['kind']=='Game' else 'choice' if supported else 'unavailable'
    if data['status']!=expected:raise XodusInstallError('Inconsistent Store package status.')
    # DLC needs parent-package binding; never install it as a standalone base game.
    return [c for c in choices if c['supported'] and c['kind']=='Game']


@dataclass(frozen=True)
class XodusInstallPlan:
    binary: Path
    product_id: str
    selected_product_id: str
    content_id: str
    title: str
    root: Path
    destination: Path
    required_bytes: int | None
    market: str
    expires_at: float
    filesystem_device: int

    @property
    def argv(self):
        return (str(self.binary),'streaming',self.product_id,str(self.destination),
                '--market',self.market,'--selected-content-id',self.content_id,'--progress-json')


class XodusProgress:
    def __init__(self):self.sequence=-1;self.terminal=None
    def consume(self,line):
        if self.terminal or not line.startswith('NEXUS_PROGRESS ') or len(line)>4096:return None
        try:d=json.loads(line[len('NEXUS_PROGRESS '):])
        except ValueError:return None
        if not isinstance(d,dict):return None
        if (type(d.get('schema')) is not int or d['schema']!=1 or not _count(d.get('sequence'))
                or d['sequence']<=self.sequence or d.get('phase') not in PHASES
                or not _count(d.get('completedBytes')) or not _count(d.get('totalBytes'),optional=True)
                or not _count(d.get('bytesPerSecond')) or type(d.get('paused')) is not bool
                or type(d.get('canPause')) is not bool):return None
        total=d['totalBytes'];done=d['completedBytes']
        if total is not None and done>total:return None
        for key in ('networkBytes','reusedBytes'):
            if key in d and not _count(d[key]):return None
        self.sequence=d['sequence'];phase=d['phase']
        if phase in ('complete','failed'):self.terminal=phase
        percent=min(99.9,done/total*100) if total else None
        return ProviderJobProgress('running',percent,PHASES[phase])


def _case_path(root,relative):
    value=PureWindowsPath(relative)
    if value.is_absolute() or value.drive or not value.parts or '..' in value.parts:
        raise XodusInstallError('Package executable path leaves its installation.')
    current=root
    for part in value.parts:
        matches=[p for p in current.iterdir() if p.name.casefold()==part.casefold()]
        if len(matches)!=1:raise XodusInstallError('Package executable is absent or ambiguous.')
        current=matches[0]
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise XodusInstallError('Package executable leaves its installation.')
    return current


def verify_installation(plan):
    root=plan.destination
    if (root/'.nexus-update-incomplete-v1').exists() or (root/'.xodus-streaming-tmp.msixvc').exists():
        raise XodusInstallError('Xbox files are incomplete. Partial files were kept for resume.')
    container=root/'.xodus-streaming.msixvc'
    if not container.is_file() or container.stat().st_size<=0:
        raise XodusInstallError('The completed Xodus container is missing.')
    names={p.name.casefold():p for p in root.iterdir()}
    manifest=names.get('microsoftgame.config') or names.get('appxmanifest.xml')
    if manifest is None or manifest.stat().st_size>512000:
        raise XodusInstallError('A bounded game package manifest is required.')
    raw=manifest.read_bytes()
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise XodusInstallError('Unsafe package XML.')
    try:doc=ET.fromstring(raw)
    except ET.ParseError:raise XodusInstallError('Invalid package manifest.') from None
    executables=[];store_ids=[]
    for element in doc.iter():
        tag=element.tag.rsplit('}',1)[-1]
        if tag=='StoreId' and element.text:store_ids.append(element.text.strip())
        if tag=='Executable' and element.get('Name'):executables.append(element.get('Name'))
        if tag=='Application' and element.get('Executable'):executables.append(element.get('Executable'))
    if store_ids and any(s not in (plan.product_id,plan.selected_product_id) for s in store_ids):
        raise XodusInstallError('Installed package belongs to a different Store product.')
    if not executables:raise XodusInstallError('Package contains no declared game executable.')
    existing=[]
    for name in executables:
        try:existing.append(_case_path(root,name))
        except (OSError,XodusInstallError):pass
    if not existing:raise XodusInstallError('The package executable is missing. Files were retained.')
    return {'provider':'xbox','product_id':plan.product_id,'selected_product_id':plan.selected_product_id,
            'content_id':plan.content_id,'destination':str(root),'executable':str(existing[0]),
            'state':'files_verified','playability':'Requires the configured Xodus runtime; not tested by installation.'}


async def _stop(child):
    if child.returncode is not None:return
    for sig,timeout in ((signal.SIGINT,4),(signal.SIGTERM,2),(signal.SIGKILL,2)):
        try:os.killpg(child.pid,sig)
        except ProcessLookupError:break
        try:await asyncio.wait_for(child.wait(),timeout);return
        except asyncio.TimeoutError:pass
    await child.wait()


async def _process(argv, *, on_line=None, output_limit=256000, timeout=40):
    """Bounded child output, owned process group, no raw tool output in exceptions."""
    child=await asyncio.create_subprocess_exec(*argv,stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
    output=bytearray()
    async def read(stream,stdout):
        buffer=b'';count=0
        while block:=await stream.read(4096):
            count+=len(block)
            if not on_line and count>output_limit:raise XodusInstallError('Backend metadata exceeded its limit.')
            if stdout and not on_line:output.extend(block)
            if on_line and stdout:
                buffer+=block
                while b'\n' in buffer:
                    line,buffer=buffer.split(b'\n',1)
                    if len(line)<=4096:on_line(line.decode('utf8','replace'))
                if len(buffer)>8192:buffer=b''
        if on_line and stdout and buffer and len(buffer)<=4096:on_line(buffer.decode('utf8','replace'))
    readers=[asyncio.create_task(read(child.stdout,True)),asyncio.create_task(read(child.stderr,False))]
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(*readers)
            code=await child.wait()
        return code,output.decode('utf8')
    finally:
        await _stop(child)
        for task in readers:
            if not task.done():task.cancel()
        await asyncio.gather(*readers,return_exceptions=True)


async def public_product_titles(products,market):
    """Nexus DisplayCatalog contract: public titles only, no account credentials.

    Bounded optional enrichment. Unresolved IDs are retained rather than dropped
    or turned into invented titles/ownership. Called only by explicit refresh.
    """
    titles={}
    try:
        import httpx
        semaphore=asyncio.Semaphore(3)
        async with httpx.AsyncClient(timeout=5,follow_redirects=False,trust_env=False) as client:
            async def batch(ids):
                async with semaphore:
                    try:
                        params={'bigIds':','.join(ids),'market':market,'languages':'en-us,neutral'}
                        async with client.stream('GET','https://displaycatalog.mp.microsoft.com/v7.0/products',
                                params=params,headers={'Accept':'application/json'}) as response:
                            if response.status_code!=200:return
                            chunks=[];size=0
                            async for chunk in response.aiter_bytes():
                                size+=len(chunk)
                                if size>8_000_000:return
                                chunks.append(chunk)
                        data=json.loads(b''.join(chunks))
                        records=data.get('Products',[]) if isinstance(data,dict) else []
                        if not isinstance(records,list):return
                        for record in records[:100]:
                            if not isinstance(record,dict) or record.get('ProductId') not in ids:continue
                            localized=record.get('LocalizedProperties')
                            if not isinstance(localized,list):continue
                            for entry in localized[:20]:
                                title=entry.get('ProductTitle') if isinstance(entry,dict) else None
                                if isinstance(title,str) and 0<len(title)<=500 and not any(ord(c)<32 for c in title):
                                    titles[record['ProductId']]=title;break
                    except Exception:return
            tasks=[asyncio.create_task(batch(products[i:i+25])) for i in range(0,min(len(products),1000),25)]
            try:
                async with asyncio.timeout(20):await asyncio.gather(*tasks)
            except TimeoutError:pass
            finally:
                for task in tasks:
                    if not task.done():task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
    except (ImportError,OSError):pass
    return titles


class XodusInstaller:
    def __init__(self,binary,*,process=None,clock=None,busy_check=None,title_reader=None):
        self.binary=Path(binary);self.process=process or _process
        self.clock=clock or time.monotonic;self._active=None;self._cancelled_task=None
        self.busy_check=busy_check or self._external_busy
        self.title_reader=title_reader or public_product_titles;self._public_titles={}
    def _external_busy(self,destination):
        executable=self.binary.resolve()
        for process in Path('/proc').iterdir():
            if not process.name.isdecimal():continue
            try:
                if process.stat().st_uid!=os.getuid() or (process/'exe').resolve()!=executable:continue
                argv=(process/'cmdline').read_bytes().split(b'\0')
                command=argv[1].decode() if len(argv)>1 else ''
                if command in ('streaming','download','extract','login','logout'):return True
                if command=='run' and len(argv)>2 and Path(os.fsdecode(argv[2])).resolve()==destination.resolve():return True
            except (OSError,UnicodeError):continue
        return False
    def available(self):return self.binary.is_absolute() and self.binary.is_file() and os.access(self.binary,os.X_OK)
    async def library(self,market='US'):
        if not re.fullmatch('[A-Z]{2}',market) or not self.available():
            raise XodusInstallError('The configured Xodus backend or Store market is unavailable.')
        code,raw=await self.process((str(self.binary),'library','--json','--market',market),
            output_limit=8_000_000,timeout=60)
        if code:raise XodusInstallError('Xbox library could not be read. Check the existing Xodus account sign-in.')
        if not isinstance(raw,str) or len(raw.encode())>8_000_000:raise XodusInstallError('Xbox account library exceeded its limit.')
        try:
            line=next(line for line in reversed(raw.splitlines()) if line.lstrip().startswith('{'))
            data=json.loads(line)
        except (StopIteration,ValueError,TypeError):raise XodusInstallError('Invalid Xbox account library response.') from None
        if (not isinstance(data,dict) or type(data.get('schema')) is not int or data['schema']!=1
                or data.get('market')!=market or not isinstance(data.get('entitlements'),list)
                or len(data['entitlements'])>10000):raise XodusInstallError('Invalid Xbox account library scope.')
        rows=[];seen=set()
        for row in data['entitlements']:
            if not isinstance(row,dict) or not isinstance(row.get('productId'),str):continue
            product=row['productId']
            if not PRODUCT.fullmatch(product) or product in seen:continue
            seen.add(product)
            title=row.get('title')
            if not isinstance(title,str) or not title or len(title)>500 or any(ord(c)<32 for c in title):title=product
            rows.append({'product_id':product,'title':title})
        # No permanent-ownership claim: subscriptions/trials/sharing/expiry are
        # deliberately not collapsed into "owned". Streaming checks access.
        missing=[row['product_id'] for row in rows if row['title']==row['product_id'] and row['product_id'] not in self._public_titles]
        if missing:
            try:
                values=await self.title_reader(missing,market)
                if isinstance(values,dict):
                    for product,title in values.items():
                        if product in missing and isinstance(title,str) and 0<len(title)<=500 and not any(ord(c)<32 for c in title):
                            self._public_titles[product]=title
            except Exception:pass
        for row in rows:
            if row['title']==row['product_id']:row['title']=self._public_titles.get(row['product_id'],row['title'])
        return rows
    async def resolve(self,product,market='US'):
        _id(product,PRODUCT)
        if not re.fullmatch('[A-Z]{2}',market):raise XodusInstallError('Invalid Store market.')
        if not self.available():raise XodusInstallError('The configured Xodus backend is unavailable.')
        self._active=asyncio.current_task()
        try:
            code,raw=await self.process((str(self.binary),'resolve-package',product,'--json','--market',market))
            if code:raise XodusInstallError('Store packages could not be resolved. Nothing was downloaded.')
            return parse_packages(raw,product,market)
        finally:self._active=None
    def plan(self,product,choice,root,title='',market='US'):
        _id(product,PRODUCT);_id(choice.get('product_id'),PRODUCT);_id(choice.get('content_id'),CONTENT)
        if not choice.get('supported') or choice.get('kind')!='Game':raise XodusInstallError('Select a supported base-game package.')
        status=inspect_destination(root)
        required=choice.get('install_bytes') or choice.get('download_bytes')
        if not status['ready'] or (required and status['free_bytes']<required):
            raise XodusInstallError('The selected drive cannot hold this package.')
        directory=Path(status['path'])/product
        return XodusInstallPlan(self.binary,product,choice['product_id'],choice['content_id'],
            title or choice['title'],Path(status['path']),directory,required,market,self.clock()+60,status['filesystem_device'])
    async def execute(self,plan,progress):
        if plan.binary!=self.binary or self.clock()>plan.expires_at:raise XodusInstallError('Package review expired. Review the installation again.')
        _id(plan.product_id,PRODUCT);_id(plan.selected_product_id,PRODUCT);_id(plan.content_id,CONTENT)
        if (plan.destination!=plan.root/plan.product_id or not plan.root.is_absolute()
                or not re.fullmatch('[A-Z]{2}',plan.market) or not _count(plan.required_bytes,True)):
            raise XodusInstallError('Invalid Xbox installation plan.')
        if self._active is not None:raise XodusInstallError('An Xbox operation is already running.')
        if not self.available():raise XodusInstallError('The configured Xodus backend is unavailable.')
        if self.busy_check(plan.destination):raise XodusInstallError('Another Xodus transfer/sign-in or this game is active. Wait for it to finish.')
        storage=inspect_destination(str(plan.root))
        if not storage['ready'] or (plan.required_bytes and storage['free_bytes']<plan.required_bytes):
            raise XodusInstallError('The selected drive is no longer ready.')
        if storage.get('filesystem_device')!=plan.filesystem_device:
            raise XodusInstallError('The selected filesystem changed. Review the installation drive again.')
        self._active=asyncio.current_task();parser=XodusProgress()
        def receive(line):
            value=parser.consume(line)
            if value is not None:progress(value)
        try:
            plan.root.mkdir(parents=True,exist_ok=True)
            plan.destination.mkdir(exist_ok=True)
            if plan.destination.is_symlink() or plan.destination.resolve().parent!=plan.root.resolve():
                raise XodusInstallError('Destination is not the reviewed installation directory.')
            marker=plan.destination/'.waystone-xodus-install.json'
            identity={'provider':'xbox','product_id':plan.product_id,'content_id':plan.content_id}
            if marker.exists():
                try:
                    if marker.is_symlink() or marker.stat().st_size>8192 or json.loads(marker.read_text())!=identity:
                        raise ValueError()
                except (OSError,ValueError):raise XodusInstallError('Destination belongs to a different or invalid install attempt.') from None
            elif any(plan.destination.iterdir()):
                raise XodusInstallError('Destination is not empty and has no matching Waystone install record. Files were not changed.')
            else:
                fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w') as stream:json.dump(identity,stream);stream.flush();os.fsync(stream.fileno())
            lock=os.open(plan.destination/'.waystone-xodus.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            try:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise XodusInstallError('This destination already has an active installation.') from None
                code,_=await self.process(plan.argv,on_line=receive,timeout=24*60*60)
                if code or parser.terminal!='complete':
                    raise XodusInstallError('Xodus did not confirm completion. Partial files were kept for resume.')
                receipt=verify_installation(plan)
                receipt_path=plan.destination/'.waystone-install-receipt.json'
                fd=os.open(receipt_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w') as stream:json.dump(receipt,stream);stream.flush();os.fsync(stream.fileno())
                return receipt
            finally:os.close(lock)
        finally:self._active=None
    async def cancel(self):
        task=self._active
        if task is not None and not task.done():
            if self._cancelled_task is not task:
                self._cancelled_task=task;task.cancel()
            try:await task
            except asyncio.CancelledError:pass
