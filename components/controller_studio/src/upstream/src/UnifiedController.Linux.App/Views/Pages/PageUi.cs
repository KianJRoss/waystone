using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;

namespace UnifiedController.Linux.App.Views.Pages;

internal static class PageUi
{
    public static Border Card(string title, params Control[] controls) => Card(title, null, controls);
    public static Border Card(string title, string? hint, params Control[] controls)
    {
        var heading = new TextBlock { Text = title, VerticalAlignment = VerticalAlignment.Center }; heading.Classes.Add("cardTitle");
        var accent = new Border(); accent.Classes.Add("cardAccent");
        var header = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto"), Children = { accent, heading } };
        Grid.SetColumn(heading, 1);
        if (!string.IsNullOrWhiteSpace(hint)) { var value = Dim(hint); value.VerticalAlignment = VerticalAlignment.Center; value.TextAlignment = TextAlignment.Right; Grid.SetColumn(value, 2); header.Children.Add(value); }
        var panel = new StackPanel { Spacing = 9, Children = { header } }; foreach (var c in controls) panel.Children.Add(c);
        var card = new Border { Padding = new Thickness(16), Child = panel }; card.Classes.Add("card"); return card;
    }
    public static TextBlock Dim(string text) { var value = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap }; value.Classes.Add("dim"); return value; }
    public static Button Button(string text, Action click, string css = "pill") { var b = new Button { Content = text }; b.Classes.Add(css); b.Click += (_, _) => click(); return b; }
    public static ComboBox Choice(params string[] values) => new() { ItemsSource = values, SelectedIndex = 0, MinWidth = 145 };
    public static Grid Row(string label, Control control) { Grid.SetColumn(control, 1); return new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), ColumnSpacing = 10, Children = { new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center }, control } }; }
    public static TextBox ValueBox(double value, double min, double max, Action<double> changed)
    {
        var box = new TextBox { Text = value.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture) }; box.Classes.Add("value");
        box.LostFocus += (_, _) => { if (!double.TryParse(box.Text, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var parsed)) parsed = value; parsed = Math.Clamp(parsed, min, max); box.Text = parsed.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture); changed(parsed); };
        return box;
    }
    public static ScrollViewer Scroll(Control content) => new() { Content = content, HorizontalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Disabled };
}
