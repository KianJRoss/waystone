using System.Text.Json.Nodes;
using UnifiedController.Core;

namespace UnifiedController.Linux.App;

/// Additive operations. Uses the existing runtime's command queue, never a new input loop.
public sealed class ControllerCommandService
{
    public static readonly string[] AllOperations = ["command_capabilities", "motion_recenter", "motion_calibrate", "rumble_test", "rumble_stop"];
    private readonly IControllerRuntime _runtime;
    private readonly HashSet<string> _implemented;
    public ControllerCommandService(IControllerRuntime runtime, IEnumerable<string>? implemented = null)
    {
        _runtime = runtime;
        // Interface default rumble methods may throw/no-op. Never claim those as implementations.
        _implemented = new HashSet<string>(implemented ?? (runtime is ControllerRuntime ? AllOperations : ["command_capabilities"]), StringComparer.Ordinal);
        _implemented.IntersectWith(AllOperations);
    }
    public static bool Handles(string? operation) => operation is not null && AllOperations.Contains(operation);
    private bool MotionAvailable => _runtime.Snapshot.Running && _runtime.Snapshot.Motion is not null;
    private bool PhysicalAvailable {
        get {
            var s=_runtime.Snapshot;
            return s.Running && s.Devices.Any(d => d.Connected && !d.IsVirtual &&
                (s.SelectedSourceId=="all_connected" || d.StableId==s.SelectedSourceId));
        }
    }
    private IEnumerable<string> Available => AllOperations.Where(op => op == "command_capabilities" || _implemented.Contains(op) &&
        (op == "rumble_stop" ? _runtime.Snapshot.Running : (op.StartsWith("motion_") ? MotionAvailable : PhysicalAvailable)));
    private static double Motor(JsonObject request, string key)
    {
        if (request[key] is not JsonValue v || !v.TryGetValue<double>(out var n) || !double.IsFinite(n) || n < 0 || n > 1)
            throw new ControllerOperationException("ERR_INVALID_REQUEST");
        return n;
    }
    public JsonObject Handle(JsonObject request)
    {
        var op = request["operation"] is JsonValue v && v.TryGetValue<string>(out var name) ? name : null;
        if (!Handles(op)) throw new ControllerOperationException("ERR_UNSUPPORTED_OPERATION");
        var allowed = new HashSet<string>(["version", "request_id", "operation"], StringComparer.Ordinal);
        if (op == "rumble_test") allowed.UnionWith(["heavy", "light", "duration_ms"]);
        if (request.Any(x => !allowed.Contains(x.Key))) throw new ControllerOperationException("ERR_INVALID_REQUEST");
        // Validate before side effects; never silently clamp user input into a different command.
        double heavy = 0, light = 0; int duration = 0;
        if (op == "rumble_test")
        {
            heavy = Motor(request, "heavy"); light = Motor(request, "light");
            if (request["duration_ms"] is not JsonValue dv || !dv.TryGetValue<int>(out duration) || duration < 1 || duration > 2000)
                throw new ControllerOperationException("ERR_INVALID_REQUEST");
        }
        if (op == "command_capabilities") return new JsonObject {
            ["schema_version"] = 1, ["running"] = _runtime.Snapshot.Running,
            ["motion_available"] = MotionAvailable, ["physical_source_available"] = PhysicalAvailable,
            ["operations"] = new JsonArray(Available.Select(x => (JsonNode?)JsonValue.Create(x)).ToArray()) };
        if (!_implemented.Contains(op!)) throw new ControllerOperationException("ERR_UNSUPPORTED_OPERATION");
        if (!Available.Contains(op!)) throw new ControllerOperationException("ERR_COMMAND_UNAVAILABLE");
        try
        {
            switch (op)
            {
                case "motion_recenter": _runtime.RecenterMotion(); break;
                case "motion_calibrate": _runtime.CalibrateMotion(); break;
                case "rumble_test": _runtime.TestRumble(heavy, light, duration); break;
                case "rumble_stop": _runtime.StopRumble(); break;
            }
        }
        catch (NotSupportedException) { throw new ControllerOperationException("ERR_UNSUPPORTED_OPERATION"); }
        return new JsonObject { ["accepted"] = true, ["operation"] = op, ["completion"] = "queued" };
    }
}
