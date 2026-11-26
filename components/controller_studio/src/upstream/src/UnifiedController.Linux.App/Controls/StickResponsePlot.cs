using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using UnifiedController.App.Engine;
namespace UnifiedController.Linux.App.Controls;
public sealed class StickResponsePlot : Control
{
    private NativeStickSettings _settings = NativeStickSettings.Disabled;
    private short _x, _y;
    public void UpdateSettings(bool enabled, double inner, double outer, double anti, double curve)
    { _settings = new(enabled, inner, outer, anti, curve); InvalidateVisual(); }
    public void UpdatePosition(short x, short y) { _x=x; _y=y; InvalidateVisual(); }
    public override void Render(DrawingContext c)
    {
        base.Render(c);
        var edge = new Pen(Brushes.SlateGray,1);
        var radius = Math.Max(1, Math.Min(Bounds.Height/2-18, Bounds.Width*.22));
        var center = new Point(radius+12,Bounds.Height/2);
        c.DrawEllipse(null,edge,center,radius,radius);
        c.DrawEllipse(null,new Pen(Brushes.Orange,1),center,radius*_settings.InnerDeadzone,radius*_settings.InnerDeadzone);
        c.DrawLine(edge,new Point(center.X-radius,center.Y),new Point(center.X+radius,center.Y));
        c.DrawLine(edge,new Point(center.X,center.Y-radius),new Point(center.X,center.Y+radius));
        c.DrawEllipse(Brushes.DodgerBlue,null,new Point(center.X+_x/32768d*radius,center.Y-_y/32768d*radius),4,4);
        var rect = new Rect(Bounds.Width*.53,18,Math.Max(1,Bounds.Width*.43),Math.Max(1,Bounds.Height-40));
        c.DrawLine(edge,rect.BottomLeft,rect.TopLeft); c.DrawLine(edge,rect.BottomLeft,rect.BottomRight);
        var last = rect.BottomLeft;
        for(var i=1;i<=64;i++)
        {
            var input=i/64d;
            var output=NativeProfileRuntime.ProcessStick((short)Math.Round(input*32767),0,_settings).X/32767d;
            var point=new Point(rect.X+input*rect.Width,rect.Bottom-output*rect.Height);
            c.DrawLine(new Pen(Brushes.DodgerBlue,2),last,point);last=point;
        }
    }
}
