using System.IO;
using System.Text.Json;

namespace UnifiedController.App.Engine;

public struct NativeControllerReport
{
    public int Buttons;
    public byte LeftTrigger;
    public byte RightTrigger;
    public short LeftX;
    public short LeftY;
    public short RightX;
    public short RightY;
}

public readonly record struct NativeStickSettings(
    bool Enabled,
    double InnerDeadzone,
    double OuterDeadzone,
    double AntiDeadzone,
    double Curve)
{
    public static NativeStickSettings Disabled => new(false, 0.08, 1.0, 0.0, 1.0);
}

public readonly record struct NativeRouteAction(string Source, string Action);
public sealed record NativeRouteCondition(string? Layer, string[] Chord);
public sealed record NativeModConfig(string Source, string Behavior)
{
    public const string Layer = "Mod";
    public static NativeModConfig Disabled { get; } = new("", "toggle");
    public bool Enabled => Source.Length > 0;
}
public sealed record NativeControllerMouseConfig(
    bool Enabled, string ToggleSource, bool RequiresMod, string Stick,
    string LeftClickSource, string RightClickSource, string ScrollUpSource, string ScrollDownSource,
    double Sensitivity, double Deadzone)
{
    public static NativeControllerMouseConfig Disabled { get; } = new(false, "", true, "right", "", "", "", "", 18, 0.16);
}
internal sealed record NativeLayerRoute(string Source, string Layer, NativeRouteEffect Effect, NativeRouteActivator Activator, NativeRouteCondition Condition);
internal sealed record NativeDeviceKeyRoute(string Source, string DeviceId, int Key, string[] Targets);
internal sealed record NativeDeviceMouseRoute(string Source, string DeviceId, string Control, string[] Targets);

public sealed class NativeProfileRuntime
{
    private static readonly Dictionary<string, int> KeyCodes = BuildKeyCodes();
    private readonly Dictionary<int, string[]> _keyboardRoutes;
    private readonly Dictionary<string, string[]> _mouseRoutes;
    private readonly Dictionary<string, string[]> _controllerRoutes;
    private readonly NativeDeviceKeyRoute[] _deviceKeyboardRoutes;
    private readonly NativeDeviceMouseRoute[] _deviceMouseRoutes;
    private readonly Dictionary<string, NativeRouteEffect> _routeEffects;
    private readonly Dictionary<string, NativeRouteCondition> _routeConditions;
    private readonly Dictionary<string, NativeRouteActivator> _routeActivators;
    private readonly Dictionary<string, int> _routeOutputSlots;
    private readonly NativeLayerRoute[] _layerRoutes;
    private readonly NativeRouteEffectEngine _effectEngine = new();
    private readonly NativeRouteActivatorEngine _activatorEngine = new();
    private readonly HashSet<string> _activeLayers = new(StringComparer.OrdinalIgnoreCase);
    private HashSet<string> _previousActiveSources = new(StringComparer.OrdinalIgnoreCase);
    // Controller sources a layer/chord route claimed while they were held. They stay suppressed until
    // released, so a button pressed on the Mod layer never leaks its normal press once the layer ends
    // (one-shot Mod ends on that very press; hold Mod can be let go first).
    private readonly HashSet<string> _claimedSources = new(StringComparer.OrdinalIgnoreCase);
    private bool _modActive;

    public string ActiveProfileName { get; }
    public string SourceMergePolicy { get; }
    public int MouseSensitivity { get; }
    public bool MouseMovementEnabled { get; }
    public bool RumbleEnabled { get; }
    public double LeftRumbleScale { get; }
    public double RightRumbleScale { get; }
    public NativeStickSettings LeftStick { get; }
    public NativeStickSettings RightStick { get; }
    public NativeMotionConfig Motion { get; }
    public NativeBearingConfig MotionBearing { get; }
    public NativeModConfig Mod { get; }
    public NativeControllerMouseConfig ControllerMouse { get; }
    public IReadOnlyDictionary<string, NativeMacroDefinition> Macros { get; }
    public int RouteCount { get; }
    public int EffectRouteCount => _routeEffects.Count(pair => pair.Value.Type != "normal");
    public int ActivatorRouteCount => _routeActivators.Count(pair => pair.Value.Type != "while-held");
    public int[] EnabledOutputSlots { get; }
    public IReadOnlyDictionary<string, int> DeviceOutputSlots { get; }
    public string[] ActiveLayers => [.. _activeLayers.OrderBy(value => value)];
    public bool ModActive => _modActive;

    private NativeProfileRuntime(
        string activeProfileName,
        string sourceMergePolicy,
        int mouseSensitivity,
        bool mouseMovementEnabled,
        bool rumbleEnabled,
        double leftRumbleScale,
        double rightRumbleScale,
        NativeStickSettings leftStick,
        NativeStickSettings rightStick,
        NativeMotionConfig motion,
        NativeBearingConfig motionBearing,
        NativeModConfig mod,
        NativeControllerMouseConfig controllerMouse,
        IReadOnlyDictionary<string, NativeMacroDefinition> macros,
        Dictionary<int, string[]> keyboardRoutes,
        Dictionary<string, string[]> mouseRoutes,
        Dictionary<string, string[]> controllerRoutes,
        NativeDeviceKeyRoute[] deviceKeyboardRoutes,
        NativeDeviceMouseRoute[] deviceMouseRoutes,
        Dictionary<string, NativeRouteEffect> routeEffects,
        Dictionary<string, NativeRouteCondition> routeConditions,
        Dictionary<string, NativeRouteActivator> routeActivators,
        Dictionary<string, int> routeOutputSlots,
        NativeLayerRoute[] layerRoutes,
        int routeCount,
        int[] enabledOutputSlots,
        IReadOnlyDictionary<string, int> deviceOutputSlots)
    {
        ActiveProfileName = activeProfileName;
        SourceMergePolicy = sourceMergePolicy;
        MouseSensitivity = mouseSensitivity;
        MouseMovementEnabled = mouseMovementEnabled;
        RumbleEnabled = rumbleEnabled;
        LeftRumbleScale = leftRumbleScale;
        RightRumbleScale = rightRumbleScale;
        LeftStick = leftStick;
        RightStick = rightStick;
        Motion = motion;
        MotionBearing = motionBearing;
        Mod = mod;
        ControllerMouse = controllerMouse;
        Macros = macros;
        _keyboardRoutes = keyboardRoutes;
        _mouseRoutes = mouseRoutes;
        _controllerRoutes = controllerRoutes;
        _deviceKeyboardRoutes = deviceKeyboardRoutes;
        _deviceMouseRoutes = deviceMouseRoutes;
        _routeEffects = routeEffects;
        _routeConditions = routeConditions;
        _routeActivators = routeActivators;
        _routeOutputSlots = routeOutputSlots;
        _layerRoutes = layerRoutes;
        RouteCount = routeCount;
        EnabledOutputSlots = enabledOutputSlots;
        DeviceOutputSlots = deviceOutputSlots;
    }

    public static NativeProfileRuntime Empty { get; } = new(
        "Default", "first_active", 1400, false, true, 1.0, 1.0,
        NativeStickSettings.Disabled, NativeStickSettings.Disabled, NativeMotionConfig.Native,
        NativeBearingConfig.Disabled, NativeModConfig.Disabled, NativeControllerMouseConfig.Disabled, new Dictionary<string, NativeMacroDefinition>(StringComparer.OrdinalIgnoreCase),
        new(), new(StringComparer.OrdinalIgnoreCase), new(StringComparer.OrdinalIgnoreCase),
        [], [],
        new(StringComparer.OrdinalIgnoreCase), new(StringComparer.OrdinalIgnoreCase),
        new(StringComparer.OrdinalIgnoreCase), new(StringComparer.OrdinalIgnoreCase), [], 0, [1],
        new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase));

    public static NativeProfileRuntime Load(string path)
    {
        using var stream = File.OpenRead(path);
        using var document = JsonDocument.Parse(stream);
        var root = document.RootElement;
        var activeName = root.GetProperty("active_profile").GetString()
            ?? throw new InvalidDataException("active_profile is missing");
        var profile = root.GetProperty("profiles").GetProperty(activeName);

        var keyboard = new Dictionary<int, List<string>>();
        var mouse = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
        var controller = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
        var deviceKeyboard = new Dictionary<(string Device, int Key, string Source), List<string>>();
        var deviceMouse = new Dictionary<(string Device, string Control, string Source), List<string>>();
        var routeCount = 0;
        var mouseMovement = false;
        var routeEffects = new Dictionary<string, NativeRouteEffect>(StringComparer.OrdinalIgnoreCase);
        var routeConditions = new Dictionary<string, NativeRouteCondition>(StringComparer.OrdinalIgnoreCase);
        var routeActivators = new Dictionary<string, NativeRouteActivator>(StringComparer.OrdinalIgnoreCase);
        var routeOutputSlots = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var layerRoutes = new List<NativeLayerRoute>();
        var deviceOutputSlots = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        if (profile.TryGetProperty("device_output_slots", out var deviceSlots) && deviceSlots.ValueKind == JsonValueKind.Object)
            foreach (var assignment in deviceSlots.EnumerateObject())
                if (assignment.Value.TryGetInt32(out var slot) && slot is >= 1 and <= 4)
                    deviceOutputSlots[assignment.Name] = slot;

        if (profile.TryGetProperty("routes", out var routes) && routes.ValueKind == JsonValueKind.Object)
        {
            foreach (var route in routes.EnumerateObject())
            {
                if (route.Value.ValueKind != JsonValueKind.String) continue;
                var destination = route.Value.GetString();
                if (string.IsNullOrWhiteSpace(destination)) continue;
                routeCount++;

                if (TryParseDeviceSource(route.Name, "KeyboardDevice:", out var keyboardDevice, out var keyboardControl))
                {
                    if (!TryGetKeyCode(keyboardControl, out var vk)) continue;
                    var tuple = (keyboardDevice, vk, route.Name);
                    if (!deviceKeyboard.TryGetValue(tuple, out var targets)) deviceKeyboard[tuple] = targets = [];
                    targets.Add(destination);
                    AddRouteSettings(profile, route.Name, route.Name, destination, routeEffects, routeConditions, routeActivators, routeOutputSlots, layerRoutes);
                }
                else if (TryParseDeviceSource(route.Name, "MouseDevice:", out var mouseDevice, out var mouseControl))
                {
                    var tuple = (mouseDevice, mouseControl, route.Name);
                    if (!deviceMouse.TryGetValue(tuple, out var targets)) deviceMouse[tuple] = targets = [];
                    targets.Add(destination);
                    AddRouteSettings(profile, route.Name, route.Name, destination, routeEffects, routeConditions, routeActivators, routeOutputSlots, layerRoutes);
                }
                else if (route.Name.StartsWith("Keyboard:", StringComparison.OrdinalIgnoreCase))
                {
                    var name = route.Name[9..];
                    if (!TryGetKeyCode(name, out var vk)) continue;
                    if (!keyboard.TryGetValue(vk, out var targets)) keyboard[vk] = targets = [];
                    targets.Add(destination);
                    var routeId = $"Keyboard:{vk}";
                    AddRouteSettings(profile, route.Name, routeId, destination, routeEffects, routeConditions, routeActivators, routeOutputSlots, layerRoutes);
                }
                else if (route.Name.StartsWith("Mouse:", StringComparison.OrdinalIgnoreCase))
                {
                    var name = route.Name[6..];
                    if (name.Equals("Movement", StringComparison.OrdinalIgnoreCase) &&
                        destination.Equals("Controller:Right Stick", StringComparison.OrdinalIgnoreCase))
                    {
                        mouseMovement = true;
                        continue;
                    }
                    if (!mouse.TryGetValue(name, out var targets)) mouse[name] = targets = [];
                    targets.Add(destination);
                    var routeId = $"Mouse:{name}";
                    AddRouteSettings(profile, route.Name, routeId, destination, routeEffects, routeConditions, routeActivators, routeOutputSlots, layerRoutes);
                }
                else if (route.Name.StartsWith("Controller:", StringComparison.OrdinalIgnoreCase))
                {
                    var name = route.Name[11..];
                    if (!controller.TryGetValue(name, out var targets)) controller[name] = targets = [];
                    targets.Add(destination);
                    var routeId = $"Controller:{name}";
                    AddRouteSettings(profile, route.Name, routeId, destination, routeEffects, routeConditions, routeActivators, routeOutputSlots, layerRoutes);
                }
            }
        }

        var sourcePolicy = GetString(profile, "source_merge_policy", "first_active");
        if (sourcePolicy is not ("first_active" or "all_connected")) sourcePolicy = "first_active";
        var sensitivity = Math.Clamp(GetInt(profile, "mouse_sensitivity", 1400), 300, 3000);

        var rumbleEnabled = true;
        var leftRumble = 1.0;
        var rightRumble = 1.0;
        if (profile.TryGetProperty("rumble", out var rumble) && rumble.ValueKind == JsonValueKind.Object)
        {
            rumbleEnabled = GetBool(rumble, "enabled", true);
            leftRumble = Math.Clamp(GetDouble(rumble, "left_intensity", 100) / 100.0, 0, 1);
            rightRumble = Math.Clamp(GetDouble(rumble, "right_intensity", 100) / 100.0, 0, 1);
        }

        var leftStick = NativeStickSettings.Disabled;
        var rightStick = NativeStickSettings.Disabled;
        if (profile.TryGetProperty("stick_processing", out var processing) && processing.ValueKind == JsonValueKind.Object)
        {
            if (processing.TryGetProperty("left", out var left)) leftStick = ParseStick(left);
            if (processing.TryGetProperty("right", out var right)) rightStick = ParseStick(right);
        }

        var enabledOutputSlots = new HashSet<int> { 1 };
        if (profile.TryGetProperty("enabled_output_slots", out var enabledSlots) && enabledSlots.ValueKind == JsonValueKind.Array)
            foreach (var value in enabledSlots.EnumerateArray())
                if (value.TryGetInt32(out var slot) && slot is >= 1 and <= 4) enabledOutputSlots.Add(slot);
        foreach (var slot in routeOutputSlots.Values) enabledOutputSlots.Add(slot);
        foreach (var slot in deviceOutputSlots.Values) enabledOutputSlots.Add(slot);

        return new NativeProfileRuntime(
            activeName, sourcePolicy, sensitivity, mouseMovement, rumbleEnabled,
            leftRumble, rightRumble, leftStick, rightStick, NativeMotionConfig.Parse(profile),
            NativeBearingConfig.Parse(profile), ParseModConfig(profile), ParseControllerMouseConfig(profile), NativeMacroScheduler.Parse(profile),
            keyboard.ToDictionary(pair => pair.Key, pair => pair.Value.ToArray()),
            mouse.ToDictionary(pair => pair.Key, pair => pair.Value.ToArray(), StringComparer.OrdinalIgnoreCase),
            controller.ToDictionary(pair => pair.Key, pair => pair.Value.ToArray(), StringComparer.OrdinalIgnoreCase),
            deviceKeyboard.Select(pair => new NativeDeviceKeyRoute(pair.Key.Source, pair.Key.Device, pair.Key.Key, pair.Value.ToArray())).ToArray(),
            deviceMouse.Select(pair => new NativeDeviceMouseRoute(pair.Key.Source, pair.Key.Device, pair.Key.Control, pair.Value.ToArray())).ToArray(),
            routeEffects,
            routeConditions,
            routeActivators,
            routeOutputSlots,
            layerRoutes.ToArray(),
            routeCount,
            enabledOutputSlots.OrderBy(value => value).ToArray(),
            deviceOutputSlots);
    }

    public HashSet<string> BuildActiveSources(
        IReadOnlySet<int> keys,
        IReadOnlySet<string> mouseButtons,
        IReadOnlySet<string> mousePulses,
        NativeControllerReport controller,
        IReadOnlySet<string> extendedButtons)
    {
        var active = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var key in keys) active.Add($"Keyboard:{key}");
        foreach (var button in mouseButtons) active.Add($"Mouse:{button}");
        foreach (var pulse in mousePulses) active.Add($"Mouse:{pulse}");
        foreach (var control in ControllerControls)
            if (ControllerSourceActive(control, controller, extendedButtons)) active.Add($"Controller:{control}");
        return active;
    }

    public void AddRawActiveSources(NativeRawInputSnapshot snapshot, HashSet<string> active)
    {
        foreach (var route in _deviceKeyboardRoutes)
            if (snapshot.Keyboards.TryGetValue(route.DeviceId, out var keys) && keys.Contains(route.Key)) active.Add(route.Source);
        foreach (var route in _deviceMouseRoutes)
            if (snapshot.Mice.TryGetValue(route.DeviceId, out var mouse) &&
                (mouse.Buttons.Contains(route.Control) || mouse.Pulses.Contains(route.Control) ||
                 route.Control.Equals("Movement", StringComparison.OrdinalIgnoreCase) && (mouse.DeltaX != 0 || mouse.DeltaY != 0)))
                active.Add(route.Source);
    }

    public IReadOnlyList<NativeRouteAction> ApplyRawInputs(
        NativeRawInputSnapshot snapshot, ref NativeControllerReport report, double now, IReadOnlySet<string> activeSources,
        bool keyboardEnabled, bool mouseEnabled, IDictionary<int, NativeControllerReport>? outputReports = null)
    {
        var actions = new List<NativeRouteAction>();
        if (keyboardEnabled)
            foreach (var route in _deviceKeyboardRoutes)
            {
                var pressed = snapshot.Keyboards.TryGetValue(route.DeviceId, out var keys) && keys.Contains(route.Key);
                ApplyDeviceRoute(route.Source, route.Targets, pressed, ref report, now, activeSources, actions, outputReports);
            }
        if (mouseEnabled)
            foreach (var route in _deviceMouseRoutes)
            {
                if (!snapshot.Mice.TryGetValue(route.DeviceId, out var mouse)) continue;
                if (route.Control.Equals("Movement", StringComparison.OrdinalIgnoreCase))
                {
                    var condition = ConditionMet(route.Source, activeSources);
                    if (condition && (mouse.DeltaX != 0 || mouse.DeltaY != 0))
                        foreach (var target in route.Targets)
                            if (target.Equals("Controller:Right Stick", StringComparison.OrdinalIgnoreCase))
                            {
                                ApplyMouseMovementToSlot(route.Source, mouse.DeltaX, mouse.DeltaY, ref report, outputReports);
                            }
                    continue;
                }
                var pressed = mouse.Buttons.Contains(route.Control) || mouse.Pulses.Contains(route.Control);
                ApplyDeviceRoute(route.Source, route.Targets, pressed, ref report, now, activeSources, actions, outputReports);
            }
        return actions;
    }

    private void ApplyDeviceRoute(string source, string[] targets, bool pressed, ref NativeControllerReport report,
        double now, IReadOnlySet<string> activeSources, List<NativeRouteAction> actions,
        IDictionary<int, NativeControllerReport>? outputReports)
    {
        var condition = ConditionMet(source, activeSources);
        var activated = _activatorEngine.Transform($"activate:{source}", pressed && condition, Activator(source), now);
        if (!_effectEngine.ShouldEmit(source, activated, Effect(source), now) || !condition) return;
        foreach (var target in targets)
        {
            if (IsLayerAction(target)) continue;
            if (target.StartsWith("Action:", StringComparison.OrdinalIgnoreCase)) actions.Add(new NativeRouteAction(source, target[7..]));
            else if (target.StartsWith("Macro:", StringComparison.OrdinalIgnoreCase) || target.StartsWith("Desktop:", StringComparison.OrdinalIgnoreCase))
                actions.Add(new NativeRouteAction(source, target));
            else ApplyTargetForRoute(source, target, ref report, outputReports);
        }
    }

    public void UpdateLayers(IReadOnlySet<string> activeSources, double now)
    {
        _activeLayers.Clear();
        if (Mod.Enabled)
        {
            var down = activeSources.Contains(Mod.Source);
            var pressed = down && !_previousActiveSources.Contains(Mod.Source);
            _modActive = Mod.Behavior switch
            {
                "hold" => down,
                "toggle" or "one-shot" when pressed => !_modActive,
                _ => _modActive,
            };
            if (_modActive) _activeLayers.Add(NativeModConfig.Layer);
        }
        else
        {
            _modActive = false;
        }
        foreach (var route in _layerRoutes)
        {
            var chordMet = route.Condition.Chord.All(activeSources.Contains);
            var active = activeSources.Contains(route.Source) && chordMet;
            var activated = _activatorEngine.Transform($"layer-activate:{route.Source}", active, route.Activator, now);
            if (_effectEngine.ShouldEmit($"layer:{route.Source}", activated, route.Effect, now))
                _activeLayers.Add(route.Layer);
        }
    }

    public void FinishInputFrame(IReadOnlySet<string> activeSources)
    {
        if (_modActive && Mod.Behavior == "one-shot" &&
            activeSources.Any(source => !source.Equals(Mod.Source, StringComparison.OrdinalIgnoreCase) &&
                                        !_previousActiveSources.Contains(source)))
        {
            _modActive = false;
            _activeLayers.Remove(NativeModConfig.Layer);
        }
        _previousActiveSources = new HashSet<string>(activeSources, StringComparer.OrdinalIgnoreCase);
    }

    public IReadOnlyList<NativeRouteAction> ApplyKeyboard(IReadOnlySet<int> keys, ref NativeControllerReport report, double now,
        IReadOnlySet<string> activeSources, IDictionary<int, NativeControllerReport>? outputReports = null)
    {
        var actions = new List<NativeRouteAction>();
        foreach (var pair in _keyboardRoutes)
        {
            var routeId = $"Keyboard:{pair.Key}";
            var condition = ConditionMet(routeId, activeSources);
            var activated = _activatorEngine.Transform($"activate:{routeId}", keys.Contains(pair.Key) && condition, Activator(routeId), now);
            if (!_effectEngine.ShouldEmit(routeId, activated, Effect(routeId), now) || !condition) continue;
            foreach (var target in pair.Value)
            {
                if (IsLayerAction(target)) continue;
                if (target.StartsWith("Action:", StringComparison.OrdinalIgnoreCase))
                    actions.Add(new NativeRouteAction(routeId, target[7..]));
                else if (target.StartsWith("Macro:", StringComparison.OrdinalIgnoreCase))
                    actions.Add(new NativeRouteAction(routeId, target));
                else ApplyTargetForRoute(routeId, target, ref report, outputReports);
            }
        }
        return actions;
    }

    public IReadOnlyList<NativeRouteAction> ApplyMouse(
        int dx,
        int dy,
        IReadOnlySet<string> buttons,
        IReadOnlySet<string> pulses,
        ref NativeControllerReport report,
        double now,
        IReadOnlySet<string> activeSources,
        IDictionary<int, NativeControllerReport>? outputReports = null)
    {
        var actions = new List<NativeRouteAction>();
        foreach (var pair in _mouseRoutes)
        {
            var routeId = $"Mouse:{pair.Key}";
            var pressed = buttons.Contains(pair.Key) || pulses.Contains(pair.Key);
            var condition = ConditionMet(routeId, activeSources);
            var activated = _activatorEngine.Transform($"activate:{routeId}", pressed && condition, Activator(routeId), now);
            if (!_effectEngine.ShouldEmit(routeId, activated, Effect(routeId), now) || !condition) continue;
            foreach (var target in pair.Value)
            {
                if (IsLayerAction(target)) continue;
                if (target.StartsWith("Action:", StringComparison.OrdinalIgnoreCase))
                    actions.Add(new NativeRouteAction(routeId, target[7..]));
                else if (target.StartsWith("Macro:", StringComparison.OrdinalIgnoreCase))
                    actions.Add(new NativeRouteAction(routeId, target));
                else ApplyTargetForRoute(routeId, target, ref report, outputReports);
            }
        }

        // Reaching this method means the engine's mouse-capture mode is
        // active. MouseMovementEnabled is the persisted startup state.
        ApplyMouseMovementToSlot("Mouse:Movement", dx, dy, ref report, outputReports);
        return actions;
    }

    public IReadOnlyList<NativeRouteAction> ApplyControllerRoutes(
        NativeControllerReport original,
        IReadOnlySet<string> extendedButtons,
        ref NativeControllerReport report,
        double now,
        IReadOnlySet<string> activeSources,
        IDictionary<int, NativeControllerReport>? outputReports = null,
        int currentOutputSlot = 1)
    {
        var actions = new List<NativeRouteAction>();
        if (Mod.Enabled && Mod.Source.StartsWith("Controller:", StringComparison.OrdinalIgnoreCase))
            SuppressControllerSource(Mod.Source[11..], ref report);
        // A conditional route must not consume its physical source while its
        // layer/chord is inactive. This lets a button keep its normal game
        // function outside the alternate layer.
        foreach (var source in _controllerRoutes.Keys)
        {
            var routeId = $"Controller:{source}";
            var held = ControllerSourceActive(source, original, extendedButtons);
            if (!held)
            {
                _claimedSources.Remove(source);
            }
            if (ConditionMet(routeId, activeSources))
            {
                SuppressControllerSource(source, ref report);
                if (held && _routeConditions.ContainsKey(routeId)) _claimedSources.Add(source);
            }
            else if (held && _claimedSources.Contains(source))
            {
                SuppressControllerSource(source, ref report);
            }
        }

        foreach (var route in _controllerRoutes)
        {
            var active = ControllerSourceActive(route.Key, original, extendedButtons);
            var routeId = $"Controller:{route.Key}";
            var condition = ConditionMet(routeId, activeSources);
            var activated = _activatorEngine.Transform($"activate:{routeId}", active && condition, Activator(routeId), now);
            if (!_effectEngine.ShouldEmit(routeId, activated, Effect(routeId), now) || !condition) continue;

            foreach (var destination in route.Value)
            {
                if (IsLayerAction(destination)) continue;
                if (destination.StartsWith("Action:", StringComparison.OrdinalIgnoreCase))
                    actions.Add(new NativeRouteAction($"Controller:{route.Key}", destination[7..]));
                else if (destination.StartsWith("Macro:", StringComparison.OrdinalIgnoreCase))
                    actions.Add(new NativeRouteAction($"Controller:{route.Key}", destination));
                else if (destination.StartsWith("Desktop:", StringComparison.OrdinalIgnoreCase))
                    actions.Add(new NativeRouteAction($"Controller:{route.Key}", destination));
                else
                    ApplyTargetForRoute(routeId, destination, ref report, outputReports, currentOutputSlot);
            }
        }
        return actions;
    }

    public void ApplyStickProcessing(ref NativeControllerReport report)
    {
        (report.LeftX, report.LeftY) = ProcessStick(report.LeftX, report.LeftY, LeftStick);
        (report.RightX, report.RightY) = ProcessStick(report.RightX, report.RightY, RightStick);
    }

    public (byte Large, byte Small) ScaleRumble(byte large, byte small)
    {
        if (!RumbleEnabled) return (0, 0);
        return ((byte)Math.Round(large * LeftRumbleScale), (byte)Math.Round(small * RightRumbleScale));
    }

    public static (short X, short Y) ProcessStick(short x, short y, NativeStickSettings settings)
    {
        if (!settings.Enabled) return (x, y);
        var nx = x / (x < 0 ? 32768.0 : 32767.0);
        var ny = y / (y < 0 ? 32768.0 : 32767.0);
        var magnitude = Math.Sqrt(nx * nx + ny * ny);
        if (magnitude <= settings.InnerDeadzone) return (0, 0);

        var denominator = Math.Max(0.01, settings.OuterDeadzone - settings.InnerDeadzone);
        var fraction = Math.Min(1.0, (magnitude - settings.InnerDeadzone) / denominator);
        fraction = settings.AntiDeadzone + (1.0 - settings.AntiDeadzone) * Math.Pow(fraction, settings.Curve);
        var ox = nx / magnitude * fraction;
        var oy = ny / magnitude * fraction;
        return (NormalizedToShort(ox), NormalizedToShort(oy));
    }

    private static void ApplyTarget(string destination, ref NativeControllerReport report)
    {
        if (!destination.StartsWith("Controller:", StringComparison.OrdinalIgnoreCase)) return;
        switch (destination[11..])
        {
            case "A": report.Buttons |= 0x1000; break;
            case "B": report.Buttons |= 0x2000; break;
            case "X": report.Buttons |= 0x4000; break;
            case "Y": report.Buttons |= 0x8000; break;
            case "LB": report.Buttons |= 0x0100; break;
            case "RB": report.Buttons |= 0x0200; break;
            case "Left Stick": report.Buttons |= 0x0040; break;
            case "Right Stick": report.Buttons |= 0x0080; break;
            case "Menu": report.Buttons |= 0x0010; break;
            case "View": report.Buttons |= 0x0020; break;
            case "Home": report.Buttons |= 0x0400; break;
            case "D-pad Up": report.Buttons |= 0x0001; break;
            case "D-pad Down": report.Buttons |= 0x0002; break;
            case "D-pad Left": report.Buttons |= 0x0004; break;
            case "D-pad Right": report.Buttons |= 0x0008; break;
            case "LT": report.LeftTrigger = 255; break;
            case "RT": report.RightTrigger = 255; break;
            case "Left Stick Left": report.LeftX = -32768; break;
            case "Left Stick Right": report.LeftX = 32767; break;
            case "Left Stick Down": report.LeftY = -32768; break;
            case "Left Stick Up": report.LeftY = 32767; break;
            case "Right Stick Left": report.RightX = -32768; break;
            case "Right Stick Right": report.RightX = 32767; break;
            case "Right Stick Down": report.RightY = -32768; break;
            case "Right Stick Up": report.RightY = 32767; break;
        }
    }

    private void ApplyTargetForRoute(string routeId, string destination, ref NativeControllerReport primary,
        IDictionary<int, NativeControllerReport>? outputReports, int currentOutputSlot = 1)
    {
        var slot = OutputSlot(routeId, currentOutputSlot);
        if (slot == currentOutputSlot || outputReports == null)
        {
            ApplyTarget(destination, ref primary);
            return;
        }
        outputReports.TryGetValue(slot, out var output);
        ApplyTarget(destination, ref output);
        outputReports[slot] = output;
    }

    private void ApplyMouseMovementToSlot(string routeId, int dx, int dy, ref NativeControllerReport primary,
        IDictionary<int, NativeControllerReport>? outputReports)
    {
        var slot = OutputSlot(routeId);
        if (slot == 1 || outputReports == null)
        {
            primary.RightX = ClampShort(dx * MouseSensitivity);
            primary.RightY = ClampShort(-dy * MouseSensitivity);
            return;
        }
        outputReports.TryGetValue(slot, out var output);
        output.RightX = ClampShort(dx * MouseSensitivity);
        output.RightY = ClampShort(-dy * MouseSensitivity);
        outputReports[slot] = output;
    }

    private static bool ControllerSourceActive(
        string source,
        NativeControllerReport report,
        IReadOnlySet<string> extendedButtons)
    {
        if (source.Equals("Share", StringComparison.OrdinalIgnoreCase))
            return extendedButtons.Contains("Share");
        if (source.Equals("Home", StringComparison.OrdinalIgnoreCase) && extendedButtons.Contains("Home"))
            return true;

        var mask = ControllerButtonMask(source);
        if (mask != 0) return (report.Buttons & mask) != 0;
        return source switch
        {
            "LT" => report.LeftTrigger > 30,
            "RT" => report.RightTrigger > 30,
            "Left Stick Left" => report.LeftX < -9000,
            "Left Stick Right" => report.LeftX > 9000,
            "Left Stick Down" => report.LeftY < -9000,
            "Left Stick Up" => report.LeftY > 9000,
            "Right Stick Left" => report.RightX < -9000,
            "Right Stick Right" => report.RightX > 9000,
            "Right Stick Down" => report.RightY < -9000,
            "Right Stick Up" => report.RightY > 9000,
            _ => false,
        };
    }

    private static void SuppressControllerSource(string source, ref NativeControllerReport report)
    {
        var mask = ControllerButtonMask(source);
        if (mask != 0) report.Buttons &= ~mask;
        switch (source)
        {
            case "LT": report.LeftTrigger = 0; break;
            case "RT": report.RightTrigger = 0; break;
            case "Left Stick Left" when report.LeftX < 0: report.LeftX = 0; break;
            case "Left Stick Right" when report.LeftX > 0: report.LeftX = 0; break;
            case "Left Stick Down" when report.LeftY < 0: report.LeftY = 0; break;
            case "Left Stick Up" when report.LeftY > 0: report.LeftY = 0; break;
            case "Right Stick Left" when report.RightX < 0: report.RightX = 0; break;
            case "Right Stick Right" when report.RightX > 0: report.RightX = 0; break;
            case "Right Stick Down" when report.RightY < 0: report.RightY = 0; break;
            case "Right Stick Up" when report.RightY > 0: report.RightY = 0; break;
        }
    }

    private static int ControllerButtonMask(string control) => control switch
    {
        "A" => 0x1000, "B" => 0x2000, "X" => 0x4000, "Y" => 0x8000,
        "LB" => 0x0100, "RB" => 0x0200,
        "Left Stick" => 0x0040, "Right Stick" => 0x0080,
        "Menu" => 0x0010, "View" => 0x0020, "Home" => 0x0400,
        "D-pad Up" => 0x0001, "D-pad Down" => 0x0002,
        "D-pad Left" => 0x0004, "D-pad Right" => 0x0008,
        _ => 0,
    };

    private static NativeStickSettings ParseStick(JsonElement element)
    {
        if (element.ValueKind != JsonValueKind.Object) return NativeStickSettings.Disabled;
        var enabled = GetBool(element, "enabled", false);
        var inner = Math.Clamp(GetDouble(element, "inner_deadzone", 0.08), 0, 0.8);
        var outer = Math.Clamp(GetDouble(element, "outer_deadzone", 1.0), inner + 0.01, 1.0);
        var anti = Math.Clamp(GetDouble(element, "anti_deadzone", 0), 0, 0.8);
        var curve = Math.Clamp(GetDouble(element, "curve", 1), 0.5, 3.0);
        return new(enabled, inner, outer, anti, curve);
    }

    private NativeRouteEffect Effect(string routeId) =>
        _routeEffects.TryGetValue(routeId, out var effect) ? effect : NativeRouteEffect.Normal;
    private NativeRouteActivator Activator(string routeId) =>
        _routeActivators.TryGetValue(routeId, out var activator) ? activator : NativeRouteActivator.WhileHeld;
    public int OutputSlotForDevice(string? deviceId) => deviceId != null &&
        DeviceOutputSlots.TryGetValue(deviceId, out var slot) ? slot : 1;

    private int OutputSlot(string routeId, int fallbackSlot = 1)
    {
        if (_routeOutputSlots.TryGetValue(routeId, out var slot)) return slot;
        if (TryParseDeviceSource(routeId, "KeyboardDevice:", out var keyboardDevice, out _) &&
            DeviceOutputSlots.TryGetValue(keyboardDevice, out slot)) return slot;
        if (TryParseDeviceSource(routeId, "MouseDevice:", out var mouseDevice, out _) &&
            DeviceOutputSlots.TryGetValue(mouseDevice, out slot)) return slot;
        return fallbackSlot;
    }

    private static NativeRouteEffect ParseRouteEffect(JsonElement profile, string source)
    {
        if (!profile.TryGetProperty("route_effects", out var effects) || effects.ValueKind != JsonValueKind.Object ||
            !effects.TryGetProperty(source, out var raw) || raw.ValueKind != JsonValueKind.Object)
            return NativeRouteEffect.Normal;
        var type = GetString(raw, "type", "normal");
        if (type is not ("normal" or "toggle" or "turbo")) type = "normal";
        var activation = GetString(raw, "activation", "while-held");
        if (activation is not ("while-held" or "toggle")) activation = "while-held";
        return new(
            type,
            Math.Clamp(GetDouble(raw, "rate_hz", 12), 1, 30),
            Math.Clamp(GetDouble(raw, "duty_cycle", 0.5), 0.05, 0.95),
            activation,
            Math.Clamp(GetDouble(raw, "initial_delay_ms", 0) / 1000.0, 0, 5),
            Math.Clamp(GetInt(raw, "burst_count", 0), 0, 100));
    }

    private bool ConditionMet(string routeId, IReadOnlySet<string> activeSources)
    {
        if (!_routeConditions.TryGetValue(routeId, out var condition)) return true;
        if (condition.Layer != null && !_activeLayers.Contains(condition.Layer)) return false;
        return condition.Chord.All(activeSources.Contains);
    }

    private static void AddRouteSettings(
        JsonElement profile, string source, string routeId, string destination,
        Dictionary<string, NativeRouteEffect> effects,
        Dictionary<string, NativeRouteCondition> conditions,
        Dictionary<string, NativeRouteActivator> activators,
        Dictionary<string, int> outputSlots,
        List<NativeLayerRoute> layers)
    {
        var effect = ParseRouteEffect(profile, source);
        var condition = ParseRouteCondition(profile, source);
        var activator = ParseRouteActivator(profile, source);
        effects[routeId] = effect;
        conditions[routeId] = condition;
        activators[routeId] = activator;
        if (ParseRouteOutputSlot(profile, source) is int outputSlot)
            outputSlots[routeId] = outputSlot;
        if (TryLayerName(destination, out var layer))
            layers.Add(new(routeId, layer, effect, activator, condition));
    }

    private static int? ParseRouteOutputSlot(JsonElement profile, string source)
    {
        if (!profile.TryGetProperty("route_output_slots", out var slots) || slots.ValueKind != JsonValueKind.Object ||
            !slots.TryGetProperty(source, out var raw) || !raw.TryGetInt32(out var slot)) return null;
        return Math.Clamp(slot, 1, 4);
    }

    private static NativeRouteActivator ParseRouteActivator(JsonElement profile, string source)
    {
        if (!profile.TryGetProperty("route_activators", out var activators) || activators.ValueKind != JsonValueKind.Object ||
            !activators.TryGetProperty(source, out var raw) || raw.ValueKind != JsonValueKind.Object)
            return NativeRouteActivator.WhileHeld;
        var type = GetString(raw, "type", "while-held");
        if (type is not ("while-held" or "press" or "release" or "tap" or "hold" or "double-tap"))
            type = "while-held";
        return new(
            type,
            Math.Clamp(GetDouble(raw, "threshold_ms", 300) / 1000.0, 0.05, 2.0),
            Math.Clamp(GetDouble(raw, "pulse_ms", 80) / 1000.0, 0.02, 0.5));
    }

    private static NativeModConfig ParseModConfig(JsonElement profile)
    {
        if (!profile.TryGetProperty("mod_key", out var raw) || raw.ValueKind != JsonValueKind.Object)
            return NativeModConfig.Disabled;
        var source = NormalizeSourceId(GetString(raw, "source", "")) ?? "";
        var behavior = GetString(raw, "behavior", "toggle");
        if (behavior is not ("toggle" or "one-shot" or "hold")) behavior = "toggle";
        return new NativeModConfig(source, behavior);
    }

    private static NativeControllerMouseConfig ParseControllerMouseConfig(JsonElement profile)
    {
        if (!profile.TryGetProperty("controller_mouse", out var raw) || raw.ValueKind != JsonValueKind.Object)
            return NativeControllerMouseConfig.Disabled;
        static string Source(JsonElement value, string name, string fallback) =>
            NormalizeSourceId(GetString(value, name, fallback)) ?? fallback;
        var stick = GetString(raw, "stick", "right");
        if (stick is not ("left" or "right")) stick = "right";
        return new NativeControllerMouseConfig(
            GetBool(raw, "enabled", true),
            Source(raw, "toggle_source", "Controller:Right Stick"),
            GetBool(raw, "requires_mod", true),
            stick,
            Source(raw, "left_click_source", "Controller:LB"),
            Source(raw, "right_click_source", "Controller:RB"),
            Source(raw, "scroll_up_source", "Controller:D-pad Up"),
            Source(raw, "scroll_down_source", "Controller:D-pad Down"),
            Math.Clamp(GetDouble(raw, "sensitivity", 18), 1, 60),
            Math.Clamp(GetDouble(raw, "deadzone", 0.16), 0, 0.8));
    }

    public bool IsSourceActive(string source, NativeControllerReport report, IReadOnlySet<string> extendedButtons,
        IReadOnlySet<string> activeSources)
    {
        if (source.StartsWith("Controller:", StringComparison.OrdinalIgnoreCase))
            return ControllerSourceActive(source[11..], report, extendedButtons);
        return activeSources.Contains(source);
    }

    public void SuppressSource(string source, ref NativeControllerReport report)
    {
        if (source.StartsWith("Controller:", StringComparison.OrdinalIgnoreCase))
            SuppressControllerSource(source[11..], ref report);
    }

    private static NativeRouteCondition ParseRouteCondition(JsonElement profile, string source)
    {
        if (!profile.TryGetProperty("route_conditions", out var conditions) || conditions.ValueKind != JsonValueKind.Object ||
            !conditions.TryGetProperty(source, out var raw) || raw.ValueKind != JsonValueKind.Object)
            return new(null, []);
        var layer = GetString(raw, "layer", "").Trim();
        var chord = new List<string>();
        if (raw.TryGetProperty("chord", out var values) && values.ValueKind == JsonValueKind.Array)
            foreach (var value in values.EnumerateArray())
                if (value.ValueKind == JsonValueKind.String && NormalizeSourceId(value.GetString() ?? "") is { } normalized)
                    chord.Add(normalized);
        return new(string.IsNullOrWhiteSpace(layer) ? null : layer, chord.Distinct(StringComparer.OrdinalIgnoreCase).ToArray());
    }

    private static string? NormalizeSourceId(string source)
    {
        source = source.Trim();
        if (source.StartsWith("Keyboard:", StringComparison.OrdinalIgnoreCase))
            return TryGetKeyCode(source[9..], out var key) ? $"Keyboard:{key}" : null;
        if (source.StartsWith("Mouse:", StringComparison.OrdinalIgnoreCase) ||
            source.StartsWith("Controller:", StringComparison.OrdinalIgnoreCase)) return source;
        return null;
    }

    private static bool IsLayerAction(string destination) => TryLayerName(destination, out _);
    private static bool TryLayerName(string destination, out string layer)
    {
        const string prefix = "Action:Layer ";
        if (destination.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) && destination.Length > prefix.Length)
        {
            layer = destination[prefix.Length..].Trim();
            return layer.Length > 0;
        }
        layer = "";
        return false;
    }

    private static bool TryParseDeviceSource(string source, string prefix, out string deviceId, out string control)
    {
        deviceId = "";
        control = "";
        if (!source.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return false;
        var remainder = source[prefix.Length..];
        var separator = remainder.LastIndexOf(':');
        if (separator <= 0 || separator >= remainder.Length - 1) return false;
        deviceId = remainder[..separator];
        control = remainder[(separator + 1)..];
        return deviceId.StartsWith("raw-", StringComparison.OrdinalIgnoreCase);
    }

    private static readonly string[] ControllerControls =
    [
        "A", "B", "X", "Y", "LB", "RB", "Left Stick", "Right Stick", "Menu", "View", "Home", "Share",
        "D-pad Up", "D-pad Down", "D-pad Left", "D-pad Right", "LT", "RT",
        "Left Stick Left", "Left Stick Right", "Left Stick Down", "Left Stick Up",
        "Right Stick Left", "Right Stick Right", "Right Stick Down", "Right Stick Up"
    ];

    private static short NormalizedToShort(double value)
    {
        value = Math.Clamp(value, -1, 1);
        return (short)Math.Clamp((int)Math.Round(value * (value < 0 ? 32768.0 : 32767.0)), -32768, 32767);
    }

    private static short ClampShort(int value) => (short)Math.Clamp(value, -32768, 32767);

    private static string GetString(JsonElement element, string name, string fallback) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? fallback : fallback;

    private static int GetInt(JsonElement element, string name, int fallback) =>
        element.TryGetProperty(name, out var value) && value.TryGetInt32(out var number) ? number : fallback;

    private static double GetDouble(JsonElement element, string name, double fallback) =>
        element.TryGetProperty(name, out var value) && value.TryGetDouble(out var number) && double.IsFinite(number)
            ? number : fallback;

    private static bool GetBool(JsonElement element, string name, bool fallback) =>
        element.TryGetProperty(name, out var value) && value.ValueKind is JsonValueKind.True or JsonValueKind.False
            ? value.GetBoolean() : fallback;

    private static bool TryGetKeyCode(string name, out int keyCode)
    {
        if (name.StartsWith("Numpad", StringComparison.OrdinalIgnoreCase))
        {
            var digit = name[6..].Trim();
            if (digit.Length == 1 && digit[0] is >= '0' and <= '9')
            { keyCode = 0x60 + digit[0] - '0'; return true; }
        }
        if (name.StartsWith("F", StringComparison.OrdinalIgnoreCase) &&
            int.TryParse(name[1..], out var functionKey) && functionKey is >= 1 and <= 24)
        { keyCode = 0x70 + functionKey - 1; return true; }
        if (name.Length == 1)
        {
            var ch = char.ToUpperInvariant(name[0]);
            if (ch is >= 'A' and <= 'Z' or >= '0' and <= '9')
            {
                keyCode = ch;
                return true;
            }
        }
        return KeyCodes.TryGetValue(name, out keyCode);
    }

    public static string? KeyName(int keyCode)
    {
        if (keyCode is >= 0x60 and <= 0x69) return "Numpad" + (keyCode - 0x60);
        if (keyCode is >= 'A' and <= 'Z' or >= '0' and <= '9') return ((char)keyCode).ToString();
        if (keyCode is >= 0x70 and <= 0x87) return "F" + (keyCode - 0x70 + 1);
        return KeyCodes.FirstOrDefault(p => p.Value == keyCode).Key;
    }

    private static Dictionary<string, int> BuildKeyCodes() => new(StringComparer.OrdinalIgnoreCase)
    {
        ["Esc"] = 0x1B, ["Tab"] = 0x09, ["Space"] = 0x20,
        ["Left Shift"] = 0xA0, ["Right Shift"] = 0xA1,
        ["Left Ctrl"] = 0xA2, ["Right Ctrl"] = 0xA3,
        ["Left Alt"] = 0xA4, ["Right Alt"] = 0xA5,
        ["Left Win"] = 0x5B, ["Right Win"] = 0x5C, ["Menu Key"] = 0x5D,
        ["Up"] = 0x26, ["Down"] = 0x28, ["Left"] = 0x25, ["Right"] = 0x27,
        ["Backtick"] = 0xC0, ["Minus"] = 0xBD, ["Equals"] = 0xBB,
        ["Left Bracket"] = 0xDB, ["Right Bracket"] = 0xDD, ["Backslash"] = 0xDC,
        ["Semicolon"] = 0xBA, ["Apostrophe"] = 0xDE, ["Comma"] = 0xBC,
        ["Period"] = 0xBE, ["Slash"] = 0xBF,
        ["Backspace"] = 0x08, ["Enter"] = 0x0D, ["Caps Lock"] = 0x14,
        ["Insert"] = 0x2D, ["Delete"] = 0x2E, ["Home"] = 0x24, ["End"] = 0x23,
        ["Page Up"] = 0x21, ["Page Down"] = 0x22,
        ["Print Screen"] = 0x2C, ["Scroll Lock"] = 0x91, ["Pause"] = 0x13,
        ["Num Lock"] = 0x90, ["Numpad Divide"] = 0x6F, ["Numpad Multiply"] = 0x6A,
        ["Numpad Minus"] = 0x6D, ["Numpad Plus"] = 0x6B, ["Numpad Enter"] = 0x0D,
        ["Numpad Decimal"] = 0x6E,
    };
}
