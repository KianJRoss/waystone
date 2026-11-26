"""Render only the current explicit Steam sign-in challenge; no disk/cache/network."""
from pathlib import Path
import sys
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtGui import QImage,QColor,QPainter
class SteamQrImage(QQuickImageProvider):
    def __init__(self,view):super().__init__(QQuickImageProvider.Image);self.view=view
    def requestImage(self,identifier,size,requestedSize):
        image=QImage(360,360,QImage.Format_RGB32);image.fill(QColor('white'))
        value=self.view.qrUrl
        if value:
            dependency=Path(__file__).resolve().parents[3]/'components/waystone_dependencies'
            if str(dependency) not in sys.path:sys.path.insert(0,str(dependency))
            import qrcode
            qr=qrcode.QRCode(border=4);qr.add_data(value);qr.make(fit=True);matrix=qr.get_matrix()
            scale=max(1,360//len(matrix));offset=(360-len(matrix)*scale)//2;painter=QPainter(image)
            for y,row in enumerate(matrix):
                for x,black in enumerate(row):
                    if black:painter.fillRect(offset+x*scale,offset+y*scale,scale,scale,QColor('black'))
            painter.end()
        size.setWidth(image.width());size.setHeight(image.height());return image
