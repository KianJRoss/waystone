using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;

namespace UnifiedController.Linux.App.Controls;

/// <summary>Compact pseudo-3D controller attitude view driven by the live pitch, roll and yaw estimate.</summary>
public sealed class OrientationView : Control
{
    private double _pitch, _roll, _yaw;
    public void Update(double pitch, double roll, double yaw) { _pitch = pitch; _roll = roll; _yaw = yaw; InvalidateVisual(); }
    public override void Render(DrawingContext c)
    {
        var center = Bounds.Center; var width = Math.Max(40, Bounds.Width * .62); var height = Math.Max(24, Bounds.Height * .34);
        var roll = _roll * Math.PI / 180; var pitchScale = .45 + .55 * Math.Abs(Math.Cos(_pitch * Math.PI / 180));
        Point Rotate(double x, double y) => new(center.X + x * Math.Cos(roll) - y * pitchScale * Math.Sin(roll), center.Y + x * Math.Sin(roll) + y * pitchScale * Math.Cos(roll));
        var points = new[] { Rotate(-width/2,-height/2), Rotate(width/2,-height/2), Rotate(width/2,height/2), Rotate(-width/2,height/2) };
        var body = new StreamGeometry(); using (var g = body.Open()) { g.BeginFigure(points[0], true); g.LineTo(points[1]); g.LineTo(points[2]); g.LineTo(points[3]); g.EndFigure(true); }
        c.DrawGeometry(new SolidColorBrush(Color.Parse("#172538")), new Pen(new SolidColorBrush(Color.Parse("#4B91F1")), 2), body);
        c.DrawLine(new Pen(new SolidColorBrush(Color.Parse("#91A0B4")), 1), Rotate(-width*.35,0), Rotate(width*.35,0));
        c.DrawEllipse(Brushes.White, null, Rotate(-width*.27,0), 5, 5); c.DrawEllipse(Brushes.White, null, Rotate(width*.27,0), 5, 5);
        var heading = (_yaw - 90) * Math.PI / 180; var ring = Math.Min(Bounds.Width, Bounds.Height) * .42; var mark = center + new Vector(Math.Cos(heading)*ring, Math.Sin(heading)*ring);
        c.DrawEllipse(new SolidColorBrush(Color.Parse("#3CCB7F")), null, mark, 4, 4);
    }
}
