using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.IO;

namespace UnifiedController.App.Engine;

public sealed record NativeRawDevice(string Id, string Kind, string Name, string Path, bool Connected, DateTime LastSeenUtc, string LastInput);
public sealed record NativeRawMouseState(int DeltaX, int DeltaY, HashSet<string> Buttons, HashSet<string> Pulses);
public sealed record NativeRawInputEvent(long Sequence, string DeviceId, string Kind, string Control);
public sealed record NativeRawInputSnapshot(
    IReadOnlyDictionary<string, HashSet<int>> Keyboards,
    IReadOnlyDictionary<string, NativeRawMouseState> Mice);

public sealed class NativeRawInputManager
{
    private const int WM_INPUT = 0x00FF;
    private const int WM_INPUT_DEVICE_CHANGE = 0x00FE;
    private const uint RID_INPUT = 0x10000003;
    private const uint RIDI_DEVICENAME = 0x20000007;
    private const uint RIDEV_INPUTSINK = 0x00000100;
    private const uint RIDEV_DEVNOTIFY = 0x00002000;
    private readonly object _gate = new();
    private readonly Dictionary<nint, NativeRawDevice> _devices = [];
    private readonly Dictionary<string, NativeRawDevice> _knownDevices = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<nint, HashSet<int>> _keys = [];
    private readonly Dictionary<nint, MouseAccumulator> _mice = [];
    private long _eventSequence;
    private NativeRawInputEvent? _lastEvent;
    private readonly string _registryPath;
    private bool _registryDirty;
    private DateTime _lastRegistryWriteUtc;

    private sealed class MouseAccumulator
    {
        public int X;
        public int Y;
        public readonly HashSet<string> Buttons = new(StringComparer.OrdinalIgnoreCase);
        public readonly HashSet<string> Pulses = new(StringComparer.OrdinalIgnoreCase);
    }

    public NativeRawInputManager(nint windowHandle, string rootDirectory)
    {
        _registryPath = Path.Combine(rootDirectory, "devices.json");
        LoadRegistry();
        RAWINPUTDEVICE[] devices =
        [
            new() { usUsagePage = 1, usUsage = 6, dwFlags = RIDEV_INPUTSINK | RIDEV_DEVNOTIFY, hwndTarget = windowHandle },
            new() { usUsagePage = 1, usUsage = 2, dwFlags = RIDEV_INPUTSINK | RIDEV_DEVNOTIFY, hwndTarget = windowHandle }
        ];
        if (!RegisterRawInputDevices(devices, (uint)devices.Length, (uint)Marshal.SizeOf<RAWINPUTDEVICE>()))
            throw new InvalidOperationException($"Raw Input registration failed ({Marshal.GetLastWin32Error()}).");
        RefreshDevices();
    }

    public IReadOnlyList<NativeRawDevice> Devices
    {
        get
        {
            lock (_gate)
            {
                PersistRegistryIfDue();
                return _knownDevices.Values.OrderBy(value => value.Kind).ThenBy(value => value.Name).ToArray();
            }
        }
    }
    public NativeRawInputEvent? LastEvent { get { lock (_gate) return _lastEvent; } }

    public bool ProcessMessage(int message, nint wParam, nint lParam)
    {
        if (message == WM_INPUT_DEVICE_CHANGE)
        {
            RefreshDevices();
            return false;
        }
        if (message != WM_INPUT) return false;
        ReadInput(lParam);
        return false;
    }

    public NativeRawInputSnapshot DrainSnapshot()
    {
        lock (_gate)
        {
            var keyboards = new Dictionary<string, HashSet<int>>(StringComparer.OrdinalIgnoreCase);
            foreach (var pair in _keys)
                if (_devices.TryGetValue(pair.Key, out var device) && device.Connected)
                    keyboards[device.Id] = new HashSet<int>(pair.Value);
            var mice = new Dictionary<string, NativeRawMouseState>(StringComparer.OrdinalIgnoreCase);
            foreach (var pair in _mice)
            {
                if (!_devices.TryGetValue(pair.Key, out var device) || !device.Connected) continue;
                mice[device.Id] = new NativeRawMouseState(pair.Value.X, pair.Value.Y,
                    new HashSet<string>(pair.Value.Buttons, StringComparer.OrdinalIgnoreCase),
                    new HashSet<string>(pair.Value.Pulses, StringComparer.OrdinalIgnoreCase));
                pair.Value.X = 0;
                pair.Value.Y = 0;
                pair.Value.Pulses.Clear();
            }
            return new NativeRawInputSnapshot(keyboards, mice);
        }
    }

    private void ReadInput(nint rawHandle)
    {
        uint size = 0;
        var headerSize = (uint)Marshal.SizeOf<RAWINPUTHEADER>();
        if (GetRawInputData(rawHandle, RID_INPUT, nint.Zero, ref size, headerSize) != 0 || size == 0) return;
        var buffer = Marshal.AllocHGlobal((int)size);
        try
        {
            if (GetRawInputData(rawHandle, RID_INPUT, buffer, ref size, headerSize) != size) return;
            var header = Marshal.PtrToStructure<RAWINPUTHEADER>(buffer);
            if (header.hDevice == nint.Zero) return;
            var body = buffer + Marshal.SizeOf<RAWINPUTHEADER>();
            lock (_gate)
            {
                EnsureDevice(header.hDevice, header.dwType);
                if (header.dwType == 1)
                {
                    var keyboard = Marshal.PtrToStructure<RAWKEYBOARD>(body);
                    if (keyboard.VKey == 255) return;
                    if (!_keys.TryGetValue(header.hDevice, out var keys)) _keys[header.hDevice] = keys = [];
                    if ((keyboard.Flags & 1) != 0) keys.Remove(keyboard.VKey);
                    else
                    {
                        keys.Add(keyboard.VKey);
                        PublishEvent(header.hDevice, "keyboard", keyboard.VKey.ToString());
                    }
                    Touch(header.hDevice, $"Key {((keyboard.Flags & 1) != 0 ? "up" : "down")}: {keyboard.VKey}");
                }
                else if (header.dwType == 0)
                {
                    var mouse = Marshal.PtrToStructure<RAWMOUSE>(body);
                    if (!_mice.TryGetValue(header.hDevice, out var state)) _mice[header.hDevice] = state = new MouseAccumulator();
                    state.X += mouse.lLastX;
                    state.Y += mouse.lLastY;
                    var flags = (ushort)(mouse.ulButtons & 0xFFFF);
                    UpdateButton(flags, 0x0001, 0x0002, "Left", state);
                    UpdateButton(flags, 0x0004, 0x0008, "Right", state);
                    UpdateButton(flags, 0x0010, 0x0020, "Middle", state);
                    UpdateButton(flags, 0x0040, 0x0080, "X1", state);
                    UpdateButton(flags, 0x0100, 0x0200, "X2", state);
                    PublishMouseDown(header.hDevice, flags, 0x0001, "Left");
                    PublishMouseDown(header.hDevice, flags, 0x0004, "Right");
                    PublishMouseDown(header.hDevice, flags, 0x0010, "Middle");
                    PublishMouseDown(header.hDevice, flags, 0x0040, "X1");
                    PublishMouseDown(header.hDevice, flags, 0x0100, "X2");
                    if ((flags & 0x0400) != 0)
                    {
                        var delta = unchecked((short)(mouse.ulButtons >> 16));
                        var wheel = delta > 0 ? "Wheel Up" : "Wheel Down";
                        state.Pulses.Add(wheel);
                        PublishEvent(header.hDevice, "mouse", wheel);
                    }
                    Touch(header.hDevice, mouse.lLastX != 0 || mouse.lLastY != 0 ? $"Move {mouse.lLastX}, {mouse.lLastY}" : "Button");
                }
            }
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    private static void UpdateButton(ushort flags, ushort down, ushort up, string name, MouseAccumulator state)
    {
        if ((flags & down) != 0) state.Buttons.Add(name);
        if ((flags & up) != 0) state.Buttons.Remove(name);
    }

    private void PublishMouseDown(nint handle, ushort flags, ushort down, string control)
    {
        if ((flags & down) != 0) PublishEvent(handle, "mouse", control);
    }

    private void PublishEvent(nint handle, string kind, string control)
    {
        if (_devices.TryGetValue(handle, out var device))
            _lastEvent = new NativeRawInputEvent(++_eventSequence, device.Id, kind, control);
    }

    private void Touch(nint handle, string input)
    {
        if (_devices.TryGetValue(handle, out var device))
        {
            var updated = device with { LastSeenUtc = DateTime.UtcNow, LastInput = input, Connected = true };
            _devices[handle] = updated;
            _knownDevices[updated.Id] = updated;
            _registryDirty = true;
        }
    }

    private void RefreshDevices()
    {
        uint count = 0;
        var itemSize = (uint)Marshal.SizeOf<RAWINPUTDEVICELIST>();
        if (GetRawInputDeviceList(null, ref count, itemSize) != 0 || count == 0) return;
        var list = new RAWINPUTDEVICELIST[count];
        if (GetRawInputDeviceList(list, ref count, itemSize) == uint.MaxValue) return;
        lock (_gate)
        {
            var connected = new HashSet<nint>();
            foreach (var item in list)
            {
                if (item.dwType is not (0u or 1u)) continue;
                connected.Add(item.hDevice);
                EnsureDevice(item.hDevice, item.dwType);
            }
            foreach (var pair in _devices.ToArray())
                if (!connected.Contains(pair.Key))
                {
                    _devices[pair.Key] = pair.Value with { Connected = false };
                    _knownDevices[pair.Value.Id] = pair.Value with { Connected = false };
                    _registryDirty = true;
                    _keys.Remove(pair.Key);
                    _mice.Remove(pair.Key);
                }
        }
    }

    private void EnsureDevice(nint handle, uint type)
    {
        if (_devices.TryGetValue(handle, out var existing))
        {
            if (!existing.Connected) _devices[handle] = existing with { Connected = true };
            return;
        }
        var path = DevicePath(handle);
        var kind = type == 1 ? "keyboard" : "mouse";
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(path.ToLowerInvariant())))[..12].ToLowerInvariant();
        var id = $"raw-{kind}-{hash}";
        var vidPid = ExtractVidPid(path);
        _devices[handle] = new NativeRawDevice(id, kind, $"{char.ToUpperInvariant(kind[0])}{kind[1..]} {vidPid} [{hash[..6]}]", path, true, DateTime.UtcNow, "Idle");
        _knownDevices[id] = _devices[handle];
        _registryDirty = true;
        if (type == 1) _keys[handle] = [];
        else _mice[handle] = new MouseAccumulator();
    }

    private static string DevicePath(nint handle)
    {
        uint chars = 0;
        GetRawInputDeviceInfo(handle, RIDI_DEVICENAME, nint.Zero, ref chars);
        if (chars == 0) return $"handle:{handle}";
        var buffer = Marshal.AllocHGlobal((int)((chars + 1) * 2));
        try
        {
            return GetRawInputDeviceInfo(handle, RIDI_DEVICENAME, buffer, ref chars) == uint.MaxValue
                ? $"handle:{handle}" : Marshal.PtrToStringUni(buffer) ?? $"handle:{handle}";
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    private static string ExtractVidPid(string path)
    {
        var upper = path.ToUpperInvariant();
        var vid = upper.IndexOf("VID_", StringComparison.Ordinal);
        var pid = upper.IndexOf("PID_", StringComparison.Ordinal);
        return vid >= 0 && pid >= 0 && upper.Length >= Math.Max(vid, pid) + 8
            ? $"{upper.Substring(vid, 8)} {upper.Substring(pid, 8)}" : "device";
    }

    private void LoadRegistry()
    {
        lock (NativeDeviceRegistryStore.Gate)
        {
            try
            {
                if (!File.Exists(_registryPath)) return;
                var root = JsonNode.Parse(File.ReadAllText(_registryPath)) as JsonObject;
                if (root?["devices"] is not JsonObject devices) return;
                foreach (var pair in devices)
                {
                    if (pair.Value is not JsonObject raw || raw["provider"]?.GetValue<string>() != "RawInput") continue;
                    var id = raw["id"]?.GetValue<string>() ?? pair.Key;
                    var kind = raw["kind"]?.GetValue<string>() ?? "unknown";
                    var name = raw["display_name"]?.GetValue<string>() ?? id;
                    var path = raw["device_path"]?.GetValue<string>() ?? "";
                    if (!path.StartsWith(@"\\?\", StringComparison.Ordinal)) continue;
                    var lastSeen = raw["last_seen"]?.GetValue<double>() is double seconds
                        ? DateTime.UnixEpoch.AddSeconds(seconds) : DateTime.UnixEpoch;
                    _knownDevices[id] = new NativeRawDevice(id, kind, name, path, false, lastSeen, "Disconnected");
                }
            }
            catch (Exception ex) when (ex is IOException or JsonException or InvalidOperationException) { }
        }
    }

    private void PersistRegistryIfDue()
    {
        if (!_registryDirty || DateTime.UtcNow - _lastRegistryWriteUtc < TimeSpan.FromSeconds(2)) return;
        lock (NativeDeviceRegistryStore.Gate)
        {
            try
            {
                var root = File.Exists(_registryPath) ? JsonNode.Parse(File.ReadAllText(_registryPath)) as JsonObject : null;
                root ??= new JsonObject();
                root["version"] = 1;
                if (root["devices"] is not JsonObject devices) root["devices"] = devices = new JsonObject();
                foreach (var stale in devices.Where(pair => pair.Value is JsonObject value &&
                             value["provider"]?.GetValue<string>() == "RawInput" && !_knownDevices.ContainsKey(pair.Key))
                         .Select(pair => pair.Key).ToArray())
                    devices.Remove(stale);
                foreach (var device in _knownDevices.Values)
                {
                    var existing = devices[device.Id] as JsonObject;
                    var firstSeen = existing?["first_seen"]?.GetValue<double>() ?? DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
                    devices[device.Id] = new JsonObject
                    {
                        ["id"] = device.Id,
                        ["kind"] = device.Kind,
                        ["provider"] = "RawInput",
                        ["native_id"] = device.Id,
                        ["display_name"] = device.Name,
                        ["device_path"] = device.Path,
                        ["identity_stability"] = "device_path",
                        ["assignable"] = true,
                        ["connected"] = device.Connected,
                        ["first_seen"] = firstSeen,
                        ["last_seen"] = new DateTimeOffset(device.LastSeenUtc).ToUnixTimeMilliseconds() / 1000.0
                    };
                }
                var temporary = _registryPath + $".{Guid.NewGuid():N}.tmp";
                File.WriteAllText(temporary, root.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) + "\n");
                File.Move(temporary, _registryPath, true);
                _registryDirty = false;
                _lastRegistryWriteUtc = DateTime.UtcNow;
            }
            catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException) { }
        }
    }

    [DllImport("user32.dll", SetLastError = true)] private static extern bool RegisterRawInputDevices(RAWINPUTDEVICE[] devices, uint count, uint size);
    [DllImport("user32.dll", SetLastError = true)] private static extern uint GetRawInputData(nint raw, uint command, nint data, ref uint size, uint headerSize);
    [DllImport("user32.dll", SetLastError = true)] private static extern uint GetRawInputDeviceList([In, Out] RAWINPUTDEVICELIST[]? list, ref uint count, uint size);
    [DllImport("user32.dll", EntryPoint = "GetRawInputDeviceInfoW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetRawInputDeviceInfo(nint device, uint command, nint data, ref uint size);
    [StructLayout(LayoutKind.Sequential)] private struct RAWINPUTDEVICE { public ushort usUsagePage; public ushort usUsage; public uint dwFlags; public nint hwndTarget; }
    [StructLayout(LayoutKind.Sequential)] private struct RAWINPUTDEVICELIST { public nint hDevice; public uint dwType; }
    [StructLayout(LayoutKind.Sequential)] private struct RAWINPUTHEADER { public uint dwType; public uint dwSize; public nint hDevice; public nint wParam; }
    [StructLayout(LayoutKind.Sequential)] private struct RAWKEYBOARD { public ushort MakeCode; public ushort Flags; public ushort Reserved; public ushort VKey; public uint Message; public uint ExtraInformation; }
    [StructLayout(LayoutKind.Sequential)] private struct RAWMOUSE { public ushort usFlags; public uint ulButtons; public uint ulRawButtons; public int lLastX; public int lLastY; public uint ulExtraInformation; }
}
