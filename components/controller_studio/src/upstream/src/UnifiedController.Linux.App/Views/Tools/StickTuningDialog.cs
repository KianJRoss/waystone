using System.Globalization;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Threading;
using UnifiedController.App.Engine;
using UnifiedController.Core;
using UnifiedController.Linux.App.Controls;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.Views.Tools;

/// <summary>Profile-safe, live stick tuning based on the current C# tuning window.</summary>
public sealed class StickTuningDialog : Window
{
    private readonly ProfileDocumentService _profiles;
    private readonly RuntimeUiAdapter _runtime;
    private readonly ComboBox _side = new() { ItemsSource = new[] { "left", "right" }, Width = 130 };
    private readonly CheckBox _enabled = new() { Content = "Enable stick processing" };
    private readonly Slider _inner = Slider(0, .8, .01), _outer = Slider(.01, 1, .01),
        _anti = Slider(0, .8, .01), _curve = Slider(.5, 3, .05);
    private readonly TextBlock _innerValue = Value(), _outerValue = Value(), _antiValue = Value(), _curveValue = Value();
    private readonly TextBlock _raw = new(), _output = new(), _status = new() { TextWrapping = TextWrapping.Wrap };
    private readonly StickResponsePlot _plot = new() { Height = 230 };
    private readonly DispatcherTimer _timer;
    private bool _loading;

    public StickTuningDialog(ProfileDocumentService profiles, RuntimeUiAdapter runtime, string side = "left")
    {
        _profiles = profiles; _runtime = runtime;
        Title = "Stick tuning — " + profiles.ActiveName; Width = 720; Height = 650; MinWidth = 620; MinHeight = 560;
        Background = new SolidColorBrush(Color.Parse("#111827"));
        _side.SelectedItem = side.Equals("right", StringComparison.OrdinalIgnoreCase) ? "right" : "left";
        _side.SelectionChanged += (_, _) => LoadSettings();
        _enabled.IsCheckedChanged += (_, _) => RefreshPreview();
        foreach (var slider in new[] { _inner, _outer, _anti, _curve }) slider.PropertyChanged += (_, e) => { if (e.Property == RangeBase.ValueProperty) RefreshPreview(); };
        var save = new Button { Content = "Save", HorizontalAlignment = HorizontalAlignment.Right, MinWidth = 100 };
        save.Click += (_, _) => Save();
        var reset = new Button { Content = "Passthrough", MinWidth = 100 };
        reset.Click += (_, _) => { _enabled.IsChecked=false; _inner.Value=.08; _outer.Value=1; _anti.Value=0; _curve.Value=1; _status.Text="Passthrough values entered. Save to apply."; };
        Content = new ScrollViewer { Content = new StackPanel { Margin = new Thickness(22), Spacing = 12, Children =
        {
            new TextBlock { Text="STICK TUNING", FontSize=12, Foreground=Brushes.SlateGray },
            new TextBlock { Text="Deadzone and response", FontSize=24, FontWeight=FontWeight.SemiBold },
            Row("Selected stick", _side), _enabled,
            new Border { Background=new SolidColorBrush(Color.Parse("#0b1220")), CornerRadius=new CornerRadius(8), Padding=new Thickness(8), Child=_plot },
            new TextBlock { Text="Live input / processed output", FontWeight=FontWeight.SemiBold }, _raw, _output,
            Setting("Inner deadzone", _inner, _innerValue), Setting("Full output at", _outer, _outerValue),
            Setting("Minimum output", _anti, _antiValue), Setting("Response curve", _curve, _curveValue),
            new StackPanel { Orientation=Orientation.Horizontal, Spacing=8, HorizontalAlignment=HorizontalAlignment.Right, Children={ reset, save } }, _status
        } } };
        LoadSettings();
        _timer = new DispatcherTimer(TimeSpan.FromMilliseconds(50), DispatcherPriority.Background, (_, _) => RefreshLive());
        _timer.Start(); Closed += (_, _) => _timer.Stop();
    }

    private string Side => _side.SelectedItem?.ToString() ?? "left";
    private void LoadSettings()
    {
        if (_profiles is null) return;
        _loading = true;
        var settings = _profiles.ActiveProfile["stick_processing"]?[Side];
        _enabled.IsChecked = settings?["enabled"]?.GetValue<bool>() ?? false;
        _inner.Value = settings?["inner_deadzone"]?.GetValue<double>() ?? .08;
        _outer.Value = settings?["outer_deadzone"]?.GetValue<double>() ?? 1;
        _anti.Value = settings?["anti_deadzone"]?.GetValue<double>() ?? 0;
        _curve.Value = settings?["curve"]?.GetValue<double>() ?? 1;
        _loading = false; RefreshPreview();
    }

    private void RefreshPreview()
    {
        if (_loading) return;
        _innerValue.Text=$"{_inner.Value:P0}"; _outerValue.Text=$"{_outer.Value:P0}";
        _antiValue.Text=$"{_anti.Value:P0}"; _curveValue.Text=_curve.Value.ToString("0.00",CultureInfo.InvariantCulture);
        _plot.UpdateSettings(_enabled.IsChecked==true,_inner.Value,_outer.Value,_anti.Value,_curve.Value);
    }

    private void RefreshLive()
    {
        var snapshot = _runtime.Snapshot;
        var input = Side == "left" ? (snapshot.Input.LeftX,snapshot.Input.LeftY) : (snapshot.Input.RightX,snapshot.Input.RightY);
        var output = Side == "left" ? (snapshot.Output.LeftX,snapshot.Output.LeftY) : (snapshot.Output.RightX,snapshot.Output.RightY);
        _raw.Text=$"Input   X {Percent(input.Item1),7}   Y {Percent(input.Item2),7}";
        _output.Text=$"Output  X {Percent(output.Item1),7}   Y {Percent(output.Item2),7}";
        _plot.UpdatePosition(input.Item1,input.Item2);
    }

    private void Save()
    {
        try
        {
            if (_outer.Value <= _inner.Value) throw new InvalidOperationException("Full output must be greater than the inner deadzone.");
            _profiles.MutateActiveProfile(profile => ProfileEditorService.SetStick(profile,Side,_enabled.IsChecked==true,
                Number(_inner.Value),Number(_outer.Value),Number(_anti.Value),Number(_curve.Value)));
            _status.Text=$"Saved {Side} stick tuning to {_profiles.ActiveName}.";
        }
        catch(Exception ex) { _status.Text=ex.Message; }
    }

    private static string Percent(short value) => (value/(value<0?32768d:32767d)).ToString("+0.0%;-0.0%;0.0%",CultureInfo.InvariantCulture);
    private static string Number(double value) => value.ToString("0.###",CultureInfo.InvariantCulture);
    private static Slider Slider(double min,double max,double tick) => new() { Minimum=min,Maximum=max,TickFrequency=tick,Width=360 };
    private static TextBlock Value() => new() { Width=60,TextAlignment=TextAlignment.Right };
    private static Control Row(string label,Control control) => new Grid { ColumnDefinitions=new ColumnDefinitions("*,Auto"), Children={ new TextBlock { Text=label,VerticalAlignment=VerticalAlignment.Center }, control } };
    private static Control Setting(string label,Control slider,Control value)
    { Grid.SetColumn(slider,1); Grid.SetColumn(value,2); return new Grid { ColumnDefinitions=new ColumnDefinitions("150,*,60"),ColumnSpacing=8,Children={new TextBlock{Text=label,VerticalAlignment=VerticalAlignment.Center},slider,value} }; }
}
