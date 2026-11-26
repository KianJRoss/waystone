using System.Text.Json.Nodes;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Platform.Storage;
using UnifiedController.Linux.App.ViewModels;
using UnifiedController.Linux.App.Views.Tools;
using UnifiedController.Linux.App.Services;
using UnifiedController.Linux.App.Models;
using UnifiedController.Linux.App.Styles;
using UnifiedController.Linux.App.Views.Pages;
using UnifiedController.Core;

namespace UnifiedController.Linux.App.Views;

public sealed partial class MainWindow : Window
{
    private readonly MainViewModel _vm = new();
    private readonly BindingWorkspaceViewModel _mapper;
    private bool _exiting, _listening, _refreshingMapper;
    private long _listenAfter;
    private Key? _capturedKeyRelease;
    private JsonArray? _steps;
    private readonly SticksPage _sticksPage;
    private readonly MotionPage _motionPage;
    private readonly VibrationPage _vibrationPage;
    private readonly MacrosPage _macrosPage;
    private readonly HotkeysPage _hotkeysPage;
    private readonly ModulesPage _modulesPage;
    private readonly ThemeService _theme;
    public MainWindow()
    {
        InitializeComponent(); DataContext = _vm;
        AddHandler(KeyDownEvent, Window_KeyDown, RoutingStrategies.Tunnel, handledEventsToo: true);
        AddHandler(KeyUpEvent, Window_KeyUp, RoutingStrategies.Tunnel, handledEventsToo: true);
        _mapper = new BindingWorkspaceViewModel(_vm.Profiles, Program.Runtime ?? throw new InvalidOperationException("Runtime unavailable"));
        var runtime = Program.Runtime;
        _sticksPage = new(_vm.Profiles, runtime); _motionPage = new(_vm.Profiles, runtime); _vibrationPage = new(_vm.Profiles, runtime);
        _macrosPage = new(_vm.Profiles, runtime); _hotkeysPage = new(_vm.Profiles, runtime); _modulesPage = new(_vm.Profiles, runtime);
        SticksHost.Content = _sticksPage; MotionHost.Content = _motionPage; VibrationHost.Content = _vibrationPage;
        MacrosHost.Content = _macrosPage; HotkeysHost.Content = _hotkeysPage; ModulesHost.Content = _modulesPage;
        _theme = ((App)Application.Current!).Theme;
        ThemePreset.ItemsSource = ThemeService.Presets.Select(x => x.Name).ToArray(); ThemePreset.SelectedItem = _theme.Settings.Preset;
        DensityPicker.SelectedItem = DensityPicker.Items.OfType<ComboBoxItem>().FirstOrDefault(x => x.Content?.ToString() == _theme.Settings.Density);
        ApplyDensity(_theme.Settings.Density);
        runtime.ActionRequested += Runtime_ActionRequested;
        _mapper.Changed += RefreshMapperUi;
        ControllerMapper.ControlInvoked += MapperControlInvoked;
        MouseMapper.ControlInvoked += MapperControlInvoked;
        KeyboardMapper.ControlInvoked += MapperControlInvoked;
        ProfilePathText.Text = _vm.Profiles.Path;
        ReloadProfileUi();
        // Live input highlights refresh at ~30 Hz so short presses show; the device grid keeps the slower 300 ms rate.
        var ticks = 0;
        var timer = new Avalonia.Threading.DispatcherTimer { Interval = TimeSpan.FromMilliseconds(33) };
        timer.Tick += (_, _) => { UpdateShell(); TickVisiblePage(); if (ticks++ % 9 == 0) DevicesGrid.ItemsSource = DeviceCards(); _mapper.ObserveSnapshot(_vm.Snapshot); UpdateMapperSurfaces(); UpdateLivePhysicalInput(); if (_listening) ListenFrame(); };
        timer.Start();
        Opened += StartOnFirstOpen;
        // Open on a named page (e.g. "Vibration"); used for screenshots and launcher shortcuts.
        if (Environment.GetEnvironmentVariable("UNIFIED_CONTROLLER_START_PAGE") is { Length: > 0 } page &&
            MainTabs.Items.OfType<TabItem>().FirstOrDefault(t => t.IsVisible && string.Equals(t.Header as string, page, StringComparison.OrdinalIgnoreCase)) is { } tab)
            MainTabs.SelectedItem = tab;
    }

    private async void StartOnFirstOpen(object? sender, EventArgs args)
    {
        Opened -= StartOnFirstOpen;
        try { await _vm.Runtime.Start(); }
        catch (Exception error) { Feedback(error.Message); }
    }

    private JsonObject Profile => _vm.Profiles.ActiveProfile;
    private static string Text(TextBox box) => box.Text?.Trim() ?? "";
    private void Feedback(string text) => FeedbackText.Text = text;
    private void Guard(Action action) { try { action(); Feedback("Saved."); ReloadProfileUi(); } catch (Exception e) { Feedback(e.Message); } }

    // Resetting ItemsSource raises SelectionChanged with a transient selection; without this guard the two
    // profile pickers re-select each other and recurse until the stack overflows.
    private bool _reloadingProfiles;

    private void ReloadProfileUi()
    {
        _reloadingProfiles = true;
        try
        {
            ProfileList.ItemsSource = _vm.Profiles.Names.ToArray(); ProfileList.SelectedItem = _vm.Profiles.ActiveName;
            MapperProfile.ItemsSource = _vm.Profiles.Names.ToArray(); MapperProfile.SelectedItem = _vm.Profiles.ActiveName;
        }
        finally { _reloadingProfiles = false; }
        _mapper.RefreshFromActiveProfile(); RefreshMapperUi();
        ReloadRoutes(); ReloadMacros(); LoadStickAndMouse(); LoadMotion(); LoadProfileSettings(); LoadDevices(); LoadMapMod();
        ReloadHostedPages();
    }

    private void ReloadHostedPages() { _sticksPage.Reload(); _motionPage.Reload(); _vibrationPage.Reload(); _macrosPage.Reload(); _hotkeysPage.Reload(); _modulesPage.Reload(); }
    private void TickVisiblePage()
    {
        var snapshot = _vm.Snapshot;
        if (ReferenceEquals(MainTabs.SelectedContent, SticksHost)) _sticksPage.Tick(snapshot);
        else if (ReferenceEquals(MainTabs.SelectedContent, MotionHost)) _motionPage.Tick(snapshot);
        else if (ReferenceEquals(MainTabs.SelectedContent, VibrationHost)) _vibrationPage.Tick(snapshot);
        else if (ReferenceEquals(MainTabs.SelectedContent, MacrosHost)) _macrosPage.Tick(snapshot);
        else if (ReferenceEquals(MainTabs.SelectedContent, HotkeysHost)) _hotkeysPage.Tick(snapshot);
        else if (ReferenceEquals(MainTabs.SelectedContent, ModulesHost)) _modulesPage.Tick(snapshot);
    }
    private void UpdateShell()
    {
        var snapshot = _vm.Snapshot;
        StatusPillText.Text = $"{snapshot.Health} · {snapshot.Devices.Count} · {snapshot.AverageLoopMs:F1} ms";
        ToolTip.SetTip(StatusPill, string.IsNullOrWhiteSpace(snapshot.Error) ? snapshot.Health : snapshot.Error);
        // Clear rather than assign null: a null local Foreground hides the text instead of inheriting the theme color.
        if (snapshot.Error is not null) StatusPillText.Foreground = Avalonia.Media.Brushes.OrangeRed;
        else if (snapshot.Health.Contains("warn", StringComparison.OrdinalIgnoreCase)) StatusPillText.Foreground = Avalonia.Media.Brushes.Orange;
        else StatusPillText.ClearValue(TextBlock.ForegroundProperty);
        // OutputPill.Content comes from the OutputSummary binding; also setting it here made the two alternate every tick.
        HealthDot.Fill = snapshot.Health.Contains("error", StringComparison.OrdinalIgnoreCase) ? Avalonia.Media.Brushes.OrangeRed : snapshot.Running ? (snapshot.Devices.Count > 0 ? (Avalonia.Media.IBrush)Application.Current!.FindResource("UcOk")! : (Avalonia.Media.IBrush)Application.Current!.FindResource("UcWarn")!) : Avalonia.Media.Brushes.OrangeRed;
        QuickMouseToggle.IsChecked = snapshot.ControllerMouseModeEnabled;
        // This runs ~30 times a second: only touch the picker when the device set or selection actually changes,
        // and never while its dropdown is open, or it flickers and closes under the pointer.
        var devices = snapshot.Devices.Where(x => x.Connected && !x.IsVirtual).ToArray();
        var signature = string.Join("|", devices.Select(x => x.StableId + "=" + x.Name));
        if (signature != _sourceSignature && !InputSourcePicker.IsDropDownOpen)
        {
            _sourceSignature = signature;
            InputSourcePicker.ItemsSource = devices; InputSourcePicker.DisplayMemberBinding ??= new Avalonia.Data.Binding(nameof(InputDeviceInfo.Name));
        }
        if (!InputSourcePicker.IsDropDownOpen && (InputSourcePicker.SelectedItem as InputDeviceInfo)?.StableId != snapshot.SelectedSourceId)
            InputSourcePicker.SelectedItem = (InputSourcePicker.ItemsSource as InputDeviceInfo[])?.FirstOrDefault(x => x.StableId == snapshot.SelectedSourceId);
        OpenDeadbandButton.IsVisible = DeadbandExecutable.Value is not null && devices.Any(x => x.VendorId == 0x3537 || x.Name.Contains("GameSir", StringComparison.OrdinalIgnoreCase) || x.Name.Contains("Cyclone", StringComparison.OrdinalIgnoreCase));
    }
    private void Runtime_ActionRequested(string action)
    {
        if (!action.Equals(RuntimeActions.OpenControlCenter, StringComparison.OrdinalIgnoreCase)) return;
        Avalonia.Threading.Dispatcher.UIThread.Post(() => { Show(); WindowState = WindowState.Normal; Activate(); });
    }

    private void RefreshMapperUi()
    {
        if (_refreshingMapper) return;
        _refreshingMapper = true;
        try
        {
        MapperSourceText.Text = _mapper.SelectedSource is null ? "Nothing selected" : Friendly(_mapper.SelectedSource);
        AssignTitle.Text = _mapper.SelectedSource is null ? "Assign" : $"Assign — {Friendly(_mapper.SelectedSource)}";
        MapperOutputText.Text = _mapper.SelectedDestination is null ? "Nothing selected" : Friendly(_mapper.SelectedDestination);
        MapperCurrentBinding.Text = _mapper.SelectedSource is not null && _mapper.Mappings.TryGetValue(_mapper.SelectedSource, out var current) ? $"Current binding: {Friendly(current)}" : "Current binding: none";
        MapperInstruction.Text = _mapper.Instruction;
        MapperRoutes.ItemsSource = _mapper.Routes;
        MappingChipList.ItemsSource = _mapper.Routes;
        if (_mapper.SelectedSource is { } selected)
        {
            var route = _mapper.Routes.FirstOrDefault(x => x.Source == selected);
            SelectedMappingPill.Text = $"{Friendly(selected)} → {(route is null ? "unmapped" : route.DestinationLabel)}";
            MappingChipList.SelectedItem = route;
        }
        else SelectedMappingPill.Text = "Select a control → unmapped";
        DestinationModeButton.IsChecked = IsDestinationCapture;
        DestinationModeButton.Content = IsDestinationCapture ? "Picking output…" : "Pick output on picture";
        CaptureButton.Content = _mapper.IsCapturing ? "Cancel listening" : "Listen for input";
        CaptureOutputButton.Content = IsDestinationCapture ? "Cancel listening" : "Listen for output";
        var groups = _mapper.DestinationGroups.ToArray();
        var previous = DestinationGroup.SelectedItem as string;
        DestinationGroup.ItemsSource = groups;
        DestinationGroup.SelectedItem = previous is not null && groups.Contains(previous) ? previous : groups.FirstOrDefault();
        RefreshDestinationTargets();
        UpdateMapperSurfaces();
        }
        finally { _refreshingMapper = false; }
    }

    private void RefreshDestinationTargets()
    {
        var group = DestinationGroup.SelectedItem as string;
        var targets = _mapper.Destinations.Where(x => group is null || x.Group == group).ToArray();
        DestinationTarget.ItemsSource = targets;
        DestinationTarget.SelectedItem = targets.FirstOrDefault(x => x.Code == _mapper.SelectedDestination);
    }

    private void UpdateMapperSurfaces()
    {
        var state = _mapper.CreateSurfaceState(_vm.Snapshot);
        ControllerMapper.UpdateState(state); MouseMapper.UpdateState(state); KeyboardMapper.UpdateState(state);
        LeftStickPlot.UpdatePosition(_vm.Snapshot.Input.LeftX, _vm.Snapshot.Input.LeftY);
        RightStickPlot.UpdatePosition(_vm.Snapshot.Input.RightX, _vm.Snapshot.Input.RightY);
    }

    private void ShowMotionAxes_Click(object? sender, RoutedEventArgs e) => ControllerMapper.ShowMotionAxes = ShowMotionAxes.IsChecked == true;

    private void MapperControlInvoked(VisualControlInvocation invocation)
    {
        if (invocation.Kind == VisualInvocationKind.OpenStickTuning)
        {
            _ = OpenStickTuning(invocation.Code);
            return;
        }
        if (invocation.Kind == VisualInvocationKind.OpenMotionAxis)
        {
            _ = OpenMotionAxis(Friendly(invocation.Code));
            return;
        }
        if (IsDestinationCapture) AcceptCapturedControl(invocation.Code);
        else _mapper.SelectSource(invocation.Code);
    }

    private static string DestinationCode(string code)
    {
        if (code.StartsWith("Keyboard:", StringComparison.OrdinalIgnoreCase)) return "Desktop:Key:" + code[9..];
        if (code.StartsWith("Mouse:Wheel ", StringComparison.OrdinalIgnoreCase)) return "Desktop:Wheel:" + code[12..];
        if (code.StartsWith("Mouse:", StringComparison.OrdinalIgnoreCase) && !code.Equals("Mouse:Movement", StringComparison.OrdinalIgnoreCase))
            return "Desktop:Mouse:" + code[6..];
        return code;
    }

    private void MapperProfile_SelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (!_reloadingProfiles && MapperProfile.SelectedItem is string name && name != _vm.Profiles.ActiveName) Guard(() => _vm.Profiles.Select(name));
    }
    private void OpenProfiles_Click(object? sender, RoutedEventArgs e) => MainTabs.SelectedIndex = 7;
    private void OpenRoutes_Click(object? sender, RoutedEventArgs e) => MainTabs.SelectedIndex = 8;
    private void OpenProfileSettings_Click(object? sender, RoutedEventArgs e) => MainTabs.SelectedIndex = 12;
    private void InputSourcePicker_SelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (InputSourcePicker.SelectedItem is InputDeviceInfo d && d.StableId != _vm.Snapshot.SelectedSourceId)
            Feedback("The runtime selects the active source automatically; move that controller to make it active.");
    }
    private void QuickMouseToggle_Click(object? sender, RoutedEventArgs e) => Guard(() =>
    {
        var mouse = Obj(Profile, "controller_mouse"); mouse["enabled"] = QuickMouseToggle.IsChecked == true; _vm.Profiles.Save();
    });
    private void ThemePreset_SelectionChanged(object? sender, SelectionChangedEventArgs e) { if (ThemePreset.SelectedItem is string name && name != _theme.Settings.Preset) _theme.SelectPreset(name); }
    private void ApplyAccent_Click(object? sender, RoutedEventArgs e)
    {
        var value = Text(AccentColor); if (!Avalonia.Media.Color.TryParse(value, out _)) { Feedback("Accent must be a color such as #4B91F1."); return; } _theme.SetAccent(value);
    }
    private void DensityPicker_SelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (DensityPicker.SelectedItem is ComboBoxItem item && item.Content is string density) { ApplyDensity(density); if (density != _theme.Settings.Density) _theme.SetDensity(density); }
    }
    private void ApplyDensity(string density) => RootGrid.Margin = new Thickness(density switch { "Compact" => 8, "Relaxed" => 20, _ => 14 });
    private string _sourceSignature = "";
    private static readonly Lazy<string?> DeadbandExecutable = new(() => FindExecutable("deadband"));
    private static string? FindExecutable(string name)
    {
        foreach (var directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
        { var candidate = Path.Combine(directory, name); if (File.Exists(candidate)) return candidate; }
        return null;
    }
    private void OpenDeadband_Click(object? sender, RoutedEventArgs e)
    {
        var executable = FindExecutable("deadband"); if (executable is null) return;
        try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(executable) { UseShellExecute = false }); }
        catch (Exception ex) { Feedback("Could not open Deadband: " + ex.Message); }
    }
    private void MapperCapture_Click(object? sender, RoutedEventArgs e) { if (_mapper.IsCapturing) _mapper.CancelCapture(); else _mapper.BeginCapture(); }
    private void MapperOutputCapture_Click(object? sender, RoutedEventArgs e) { if (IsDestinationCapture) CancelDestinationCapture(); else BeginDestinationCapture(); }
    private void MapperClear_Click(object? sender, RoutedEventArgs e) => _mapper.ClearSelection();
    private void MapperDestinationMode_Click(object? sender, RoutedEventArgs e) { if (IsDestinationCapture) CancelDestinationCapture(); else BeginDestinationCapture(); }
    private void MapperCancel_Click(object? sender, RoutedEventArgs e) { _mapper.CancelCapture(); _mapper.CancelDestinationMode(); MapperFeedback.Text = "Listening cancelled; the current selection was kept."; }
    private void MapperAssign_Click(object? sender, RoutedEventArgs e) => ShowMapperResult(_mapper.AssignSelected((int)(MapperSlot.Value ?? 1)));
    private void MapperRemove_Click(object? sender, RoutedEventArgs e) => ShowMapperResult(_mapper.RemoveSelectedRoute());
    private void NintendoLayout_Click(object? sender, RoutedEventArgs e) => ShowMapperResult(ApplyNintendoLayout());
    private void ShowMapperResult(BindingMutationResult result) { MapperFeedback.Text = result.Message; MapperFeedback.Foreground = Avalonia.Media.Brushes.OrangeRed; if (result.Succeeded) { MapperFeedback.Foreground = Avalonia.Media.Brushes.LightGreen; ReloadProfileUi(); } }
    private void DestinationGroup_SelectionChanged(object? sender, SelectionChangedEventArgs e) { if (!_refreshingMapper) RefreshDestinationTargets(); }
    private void DestinationTarget_SelectionChanged(object? sender, SelectionChangedEventArgs e) { if (!_refreshingMapper && DestinationTarget.SelectedItem is BindingTarget target) _mapper.SelectDestination(target.Code); }
    private void MapperRoute_SelectionChanged(object? sender, SelectionChangedEventArgs e) { if (MapperRoutes.SelectedItem is VisualBindingRoute route) { _mapper.CancelDestinationMode(); _mapper.SelectSource(route.Source); MapperSlot.Value = route.Slot; } }
    private void MappingChipList_SelectionChanged(object? sender, SelectionChangedEventArgs e) { if (!_refreshingMapper && MappingChipList.SelectedItem is VisualBindingRoute route) { _mapper.CancelDestinationMode(); _mapper.SelectSource(route.Source); MapperSlot.Value = route.Slot; } }
    private static string Friendly(string code)
    {
        if (code.StartsWith("Desktop:Key:", StringComparison.Ordinal)) return "Keyboard · " + code[12..];
        if (code.StartsWith("Desktop:Mouse:", StringComparison.Ordinal)) return "Mouse · " + code[14..];
        if (code.StartsWith("KeyboardDevice:", StringComparison.Ordinal)) return "Keyboard · " + code[(code.LastIndexOf(':') + 1)..];
        if (code.StartsWith("MouseDevice:", StringComparison.Ordinal)) return "Mouse · " + code[(code.LastIndexOf(':') + 1)..];
        return code.Contains(':') ? code[(code.IndexOf(':') + 1)..] : code;
    }

    private void UpdateLivePhysicalInput()
    {
        var source = _vm.Snapshot.LastInputSource;
        LivePhysicalInputText.Text = string.IsNullOrWhiteSpace(source) ? "Live physical input: idle" : "Live physical input: " + Friendly(source);
    }

    private void Window_KeyDown(object? sender, KeyEventArgs e)
    {
        if (!_mapper.IsCapturing && !IsDestinationCapture) return;
        var code = CanonicalKey(e.Key);
        if (code is null) return;
        AcceptCapturedControl("Keyboard:" + code);
        _capturedKeyRelease = e.Key;
        e.Handled = true;
    }

    private void Window_KeyUp(object? sender, KeyEventArgs e)
    {
        if (_capturedKeyRelease != e.Key) return;
        _capturedKeyRelease = null;
        e.Handled = true;
    }

    private static string? CanonicalKey(Key key) => key switch
    {
        Key.Escape => "Esc", Key.Oem3 => "Backtick", Key.OemMinus => "Minus", Key.OemPlus => "Equals",
        Key.OemOpenBrackets => "Left Bracket", Key.OemCloseBrackets => "Right Bracket", Key.OemPipe => "Backslash",
        Key.OemSemicolon => "Semicolon", Key.OemQuotes => "Apostrophe", Key.OemComma => "Comma", Key.OemPeriod => "Period", Key.OemQuestion => "Slash",
        Key.LeftShift => "Left Shift", Key.RightShift => "Right Shift", Key.LeftCtrl => "Left Ctrl", Key.RightCtrl => "Right Ctrl",
        Key.LeftAlt => "Left Alt", Key.RightAlt => "Right Alt", Key.LWin => "Left Win", Key.RWin => "Right Win",
        Key.PageUp => "Page Up", Key.PageDown => "Page Down", Key.PrintScreen => "Print Screen", Key.Scroll => "Scroll Lock",
        Key.NumLock => "Num Lock", Key.Divide => "Numpad Divide", Key.Multiply => "Numpad Multiply", Key.Subtract => "Numpad Minus",
        Key.Add => "Numpad Plus", Key.Decimal => "Numpad Decimal",
        >= Key.NumPad0 and <= Key.NumPad9 => "Numpad" + ((int)key - (int)Key.NumPad0),
        >= Key.D0 and <= Key.D9 => ((int)key - (int)Key.D0).ToString(),
        >= Key.A and <= Key.Z or >= Key.F1 and <= Key.F12 => key.ToString(),
        Key.System or Key.None => null,
        _ => key.ToString()
    };

    private void LoadMapMod()
    {
        var mod = Profile["mod_key"] as JsonObject;
        MapModSource.Text = Str(mod, "source");
        Select(MapModBehavior, Str(mod, "behavior", "toggle"));
    }

    private void SaveMapMod_Click(object? sender, RoutedEventArgs e) => Guard(() =>
    {
        Profile["mod_key"] = new JsonObject { ["source"] = Text(MapModSource), ["behavior"] = Combo(MapModBehavior, "toggle") };
        _vm.Profiles.Save();
    });

    private async void OpenStickTuning_Click(object? sender, RoutedEventArgs e) => await OpenStickTuning("left");
    private async void OpenMotionAxis_Click(object? sender, RoutedEventArgs e) => await OpenMotionAxis("yaw");
    private async Task OpenStickTuning(string side)
    {
        var selectedSide = side.Contains("right", StringComparison.OrdinalIgnoreCase) ? "right" : "left";
        await new StickTuningDialog(_vm.Profiles, _vm.Runtime, selectedSide).ShowDialog(this);
        ReloadProfileUi();
    }
    private async Task OpenMotionAxis(string axis)
    {
        var canonical = axis.ToLowerInvariant() switch { var x when x.Contains("pitch") => "pitch", var x when x.Contains("roll") => "roll", _ => "yaw" };
        await new MotionAxisDialog(_vm.Profiles, _vm.Runtime, canonical).ShowDialog(this);
        ReloadProfileUi();
    }

    private bool IsDestinationCapture => _mapper.IsCapturingDestination;
    private void BeginDestinationCapture() => _mapper.BeginDestinationCapture();
    private void CancelDestinationCapture() => _mapper.CancelCapture();
    private void AcceptCapturedControl(string code) => _mapper.AcceptCapturedControl(code);
    private BindingMutationResult ApplyNintendoLayout() => _mapper.ApplyNintendoFaceLayout();
    private void ProfileList_SelectionChanged(object? s, SelectionChangedEventArgs e)
    { if (!_reloadingProfiles && ProfileList.SelectedItem is string n && n != _vm.Profiles.ActiveName) Guard(() => _vm.Profiles.Select(n)); }
    private void Duplicate_Click(object? s, RoutedEventArgs e) => Guard(() => _vm.Profiles.Duplicate(_vm.Profiles.ActiveName, Text(ProfileNameBox)));
    private void Rename_Click(object? s, RoutedEventArgs e) => Guard(() => _vm.Profiles.Rename(_vm.Profiles.ActiveName, Text(ProfileNameBox)));
    private async void Delete_Click(object? s, RoutedEventArgs e)
    {
        var dialog = new Window { Title = "Delete profile?", Width = 390, Height = 170, CanResize = false, WindowStartupLocation = WindowStartupLocation.CenterOwner };
        var yes = new Button { Content = "Delete" }; yes.Classes.Add("danger"); var no = new Button { Content = "Cancel" }; no.Classes.Add("pill");
        yes.Click += (_, _) => dialog.Close(true); no.Click += (_, _) => dialog.Close(false);
        dialog.Content = new StackPanel { Margin = new Thickness(18), Spacing = 14, Children = { new TextBlock { Text = $"Delete profile ‘{_vm.Profiles.ActiveName}’? This cannot be undone.", TextWrapping = Avalonia.Media.TextWrapping.Wrap }, new StackPanel { Orientation = Avalonia.Layout.Orientation.Horizontal, Spacing = 8, HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Right, Children = { no, yes } } } };
        if (await dialog.ShowDialog<bool>(this)) Guard(() => _vm.Profiles.Delete(_vm.Profiles.ActiveName));
    }
    private async void Import_Click(object? s, RoutedEventArgs e)
    {
        var files = await StorageProvider.OpenFilePickerAsync(new() { Title = "Import profile document", AllowMultiple = false,
            FileTypeFilter = [new("JSON") { Patterns = ["*.json"] }] });
        if (files.Count > 0) Guard(() => _vm.Profiles.Import(files[0].Path.LocalPath));
    }
    private async void Export_Click(object? s, RoutedEventArgs e)
    {
        var file = await StorageProvider.SaveFilePickerAsync(new() { Title = "Export profile", SuggestedFileName = _vm.Profiles.ActiveName + ".json",
            FileTypeChoices = [new("JSON") { Patterns = ["*.json"] }] });
        if (file is not null) Guard(() => _vm.Profiles.Export(_vm.Profiles.ActiveName, file.Path.LocalPath));
    }

    private sealed record RouteRow(string Source, string Destination, string Condition, string Activator, string Effect, int Slot, bool Typed, JsonObject? Node);
    private void ReloadRoutes()
    {
        var rows = new List<RouteRow>();
        if (Profile["routes"] is JsonObject routes) foreach (var r in routes)
        {
            var conditions = Profile["route_conditions"]?[r.Key] as JsonObject;
            var activator = Profile["route_activators"]?[r.Key]?["type"]?.ToString() ?? "press";
            var effect = Profile["route_effects"]?[r.Key]?["type"]?.ToString() ?? "normal";
            var slot = Profile["route_output_slots"]?[r.Key]?.GetValue<int>() ?? 1;
            rows.Add(new(r.Key, r.Value?.ToString() ?? "", Condition(conditions), activator, effect, slot, false, null));
        }
        if (Profile["typed_routes"] is JsonArray typed) foreach (var n in typed.OfType<JsonObject>())
            rows.Add(new(n["source"]?.ToString() ?? "", n["destination"]?.ToString() ?? "", Condition(n["condition"] as JsonObject), n["activator"]?["type"]?.ToString() ?? "press", n["effect"]?["type"]?.ToString() ?? "normal", n["output_slot"]?.GetValue<int>() ?? 1, true, n));
        RoutesGrid.ItemsSource = rows;
    }
    private static string Condition(JsonObject? c) => c is null ? "Any" : (c["layer"]?.ToString() ?? "Any") + (c["chord"] is JsonArray a && a.Count > 0 ? " + " + string.Join(",", a.Select(x => x?.ToString())) : "");
    private void Listen_Click(object? s, RoutedEventArgs e) { _listening = !_listening; _listenAfter = _vm.Snapshot.LastInputTimestamp; Feedback(_listening ? "Listening to actual runtime input…" : "Listening stopped."); }
    private void ListenFrame()
    {
        var source = ProfileEditorService.Listen(_vm.Snapshot, _listenAfter);
        if (source is null) return;
        RouteSource.Text = source; _listening = false; Feedback("Captured " + source);
    }
    private void AddRoute_Click(object? s, RoutedEventArgs e) => Guard(() =>
    {
        var source = Text(RouteSource); var destination = Text(RouteDestination); if (source.Length == 0 || destination.Length == 0) throw new InvalidOperationException("Source and destination are required.");
        var layer = Combo(RouteLayer, "Any"); var chord = Text(RouteChord).Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
        ProfileEditorService.SetRoute(Profile, source, destination, layer == "Any" ? null : layer, chord,
            Combo(RouteActivator, "while-held"), Combo(RouteEffect, "normal"), (int)(RouteSlot.Value ?? 1),
            (double)(TurboRate.Value ?? 12), (double)(TurboDuty.Value ?? 50) / 100); _vm.Profiles.Save();
    });
    private void RemoveRoute_Click(object? s, RoutedEventArgs e) => Guard(() =>
    {
        if (RoutesGrid.SelectedItem is not RouteRow row) throw new InvalidOperationException("Select a route.");
        if (row.Typed) (Profile["typed_routes"] as JsonArray)?.Remove(row.Node); else { (Profile["routes"] as JsonObject)?.Remove(row.Source); foreach (var k in new[] { "route_conditions", "route_activators", "route_effects", "route_output_slots" }) (Profile[k] as JsonObject)?.Remove(row.Source); }
        _vm.Profiles.Save();
    });

    private void ReloadMacros() { MacroList.ItemsSource = (Profile["macros"] as JsonObject)?.Select(x => x.Key).ToArray() ?? []; }
    private void MacroList_SelectionChanged(object? s, SelectionChangedEventArgs e)
    {
        if (MacroList.SelectedItem is not string name || Profile["macros"]?[name] is not JsonObject m) return;
        MacroName.Text = name; MacroRepeats.Value = m["repeat_count"]?.GetValue<int>() ?? 1; MacroCancel.IsChecked = m["cancel_on_release"]?.GetValue<bool>() ?? false;
        _steps = m["steps"] as JsonArray ?? []; StepsGrid.ItemsSource = StepRows(_steps);
    }
    private static object[] StepRows(JsonArray steps) => steps.OfType<JsonObject>().Select((x, i) => new { Number = i + 1, Type = x["type"]?.ToString(), Target = x["target"]?.ToString() ?? x["action"]?.ToString(), Value = x["value"]?.ToString(), Milliseconds = x["duration_ms"]?.ToString() }).ToArray();
    private void NewMacro_Click(object? s, RoutedEventArgs e) { MacroName.Text = "New macro"; _steps = []; StepsGrid.ItemsSource = StepRows(_steps); }
    private void DeleteMacro_Click(object? s, RoutedEventArgs e) => Guard(() => { if (MacroList.SelectedItem is string n) { (Profile["macros"] as JsonObject)?.Remove(n); _vm.Profiles.Save(); } });
    private void AddStep_Click(object? s, RoutedEventArgs e)
    {
        _steps ??= []; var step = new JsonObject { ["type"] = Combo(StepType, "press") };
        if (Text(StepTarget).Length > 0) step[step["type"]?.ToString() == "action" ? "action" : "target"] = Text(StepTarget);
        if (double.TryParse(Text(StepValue), out var value)) step["value"] = value; if (int.TryParse(Text(StepDuration), out var ms)) step["duration_ms"] = ms;
        _steps.Add(step); StepsGrid.ItemsSource = StepRows(_steps);
    }
    private void RemoveStep_Click(object? s, RoutedEventArgs e) { if (_steps is not null && StepsGrid.SelectedIndex >= 0) _steps.RemoveAt(StepsGrid.SelectedIndex); if (_steps is not null) StepsGrid.ItemsSource = StepRows(_steps); }
    private void MoveStepUp_Click(object? s, RoutedEventArgs e) => MoveStep(-1);
    private void MoveStepDown_Click(object? s, RoutedEventArgs e) => MoveStep(1);
    private void MoveStep(int delta) { if (_steps is null) return; var i = StepsGrid.SelectedIndex; var j = i + delta; if (i < 0 || j < 0 || j >= _steps.Count) return; var node = _steps[i]; _steps.RemoveAt(i); _steps.Insert(j, node); StepsGrid.ItemsSource = StepRows(_steps); StepsGrid.SelectedIndex = j; }
    private void SaveMacro_Click(object? s, RoutedEventArgs e) => Guard(() => { var name = Text(MacroName); if (name.Length == 0) throw new InvalidOperationException("Macro name required."); ProfileEditorService.SetMacro(Profile, name, (int)(MacroRepeats.Value ?? 1), MacroCancel.IsChecked == true, _steps ?? []); _vm.Profiles.Save(); });
    private void StopMacros_Click(object? s, RoutedEventArgs e) => _vm.Runtime.StopAllMacros();

    private void LoadStickAndMouse()
    {
        LoadStick("left", LeftEnabled, LeftInner, LeftOuter, LeftAnti, LeftCurve); LoadStick("right", RightEnabled, RightInner, RightOuter, RightAnti, RightCurve);
        LoadStickPlot("left", LeftStickPlot); LoadStickPlot("right", RightStickPlot);
        var m = Profile["controller_mouse"] as JsonObject; ControllerMouseEnabled.IsChecked = Bool(m, "enabled"); MouseToggle.Text = Str(m, "toggle_source"); MouseStick.Text = Str(m, "stick"); MouseSensitivity.Text = Str(m, "sensitivity"); MouseDeadzone.Text = Str(m, "deadzone"); MouseLeftClick.Text = Str(m, "left_click_source"); MouseRightClick.Text = Str(m, "right_click_source"); MouseScrollUp.Text = Str(m, "scroll_up_source"); MouseScrollDown.Text = Str(m, "scroll_down_source");
    }
    private void LoadStick(string side, CheckBox enabled, params TextBox[] b) { var s = Profile["stick_processing"]?[side] as JsonObject; enabled.IsChecked = Bool(s, "enabled"); b[0].Text = Str(s, "inner_deadzone"); b[1].Text = Str(s, "outer_deadzone"); b[2].Text = Str(s, "anti_deadzone"); b[3].Text = Str(s, "curve"); }
    private void LoadStickPlot(string side, UnifiedController.Linux.App.Controls.StickResponsePlot plot)
    {
        var s = Profile["stick_processing"]?[side] as JsonObject;
        plot.UpdateSettings(Bool(s, "enabled"), s?["inner_deadzone"]?.GetValue<double>() ?? .08,
            s?["outer_deadzone"]?.GetValue<double>() ?? 1, s?["anti_deadzone"]?.GetValue<double>() ?? 0,
            s?["curve"]?.GetValue<double>() ?? 1);
    }
    private void SaveSticks_Click(object? s, RoutedEventArgs e) => Guard(() => { ProfileEditorService.SetStick(Profile, "left", LeftEnabled.IsChecked == true, Text(LeftInner), Text(LeftOuter), Text(LeftAnti), Text(LeftCurve)); ProfileEditorService.SetStick(Profile, "right", RightEnabled.IsChecked == true, Text(RightInner), Text(RightOuter), Text(RightAnti), Text(RightCurve)); _vm.Profiles.Save(); });
    private void SaveMouse_Click(object? s, RoutedEventArgs e) => Guard(() => { var m = Obj(Profile, "controller_mouse"); m["enabled"] = ControllerMouseEnabled.IsChecked == true; String(m, "toggle_source", MouseToggle); String(m, "stick", MouseStick); Number(m, "sensitivity", MouseSensitivity); Number(m, "deadzone", MouseDeadzone); String(m, "left_click_source", MouseLeftClick); String(m, "right_click_source", MouseRightClick); String(m, "scroll_up_source", MouseScrollUp); String(m, "scroll_down_source", MouseScrollDown); _vm.Profiles.Save(); });

    private void LoadMotion() { var m = Profile["motion"] as JsonObject; Select(MotionPreset, Str(m, "preset", "native")); MotionLeftConfig.Text = m?["left"]?.ToJsonString(new() { WriteIndented = true }) ?? "{}"; MotionRightConfig.Text = m?["right"]?.ToJsonString(new() { WriteIndented = true }) ?? "{}"; }
    private void SaveMotion_Click(object? s, RoutedEventArgs e) => Guard(() => { var left = JsonNode.Parse(Text(MotionLeftConfig))?.AsObject() ?? new JsonObject(); var right = JsonNode.Parse(Text(MotionRightConfig))?.AsObject() ?? new JsonObject(); ProfileEditorService.SetMotion(Profile, Combo(MotionPreset, "native"), left, right); _vm.Profiles.Save(); });

    private void LoadProfileSettings()
    {
        Select(SourceMergePolicy, Str(Profile, "source_merge_policy", "first_active"));
        var mod = Profile["mod_key"] as JsonObject; ModSource.Text = Str(mod, "source"); Select(ModBehavior, Str(mod, "behavior", "toggle"));
        var enabled = (Profile["enabled_output_slots"] as JsonArray)?.Select(x => x?.ToString()).ToHashSet() ?? [];
        Output1.IsChecked = enabled.Contains("1"); Output2.IsChecked = enabled.Contains("2"); Output3.IsChecked = enabled.Contains("3"); Output4.IsChecked = enabled.Contains("4");
        DeviceSlots.Text = Profile["device_output_slots"]?.ToJsonString() ?? "{}";
    }
    private void SaveProfileSettings_Click(object? s, RoutedEventArgs e) => Guard(() =>
    {
        Profile["source_merge_policy"] = Combo(SourceMergePolicy, "first_active");
        Profile["mod_key"] = new JsonObject { ["source"] = Text(ModSource), ["behavior"] = Combo(ModBehavior, "toggle") };
        Profile["enabled_output_slots"] = new JsonArray(new[] { (Output1, 1), (Output2, 2), (Output3, 3), (Output4, 4) }.Where(x => x.Item1.IsChecked == true).Select(x => (JsonNode?)JsonValue.Create(x.Item2)).ToArray());
        Profile["device_output_slots"] = JsonNode.Parse(Text(DeviceSlots))?.AsObject() ?? new JsonObject(); _vm.Profiles.Save();
    });
    private void Calibrate_Click(object? s, RoutedEventArgs e) => _vm.Runtime.Calibrate();
    private void Recenter_Click(object? s, RoutedEventArgs e) => _vm.Runtime.Recenter();

    private sealed record DeviceCard(string StableId, string Name, string Identity, string Status);
    private object[] DeviceCards() => _vm.Snapshot.Devices.Select(d => (object)new DeviceCard(d.StableId, d.Name, $"{(d.IsVirtual ? "Virtual controller" : "Physical controller")} · {d.VendorId:X4}:{d.ProductId:X4}", d.IsVirtual ? "Virtual" : d.Connected ? "Connected" : "Disconnected")).ToArray();
    private void LoadDevices() { DevicesGrid.ItemsSource = DeviceCards(); var c = _vm.Runtime.Configuration; PairList.ItemsSource = c?.JoyConPairs; ExclusiveCapture.IsChecked = c?.ExclusiveCapture ?? false; ExclusiveCapture.IsEnabled = c is not null; KeyboardCapture.IsChecked = _vm.Runtime.KeyboardCapture; MouseCapture.IsChecked = _vm.Runtime.MouseCapture; }
    private void Pair_Click(object? s, RoutedEventArgs e) { try { (_vm.Runtime.Configuration ?? throw new NotSupportedException("Provider does not support Joy-Con pairing.")).PairJoyCons(Text(LeftJoyCon), Text(RightJoyCon)); LoadDevices(); } catch (Exception ex) { Feedback(ex.Message); } }
    private void Unpair_Click(object? s, RoutedEventArgs e) { if (PairList.SelectedItem is UnifiedController.Core.JoyConPairInfo p) { _vm.Runtime.Configuration?.UnpairJoyCons(p.Id); LoadDevices(); } }
    private void Capture_Click(object? s, RoutedEventArgs e) { var c = _vm.Runtime.Configuration; if (c is not null) c.ExclusiveCapture = ExclusiveCapture.IsChecked == true; _vm.Runtime.KeyboardCapture = KeyboardCapture.IsChecked == true; _vm.Runtime.MouseCapture = MouseCapture.IsChecked == true; }

    private async void Start_Click(object? s, RoutedEventArgs e) { try { await _vm.Runtime.Start(); } catch (Exception ex) { Feedback(ex.Message); } }
    private async void Stop_Click(object? s, RoutedEventArgs e) { await _vm.Runtime.Stop(); }
    private void Hide_Click(object? s, RoutedEventArgs e) => Hide();
    private void OnClosing(object? s, WindowClosingEventArgs e) { if (!_exiting) { e.Cancel = true; Hide(); } }
    private async void Exit_Click(object? s, RoutedEventArgs e)
    {
        _exiting = true;
        try { await Program.SafeShutdownAsync(); }
        catch (Exception ex) { Feedback("Shutdown error: " + ex.Message); }
        finally { if (Program.Runtime is { } runtime) runtime.ActionRequested -= Runtime_ActionRequested; _vm.Dispose(); (Application.Current?.ApplicationLifetime as Avalonia.Controls.ApplicationLifetimes.IClassicDesktopStyleApplicationLifetime)?.Shutdown(); }
    }

    private static JsonObject Obj(JsonObject owner, string key) { if (owner[key] is JsonObject o) return o; o = []; owner[key] = o; return o; }
    private static string Str(JsonObject? o, string key, string fallback = "") => o?[key]?.ToString() ?? fallback;
    private static bool Bool(JsonObject? o, string key) => o?[key]?.GetValue<bool>() ?? false;
    private static void String(JsonObject o, string k, TextBox b) { if (Text(b).Length > 0) o[k] = Text(b); }
    private static void Number(JsonObject o, string k, TextBox b) { if (Text(b).Length > 0) { if (!double.TryParse(Text(b), out var n)) throw new InvalidOperationException($"{k} must be numeric."); o[k] = n; } }
    private static string Combo(ComboBox b, string fallback) => (b.SelectedItem as ComboBoxItem)?.Content?.ToString() ?? fallback;
    private static void Select(ComboBox b, string value) { b.SelectedItem = b.Items.OfType<ComboBoxItem>().FirstOrDefault(i => i.Content?.ToString() == value); }
}
