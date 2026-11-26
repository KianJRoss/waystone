using System.Text.Json.Nodes;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using UnifiedController.Core;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.Views.Pages;

public sealed class MacrosPage : UserControl
{
    private readonly ProfileDocumentService _profiles; private readonly IControllerRuntime _runtime;
    private readonly ListBox _macros = new() { MinWidth = 190 }; private readonly StackPanel _steps = new() { Spacing = 6 };
    private readonly TextBox _name = new() { Watermark = "Macro name" }, _value = new() { Watermark = "Target, value, or action" }, _trigger = new() { Watermark = "Controller:A" };
    private readonly ComboBox _type = PageUi.Choice("press", "release", "axis", "delay", "action"); private readonly NumericUpDown _duration = new() { Minimum = 0, Maximum = 10000 };
    private readonly NumericUpDown _repeat = new() { Minimum = 1, Maximum = 100, Value = 1 }; private readonly CheckBox _cancel = new() { Content = "Cancel on trigger release" }; private readonly TextBlock _status = PageUi.Dim(""); private JsonArray _draft = [];
    public MacrosPage(ProfileDocumentService profiles, IControllerRuntime runtime)
    {
        _profiles = profiles; _runtime = runtime; _macros.SelectionChanged += (_, _) => LoadSelected();
        var left = PageUi.Card("Macros", PageUi.Dim("Select a macro to edit its ordered playback steps."), _macros, PageUi.Button("New macro", NewMacro, "accent"), PageUi.Button("Stop all running macros", runtime.StopAllMacros, "danger"));
        var sequence = PageUi.Card("Sequence", _steps, new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, Children = { _type, _value, _duration, PageUi.Button("Add step", AddStep, "accent") } }, PageUi.Dim("Duration becomes hold ms for input steps and delay ms for delay steps."));
        var playback = PageUi.Card("Playback", PageUi.Row("Name", _name), PageUi.Row("Repeat count", _repeat), PageUi.Row("Trigger", _trigger), _cancel, new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { PageUi.Button("Save macro", Save, "accent"), PageUi.Button("Delete macro", Delete, "danger") } }, _status);
        var right = new StackPanel { Spacing = 12, Children = { sequence, playback } }; var grid = new Grid { ColumnDefinitions = new ColumnDefinitions("220,*"), ColumnSpacing = 14, Children = { left } }; Grid.SetColumn(right, 1); grid.Children.Add(right); Content = PageUi.Scroll(new Border { Padding = new Thickness(20), Child = grid }); Reload();
    }
    private string? Selected => _macros.SelectedItem?.ToString();
    private void NewMacro() { _macros.SelectedItem = null; _name.Text = "New macro"; _draft = []; RefreshSteps(); }
    private void LoadSelected() { var n = Selected is { } name ? _profiles.ActiveProfile["macros"]?[name] : null; _name.Text = Selected ?? ""; _repeat.Value = n?["repeat_count"]?.GetValue<int>() ?? 1; _cancel.IsChecked = n?["cancel_on_release"]?.GetValue<bool>() ?? false; _draft = n?["steps"]?.DeepClone() as JsonArray ?? []; _trigger.Text = FindTrigger(Selected); RefreshSteps(); }
    private string FindTrigger(string? name) { if (name is null) return ""; var dest = "Macro:" + name; return (_profiles.ActiveProfile["routes"] as JsonObject)?.FirstOrDefault(x => x.Value?.ToString() == dest).Key ?? ""; }
    private void RefreshSteps()
    {
        _steps.Children.Clear(); if (_draft.Count == 0) { _steps.Children.Add(PageUi.Dim("No steps yet. Add the first step below.")); return; }
        for (var i = 0; i < _draft.Count; i++) { var index = i; var o = _draft[i] as JsonObject ?? []; var type = o["type"]?.ToString() ?? "step"; var target = o["target"]?.ToString() ?? o["action"]?.ToString() ?? "—"; var value = o["value"]?.ToString() ?? "—"; var ms = o["duration_ms"]?.ToString() ?? "0"; var row = new Grid { ColumnDefinitions = new ColumnDefinitions("32,70,*,70,72,72,Auto,Auto,Auto"), ColumnSpacing = 6 }; row.Children.Add(new TextBlock { Text = (i + 1).ToString(), VerticalAlignment = VerticalAlignment.Center }); Add(row, Pill(type), 1); Add(row, new TextBlock { Text = target, VerticalAlignment = VerticalAlignment.Center, TextTrimming = Avalonia.Media.TextTrimming.CharacterEllipsis }, 2); Add(row, PageUi.Dim(value), 3); Add(row, PageUi.Dim(type == "delay" ? "—" : ms + " ms"), 4); Add(row, PageUi.Dim(type == "delay" ? ms + " ms" : "0 ms"), 5); Add(row, PageUi.Button("↑", () => Move(index, -1)), 6); Add(row, PageUi.Button("↓", () => Move(index, 1)), 7); Add(row, PageUi.Button("×", () => Remove(index), "danger"), 8); var card = new Border { Padding = new Thickness(9, 6), Child = row }; card.Classes.Add("card"); _steps.Children.Add(card); }
    }
    private static Border Pill(string text) { var b = new Border { Padding = new Thickness(7, 3), Child = new TextBlock { Text = text } }; b.Classes.Add("card"); return b; }
    private static void Add(Grid grid, Control control, int column) { Grid.SetColumn(control, column); grid.Children.Add(control); }
    private void AddStep() { try { _draft.Add(ProfileEditorService.CreateMacroStep(_type.SelectedItem?.ToString() ?? "press", _value.Text ?? "", (int)(_duration.Value ?? 0))); RefreshSteps(); _status.Text = "Step added."; } catch (Exception ex) { _status.Text = ex.Message; } }
    private void Remove(int i) { _draft.RemoveAt(i); RefreshSteps(); } private void Move(int i, int d) { var j = i + d; if (j < 0 || j >= _draft.Count) return; var node = _draft[i]; _draft.RemoveAt(i); _draft.Insert(j, node); RefreshSteps(); }
    private void Save() { try { var name = _name.Text?.Trim(); if (string.IsNullOrEmpty(name)) throw new InvalidOperationException("Enter a macro name."); _profiles.MutateActiveProfile(p => { ProfileEditorService.SetMacro(p, name, (int)(_repeat.Value ?? 1), _cancel.IsChecked == true, _draft); if (!string.IsNullOrWhiteSpace(_trigger.Text)) ProfileEditorService.SetRoute(p, _trigger.Text.Trim(), "Macro:" + name, null, [], "while-held", "normal", 1); }); Reload(); _macros.SelectedItem = name; _status.Text = "Macro and trigger route saved."; } catch (Exception ex) { _status.Text = ex.Message; } }
    private void Delete() { if (Selected is not { } n) return; _profiles.MutateActiveProfile(p => ProfileEditorService.RemoveMacro(p, n)); Reload(); }
    public void Reload() { _macros.ItemsSource = (_profiles.ActiveProfile["macros"] as JsonObject)?.Select(x => x.Key).ToArray() ?? []; if (_macros.ItemCount > 0) _macros.SelectedIndex = 0; else NewMacro(); } public void Tick(RuntimeSnapshot snapshot) { if (snapshot.ActiveMacros > 0) _status.Text = $"{snapshot.ActiveMacros} macro(s) running"; }
}
