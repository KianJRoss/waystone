using Avalonia;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using UnifiedController.Linux.App.Models;

namespace UnifiedController.Linux.App.Controls;

public sealed class ControllerSurface : VisualSurfaceControl
{
    private sealed record Region(string Code, Rect Rect);
    private static readonly Region[] Regions =
    {
        new("Controller:LT",new(106,34,52,30)), new("Controller:RT",new(366,34,52,30)),
        new("Controller:LB",new(95,64,104,33)), new("Controller:RB",new(324,64,104,33)),
        new("Controller:Home",new(245,121,30,30)), new("Controller:View",new(216,149,24,24)),
        new("Controller:Share",new(249,170,22,22)), new("Controller:Menu",new(283,149,24,24)),
        new("Controller:Y",new(365,114,32,32)), new("Controller:X",new(333,144,32,32)),
        new("Controller:B",new(396,142,32,32)), new("Controller:A",new(364,172,32,32)),
        new("Controller:Left Stick",new(114,135,60,60)), new("Controller:Right Stick",new(292,201,60,60)),
        new("Controller:D-pad Up",new(190,196,24,24)), new("Controller:D-pad Left",new(170,216,24,24)),
        new("Controller:D-pad Right",new(210,216,24,24)), new("Controller:D-pad Down",new(190,236,24,24))
    };
    private readonly Dictionary<string, Bitmap> _layers = new(StringComparer.Ordinal);
    private static readonly IReadOnlyDictionary<string, string> Masks = new Dictionary<string, string>
    {
        ["Controller:LT"]="trigger_left", ["Controller:RT"]="trigger_right",
        ["Controller:LB"]="bumper_left", ["Controller:RB"]="bumper_right",
        ["Controller:Home"]="button_home", ["Controller:View"]="button_view",
        ["Controller:Share"]="button_share", ["Controller:Menu"]="button_menu",
        ["Controller:A"]="button_a", ["Controller:B"]="button_b",
        ["Controller:X"]="button_x", ["Controller:Y"]="button_y",
        ["Controller:Left Stick"]="stick_left", ["Controller:Right Stick"]="stick_right",
        ["Controller:D-pad Up"]="dpad_up", ["Controller:D-pad Down"]="dpad_down",
        ["Controller:D-pad Left"]="dpad_left", ["Controller:D-pad Right"]="dpad_right"
    };
    private bool _showMotionAxes;
    // One brush per mask for the control's lifetime: a new ImageBrush per frame leaked native Skia memory (~30 MB/s at 30 Hz).
    private readonly Dictionary<string, ImageBrush> _masks = new();
    protected override Size LogicalSize => new(520, 360);
    public bool ShowMotionAxes { get => _showMotionAxes; set { if (_showMotionAxes == value) return; _showMotionAxes = value; InvalidateVisual(); } }

    public ControllerSurface()
    {
        foreach (var name in new[] { "body", "grip_left", "grip_right", "trigger_left", "trigger_right", "bumper_left", "bumper_right", "stick_left", "stick_right", "dpad", "dpad_up", "dpad_down", "dpad_left", "dpad_right", "button_a", "button_b", "button_x", "button_y", "button_home", "button_share", "button_view", "button_menu" })
            _layers[name] = new Bitmap(AssetLoader.Open(new Uri($"avares://UnifiedController.Linux.App/Assets/controller-parts/{name}.png")));
        PointerMoved += (_, e) => { var p=ToLogical(e.GetPosition(this)); var motion=ShowMotionAxes?MotionHit(p):null; if(motion is not null){SetHoverCode("MotionAxis:"+motion);return;} SetHover(Hit(p)); };
        PointerExited += (_, _) => SetHover(null);
        PointerPressed += OnPressed;
    }
    private static Region? Hit(Point p) => Regions.LastOrDefault(r => r.Rect.Contains(p));
    private void SetHover(Region? r) { var code = r?.Code; if (Hovered == code) return; Hovered = code; InvalidateVisual(); }
    private void SetHoverCode(string? code) { if (Hovered == code) return; Hovered = code; InvalidateVisual(); }
    // Badges first, then rings in C# z-order (pitch over roll over yaw); a ring is hit within its 26px-wide band.
    private static string? MotionHit(Point p)
    {
        foreach (var spec in MotionAxes.Reverse()) if (BadgeRect(spec).Contains(p)) return spec.Axis;
        foreach (var spec in MotionAxes.Reverse())
        {
            double a = spec.Ring.Width / 2, b = spec.Ring.Height / 2, dx = (p.X - spec.Ring.Center.X) / a, dy = (p.Y - spec.Ring.Center.Y) / b;
            if (Math.Abs(Math.Sqrt(dx * dx + dy * dy) - 1) * Math.Min(a, b) <= 13) return spec.Axis;
        }
        return null;
    }
    private void OnPressed(object? sender, PointerPressedEventArgs e)
    {
        var point=ToLogical(e.GetPosition(this)); var motion=ShowMotionAxes?MotionHit(point):null;
        if(motion is not null){Invoke(motion,VisualInvocationKind.OpenMotionAxis);e.Handled=true;return;}
        var r = Hit(point); if (r is null) return;
        var props = e.GetCurrentPoint(this).Properties;
        var stick=r.Code.EndsWith(" Stick",StringComparison.Ordinal);
        if (stick && (props.PointerUpdateKind == PointerUpdateKind.RightButtonPressed || e.ClickCount >= 2))
            Invoke(r.Code.EndsWith("Left Stick", StringComparison.Ordinal) ? "Left" : "Right", VisualInvocationKind.OpenStickTuning);
        else Invoke(r.Code);
        e.Handled = true;
    }
    public override void Render(DrawingContext context)
    {
        base.Render(context); context.FillRectangle(Brush(Panel), new Rect(Bounds.Size));
        WithLogicalTransform(context, () =>
        {
            var art = new Rect(20, 25, 480, 311);
            foreach (var (name, opacity) in new[] { ("body",.45), ("grip_left",.6), ("grip_right",.6), ("trigger_left",.65), ("trigger_right",.65), ("bumper_left",.65), ("bumper_right",.65), ("stick_left",.75), ("stick_right",.75), ("dpad",.45), ("dpad_up",.75), ("dpad_down",.75), ("dpad_left",.75), ("dpad_right",.75), ("button_a",.75), ("button_b",.75), ("button_x",.75), ("button_y",.75), ("button_home",.75), ("button_share",.75), ("button_view",.75), ("button_menu",.75) })
            {
                using var layerOpacity = context.PushOpacity(opacity);
                var layer = _layers[name];
                context.DrawImage(layer, new Rect(layer.Size), art);
            }
            foreach (var r in Regions)
            {
                var color = ColorFor(r.Code);
                if (color != Key && Masks.TryGetValue(r.Code, out var maskName))
                {
                    if (!_masks.TryGetValue(maskName, out var mask)) _masks[maskName] = mask = new ImageBrush(_layers[maskName]) { Stretch = Stretch.Fill };
                    using (context.PushOpacityMask(mask, art)) context.FillRectangle(Brush(color, color == Live ? (byte)220 : (byte)190), art);
                }
            }
            DrawPressure(context, new Rect(106, 34, 52, 30), State?.Input.LeftTrigger ?? 0);
            DrawPressure(context, new Rect(366, 34, 52, 30), State?.Input.RightTrigger ?? 0);
            DrawStick(context, new Point(144,165), State?.Input.LeftX ?? 0, State?.Input.LeftY ?? 0, Color.Parse("#3979d7"));
            DrawStick(context, new Point(322,231), State?.Input.RightX ?? 0, State?.Input.RightY ?? 0, Color.Parse("#2f9d69"));
            DrawOutputDial(context, new Point(112,310), "L OUT", State?.Output.LeftX ?? 0, State?.Output.LeftY ?? 0, "Left");
            DrawOutputDial(context, new Point(408,310), "R OUT", State?.Output.RightX ?? 0, State?.Output.RightY ?? 0, "Right");
            if (ShowMotionAxes) DrawMotion(context);
        });
    }
    private static void DrawPressure(DrawingContext c, Rect r, byte pressure)
    {
        if (pressure == 0) return; var h = r.Height * pressure / 255d;
        c.FillRectangle(Brush(Live, 185), new Rect(r.X, r.Bottom - h, r.Width, h), 5);
    }
    private static void DrawStick(DrawingContext c, Point center, short x, short y, Color color)
    {
        var px = center.X + x / 32768d * 20; var py = center.Y - y / 32768d * 20;
        c.DrawEllipse(Brush(color), Pen(Text, 1), new Point(px, py), 4, 4);
    }
    private static void DrawOutputDial(DrawingContext c, Point center, string label, short x, short y, string side)
    {
        c.DrawEllipse(Brush(Color.Parse("#0e1622"), 225), Pen(Edge), center, 25, 25);
        c.DrawLine(Pen(Edge), new Point(center.X-22,center.Y), new Point(center.X+22,center.Y));
        c.DrawLine(Pen(Edge), new Point(center.X,center.Y-22), new Point(center.X,center.Y+22));
        var point = new Point(center.X + x / 32768d * 21, center.Y - y / 32768d * 21);
        c.DrawEllipse(Brush(Target), Pen(Text), point, 3.5, 3.5);
        Label(c, label, new Point(center.X,center.Y-32), 8, Muted);
        Label(c, $"{x/327.68:+0;-0;0}% {y/327.68:+0;-0;0}%", new Point(center.X,center.Y+33), 7, Muted);
        Label(c, "double-click to tune", new Point(center.X,center.Y+43), 6, Muted);
    }
    // Gyro axes overlay, matching the C# app's GyroAxesOverlay (MainWindow.xaml): three dashed ellipse rings on the
    // same 520x360 canvas plus pill badges; hovering an axis turns its ring solid and brightens its badge.
    private sealed record MotionAxisSpec(string Axis, Rect Ring, Point BadgePosition, Color Color);
    private static readonly MotionAxisSpec[] MotionAxes =
    [
        new("yaw", new Rect(60, 0, 400, 360), new Point(45, 330), Color.Parse("#59D3C0")),
        new("roll", new Rect(10, 105, 500, 150), new Point(0, 170), Color.Parse("#F5B45A")),
        new("pitch", new Rect(175, 0, 170, 360), new Point(232, 0), Color.Parse("#70B8FF")),
    ];
    private static readonly Dictionary<string, FormattedText> BadgeText = new();
    private static FormattedText Badge(MotionAxisSpec spec)
    {
        if (!BadgeText.TryGetValue(spec.Axis, out var text))
            BadgeText[spec.Axis] = text = new FormattedText(spec.Axis.ToUpperInvariant(), System.Globalization.CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight, new Typeface(FontFamily.Default, FontStyle.Normal, FontWeight.Bold), 9.5, Brush(spec.Color));
        return text;
    }
    private static Rect BadgeRect(MotionAxisSpec spec)
    {
        var text = Badge(spec);
        return new Rect(spec.BadgePosition, new Size(text.Width + 16, text.Height + 6)); // padding 7,2 plus the 1px border
    }
    private void DrawMotion(DrawingContext c)
    {
        foreach (var spec in MotionAxes)
        {
            var hovered = Hovered == "MotionAxis:" + spec.Axis;
            var ring = hovered
                ? new Avalonia.Media.Immutable.ImmutablePen(Color.FromArgb(230, spec.Color.R, spec.Color.G, spec.Color.B).ToUInt32(), 2.5)
                : new Avalonia.Media.Immutable.ImmutablePen(Color.FromArgb(56, spec.Color.R, spec.Color.G, spec.Color.B).ToUInt32(), 1.75,
                    new Avalonia.Media.Immutable.ImmutableDashStyle([8, 4], 0));
            c.DrawEllipse(null, ring, spec.Ring.Center, spec.Ring.Width / 2, spec.Ring.Height / 2);
        }
        foreach (var spec in MotionAxes)
        {
            var hovered = Hovered == "MotionAxis:" + spec.Axis;
            var rect = BadgeRect(spec);
            using (c.PushOpacity(hovered ? 1 : .5))
            {
                c.DrawRectangle(hovered ? Brush(Color.Parse("#1A2536"), 230) : Brush(Color.Parse("#121924"), 166), Pen(spec.Color), rect, 8, 8);
                var text = Badge(spec);
                c.DrawText(text, new Point(rect.Center.X - text.Width / 2, rect.Center.Y - text.Height / 2));
            }
        }
    }
}
