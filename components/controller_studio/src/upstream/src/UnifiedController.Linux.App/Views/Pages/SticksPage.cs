using System.Globalization;
using System.Text.Json.Nodes;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using UnifiedController.Core;
using UnifiedController.Linux.App.Controls;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.Views.Pages;

public sealed class SticksPage : UserControl
{
    private sealed record Side(CheckBox Enabled, RangeSlider Deadzone, RangeSlider Anti, Slider Curve, CurveEditor Plot, LiveStickView Live, TextBlock Coordinates, StackPanel Custom, ToggleButton[] Pills);
    private readonly ProfileDocumentService _profiles; private readonly Side _left, _right; private JsonObject? _draft; private int _changes;
    private readonly TextBlock _pending = PageUi.Dim(""); private readonly CheckBox _mouseEnabled = new() { Content = "Enable Controller → Mouse" };
    private readonly RangeSlider _leftTrigger = new() { Minimum = 0, Maximum = 1, Step = .01 }, _rightTrigger = new() { Minimum = 0, Maximum = 1, Step = .01 };
    private readonly ComboBox _mouseStick = PageUi.Choice("right", "left"); private readonly TextBox _mouseSpeed = Value(), _mouseDeadzone = Value();

    public SticksPage(ProfileDocumentService profiles, IControllerRuntime runtime)
    {
        _profiles = profiles; _left = CreateSide(); _right = CreateSide();
        var halves = new Grid { ColumnDefinitions = new ColumnDefinitions("*,1,*"), ColumnSpacing = 16 };
        halves.Children.Add(SidePanel("Left Stick", _left, false));
        var divider = new Border { Width = 1, Background = Avalonia.Media.Brushes.DimGray }; Grid.SetColumn(divider, 1); halves.Children.Add(divider);
        var right = SidePanel("Right Stick", _right, true); Grid.SetColumn(right, 2); halves.Children.Add(right);
        _leftTrigger.ValueChanged += (_, _) => Dirty(); _rightTrigger.ValueChanged += (_, _) => Dirty();
        var lower = new Grid { ColumnDefinitions = new ColumnDefinitions("*,*"), ColumnSpacing = 14 };
        lower.Children.Add(PageUi.Card("Triggers", PageUi.Dim("Left trigger deadzone / full output"), _leftTrigger, PageUi.Dim("Right trigger deadzone / full output"), _rightTrigger));
        var mouse = PageUi.Card("Controller → mouse", _mouseEnabled, PageUi.Row("Pointer stick", _mouseStick), PageUi.Row("Speed (1–60)", _mouseSpeed), PageUi.Row("Deadzone %", _mouseDeadzone)); Grid.SetColumn(mouse, 1); lower.Children.Add(mouse);
        var pendingBar = new Border { Padding = new Avalonia.Thickness(14, 10), Child = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Children = { _pending } } }; pendingBar.Classes.Add("card");
        var pendingActions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { PageUi.Button("Discard", Reload), PageUi.Button("Save", Save, "accent") } }; Grid.SetColumn(pendingActions, 1); ((Grid)pendingBar.Child).Children.Add(pendingActions);
        var body = PageUi.Scroll(new StackPanel { Margin = new Avalonia.Thickness(20), Spacing = 14, Children = { halves, lower } });
        var page = new Grid { RowDefinitions = new RowDefinitions("*,Auto"), Children = { body, pendingBar } }; Grid.SetRow(pendingBar, 1); pendingBar.Margin = new Avalonia.Thickness(20, 0, 20, 14); Content = page; Reload();
    }
    private static TextBox Value() { var b = new TextBox(); b.Classes.Add("value"); return b; }
    private Side CreateSide()
    {
        var enabled = new CheckBox { Content = "Enabled" }; var dz = new RangeSlider { Minimum = 0, Maximum = 1, Step = .01 }; var anti = new RangeSlider { Minimum = 0, Maximum = 1, Step = .01 };
        var curve = new Slider { Minimum = .5, Maximum = 3, TickFrequency = .05 }; curve.Classes.Add("accent"); var plot = new CurveEditor { Width = 205, Height = 205 }; var live = new LiveStickView { Width = 150, Height = 150 }; var coordinates = new TextBlock { Text = "X 0%   Y 0%", HorizontalAlignment = HorizontalAlignment.Center }; coordinates.Classes.Add("live");
        var custom = new StackPanel { Spacing = 4, IsVisible = false, Children = { PageUi.Dim("Curve exponent"), curve } };
        var pills = new[] { "Linear", "Concave", "S-Curve", "Custom" }.Select(x => { var p = new ToggleButton { Content = x }; p.Classes.Add("pill"); return p; }).ToArray();
        var side = new Side(enabled, dz, anti, curve, plot, live, coordinates, custom, pills);
        enabled.IsCheckedChanged += (_, _) => Dirty(); dz.ValueChanged += (_, _) => Dirty(); anti.ValueChanged += (_, _) => Dirty(); curve.ValueChanged += (_, _) => { plot.SetCurve(curve.Value); SelectCurve(side, 3, false); Dirty(); };
        var values = new[] { 1d, 1.5, 2d, 1.2 }; for (var i = 0; i < pills.Length; i++) { var index = i; pills[i].Click += (_, _) => { curve.Value = values[index]; SelectCurve(side, index, true); Dirty(); }; }
        return side;
    }
    private Control SidePanel(string title, Side side, bool mirror)
    {
        var titleBlock = new TextBlock { Text = title, FontSize = 18, FontWeight = Avalonia.Media.FontWeight.SemiBold, HorizontalAlignment = mirror ? HorizontalAlignment.Right : HorizontalAlignment.Left };
        var controls = new StackPanel { Spacing = 10, Children = { PageUi.Card("Stick", side.Enabled), PageUi.Card("Deadzone", "0–100", side.Deadzone), PageUi.Card("Anti-Deadzone", "0–100", side.Anti), PageUi.Card("Curve Adjustment", new StackPanel { Orientation = Orientation.Horizontal, Spacing = 5, Children = { side.Pills[0], side.Pills[1], side.Pills[2], side.Pills[3] } }, side.Custom) } };
        var reset = PageUi.Button("Reset to defaults", () => Reset(side)); reset.HorizontalAlignment = mirror ? HorizontalAlignment.Left : HorizontalAlignment.Right;
        var output = PageUi.Dim("Output"); Grid.SetColumn(output, 1);
        var plot = PageUi.Card("Response Curve", reset, side.Plot, new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Children = { PageUi.Dim("Input"), output } });
        var live = PageUi.Card("Live", side.Live, side.Coordinates);
        var visuals = new StackPanel { Spacing = 10, Children = { plot, live } };
        var body = new Grid { ColumnDefinitions = new ColumnDefinitions(mirror ? "Auto,*" : "*,Auto"), ColumnSpacing = 12 };
        if (mirror) { body.Children.Add(visuals); Grid.SetColumn(controls, 1); body.Children.Add(controls); } else { body.Children.Add(controls); Grid.SetColumn(visuals, 1); body.Children.Add(visuals); }
        return new StackPanel { Spacing = 10, Children = { titleBlock, body } };
    }
    private static void SelectCurve(Side side, int selected, bool updateVisibility) { for (var i = 0; i < side.Pills.Length; i++) side.Pills[i].IsChecked = i == selected; if (updateVisibility) side.Custom.IsVisible = selected == 3; }
    private void Reset(Side s) { s.Enabled.IsChecked = false; s.Deadzone.Set(.08, 1); s.Anti.Set(0, 1); s.Curve.Value = 1; SelectCurve(s, 0, true); Dirty(); }
    private void Dirty() { if (_draft is null) return; _changes++; _pending.Text = $"{_changes} unsaved change{(_changes == 1 ? "" : "s")}"; }
    private void LoadSide(Side ui, string side) { var n = _draft?["stick_processing"]?[side]; ui.Enabled.IsChecked = n?["enabled"]?.GetValue<bool>() ?? false; ui.Deadzone.Set(Number(n?["inner_deadzone"], .08), Number(n?["outer_deadzone"], 1)); ui.Anti.Set(Number(n?["anti_deadzone"], 0), 1); ui.Curve.Value = Number(n?["curve"], 1); ui.Plot.SetCurve(ui.Curve.Value); var selected = Math.Abs(ui.Curve.Value - 1) < .01 ? 0 : Math.Abs(ui.Curve.Value - 1.5) < .01 ? 1 : Math.Abs(ui.Curve.Value - 2) < .01 ? 2 : 3; SelectCurve(ui, selected, true); }
    public void Reload() { _draft = _profiles.ActiveProfile.DeepClone().AsObject(); LoadSide(_left, "left"); LoadSide(_right, "right"); var t = _draft["trigger_processing"]; _leftTrigger.Set(Number(t?["left"]?["inner_deadzone"], 0), Number(t?["left"]?["outer_deadzone"], 1)); _rightTrigger.Set(Number(t?["right"]?["inner_deadzone"], 0), Number(t?["right"]?["outer_deadzone"], 1)); var m = _draft["controller_mouse"]; _mouseEnabled.IsChecked = m?["enabled"]?.GetValue<bool>() ?? false; _mouseStick.SelectedItem = m?["stick"]?.ToString() ?? "right"; _mouseSpeed.Text = Number(m?["sensitivity"], 18).ToString("0.##", CultureInfo.InvariantCulture); _mouseDeadzone.Text = (Number(m?["deadzone"], .16) * 100).ToString("0.##", CultureInfo.InvariantCulture); _changes = 0; _pending.Text = "No unsaved changes"; }
    private void Save() { try { var draft = _draft!; WriteSide(draft, "left", _left); WriteSide(draft, "right", _right); ProfileEditorService.SetTriggers(draft, new JsonObject { { "inner_deadzone", _leftTrigger.Low }, { "outer_deadzone", _leftTrigger.High } }, new JsonObject { { "inner_deadzone", _rightTrigger.Low }, { "outer_deadzone", _rightTrigger.High } }); ProfileEditorService.SetControllerMouse(draft, new JsonObject { { "enabled", _mouseEnabled.IsChecked == true }, { "stick", _mouseStick.SelectedItem?.ToString() ?? "right" }, { "sensitivity", Math.Clamp(Number(_mouseSpeed.Text, 18), 1, 60) }, { "deadzone", Math.Clamp(Number(_mouseDeadzone.Text, 16), 0, 80) / 100 } }); _profiles.MutateActiveProfile(p => { foreach (var x in draft) p[x.Key] = x.Value?.DeepClone(); }); Reload(); _pending.Text = "Saved"; } catch (Exception ex) { _pending.Text = ex.Message; } }
    private static void WriteSide(JsonObject p, string name, Side s) => ProfileEditorService.SetStick(p, name, s.Enabled.IsChecked == true, N(s.Deadzone.Low), N(s.Deadzone.High), N(s.Anti.Low), N(s.Curve.Value));
    private static string N(double v) => v.ToString("0.###", CultureInfo.InvariantCulture); private static double Number(JsonNode? n, double d) => Number(n?.ToString(), d); private static double Number(string? text, double d) => double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : d;
    public void Tick(RuntimeSnapshot snapshot) { UpdateLive(_left, snapshot.Input.LeftX / 32767d, snapshot.Input.LeftY / 32767d); UpdateLive(_right, snapshot.Input.RightX / 32767d, snapshot.Input.RightY / 32767d); }
    private static void UpdateLive(Side side, double x, double y) { side.Live.Update(x, y); side.Coordinates.Text = $"X {x * 100:0}%   Y {y * 100:0}%"; }
}
