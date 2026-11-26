"""Main-thread, event-assisted polling of committed shared library state.

Parents own timer/watcher lifetime; no observer thread, network or state writes.
SQLite subscribers can poll revisions even if WAL timestamps fail to reveal a commit.
"""
from pathlib import Path
from PySide6.QtCore import QObject,QTimer,QFileSystemWatcher,Property,Signal,Slot


class LiveLibraryUpdates(QObject):
    changed=Signal()
    def __init__(self,parent=None,interval_ms=750):
        super().__init__(parent)
        self._entries=[];self._error='';self._busy=False
        self._watcher=QFileSystemWatcher(self)
        self._timer=QTimer(self);self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.checkNow)
        self._watcher.fileChanged.connect(self.checkNow)
        self._watcher.directoryChanged.connect(self.checkNow)

    @Property(str,notify=changed)
    def error(self):return self._error

    @staticmethod
    def _stamp(path):
        result=[]
        for p in (path,Path(str(path)+'-wal')):
            try:
                st=p.stat();result.append((st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns))
            except FileNotFoundError:result.append(None)
        return tuple(result)

    def watch(self,path,callback,*,poll_revision=False):
        if path is None:return
        path=Path(path)
        self._entries.append({'path':path,'callback':callback,'revision':poll_revision,'stamp':self._stamp(path)})
        self._arm()

    def _arm(self):
        existing=set(self._watcher.files()+self._watcher.directories())
        for entry in self._entries:
            for path in (entry['path'],entry['path'].parent):
                if path.exists() and str(path) not in existing:
                    self._watcher.addPath(str(path));existing.add(str(path))

    def start(self):self._timer.start()
    @Slot()
    def stop(self):self._timer.stop()

    @Slot()
    def checkNow(self,*_):
        if self._busy:return
        self._busy=True;errors=[]
        try:
            for entry in self._entries:
                try:
                    stamp=self._stamp(entry['path'])
                    if entry['revision'] or stamp!=entry['stamp']:
                        if entry['callback']() is not False:entry['stamp']=stamp
                except Exception:
                    errors.append('A library update could not be loaded; the previous view was kept')
            self._arm()
            error='; '.join(set(errors))
            if error!=self._error:self._error=error;self.changed.emit()
        finally:self._busy=False
