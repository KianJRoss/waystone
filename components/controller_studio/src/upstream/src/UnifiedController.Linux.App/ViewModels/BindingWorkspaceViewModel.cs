using System.Text.Json.Nodes;
using UnifiedController.Core;
using UnifiedController.Linux.App.Models;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.ViewModels;

/// <summary>Source-first, lossless binding workflow shared by the visual mapper surfaces.</summary>
public sealed class BindingWorkspaceViewModel
{
    private readonly ProfileDocumentService _profiles;
    private readonly IControllerRuntime _runtime;
    private string _profileName;
    private bool _awaitNeutral;
    private long _captureAfter;

    public event Action? Changed;
    public string? SelectedSource { get; private set; }
    public string? SelectedDestination { get; private set; }
    public MapperInteractionMode InteractionMode { get; private set; } = MapperInteractionMode.Source;
    public string Instruction => IsCapturing || IsCapturingDestination ? (_awaitNeutral ? "Release all controls to begin capture." : IsCapturingDestination ? "Press an output control." : "Press a source control.")
        : InteractionMode == MapperInteractionMode.Destination ? "Choose a destination." : SelectedSource is null ? "Choose a source control." : "Choose or capture a destination.";
    public IReadOnlyList<BindingTarget> Destinations { get; private set; } = [];
    public IReadOnlyList<string> DestinationGroups => Destinations.Select(x => x.Group).Distinct().ToArray();
    public IReadOnlyList<VisualBindingRoute> Routes { get; private set; } = [];
    public IReadOnlyDictionary<string, string> Mappings { get; private set; } = new Dictionary<string, string>();
    public bool IsCapturing { get; private set; }
    public bool IsCapturingDestination { get; private set; }

    public BindingWorkspaceViewModel(ProfileDocumentService profiles, IControllerRuntime runtime)
    {
        _profiles = profiles;
        _runtime = runtime;
        _profileName = profiles.ActiveName;
        Rebuild();
    }

    public void SelectSource(string source)
    {
        if (string.IsNullOrWhiteSpace(source)) return;
        CancelCaptureCore();
        SelectedSource = source;
        SelectedDestination = Mappings.TryGetValue(source, out var destination) ? destination : null;
        InteractionMode = MapperInteractionMode.Source;
        Notify();
    }

    public void BeginDestinationMode()
    {
        CancelCaptureCore();
        InteractionMode = MapperInteractionMode.Destination;
        Notify();
    }

    public void SelectDestination(string destination)
    {
        if (!Destinations.Any(x => x.Code.Equals(destination, StringComparison.OrdinalIgnoreCase))) return;
        SelectedDestination = destination;
        InteractionMode = MapperInteractionMode.Source;
        Notify();
    }

    public void CancelDestinationMode() { InteractionMode = MapperInteractionMode.Source; SelectedDestination = null; Notify(); }

    public void ClearSelection()
    {
        CancelCaptureCore();
        SelectedSource = SelectedDestination = null;
        InteractionMode = MapperInteractionMode.Source;
        Notify();
    }

    public void BeginCapture()
    {
        InteractionMode = MapperInteractionMode.Source;
        SelectedDestination = null;
        IsCapturing = true;
        IsCapturingDestination = false;
        _awaitNeutral = true;
        _captureAfter = _runtime.Snapshot.LastInputTimestamp;
        Notify();
    }

    public void BeginDestinationCapture()
    {
        InteractionMode = MapperInteractionMode.Destination;
        IsCapturing = false;
        IsCapturingDestination = true;
        _awaitNeutral = true;
        _captureAfter = _runtime.Snapshot.LastInputTimestamp;
        Notify();
    }

    /// <summary>Accepts the source vocabulary emitted by focused input or a device picture.</summary>
    public void AcceptCapturedControl(string code)
    {
        if (string.IsNullOrWhiteSpace(code) || (!IsCapturing && !IsCapturingDestination)) return;
        if (IsCapturingDestination)
        {
            var destination = ProfileEditorService.ToDestination(code);
            if (destination is null || !Destinations.Any(x => x.Code.Equals(destination, StringComparison.OrdinalIgnoreCase))) return;
            SelectedDestination = destination;
            InteractionMode = MapperInteractionMode.Source;
        }
        else
        {
            SelectedSource = code;
            SelectedDestination = Mappings.TryGetValue(code, out var target) ? target : null;
        }
        CancelCaptureCore();
        Notify();
    }

    public void CancelCapture() { CancelCaptureCore(); Notify(); }

    public BindingMutationResult AssignSelected(int slot = 1)
    {
        if (SelectedSource is null || SelectedDestination is null) return new(false, "Select both a source and destination.");
        if (IsMovementSource(SelectedSource) && !SelectedDestination.Equals("Controller:Right Stick", StringComparison.OrdinalIgnoreCase))
            return new(false, "Mouse movement can only target the controller right stick.", SelectedSource, SelectedDestination);
        var source = SelectedSource; var destination = SelectedDestination;
        try
        {
            _profiles.MutateActiveProfile(profile =>
            {
                ProfileEditorService.Obj(profile, "routes")[source] = destination;
                var slots = profile["route_output_slots"] as JsonObject;
                if (slot == 1) slots?.Remove(source); else ProfileEditorService.Obj(profile, "route_output_slots")[source] = Math.Clamp(slot, 1, 4);
            });
            Rebuild(); Notify();
            return new(true, "Binding assigned.", source, destination);
        }
        catch (Exception ex) { Rebuild(); Notify(); return new(false, ex.Message, source, destination); }
    }

    public BindingMutationResult RemoveSelectedRoute()
    {
        if (SelectedSource is null) return new(false, "Select a source binding to remove.");
        var source = SelectedSource;
        try
        {
            _profiles.MutateActiveProfile(profile =>
            {
                (profile["routes"] as JsonObject)?.Remove(source);
                foreach (var name in new[] { "route_conditions", "route_effects", "route_activators", "route_output_slots" })
                    (profile[name] as JsonObject)?.Remove(source);
            });
            SelectedDestination = null; Rebuild(); Notify();
            return new(true, "Binding removed.", source);
        }
        catch (Exception ex) { Rebuild(); Notify(); return new(false, ex.Message, source); }
    }

    public BindingMutationResult ApplyNintendoFaceLayout()
    {
        try
        {
            _profiles.MutateActiveProfile(profile =>
            {
                var routes = ProfileEditorService.Obj(profile, "routes");
                routes["Controller:A"] = "Controller:B";
                routes["Controller:B"] = "Controller:A";
                routes["Controller:X"] = "Controller:Y";
                routes["Controller:Y"] = "Controller:X";
            });
            Rebuild(); Notify();
            return new(true, "Nintendo face-button layout saved.");
        }
        catch (Exception ex) { Rebuild(); Notify(); return new(false, ex.Message); }
    }

    public void RefreshFromActiveProfile()
    {
        _profileName = _profiles.ActiveName;
        SelectedSource = SelectedDestination = null;
        InteractionMode = MapperInteractionMode.Source;
        CancelCaptureCore(); Rebuild(); Notify();
    }

    public void ObserveSnapshot(RuntimeSnapshot snapshot)
    {
        if (!_profileName.Equals(_profiles.ActiveName, StringComparison.Ordinal)) { RefreshFromActiveProfile(); return; }
        if (!IsCapturing && !IsCapturingDestination) return;
        var captured = ProfileEditorService.Listen(snapshot, _captureAfter);
        if (_awaitNeutral)
        {
            if (captured is null && snapshot.ActiveDesktopControls.Count == 0 && snapshot.ExtendedButtons.Count == 0 &&
                snapshot.Input.Buttons == 0 && snapshot.Input.LeftTrigger <= 20 && snapshot.Input.RightTrigger <= 20 &&
                Math.Abs((int)snapshot.Input.LeftX) < 2622 && Math.Abs((int)snapshot.Input.LeftY) < 2622 &&
                Math.Abs((int)snapshot.Input.RightX) < 2622 && Math.Abs((int)snapshot.Input.RightY) < 2622)
            { _awaitNeutral = false; _captureAfter = Math.Max(_captureAfter, snapshot.LastInputTimestamp); Notify(); }
            return;
        }
        if (captured is null) return;
        AcceptCapturedControl(captured);
    }

    public VisualSurfaceState CreateSurfaceState(RuntimeSnapshot snapshot) => new(
        SurfaceMappings(), SelectedSource is null ? null : Generic(SelectedSource), SelectedDestination is null ? null : Generic(SelectedDestination), InteractionMode,
        snapshot.Input, snapshot.Output, snapshot.ActiveDesktopControls.Select(Generic).ToHashSet(StringComparer.OrdinalIgnoreCase),
        snapshot.ExtendedButtons, snapshot.Motion);

    private void Rebuild()
    {
        var profile = _profiles.ActiveProfile;
        var mappings = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (profile["routes"] is JsonObject routes)
            foreach (var route in routes.Where(x => x.Value is JsonValue))
                if (route.Value!.GetValueKind() == System.Text.Json.JsonValueKind.String) mappings[route.Key] = route.Value.GetValue<string>();
        Mappings = mappings;
        Routes = mappings.Select(pair => new VisualBindingRoute(pair.Key, pair.Value, Label(pair.Key), Label(pair.Value),
            Math.Clamp((profile["route_output_slots"] as JsonObject)?[pair.Key]?.GetValue<int>() ?? 1, 1, 4))).ToArray();
        Destinations = BuildDestinations(profile);
    }

    private static IReadOnlyList<BindingTarget> BuildDestinations(JsonObject profile)
    {
        var targets = new List<BindingTarget>();
        targets.AddRange(ControllerControls.Select(x => new BindingTarget("Controller:" + x, x, "Controller")));
        targets.AddRange(KeyboardControls.Select(x => new BindingTarget("Desktop:Key:" + x, x, "Keyboard")));
        targets.AddRange(new[] { "Left", "Right", "Middle", "X1", "X2" }.Select(x => new BindingTarget("Desktop:Mouse:" + x, "Mouse " + x, "Mouse")));
        targets.AddRange(new[] { "Up", "Down" }.Select(x => new BindingTarget("Desktop:Wheel:" + x, "Wheel " + x, "Mouse")));
        targets.AddRange(new[] { "Up", "Down", "Left", "Right" }.Select(x => new BindingTarget("Desktop:Move:" + x, "Move " + x, "Mouse")));
        if (profile["macros"] is JsonObject macros)
            targets.AddRange(macros.Select(x => new BindingTarget("Action:Macro:" + x.Key, x.Key, "Macros")));
        return targets;
    }

    private IReadOnlyDictionary<string, string> SurfaceMappings()
    { var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase); foreach (var x in Mappings) result[Generic(x.Key)] = Generic(x.Value); return result; }
    private static string Generic(string value)
    {
        if (value.StartsWith("KeyboardDevice:", StringComparison.OrdinalIgnoreCase)) return "Keyboard:" + value[(value.LastIndexOf(':') + 1)..];
        if (value.StartsWith("MouseDevice:", StringComparison.OrdinalIgnoreCase)) return "Mouse:" + value[(value.LastIndexOf(':') + 1)..];
        if (value.StartsWith("Desktop:Key:", StringComparison.OrdinalIgnoreCase)) return "Keyboard:" + value[12..];
        if (value.StartsWith("Desktop:Mouse:", StringComparison.OrdinalIgnoreCase)) return "Mouse:" + value[14..];
        if (value.StartsWith("Desktop:Wheel:", StringComparison.OrdinalIgnoreCase)) return "Mouse:Wheel " + value[14..];
        return value;
    }
    private static bool IsMovementSource(string source) => Generic(source).Equals("Mouse:Movement", StringComparison.OrdinalIgnoreCase);
    private static string Label(string code) => code[(code.LastIndexOf(':') + 1)..];
    private void CancelCaptureCore() { IsCapturing = false; IsCapturingDestination = false; _awaitNeutral = false; }
    private void Notify() => Changed?.Invoke();

    private static readonly string[] ControllerControls = ["A","B","X","Y","LB","RB","LT","RT","Menu","View","Home","Share","Left Stick","Right Stick","D-pad Up","D-pad Down","D-pad Left","D-pad Right","Left Stick Up","Left Stick Down","Left Stick Left","Left Stick Right","Right Stick Up","Right Stick Down","Right Stick Left","Right Stick Right"];
    private static readonly string[] KeyboardControls = ["Esc","F1","F2","F3","F4","F5","F6","F7","F8","F9","F10","F11","F12","Print Screen","Scroll Lock","Pause","Backtick","1","2","3","4","5","6","7","8","9","0","Minus","Equals","Backspace","Tab","Q","W","E","R","T","Y","U","I","O","P","Left Bracket","Right Bracket","Backslash","Caps Lock","A","S","D","F","G","H","J","K","L","Semicolon","Apostrophe","Enter","Left Shift","Z","X","C","V","B","N","M","Comma","Period","Slash","Right Shift","Left Ctrl","Left Win","Left Alt","Space","Right Alt","Right Win","Menu Key","Right Ctrl","Insert","Delete","Home","End","Page Up","Page Down","Up","Down","Left","Right","Num Lock","Numpad Divide","Numpad Multiply","Numpad Minus","Numpad Plus","Numpad Enter","Numpad Decimal","Numpad0","Numpad1","Numpad2","Numpad3","Numpad4","Numpad5","Numpad6","Numpad7","Numpad8","Numpad9"];
}
