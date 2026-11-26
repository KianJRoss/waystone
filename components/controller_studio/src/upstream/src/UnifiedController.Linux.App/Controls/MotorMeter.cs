using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;

namespace UnifiedController.Linux.App.Controls;

public sealed class MotorMeter : Control
{
    private double _value;
    public double Value { get => _value; set { _value = Math.Clamp(value, 0, 255); InvalidateVisual(); } }
    public override void Render(DrawingContext c)
    {
        var box = new Rect(0, Math.Max(0, (Bounds.Height-8)/2), Bounds.Width, 8);
        c.DrawRectangle(new SolidColorBrush(Color.Parse("#2A3B51")), null, box, 4);
        c.DrawRectangle(new SolidColorBrush(Color.Parse("#4B91F1")), null, new Rect(box.X, box.Y, box.Width*_value/255, box.Height), 4);
    }
}
