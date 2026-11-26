using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;

namespace UnifiedController.Linux.App.Controls;

/// <summary>A compact two-ended range editor with typed values.</summary>
public sealed class RangeSlider : UserControl
{
    private readonly Slider _low = new(), _high = new();
    private readonly TextBox _lowValue = new(), _highValue = new();
    private bool _updating;
    public event EventHandler? ValueChanged;
    public double Minimum { get => _low.Minimum; set => _low.Minimum = _high.Minimum = value; }
    public double Maximum { get => _low.Maximum; set => _low.Maximum = _high.Maximum = value; }
    public double Low { get => _low.Value; set => Set(value, High); }
    public double High { get => _high.Value; set => Set(Low, value); }
    public double Step { get => _low.TickFrequency; set => _low.TickFrequency = _high.TickFrequency = value; }

    public RangeSlider()
    {
        _low.Classes.Add("accent"); _high.Classes.Add("accent");
        _lowValue.Classes.Add("value"); _highValue.Classes.Add("value");
        _low.PropertyChanged += SliderChanged; _high.PropertyChanged += SliderChanged;
        _lowValue.LostFocus += TypedChanged; _highValue.LostFocus += TypedChanged;
        Grid.SetColumn(_high, 1); Grid.SetColumn(_lowValue, 2); Grid.SetColumn(_highValue, 3);
        Content = new Grid { ColumnDefinitions = new ColumnDefinitions("*,*,Auto,Auto"), ColumnSpacing = 6,
            Children = { _low, _high, _lowValue, _highValue } };
        Minimum = 0; Maximum = 1; Step = .01; Set(0, 1);
    }

    public void Set(double low, double high)
    {
        _updating = true;
        low = Math.Clamp(low, Minimum, Maximum); high = Math.Clamp(high, low, Maximum);
        _low.Value = low; _high.Value = high; _lowValue.Text = Format(low); _highValue.Text = Format(high);
        _updating = false;
    }
    private void SliderChanged(object? sender, AvaloniaPropertyChangedEventArgs e)
    { if (!_updating && e.Property == RangeBase.ValueProperty) { Set(Math.Min(_low.Value, _high.Value), Math.Max(_low.Value, _high.Value)); ValueChanged?.Invoke(this, EventArgs.Empty); } }
    private void TypedChanged(object? sender, Avalonia.Interactivity.RoutedEventArgs e)
    { if (!_updating) { Set(Parse(_lowValue.Text, Low), Parse(_highValue.Text, High)); ValueChanged?.Invoke(this, EventArgs.Empty); } }
    private static string Format(double value) => value.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture);
    private static double Parse(string? text, double fallback) => double.TryParse(text, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var value) ? value : fallback;
}
