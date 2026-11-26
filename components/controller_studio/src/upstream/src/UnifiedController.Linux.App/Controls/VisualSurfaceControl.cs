using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using UnifiedController.Linux.App.Models;

namespace UnifiedController.Linux.App.Controls;

public abstract class VisualSurfaceControl : Control
{
    protected static readonly Color Panel = Color.Parse("#171d27");
    protected static readonly Color Key = Color.Parse("#293241");
    protected static readonly Color Edge = Color.Parse("#526175");
    protected static readonly Color Text = Color.Parse("#edf3fb");
    protected static readonly Color Muted = Color.Parse("#9aa8ba");
    protected static readonly Color Mapped = Color.Parse("#2457a7");
    protected static readonly Color Source = Color.Parse("#e49b2f");
    protected static readonly Color Target = Color.Parse("#2f9d69");
    protected static readonly Color Live = Color.Parse("#d94141");
    protected static readonly Color Hover = Color.Parse("#53657b");
    protected VisualSurfaceState? State;
    protected string? Hovered;
    protected abstract Size LogicalSize { get; }
    public event Action<VisualControlInvocation>? ControlInvoked;

    // Called ~30 times a second; repaint only when something drawn changed (the controller art is 22 bitmap layers).
    public virtual void UpdateState(VisualSurfaceState state)
    {
        var changed = !SameAppearance(State, state);
        State = state;
        if (changed) InvalidateVisual();
    }
    private bool SameAppearance(VisualSurfaceState? a, VisualSurfaceState b) =>
        a is not null && a.SelectedSource == b.SelectedSource && a.SelectedDestination == b.SelectedDestination &&
        a.InteractionMode == b.InteractionMode && a.Input == b.Input && a.Output == b.Output &&
        a.ActiveDesktopControls.SetEquals(b.ActiveDesktopControls) &&
        a.ExtendedButtons.SetEquals(b.ExtendedButtons) && SameRoutes(a.Routes, b.Routes);
    private static bool SameRoutes(IReadOnlyDictionary<string, string> a, IReadOnlyDictionary<string, string> b) =>
        ReferenceEquals(a, b) || (a.Count == b.Count && a.All(pair => b.TryGetValue(pair.Key, out var value) && value == pair.Value));
    protected void Invoke(string code, VisualInvocationKind kind = VisualInvocationKind.SelectControl) =>
        ControlInvoked?.Invoke(new VisualControlInvocation(code, kind));
    protected (double Scale, Vector Offset) Transform()
    {
        var scale = Math.Min(Bounds.Width / LogicalSize.Width, Bounds.Height / LogicalSize.Height);
        if (!double.IsFinite(scale) || scale <= 0) scale = 1;
        return (scale, new Vector((Bounds.Width - LogicalSize.Width * scale) / 2,
            (Bounds.Height - LogicalSize.Height * scale) / 2));
    }
    protected Point ToLogical(Point p)
    {
        var (s, o) = Transform(); return new Point((p.X - o.X) / s, (p.Y - o.Y) / s);
    }
    protected void WithLogicalTransform(DrawingContext context, Action draw)
    {
        var (s, o) = Transform();
        using (context.PushTransform(Matrix.CreateScale(s, s) * Matrix.CreateTranslation(o.X, o.Y))) draw();
    }
    protected Color ColorFor(string code)
    {
        var color = BaseColorFor(code);
        return Hovered == code
            ? Color.FromRgb((byte)(color.R * .62), (byte)(color.G * .62), (byte)(color.B * .62))
            : color;
    }
    private Color BaseColorFor(string code)
    {
        if (IsLive(code)) return Live;
        if (State?.SelectedSource == code) return Source;
        if (State?.SelectedDestination == code ||
            (State?.SelectedSource is { } source && State.Routes.TryGetValue(source, out var destination) && destination == code)) return Target;
        if (State?.Routes.ContainsKey(code) == true || State?.Routes.Values.Contains(code) == true) return Mapped;
        return Key;
    }
    protected bool IsLive(string code)
    {
        return State?.IsActive(code) == true;
    }
    // Surfaces repaint ~30 times a second. Immutable brushes/pens and cached label layouts keep that from
    // allocating per frame; a new FormattedText per keyboard label per frame leaked ~30 MB/s of native memory.
    protected static IPen Pen(Color color, double width = 1) => new Avalonia.Media.Immutable.ImmutablePen(color.ToUInt32(), width);
    protected static IBrush Brush(Color color, byte alpha = 255) => new Avalonia.Media.Immutable.ImmutableSolidColorBrush(Color.FromArgb(alpha, color.R, color.G, color.B));
    private static readonly Dictionary<(string Text, double Size, Color Color), FormattedText> Labels = new();
    protected static void Label(DrawingContext c, string text, Point p, double size = 11, Color? color = null)
    {
        var key = (text, size, color ?? Text);
        if (!Labels.TryGetValue(key, out var ft))
        {
            if (Labels.Count >= 1024) Labels.Clear(); // live readouts (stick %) would otherwise grow it slowly
            Labels[key] = ft = new FormattedText(text, System.Globalization.CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight, Typeface.Default, size, Brush(key.Item3));
        }
        c.DrawText(ft, new Point(p.X - ft.Width / 2, p.Y - ft.Height / 2));
    }
}
