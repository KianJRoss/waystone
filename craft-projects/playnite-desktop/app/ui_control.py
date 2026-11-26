"""Session-bus UI activation only; never starts an application, game or account."""
from PySide6.QtCore import QObject, Slot, ClassInfo
from PySide6.QtGui import QWindow
from PySide6.QtDBus import QDBusConnection, QDBusAbstractAdaptor

SERVICE='io.waystone.Library'
PATH='/io/waystone/Library'

@ClassInfo(**{'D-Bus Interface':'io.waystone.Library.UI'})
class UiControl(QDBusAbstractAdaptor):
    def __init__(self, window, parent=None):
        super().__init__(parent or window); self.window=window
    @Slot()
    def ToggleOverlay(self): self.window.togglePartyOverlay()
    @Slot()
    def ShowLibrary(self):
        self.window.showLibraryView()
        self.window.setProperty('organizerMode', False)
        if self.window.property('consoleMode'): self.window.toggleConsole()
        if self.window.visibility() in (QWindow.Hidden, QWindow.Minimized): self.window.showNormal()
        self.window.raise_(); self.window.requestActivate()
    @Slot()
    def ShowOrganizer(self):
        self.ShowLibrary()
        self.window.setProperty('organizerMode', True)
    @Slot()
    def ShowPlatforms(self):
        self.ShowLibrary()
        self.window.openFriendsPlatforms()
    @Slot()
    def ShowFriends(self):
        self.ShowLibrary()
        self.window.openFriends()
    @Slot()
    def ShowAchievements(self):
        self.ShowLibrary()
        self.window.openAchievements()
    @Slot()
    def ShowControllers(self):
        self.ShowLibrary()
        self.window.openControllers()
    @Slot()
    def ShowDownloads(self):
        self.ShowLibrary()
        self.window.openDownloads()
    @Slot()
    def ShowConsole(self):
        if not self.window.property('consoleMode'): self.window.toggleConsole()
        else: self.window.showFullScreen()
        self.window.raise_(); self.window.requestActivate()

def register_control(window,parent):
    bus=QDBusConnection.sessionBus()
    if not bus.isConnected() or not bus.registerService(SERVICE): return None
    host=QObject(parent)
    host.control=UiControl(window,host)
    if not bus.registerObject(PATH,host,QDBusConnection.ExportAdaptors):
        bus.unregisterService(SERVICE); return None
    return host
