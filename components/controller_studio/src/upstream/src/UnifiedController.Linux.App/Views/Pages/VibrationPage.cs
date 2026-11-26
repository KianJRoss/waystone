using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using UnifiedController.Core;
using UnifiedController.Linux.App.Controls;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.Views.Pages;

public sealed class VibrationPage : UserControl
{
    private readonly ProfileDocumentService _profiles; private readonly IControllerRuntime _runtime;
    private readonly CheckBox _enabled = new() { Content = "Enable controller rumble" };
    private readonly Slider _heavy = MakeSlider(), _light = MakeSlider();
    private readonly TextBlock _heavyValue = new(), _lightValue = new(), _status = PageUi.Dim("");
    private readonly MotorMeter _heavyMeter = new() { Height = 18 }, _lightMeter = new() { Height = 18 };

    public VibrationPage(ProfileDocumentService profiles, IControllerRuntime runtime)
    {
        _profiles = profiles; _runtime = runtime; _heavy.PropertyChanged += Changed; _light.PropertyChanged += Changed;
        var motors = new Grid { ColumnDefinitions = new ColumnDefinitions("*,*"), ColumnSpacing = 14 };
        motors.Children.Add(Motor("Left grip · heavy motor", "Low-frequency impacts and recoil", _heavy, _heavyValue, _heavyMeter, () => Test(1, 0)));
        var light = Motor("Right grip · light motor", "High-frequency textures and engine hum", _light, _lightValue, _lightMeter, () => Test(0, 1)); Grid.SetColumn(light, 1); motors.Children.Add(light);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { _enabled, PageUi.Button("Test both", () => Test(1, 1)), PageUi.Button("Stop", _runtime.StopRumble, "danger"), PageUi.Button("Save", Save, "accent") } };
        Content = PageUi.Scroll(new StackPanel { Margin = new Avalonia.Thickness(20), Spacing = 14, Children = { PageUi.Card("Vibration", actions, _status), motors } }); Reload();
    }
    private static Slider MakeSlider() { var slider = new Slider { Minimum = 0, Maximum = 100, HorizontalAlignment = HorizontalAlignment.Stretch }; slider.Classes.Add("accent"); return slider; }
    private static Border Motor(string title, string description, Slider slider, TextBlock value, MotorMeter meter, Action test)
    {
        value.HorizontalAlignment = HorizontalAlignment.Right;
        var titleRow = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Children = { new TextBlock { Text = description, TextWrapping = Avalonia.Media.TextWrapping.Wrap }, value } }; Grid.SetColumn(value, 1);
        return PageUi.Card(title, titleRow, slider, PageUi.Dim("Live motor output"), meter, PageUi.Button("Test this motor", test));
    }
    private void Changed(object? sender, Avalonia.AvaloniaPropertyChangedEventArgs e) { if (e.Property != RangeBase.ValueProperty) return; _heavyValue.Text = $"{_heavy.Value:0}%"; _lightValue.Text = $"{_light.Value:0}%"; }
    private void Test(double heavy, double light) { try { _runtime.TestRumble(heavy * _heavy.Value / 100, light * _light.Value / 100, 500); _status.Text = "Rumble test sent to the selected source."; } catch (Exception ex) { _status.Text = "The selected source cannot rumble: " + ex.Message; } }
    private void Save() { try { _profiles.MutateActiveProfile(p => ProfileEditorService.SetRumble(p, _enabled.IsChecked == true, (int)_heavy.Value, (int)_light.Value)); _status.Text = "Rumble settings saved."; } catch (Exception ex) { _status.Text = ex.Message; } }
    public void Reload() { var r = _profiles.ActiveProfile["rumble"]; _enabled.IsChecked = r?["enabled"]?.GetValue<bool>() ?? true; _heavy.Value = r?["left_intensity"]?.GetValue<double>() ?? 100; _light.Value = r?["right_intensity"]?.GetValue<double>() ?? 100; }
    public void Tick(RuntimeSnapshot snapshot) { _heavyMeter.Value = snapshot.Rumble.Heavy; _lightMeter.Value = snapshot.Rumble.Light; if (snapshot.SelectedSourceId is null) _status.Text = "No physical controller source is selected; live rumble and tests are unavailable."; }
}
