using System.Text.Json;

namespace UnifiedController.App.Engine;

public sealed record NativeMacroStep(string Type, string Target, double Value, int DurationMs, string Action);
public sealed record NativeMacroDefinition(
    string Name, NativeMacroStep[] Steps, int RepeatCount, bool CancelOnRelease);

public sealed class NativeMacroScheduler
{
    private sealed class Instance
    {
        public required NativeMacroDefinition Definition;
        public required string Source;
        public int StepIndex;
        public int CompletedRepeats;
        public double NextAt;
        public readonly HashSet<string> HeldTargets = new(StringComparer.OrdinalIgnoreCase);
        public readonly Dictionary<string, double> AxisValues = new(StringComparer.OrdinalIgnoreCase);
    }

    private IReadOnlyDictionary<string, NativeMacroDefinition> _definitions =
        new Dictionary<string, NativeMacroDefinition>(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, Instance> _instances = new(StringComparer.OrdinalIgnoreCase);
    private readonly Queue<string> _pendingActions = new();

    public int ActiveCount => _instances.Count;
    public string[] ActiveNames => _instances.Values.Select(value => value.Definition.Name).Distinct().OrderBy(value => value).ToArray();

    public void Configure(IReadOnlyDictionary<string, NativeMacroDefinition> definitions)
    {
        _definitions = definitions;
        _instances.Clear();
        _pendingActions.Clear();
    }

    public bool Start(string name, string source, double now)
    {
        if (!_definitions.TryGetValue(name, out var definition) || definition.Steps.Length == 0) return false;
        if (_instances.ContainsKey(source)) return false;
        _instances[source] = new Instance { Definition = definition, Source = source, NextAt = now };
        return true;
    }

    public void CancelReleased(IReadOnlySet<string> activeMacroSources)
    {
        foreach (var pair in _instances.ToArray())
            if (pair.Value.Definition.CancelOnRelease && !activeMacroSources.Contains(pair.Key))
                _instances.Remove(pair.Key);
    }

    public void CancelAll()
    {
        _instances.Clear();
        _pendingActions.Clear();
    }

    public void Apply(double now, ref NativeControllerReport report)
    {
        foreach (var pair in _instances.ToArray())
        {
            var instance = pair.Value;
            Advance(instance, now);
            if (instance.CompletedRepeats >= instance.Definition.RepeatCount)
            {
                _instances.Remove(pair.Key);
                continue;
            }
            foreach (var target in instance.HeldTargets) ApplyDigital(target, ref report);
            foreach (var axis in instance.AxisValues) ApplyAxis(axis.Key, axis.Value, ref report);
        }
    }

    public string[] DrainActions()
    {
        var result = _pendingActions.ToArray();
        _pendingActions.Clear();
        return result;
    }

    private void Advance(Instance instance, double now)
    {
        var operations = 0;
        while (now >= instance.NextAt && instance.CompletedRepeats < instance.Definition.RepeatCount && operations++ < 512)
        {
            if (instance.StepIndex >= instance.Definition.Steps.Length)
            {
                instance.CompletedRepeats++;
                if (instance.CompletedRepeats >= instance.Definition.RepeatCount) return;
                instance.StepIndex = 0;
                instance.HeldTargets.Clear();
                instance.AxisValues.Clear();
                continue;
            }

            var step = instance.Definition.Steps[instance.StepIndex++];
            switch (step.Type)
            {
                case "press":
                    instance.HeldTargets.Add(step.Target);
                    break;
                case "release":
                    instance.HeldTargets.Remove(step.Target);
                    break;
                case "axis":
                    instance.AxisValues[step.Target] = step.Value;
                    break;
                case "delay":
                    instance.NextAt += step.DurationMs / 1000.0;
                    return;
                case "action":
                    if (!string.IsNullOrWhiteSpace(step.Action)) _pendingActions.Enqueue(step.Action);
                    break;
            }
        }
    }

    public static IReadOnlyDictionary<string, NativeMacroDefinition> Parse(JsonElement profile)
    {
        var result = new Dictionary<string, NativeMacroDefinition>(StringComparer.OrdinalIgnoreCase);
        if (!profile.TryGetProperty("macros", out var macros) || macros.ValueKind != JsonValueKind.Object) return result;
        foreach (var macro in macros.EnumerateObject())
        {
            if (string.IsNullOrWhiteSpace(macro.Name) || macro.Name.Length > 64 || macro.Value.ValueKind != JsonValueKind.Object) continue;
            var raw = macro.Value;
            var repeat = raw.TryGetProperty("repeat_count", out var repeatValue) && repeatValue.TryGetInt32(out var parsedRepeat)
                ? Math.Clamp(parsedRepeat, 1, 100) : 1;
            var cancel = raw.TryGetProperty("cancel_on_release", out var cancelValue) && cancelValue.ValueKind == JsonValueKind.True;
            var steps = new List<NativeMacroStep>();
            if (raw.TryGetProperty("steps", out var rawSteps) && rawSteps.ValueKind == JsonValueKind.Array)
            {
                foreach (var step in rawSteps.EnumerateArray().Take(256))
                {
                    if (step.ValueKind != JsonValueKind.Object) continue;
                    var type = Text(step, "type", "");
                    if (type is not ("press" or "release" or "axis" or "delay" or "action")) continue;
                    var target = Text(step, "target", "");
                    var action = Text(step, "action", "");
                    var value = Number(step, "value", 0, -1, 1);
                    var duration = (int)Number(step, "duration_ms", 0, 0, 10000);
                    if ((type is "press" or "release") && !target.StartsWith("Controller:", StringComparison.OrdinalIgnoreCase)) continue;
                    if (type == "axis" && !AxisTargets.Contains(target)) continue;
                    if (type == "action" && action is not ("Fishing Helper Toggle" or "Keyboard Controller Mode" or "Mouse Aim Mode" or "Open Control Center" or "Desktop Mode" or "Cancel Macros")) continue;
                    steps.Add(new(type, target, value, duration, action));
                }
            }
            if (steps.Count > 0) result[macro.Name] = new(macro.Name, steps.ToArray(), repeat, cancel);
        }
        return result;
    }

    private static readonly HashSet<string> AxisTargets = new(StringComparer.OrdinalIgnoreCase)
    {
        "Controller:Left Stick X", "Controller:Left Stick Y", "Controller:Right Stick X", "Controller:Right Stick Y",
        "Controller:LT", "Controller:RT"
    };

    private static void ApplyDigital(string target, ref NativeControllerReport report)
    {
        switch (target)
        {
            case "Controller:A": report.Buttons |= 0x1000; break;
            case "Controller:B": report.Buttons |= 0x2000; break;
            case "Controller:X": report.Buttons |= 0x4000; break;
            case "Controller:Y": report.Buttons |= 0x8000; break;
            case "Controller:LB": report.Buttons |= 0x0100; break;
            case "Controller:RB": report.Buttons |= 0x0200; break;
            case "Controller:Left Stick": report.Buttons |= 0x0040; break;
            case "Controller:Right Stick": report.Buttons |= 0x0080; break;
            case "Controller:Menu": report.Buttons |= 0x0010; break;
            case "Controller:View": report.Buttons |= 0x0020; break;
            case "Controller:Home": report.Buttons |= 0x0400; break;
            case "Controller:D-pad Up": report.Buttons |= 0x0001; break;
            case "Controller:D-pad Down": report.Buttons |= 0x0002; break;
            case "Controller:D-pad Left": report.Buttons |= 0x0004; break;
            case "Controller:D-pad Right": report.Buttons |= 0x0008; break;
            case "Controller:LT": report.LeftTrigger = 255; break;
            case "Controller:RT": report.RightTrigger = 255; break;
            case "Controller:Left Stick Left": report.LeftX = -32768; break;
            case "Controller:Left Stick Right": report.LeftX = 32767; break;
            case "Controller:Left Stick Down": report.LeftY = -32768; break;
            case "Controller:Left Stick Up": report.LeftY = 32767; break;
            case "Controller:Right Stick Left": report.RightX = -32768; break;
            case "Controller:Right Stick Right": report.RightX = 32767; break;
            case "Controller:Right Stick Down": report.RightY = -32768; break;
            case "Controller:Right Stick Up": report.RightY = 32767; break;
        }
    }

    private static void ApplyAxis(string target, double value, ref NativeControllerReport report)
    {
        value = Math.Clamp(value, -1, 1);
        var axis = (short)Math.Clamp((int)Math.Round(value * (value < 0 ? 32768 : 32767)), -32768, 32767);
        switch (target)
        {
            case "Controller:Left Stick X": report.LeftX = axis; break;
            case "Controller:Left Stick Y": report.LeftY = axis; break;
            case "Controller:Right Stick X": report.RightX = axis; break;
            case "Controller:Right Stick Y": report.RightY = axis; break;
            case "Controller:LT": report.LeftTrigger = (byte)Math.Round(Math.Clamp(value, 0, 1) * 255); break;
            case "Controller:RT": report.RightTrigger = (byte)Math.Round(Math.Clamp(value, 0, 1) * 255); break;
        }
    }

    private static string Text(JsonElement e, string k, string d) =>
        e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? d : d;
    private static double Number(JsonElement e, string k, double d, double low, double high) =>
        e.TryGetProperty(k, out var v) && v.TryGetDouble(out var n) && double.IsFinite(n) ? Math.Clamp(n, low, high) : d;
}
