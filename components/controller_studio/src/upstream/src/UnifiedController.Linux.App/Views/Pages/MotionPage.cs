using System.Globalization;
using System.Text.Json.Nodes;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;
using UnifiedController.Core;
using UnifiedController.Linux.App.Controls;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.Views.Pages;

public sealed class MotionPage : UserControl
{
    private readonly ProfileDocumentService _profiles; private readonly OrientationView _view = new() { Width = 300, Height = 220 };
    private readonly TextBlock _pitch = Readout(), _roll = Readout(), _yaw = Readout(), _velocity = Readout(), _accel = Readout(), _still = Readout(), _status = PageUi.Dim("");
    private readonly Border _dsuPill = new(); private readonly TextBlock _dsuState = new(), _dsuDetails = PageUi.Dim("");
    private readonly ComboBox _preset = PageUi.Choice("native", "gyro_aim", "tilt_drive", "dual_motion", "inertia_aim", "custom"); private readonly Axis _left = new(), _right = new();
    private sealed class Axis { public CheckBox Enabled = new() { Content = "Enable gyro mapping" }; public ComboBox Mode = PageUi.Choice("rate", "tilt", "inertia"), X = PageUi.Choice("yaw", "pitch", "roll"), Y = PageUi.Choice("pitch", "yaw", "roll"), Activation = PageUi.Choice("always", "LT", "RT", "LB", "RB"); public TextBox Scale = Value(), Dead = Value(), Curve = Value(); }
    public MotionPage(ProfileDocumentService profiles, IControllerRuntime runtime)
    {
        _profiles = profiles; _dsuPill.Classes.Add("statusPill"); _dsuPill.Child = _dsuState;
        var readouts = new Grid { ColumnDefinitions = new ColumnDefinitions("*,*"), RowDefinitions = new RowDefinitions("Auto,Auto,Auto"), ColumnSpacing = 18, RowSpacing = 14 };
        AddReadout(readouts, "Pitch", _pitch, 0, 0); AddReadout(readouts, "Roll", _roll, 1, 0); AddReadout(readouts, "Yaw", _yaw, 0, 1); AddReadout(readouts, "Angular velocity", _velocity, 1, 1); AddReadout(readouts, "Total accel", _accel, 0, 2); AddReadout(readouts, "Stillness", _still, 1, 2);
        var live = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*"), ColumnSpacing = 24, Children = { _view, readouts } }; Grid.SetColumn(readouts, 1);
        var liveActions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { PageUi.Button("Recenter", runtime.RecenterMotion), PageUi.Button("Calibrate", runtime.CalibrateMotion) } };
        var dsuHeader = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Children = { _dsuDetails, _dsuPill } }; Grid.SetColumn(_dsuPill, 1);
        var axes = new Grid { ColumnDefinitions = new ColumnDefinitions("*,*"), ColumnSpacing = 14, Children = { AxisCard("Left stick motion", _left) } }; var right = AxisCard("Right stick motion", _right); Grid.SetColumn(right, 1); axes.Children.Add(right);
        Content = PageUi.Scroll(new StackPanel { Margin = new Avalonia.Thickness(20), Spacing = 14, Children = { PageUi.Card("3D gyro · live orientation", live, liveActions), PageUi.Card("DSU / Cemuhook", dsuHeader, PageUi.Dim("Citron/Eden: Controls → Motion → cemuhook 127.0.0.1:26760")), PageUi.Card("Motion mapping", PageUi.Row("Preset", _preset), axes, PageUi.Dim("Unknown native motion fields are preserved."), PageUi.Button("Save motion settings", Save, "accent"), _status) } }); Reload();
    }
    private static TextBlock Readout() => new() { FontSize = 16, FontWeight = FontWeight.SemiBold };
    private static TextBox Value() { var b = new TextBox(); b.Classes.Add("value"); return b; }
    private static void AddReadout(Grid grid, string label, TextBlock value, int col, int row) { var panel = new StackPanel { Spacing = 2, Children = { PageUi.Dim(label), value } }; Grid.SetColumn(panel, col); Grid.SetRow(panel, row); grid.Children.Add(panel); }
    private static Border AxisCard(string title, Axis a) => PageUi.Card(title, a.Enabled, PageUi.Row("Mode", a.Mode), PageUi.Row("Horizontal source", a.X), PageUi.Row("Vertical source", a.Y), PageUi.Row("Activation", a.Activation), PageUi.Row("Full scale", a.Scale), PageUi.Row("Deadzone", a.Dead), PageUi.Row("Curve", a.Curve));
    private static double Num(JsonNode? n, double d) => double.TryParse(n?.ToString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : d; private static bool Bool(JsonNode? n) => bool.TryParse(n?.ToString(), out var v) && v;
    private static double Box(TextBox b, double fallback) => double.TryParse(b.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : fallback;
    private static void Select(ComboBox b, string v) => b.SelectedItem = b.Items.Cast<object>().Any(x => x.ToString() == v) ? v : b.Items[0];
    private static void LoadAxis(Axis a, JsonNode? n) { a.Enabled.IsChecked = Bool(n?["enabled"]); Select(a.Mode, n?["mode"]?.ToString() ?? "rate"); Select(a.X, n?["x_axis"]?.ToString() ?? "yaw"); Select(a.Y, n?["y_axis"]?.ToString() ?? "pitch"); Select(a.Activation, n?["activation"]?.ToString() ?? "always"); a.Scale.Text = Num(n?["full_scale"], 120).ToString("0.##", CultureInfo.InvariantCulture); a.Dead.Text = Num(n?["deadzone"], 2).ToString("0.##", CultureInfo.InvariantCulture); a.Curve.Text = Num(n?["curve"], 1).ToString("0.##", CultureInfo.InvariantCulture); }
    private static JsonObject Edits(Axis a) => new() { { "enabled", a.Enabled.IsChecked == true }, { "mode", a.Mode.SelectedItem?.ToString() ?? "rate" }, { "x_axis", a.X.SelectedItem?.ToString() ?? "yaw" }, { "y_axis", a.Y.SelectedItem?.ToString() ?? "pitch" }, { "x_enabled", true }, { "y_enabled", true }, { "full_scale", Math.Clamp(Box(a.Scale, 120), 5, 360) }, { "deadzone", Math.Clamp(Box(a.Dead, 2), 0, 60) }, { "curve", Math.Clamp(Box(a.Curve, 1), .5, 3) }, { "activation", a.Activation.SelectedItem?.ToString() ?? "always" } };
    private void Save() { try { _profiles.MutateActiveProfile(p => ProfileEditorService.SetMotion(p, _preset.SelectedItem?.ToString() ?? "native", Edits(_left), Edits(_right))); _status.Text = "Motion settings saved."; } catch (Exception ex) { _status.Text = ex.Message; } }
    public void Reload() { var m = _profiles.ActiveProfile["motion"]; Select(_preset, m?["preset"]?.ToString() ?? "native"); LoadAxis(_left, m?["left"]); LoadAxis(_right, m?["right"]); }
    public void Tick(RuntimeSnapshot s)
    {
        if (s.Orientation is { } o) { _view.Update(o.PitchDeg, o.RollDeg, o.YawDeg); _pitch.Text = $"{o.PitchDeg:0.0}°"; _roll.Text = $"{o.RollDeg:0.0}°"; _yaw.Text = $"{o.YawDeg:0.0}°"; _velocity.Text = $"{o.AngularVelocityDps:0.0} dps"; _accel.Text = $"{o.TotalAccelG:0.00} g"; _still.Text = o.Still ? "Still" : "Moving"; } else { _pitch.Text = _roll.Text = _yaw.Text = "—"; _velocity.Text = _accel.Text = "—"; _still.Text = "No sample"; }
        var d = s.Dsu; _dsuPill.Classes.Remove("statusOk"); _dsuPill.Classes.Remove("statusWarn"); _dsuPill.Classes.Remove("statusError");
        if (d is null || !string.IsNullOrEmpty(d.Error)) { _dsuState.Text = d?.Error is { Length: > 0 } ? "Error" : "Unavailable"; _dsuPill.Classes.Add("statusError"); _dsuDetails.Text = d?.Error ?? "DSU service unavailable"; }
        else { _dsuState.Text = d.Running ? (d.Clients > 0 ? "Running" : "No clients") : "Stopped"; _dsuPill.Classes.Add(d.Running && d.Clients > 0 ? "statusOk" : d.Running ? "statusWarn" : "statusError"); _dsuDetails.Text = $"Ports {d.Port} / {d.PreviewPort} · {d.Clients} client(s)"; }
    }
}
