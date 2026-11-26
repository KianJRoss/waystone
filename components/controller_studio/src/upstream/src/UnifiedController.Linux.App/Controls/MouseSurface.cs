using Avalonia;
using Avalonia.Input;
using Avalonia.Media;

namespace UnifiedController.Linux.App.Controls;

/// <summary>The current Windows mapper's 170 x 300 segmented vector mouse.</summary>
public sealed class MouseSurface : VisualSurfaceControl
{
    private sealed record Part(string Name, string Label, Rect Bounds, double Radius, CornerRadius Corners);
    private static readonly Part[] Parts =
    {
        new("Left", "L", new(25,24,57,82), 0, new CornerRadius(12,12,4,4)),
        new("Right", "R", new(88,24,57,82), 0, new CornerRadius(12,12,4,4)),
        new("Wheel Up", "^", new(73,28,24,22), 4, default),
        new("Middle", "M", new(72,54,26,26), 4, default),
        new("Wheel Down", "v", new(73,83,24,22), 4, default),
        new("X2", "X2", new(10,120,22,32), 0, new CornerRadius(4,0,0,4)),
        new("X1", "X1", new(10,156,22,32), 0, new CornerRadius(4,0,0,4)),
        new("Movement", "AIM\nMOVE", new(38,146,94,100), 28, default)
    };

    protected override Size LogicalSize => new(170, 300);

    public MouseSurface()
    {
        PointerMoved += (_, e) => SetHover(Hit(ToLogical(e.GetPosition(this)))?.Name);
        PointerExited += (_, _) => SetHover(null);
        PointerPressed += (_, e) =>
        {
            var part = Hit(ToLogical(e.GetPosition(this)));
            if (part is null) return;
            Invoke("Mouse:" + part.Name);
            e.Handled = true;
        };
    }

    private static Part? Hit(Point point) => Parts.LastOrDefault(part => part.Bounds.Contains(point));
    private void SetHover(string? name)
    {
        var code = name is null ? null : "Mouse:" + name;
        if (Hovered == code) return;
        Hovered = code;
        InvalidateVisual();
    }

    public override void Render(DrawingContext context)
    {
        base.Render(context);
        context.FillRectangle(Brush(Panel), new Rect(Bounds.Size));
        WithLogicalTransform(context, () =>
        {
            var shell = new Rect(15, 15, 140, 270);
            context.FillRectangle(Brush(Color.Parse("#16202c")), shell, 52);
            context.DrawRectangle(Pen(Color.Parse("#3d5068"), 2), shell, 52);
            foreach (var part in Parts)
            {
                var code = "Mouse:" + part.Name;
                var fill = ColorFor(code);
                DrawPart(context, part, fill, Hovered == code ? 2 : 1.5);
                if (part.Name == "Movement")
                {
                    Label(context,"AIM / MOVE",new Point(part.Bounds.Center.X,part.Bounds.Center.Y-6),9.5,Color.Parse("#60a5fa"));
                    Label(context,"Trackpad",new Point(part.Bounds.Center.X,part.Bounds.Center.Y+10),8,Muted);
                }
                else Label(context,part.Label,part.Bounds.Center,part.Name.StartsWith("Wheel")?8:10);

            }
        });
    }

    private static void DrawPart(DrawingContext context, Part part, Color fill, double stroke)
    {
        var corners = part.Radius > 0 ? new CornerRadius(part.Radius) : part.Corners;
        context.DrawRectangle(Brush(fill), Pen(Edge, stroke), new RoundedRect(part.Bounds, corners));
    }

    private static string Short(string value)
    {
        var colon = value.IndexOf(':');
        var text = colon >= 0 ? value[(colon + 1)..] : value;
        return text.Length <= 12 ? text : text[..11] + "…";
    }
}
