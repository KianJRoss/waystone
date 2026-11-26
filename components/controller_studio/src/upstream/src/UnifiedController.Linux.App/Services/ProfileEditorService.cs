using System.Globalization;
using System.Text.Json.Nodes;
using UnifiedController.Core;

namespace UnifiedController.Linux.App.Services;

/// <summary>Lossless mutations for fields consumed by NativeProfileRuntime.</summary>
public static class ProfileEditorService
{
    private static readonly string[] NativeMacroAxes =
        ["Controller:Left Stick X", "Controller:Left Stick Y", "Controller:Right Stick X", "Controller:Right Stick Y", "Controller:LT", "Controller:RT"];
    public static readonly IReadOnlyDictionary<int, string> ControllerButtons = new Dictionary<int, string>
    {
        [0x0001] = "D-pad Up", [0x0002] = "D-pad Down", [0x0004] = "D-pad Left", [0x0008] = "D-pad Right",
        [0x0010] = "Menu", [0x0020] = "View", [0x0040] = "Left Stick", [0x0080] = "Right Stick",
        [0x0100] = "LB", [0x0200] = "RB", [0x0400] = "Home",
        [0x1000] = "A", [0x2000] = "B", [0x4000] = "X", [0x8000] = "Y"
    };

    public static string? Listen(RuntimeSnapshot snapshot, long afterTimestamp)
    {
        if (snapshot.LastInputTimestamp > afterTimestamp && !string.IsNullOrWhiteSpace(snapshot.LastInputSource))
            return snapshot.LastInputSource;
        foreach (var pair in ControllerButtons)
            if ((snapshot.Input.Buttons & pair.Key) != 0) return "Controller:" + pair.Value;
        if (snapshot.Input.LeftTrigger > 32) return "Controller:LT";
        if (snapshot.Input.RightTrigger > 32) return "Controller:RT";
        return null;
    }

    /// <summary>Converts capture/source vocabulary into a route destination without changing the profile.</summary>
    public static string? ToDestination(string source)
    {
        if (string.IsNullOrWhiteSpace(source)) return null;
        if (source.StartsWith("Controller:", StringComparison.OrdinalIgnoreCase)) return "Controller:" + source[11..];
        if (source.StartsWith("KeyboardDevice:", StringComparison.OrdinalIgnoreCase))
            return "Desktop:Key:" + source[(source.LastIndexOf(':') + 1)..];
        if (source.StartsWith("Keyboard:", StringComparison.OrdinalIgnoreCase)) return "Desktop:Key:" + source[9..];
        if (source.StartsWith("MouseDevice:", StringComparison.OrdinalIgnoreCase))
            source = "Mouse:" + source[(source.LastIndexOf(':') + 1)..];
        if (source.StartsWith("Mouse:Wheel ", StringComparison.OrdinalIgnoreCase)) return "Desktop:Wheel:" + source[12..];
        if (source.StartsWith("Mouse:Move ", StringComparison.OrdinalIgnoreCase)) return "Desktop:Move:" + source[11..];
        if (source.StartsWith("Mouse:", StringComparison.OrdinalIgnoreCase)) return "Desktop:Mouse:" + source[6..];
        return source.StartsWith("Desktop:", StringComparison.OrdinalIgnoreCase) ? source : null;
    }

    public static void SetStick(JsonObject profile, string side, bool enabled, string inner, string outer, string anti, string curve)
    {
        var stick = Obj(Obj(profile, "stick_processing"), side);
        stick["enabled"] = enabled;
        SetNumber(stick, "inner_deadzone", inner); SetNumber(stick, "outer_deadzone", outer);
        SetNumber(stick, "anti_deadzone", anti); SetNumber(stick, "curve", curve);
    }

    public static void SetMacro(JsonObject profile, string name, int repeatCount, bool cancel, JsonArray steps)
    {
        var macros = Obj(profile, "macros");
        var macro = macros[name] as JsonObject ?? new JsonObject();
        macro["repeat_count"] = repeatCount;
        macro.Remove("repeats");
        macro["cancel_on_release"] = cancel;
        macro["steps"] = steps.DeepClone();
        macros[name] = macro;
    }

    public static JsonObject CreateMacroStep(string type, string input, int durationMs)
    {
        type = type.Trim().ToLowerInvariant(); input = input.Trim();
        return type switch
        {
            "press" or "release" when input.StartsWith("Controller:", StringComparison.OrdinalIgnoreCase)
                => new JsonObject { ["type"] = type, ["target"] = input },
            "axis" when input.Split('=', 2) is [var target, var value] &&
                NativeMacroAxes.Contains(target.Trim(), StringComparer.OrdinalIgnoreCase) &&
                double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var axis) && axis is >= -1 and <= 1
                => new JsonObject { ["type"] = type, ["target"] = target.Trim(), ["value"] = axis },
            "delay" => new JsonObject { ["type"] = type, ["duration_ms"] = Math.Clamp(durationMs, 0, 10000) },
            "action" when RuntimeActions.All.Any(x => !x.Equals(RuntimeActions.FishingHelperToggle, StringComparison.OrdinalIgnoreCase) && x.Equals(input, StringComparison.OrdinalIgnoreCase))
                => new JsonObject { ["type"] = type, ["action"] = RuntimeActions.All.First(x => x.Equals(input, StringComparison.OrdinalIgnoreCase)) },
            "axis" => throw new InvalidOperationException("Axis steps use 'Controller:Left Stick X=0.5' (value -1 to 1)."),
            "press" or "release" => throw new InvalidOperationException("Press and release steps require a Controller: target."),
            "action" => throw new InvalidOperationException("Enter one of the supported runtime action names."),
            _ => throw new InvalidOperationException("Unsupported macro step type.")
        };
    }

    public static void RemoveMacro(JsonObject profile, string name) => (profile["macros"] as JsonObject)?.Remove(name);

    public static void SetRumble(JsonObject profile, bool enabled, int leftIntensity, int rightIntensity)
    {
        var rumble = Obj(profile, "rumble");
        rumble["enabled"] = enabled;
        rumble["left_intensity"] = Math.Clamp(leftIntensity, 0, 100);
        rumble["right_intensity"] = Math.Clamp(rightIntensity, 0, 100);
    }

    public static void SetTriggers(JsonObject profile, JsonObject left, JsonObject right)
    {
        var triggers = Obj(profile, "trigger_processing");
        triggers["left"] = Merge(triggers["left"] as JsonObject, left);
        triggers["right"] = Merge(triggers["right"] as JsonObject, right);
    }

    public static void SetControllerMouse(JsonObject profile, JsonObject edits) =>
        profile["controller_mouse"] = Merge(profile["controller_mouse"] as JsonObject, edits);

    public static void SetMotionSubObject(JsonObject profile, string key, JsonObject edits)
    {
        var motion = Obj(profile, "motion");
        motion[key] = Merge(motion[key] as JsonObject, edits);
    }

    public static void SetMotionBearing(JsonObject profile, JsonObject edits) =>
        profile["motion_bearing"] = Merge(profile["motion_bearing"] as JsonObject, edits);

    public static void SetMotion(JsonObject profile, string preset, JsonObject? left = null, JsonObject? right = null)
    {
        var motion = Obj(profile, "motion");
        motion["preset"] = preset;
        if (left is not null) motion["left"] = Merge(motion["left"] as JsonObject, left);
        if (right is not null) motion["right"] = Merge(motion["right"] as JsonObject, right);
        profile.Remove("motion_mapping");
    }

    public static void SetRoute(JsonObject profile, string source, string destination, string? layer, IEnumerable<string> chord,
        string activator, string effect, int outputSlot, double rateHz = 12, double dutyCycle = .5)
    {
        Obj(profile, "routes")[source] = destination;
        var chordArray = chord.Where(x => !string.IsNullOrWhiteSpace(x)).Select(x => (JsonNode?)JsonValue.Create(x.Trim())).ToArray();
        if (!string.IsNullOrWhiteSpace(layer) || chordArray.Length > 0)
            Obj(profile, "route_conditions")[source] = new JsonObject { ["layer"] = layer, ["chord"] = new JsonArray(chordArray) };
        else (profile["route_conditions"] as JsonObject)?.Remove(source);
        Obj(profile, "route_activators")[source] = new JsonObject { ["type"] = activator, ["threshold_ms"] = 300, ["pulse_ms"] = 80 };
        var prior = Obj(profile, "route_effects")[source] as JsonObject;
        var effectNode = prior?.DeepClone().AsObject() ?? new JsonObject();
        effectNode["type"] = effect;
        if (effect == "turbo") { effectNode["rate_hz"] = rateHz; effectNode["duty_cycle"] = dutyCycle; effectNode["activation"] ??= "while-held"; }
        Obj(profile, "route_effects")[source] = effectNode;
        Obj(profile, "route_output_slots")[source] = Math.Clamp(outputSlot, 1, 4);
    }

    public static JsonObject Obj(JsonObject owner, string key)
    { if (owner[key] is JsonObject value) return value; value = new(); owner[key] = value; return value; }
    public static void SetNumber(JsonObject owner, string key, string text)
    {
        if (string.IsNullOrWhiteSpace(text)) return;
        if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) || !double.IsFinite(value))
            throw new InvalidOperationException($"{key} must be a finite number using '.' as the decimal separator.");
        owner[key] = value;
    }
    private static JsonObject Merge(JsonObject? original, JsonObject edits)
    { var result = original?.DeepClone().AsObject() ?? new(); foreach (var item in edits) result[item.Key] = item.Value?.DeepClone(); return result; }
}
