using System.Text.Json.Nodes;
using UnifiedController.App.Engine;
using UnifiedController.Core;
using UnifiedController.Linux.App.Models;
using UnifiedController.Linux.App.Services;
using UnifiedController.Linux.App.ViewModels;

namespace UnifiedController.Ui.Tests;

public sealed class BindingWorkspaceViewModelTests
{
    [Fact]
    public void Visual_keyboard_assignment_executes_in_native_runtime_and_survives_lagging_snapshot()
    {
        using var fixture = new Fixture("""{"routes":{}}""");
        var vm = fixture.ViewModel;
        vm.SelectSource("Keyboard:W");
        vm.ObserveSnapshot(Snap(default) with { ActiveProfile = "Old", Running = false });
        Assert.Equal("Keyboard:W", vm.SelectedSource);
        vm.SelectDestination("Controller:A");
        Assert.True(vm.AssignSelected().Succeeded);
        var native = NativeProfileRuntime.Load(fixture.Service.Path);
        var report = new NativeControllerReport();
        var keys = new HashSet<int> { 'W' };
        var active = native.BuildActiveSources(keys, new HashSet<string>(), new HashSet<string>(), report, new HashSet<string>());
        native.ApplyKeyboard(keys, ref report, 0, active);
        Assert.Equal(0x1000, report.Buttons);
    }

    [Fact]
    public void Visual_selection_persists_native_routes_without_discarding_advanced_metadata()
    {
        using var fixture = new Fixture("""{"routes":{"Keyboard:A":"Controller:B"},"route_conditions":{"Keyboard:A":{"layer":"shift","future":7}},"route_effects":{"Keyboard:A":{"type":"turbo","rate_hz":9}},"future_profile":"keep"}""");
        var vm = fixture.ViewModel;
        vm.SelectSource("Keyboard:A");
        vm.BeginDestinationMode();
        vm.SelectDestination("Controller:X");
        Assert.Equal(MapperInteractionMode.Source, vm.InteractionMode);
        Assert.True(vm.AssignSelected().Succeeded);

        var native = NativeProfileRuntime.Load(fixture.Service.Path);
        Assert.Equal(1, native.RouteCount);
        Assert.Equal("Controller:X", fixture.Service.ActiveProfile["routes"]!["Keyboard:A"]!.GetValue<string>());
        Assert.Equal(7, fixture.Service.ActiveProfile["route_conditions"]!["Keyboard:A"]!["future"]!.GetValue<int>());
        Assert.Equal(9, fixture.Service.ActiveProfile["route_effects"]!["Keyboard:A"]!["rate_hz"]!.GetValue<int>());
        Assert.Equal("keep", fixture.Service.ActiveProfile["future_profile"]!.GetValue<string>());
    }

    [Fact]
    public void Source_clicks_do_not_change_roles_and_removal_is_native_valid()
    {
        using var fixture = new Fixture("""{"routes":{"Controller:A":"Desktop:Key:Space"},"route_activators":{"Controller:A":{"type":"tap"}}}""");
        var vm = fixture.ViewModel;
        vm.SelectSource("Controller:A");
        Assert.Equal(MapperInteractionMode.Source, vm.InteractionMode);
        Assert.Equal("Desktop:Key:Space", vm.SelectedDestination);
        Assert.True(vm.RemoveSelectedRoute().Succeeded);
        Assert.Empty(vm.Mappings);
        Assert.Equal(0, NativeProfileRuntime.Load(fixture.Service.Path).RouteCount);
    }

    [Fact]
    public void Capture_waits_for_neutral_ignores_stale_desktop_events_and_cancel_writes_nothing()
    {
        using var fixture = new Fixture("""{"routes":{}}""");
        var vm = fixture.ViewModel;
        fixture.Runtime.Current = Snap(default) with { LastInputSource = "KeyboardDevice:raw-kbd:A", LastInputTimestamp = 10 };
        vm.BeginCapture();
        vm.ObserveSnapshot(fixture.Runtime.Current);
        Assert.Null(vm.SelectedSource);
        vm.ObserveSnapshot(Snap(default) with { LastInputTimestamp = 10 });
        vm.ObserveSnapshot(Snap(default) with { LastInputSource = "KeyboardDevice:raw-kbd:A", LastInputTimestamp = 10 });
        Assert.Null(vm.SelectedSource);
        vm.CancelCapture();
        Assert.Empty(vm.Mappings);
    }

    [Fact]
    public void Catalog_roundtrips_numpad_navigation_and_scoped_sources_highlight_generically()
    {
        using var fixture = new Fixture("""{"routes":{"KeyboardDevice:raw-kbd:Numpad7":"Controller:A"}}""");
        var vm = fixture.ViewModel;
        Assert.Contains(vm.Destinations, x => x.Code == "Desktop:Key:Numpad7");
        Assert.Contains(vm.Destinations, x => x.Code == "Desktop:Key:Page Down");
        var surface = vm.CreateSurfaceState(Snap(default) with { ActiveDesktopControls = new HashSet<string> { "KeyboardDevice:raw-kbd:Numpad7" } });
        Assert.Contains("Keyboard:Numpad7", surface.ActiveDesktopControls);
        Assert.Equal("Controller:A", surface.Routes["Keyboard:Numpad7"]);
    }

    [Fact]
    public void Desktop_destinations_use_surface_vocabulary_for_visual_highlighting()
    {
        using var fixture = new Fixture("""{"routes":{"Controller:A":"Desktop:Key:Numpad7","Controller:B":"Desktop:Wheel:Up"}}""");
        var vm = fixture.ViewModel;
        vm.SelectSource("Controller:A");
        var surface = vm.CreateSurfaceState(Snap(default));
        Assert.Equal("Keyboard:Numpad7", surface.SelectedDestination);
        Assert.Equal("Keyboard:Numpad7", surface.Routes["Controller:A"]);
        Assert.Equal("Mouse:Wheel Up", surface.Routes["Controller:B"]);
    }

    [Fact]
    public void Clear_selection_clears_both_roles_and_capture_state()
    {
        using var fixture = new Fixture("""{"routes":{"Controller:A":"Controller:B"}}""");
        var vm = fixture.ViewModel;
        vm.SelectSource("Controller:A");
        vm.BeginCapture();
        vm.ClearSelection();
        Assert.Null(vm.SelectedSource);
        Assert.Null(vm.SelectedDestination);
        Assert.False(vm.IsCapturing);
        Assert.Equal(MapperInteractionMode.Source, vm.InteractionMode);
    }

    [Fact]
    public void Input_then_output_capture_stages_until_save_and_native_runtime_applies_route()
    {
        using var fixture = new Fixture("""{"routes":{}}""");
        var vm = fixture.ViewModel;
        vm.BeginCapture();
        vm.ObserveSnapshot(Snap(default));
        vm.ObserveSnapshot(Snap(default) with { Input = new ControllerFrame { Buttons = 0x1000 } });
        Assert.Equal("Controller:A", vm.SelectedSource);

        vm.BeginDestinationCapture();
        vm.ObserveSnapshot(Snap(default));
        vm.ObserveSnapshot(Snap(default) with { Input = new ControllerFrame { Buttons = 0x2000 } });
        Assert.Equal("Controller:B", vm.SelectedDestination);
        Assert.Empty(NativeProfileRuntime.Load(fixture.Service.Path).BuildActiveSources(new HashSet<int>(), new HashSet<string>(), new HashSet<string>(), default, new HashSet<string>()));

        Assert.True(vm.AssignSelected().Succeeded);
        var native = NativeProfileRuntime.Load(fixture.Service.Path);
        var report = new NativeControllerReport { Buttons = 0x1000 };
        var active = native.BuildActiveSources(new HashSet<int>(), new HashSet<string>(), new HashSet<string>(), report, new HashSet<string>());
        native.ApplyControllerRoutes(report, new HashSet<string>(), ref report, 0, active);
        Assert.Equal(0x2000, report.Buttons);
    }

    [Fact]
    public void Output_capture_preserves_source_normalizes_focused_key_and_cancel_does_not_write()
    {
        using var fixture = new Fixture("""{"routes":{}}""");
        var vm = fixture.ViewModel;
        vm.SelectSource("Controller:A");
        vm.BeginDestinationCapture();
        vm.AcceptCapturedControl("KeyboardDevice:raw-kbd:Space");
        Assert.Equal("Controller:A", vm.SelectedSource);
        Assert.Equal("Desktop:Key:Space", vm.SelectedDestination);
        Assert.Empty(vm.Mappings);
        vm.BeginDestinationCapture();
        vm.CancelCapture();
        Assert.False(vm.IsCapturingDestination);
        Assert.Equal("Controller:A", vm.SelectedSource);
        Assert.Equal("Desktop:Key:Space", vm.SelectedDestination);
        Assert.Equal(0, NativeProfileRuntime.Load(fixture.Service.Path).RouteCount);
    }

    [Fact]
    public void Capture_treats_small_stick_drift_as_neutral_then_accepts_fresh_input()
    {
        using var fixture = new Fixture("""{"routes":{}}""");
        var vm = fixture.ViewModel;
        vm.BeginCapture();
        vm.ObserveSnapshot(Snap(new ControllerFrame { LeftX = 2000, RightY = -2000 }) with { LastInputTimestamp = 4 });
        vm.ObserveSnapshot(Snap(default) with { LastInputSource = "Keyboard:Q", LastInputTimestamp = 5 });
        Assert.Equal("Keyboard:Q", vm.SelectedSource);
        Assert.False(vm.IsCapturing);
    }

    [Fact]
    public void Nintendo_layout_is_atomic_native_and_preserves_advanced_and_unknown_metadata()
    {
        using var fixture = new Fixture("""{"routes":{"Controller:A":"Controller:X"},"route_conditions":{"Controller:A":{"layer":"mod","future":7}},"future_profile":"keep"}""");
        Assert.True(fixture.ViewModel.ApplyNintendoFaceLayout().Succeeded);
        var routes = fixture.Service.ActiveProfile["routes"]!.AsObject();
        Assert.Equal("Controller:B", routes["Controller:A"]!.GetValue<string>());
        Assert.Equal("Controller:A", routes["Controller:B"]!.GetValue<string>());
        Assert.Equal("Controller:Y", routes["Controller:X"]!.GetValue<string>());
        Assert.Equal("Controller:X", routes["Controller:Y"]!.GetValue<string>());
        Assert.Equal(7, fixture.Service.ActiveProfile["route_conditions"]!["Controller:A"]!["future"]!.GetValue<int>());
        Assert.Equal("keep", fixture.Service.ActiveProfile["future_profile"]!.GetValue<string>());
        Assert.Equal(4, NativeProfileRuntime.Load(fixture.Service.Path).RouteCount);
    }

    private static RuntimeSnapshot Snap(ControllerFrame input) => new("P", true, "ok", [], null, input, default, null, 0, 0, 0, null, []);

    private sealed class Fixture : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "uc-binding-" + Guid.NewGuid().ToString("N"));
        public ProfileDocumentService Service { get; }
        public FakeRuntime Runtime { get; } = new();
        public BindingWorkspaceViewModel ViewModel { get; }
        public Fixture(string profile)
        {
            Directory.CreateDirectory(Root);
            File.WriteAllText(Path.Combine(Root, "profiles.json"), $"{{\"version\":1,\"active_profile\":\"P\",\"profiles\":{{\"P\":{profile}}}}}");
            Service = new(Root); Runtime.Current = Snap(default); ViewModel = new(Service, Runtime);
        }
        public void Dispose() => Directory.Delete(Root, true);
    }

    private sealed class FakeRuntime : IControllerRuntime
    {
        public RuntimeSnapshot Current { get; set; } = Snap(default);
        public IControllerInputProvider ControllerInput => null!;
        public RuntimeSnapshot Snapshot => Current;
        public bool KeyboardCaptureEnabled { get; set; }
        public bool MouseCaptureEnabled { get; set; }
        public ValueTask StartAsync(CancellationToken token = default) => ValueTask.CompletedTask;
        public ValueTask StopAsync() => ValueTask.CompletedTask;
        public void StopAllMacros() { }
        public void RecenterMotion() { }
        public void CalibrateMotion() { }
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
