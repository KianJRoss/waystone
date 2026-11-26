using System.Text.Json;
using System.IO;

namespace UnifiedController.App.Engine;

public static class NativeControllerTelemetry
{
    public static JsonDocument Read(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete);
        return JsonDocument.Parse(stream);
    }

    public static bool IsFresh(JsonElement root, DateTimeOffset now, int? processId = null)
    {
        if (processId.HasValue && (!root.TryGetProperty("process_id", out var owner) ||
            !owner.TryGetInt32(out var pid) || pid != processId.Value)) return false;
        return !root.TryGetProperty("updated", out var updated) ||
            (updated.ValueKind == JsonValueKind.String && updated.TryGetDateTimeOffset(out var at) &&
                Math.Abs((now - at).TotalSeconds) <= 2);
    }

    public static bool SourcePresent(JsonElement root) => IsFresh(root, DateTimeOffset.UtcNow) && (
        root.TryGetProperty("source_present", out var present)
            ? present.ValueKind == JsonValueKind.True
            : root.TryGetProperty("active_source_index", out var index) && index.ValueKind == JsonValueKind.Number);
}
