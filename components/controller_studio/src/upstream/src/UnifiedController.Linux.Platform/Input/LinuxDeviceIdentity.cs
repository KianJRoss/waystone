using System.Security.Cryptography;
using System.Text;

namespace UnifiedController.Linux.Platform.Input;

public sealed record LinuxDeviceIdentity(string StableId, string PhysicalPath, string UniqueId, ushort? BusType);

public static class LinuxDeviceIdentityResolver
{
    /// <summary>Called by the bounded discovery pass, never by the high-frequency poll path.</summary>
    public static LinuxDeviceIdentity Resolve(Guid guid, string? serial, string? sdlPath, ushort vendor, ushort product)
    {
        var path = sdlPath ?? string.Empty;
        var phys = string.Empty;
        var uniq = serial?.Trim() ?? string.Empty;
        ushort? bus = null;
        try
        {
            var eventName = Path.GetFileName(path);
            if (eventName.StartsWith("event", StringComparison.Ordinal) && Directory.Exists($"/sys/class/input/{eventName}"))
            {
                var root = $"/sys/class/input/{eventName}/device";
                phys = Read(root + "/phys");
                if (uniq.Length == 0) uniq = Read(root + "/uniq");
                if (ushort.TryParse(Read(root + "/id/bustype"), System.Globalization.NumberStyles.HexNumber, null, out var parsed)) bus = parsed;
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }

        // Serial/uniq is reconnect-stable. Physical topology is the next best Linux identity;
        // SDL's transient enumeration index and joystick instance id are deliberately absent.
        var durable = uniq.Length > 0 ? "serial:" + uniq :
            phys.Length > 0 ? "phys:" + phys : "path:" + CanonicalPath(path);
        var material = $"{guid:N}|{vendor:x4}:{product:x4}|{durable}";
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(material))).ToLowerInvariant()[..24];
        return new LinuxDeviceIdentity("sdl-" + hash, phys.Length > 0 ? phys : path, uniq, bus);
    }

    private static string Read(string path) => File.Exists(path) ? File.ReadAllText(path).Trim() : string.Empty;
    private static string CanonicalPath(string path)
    {
        try { return Path.GetFullPath(path); }
        catch { return path; }
    }
}
