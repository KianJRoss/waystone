using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using UnifiedController.App.Engine;

namespace UnifiedController.Linux.App.Controls;

/// <summary>Native numeric response curve preview (the native schema stores an exponent, not control points).</summary>
public sealed class CurveEditor : Control
{
    public double Curve { get; private set; } = 1;
    public void SetCurve(double curve) { Curve = Math.Clamp(curve, .5, 3); InvalidateVisual(); }
    public override void Render(DrawingContext context)
    {
        var box = new Rect(8, 8, Math.Max(1, Bounds.Width - 16), Math.Max(1, Bounds.Height - 16));
        var grid = new Pen(Brushes.DimGray, 1); var line = new Pen(Brushes.DeepSkyBlue, 2);
        context.DrawRectangle(null, grid, box);
        for (var i = 1; i < 4; i++) { var x = box.X + box.Width * i / 4; var y = box.Y + box.Height * i / 4; context.DrawLine(grid, new(x, box.Top), new(x, box.Bottom)); context.DrawLine(grid, new(box.Left, y), new(box.Right, y)); }
        var settings = new NativeStickSettings(true, 0, 1, 0, Curve); var last = box.BottomLeft;
        for (var i = 1; i <= 64; i++) { var x = i / 64d; var y = NativeProfileRuntime.ProcessStick((short)(x * 32767), 0, settings).X / 32767d; var next = new Point(box.Left + x * box.Width, box.Bottom - y * box.Height); context.DrawLine(line, last, next); last = next; }
    }
}
