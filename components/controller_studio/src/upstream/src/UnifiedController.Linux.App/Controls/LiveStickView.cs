using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;

namespace UnifiedController.Linux.App.Controls;

public sealed class LiveStickView : Control
{
    private double _x, _y;
    public void Update(double x, double y) { _x = Math.Clamp(x, -1, 1); _y = Math.Clamp(y, -1, 1); InvalidateVisual(); }
    public override void Render(DrawingContext c)
    {
        var radius = Math.Max(4, Math.Min(Bounds.Width, Bounds.Height) / 2 - 8); var center = Bounds.Center;
        var grid = new Pen(new SolidColorBrush(Color.Parse("#304158")), 1); var accent = new SolidColorBrush(Color.Parse("#4B91F1"));
        c.DrawEllipse(new SolidColorBrush(Color.Parse("#0E1622")), grid, center, radius, radius);
        c.DrawLine(grid, new(center.X-radius, center.Y), new(center.X+radius, center.Y)); c.DrawLine(grid, new(center.X, center.Y-radius), new(center.X, center.Y+radius));
        c.DrawEllipse(accent, new Pen(Brushes.White, 2), center + new Vector(_x*radius*.86, -_y*radius*.86), 6, 6);
    }
}
