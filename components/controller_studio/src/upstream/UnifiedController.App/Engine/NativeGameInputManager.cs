using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace UnifiedController.App.Engine;

internal static class NativeDeviceRegistryStore
{
    internal static readonly object Gate = new();
}

public sealed record NativeGameInputDevice(
    string Id, string DeviceId, string RootId, ushort VendorId, ushort ProductId,
    string Name, bool Connected, bool IsVirtual, int? XInputSlot,
    DateTime LastSeenUtc, string LastInput);

/// <summary>
/// Uses the inbox Windows GameInput provider for durable controller identity and
/// live device discovery. XInput remains the forwarding provider; matching live
/// reports correlate its transient user index with GameInput's persistent root ID.
/// </summary>
public sealed class NativeGameInputManager : IDisposable
{
    private const uint GamepadKind = 0x00040000;
    private const uint ConnectedStatus = 0x00000001;
    private readonly object _gate = new();
    private readonly Dictionary<string, Entry> _entries = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<(string id, int slot), int> _correlationScores = [];
    private readonly string _registryPath;
    private nint _gameInput;
    private ulong _callbackToken;
    private DeviceCallback? _deviceCallback;
    private Thread? _pollThread;
    private volatile bool _running;
    private bool _registryDirty;
    private DateTime _lastRegistryWriteUtc;

    private sealed class Entry
    {
        public required NativeGameInputDevice Device;
        public nint Pointer;
        public NativeControllerReport Report = new();
        public bool HasReport;
    }

    public NativeGameInputManager(string rootDirectory)
    {
        _registryPath = Path.Combine(rootDirectory, "devices.json");
        LoadRegistry();
        try
        {
            var hr = GameInputCreate(out _gameInput);
            if (hr < 0 || _gameInput == nint.Zero)
            {
                Error = $"GameInputCreate failed (0x{hr:X8})";
                return;
            }

            _deviceCallback = OnDeviceChanged;
            var register = Method<RegisterDeviceCallbackDelegate>(_gameInput, 9);
            hr = register(_gameInput, nint.Zero, GamepadKind, ConnectedStatus, 1,
                nint.Zero, _deviceCallback, out _callbackToken);
            if (hr < 0)
            {
                Error = $"RegisterDeviceCallback failed (0x{hr:X8})";
                Release(_gameInput);
                _gameInput = nint.Zero;
                return;
            }

            Available = true;
            _running = true;
            _pollThread = new Thread(PollLoop)
            {
                IsBackground = true,
                Name = "GameInputIdentityPoll"
            };
            _pollThread.Start();
        }
        catch (Exception ex) when (ex is DllNotFoundException or EntryPointNotFoundException or BadImageFormatException)
        {
            Error = ex.Message;
        }
    }

    public bool Available { get; }
    public string? Error { get; }

    public IReadOnlyList<NativeGameInputDevice> Devices
    {
        get
        {
            lock (_gate)
            {
                PersistRegistryIfDue();
                return _entries.Values.Select(entry => entry.Device)
                    .OrderBy(device => device.IsVirtual).ThenBy(device => device.Name).ToArray();
            }
        }
    }

    public string? DeviceIdForXInputSlot(int slot)
    {
        lock (_gate)
            return _entries.Values.FirstOrDefault(entry => entry.Device.Connected && !entry.Device.IsVirtual &&
                entry.Device.XInputSlot == slot)?.Device.Id;
    }

    public void ObserveXInput(int slot, NativeControllerReport report)
    {
        if (IsNeutral(report)) return;
        lock (_gate)
        {
            foreach (var entry in _entries.Values.Where(value => value.Device.Connected && !value.Device.IsVirtual && value.HasReport))
            {
                var key = (entry.Device.Id, slot);
                if (ReportsMatch(entry.Report, report))
                {
                    var score = _correlationScores.GetValueOrDefault(key) + 1;
                    _correlationScores[key] = score;
                    if (score >= 3 && entry.Device.XInputSlot != slot)
                    {
                        foreach (var other in _entries.Values.Where(value => value.Device.XInputSlot == slot && value != entry))
                            other.Device = other.Device with { XInputSlot = null };
                        entry.Device = entry.Device with { XInputSlot = slot };
                        _registryDirty = true;
                    }
                }
                else
                {
                    _correlationScores[key] = 0;
                }
            }
        }
    }

    private void OnDeviceChanged(ulong token, nint context, nint device, ulong timestamp, uint currentStatus, uint previousStatus)
    {
        if (device == nint.Zero) return;
        try
        {
            var getInfo = Method<GetDeviceInfoDelegate>(device, 3);
            var info = getInfo(device);
            if (info == nint.Zero) return;
            var vendorId = unchecked((ushort)Marshal.ReadInt16(info, 4));
            var productId = unchecked((ushort)Marshal.ReadInt16(info, 6));
            var deviceId = ReadId(info + 32);
            var rootId = ReadId(info + 64);
            if (rootId.All(ch => ch == '0')) rootId = deviceId;
            var id = $"gameinput-{rootId}";
            var connected = (currentStatus & ConnectedStatus) != 0;
            var family = Marshal.ReadInt32(info, 96);
            var isVirtual = family == -1 || (vendorId == 0x1209 && productId == 0xC710);
            var type = isVirtual ? "Virtual controller" : "Controller";
            var name = $"{type} VID_{vendorId:X4} PID_{productId:X4} [{rootId[..8]}]";

            lock (_gate)
            {
                if (!_entries.TryGetValue(id, out var entry))
                {
                    entry = new Entry
                    {
                        Device = new NativeGameInputDevice(id, deviceId, rootId, vendorId, productId,
                            name, connected, isVirtual, null, DateTime.UtcNow,
                            connected ? "Idle" : "Disconnected")
                    };
                    _entries[id] = entry;
                }
                entry.Device = entry.Device with
                {
                    DeviceId = deviceId,
                    VendorId = vendorId,
                    ProductId = productId,
                    Name = name,
                    Connected = connected,
                    IsVirtual = isVirtual,
                    LastSeenUtc = DateTime.UtcNow,
                    LastInput = connected ? entry.Device.LastInput : "Disconnected"
                };
                if (connected && entry.Pointer == nint.Zero)
                {
                    AddRef(device);
                    entry.Pointer = device;
                }
                else if (!connected && entry.Pointer != nint.Zero)
                {
                    Release(entry.Pointer);
                    entry.Pointer = nint.Zero;
                    entry.HasReport = false;
                }
                _registryDirty = true;
            }
        }
        catch { }
    }

    private void PollLoop()
    {
        while (_running)
        {
            List<(string id, nint pointer)> targets = [];
            lock (_gate)
            {
                foreach (var pair in _entries.Where(pair => pair.Value.Pointer != nint.Zero && pair.Value.Device.Connected))
                {
                    AddRef(pair.Value.Pointer);
                    targets.Add((pair.Key, pair.Value.Pointer));
                }
            }
            foreach (var target in targets)
            {
                try
                {
                    if (TryRead(target.pointer, out var report)) UpdateReport(target.id, report);
                }
                catch { }
                finally { Release(target.pointer); }
            }
            Thread.Sleep(4);
        }
    }

    private bool TryRead(nint device, out NativeControllerReport report)
    {
        report = new NativeControllerReport();
        var getReading = Method<GetCurrentReadingDelegate>(_gameInput, 4);
        var hr = getReading(_gameInput, GamepadKind, device, out var reading);
        if (hr < 0 || reading == nint.Zero) return false;
        try
        {
            var getState = Method<GetGamepadStateDelegate>(reading, 22);
            if (!getState(reading, out var state)) return false;
            report = new NativeControllerReport
            {
                Buttons = MapButtons(state.Buttons),
                LeftTrigger = UnitByte(state.LeftTrigger),
                RightTrigger = UnitByte(state.RightTrigger),
                LeftX = UnitShort(state.LeftX),
                LeftY = UnitShort(state.LeftY),
                RightX = UnitShort(state.RightX),
                RightY = UnitShort(state.RightY)
            };
            return true;
        }
        finally { Release(reading); }
    }

    private void UpdateReport(string id, NativeControllerReport report)
    {
        lock (_gate)
        {
            if (!_entries.TryGetValue(id, out var entry)) return;
            var changed = !entry.HasReport || !ReportsMatch(entry.Report, report);
            entry.Report = report;
            entry.HasReport = true;
            if (changed)
            {
                entry.Device = entry.Device with
                {
                    LastSeenUtc = DateTime.UtcNow,
                    LastInput = Describe(report)
                };
                _registryDirty = true;
            }
        }
    }

    private static string Describe(NativeControllerReport report)
    {
        if (report.Buttons != 0) return $"Buttons 0x{report.Buttons:X4}";
        if (report.LeftTrigger > 2 || report.RightTrigger > 2) return $"Triggers {report.LeftTrigger}/{report.RightTrigger}";
        if (!IsNeutral(report)) return $"Sticks {report.LeftX},{report.LeftY} / {report.RightX},{report.RightY}";
        return "Idle";
    }

    private static bool IsNeutral(NativeControllerReport report) => report.Buttons == 0 &&
        report.LeftTrigger <= 2 && report.RightTrigger <= 2 &&
        Math.Abs((int)report.LeftX) < 1024 && Math.Abs((int)report.LeftY) < 1024 &&
        Math.Abs((int)report.RightX) < 1024 && Math.Abs((int)report.RightY) < 1024;

    private static bool ReportsMatch(NativeControllerReport a, NativeControllerReport b) =>
        a.Buttons == b.Buttons && Math.Abs(a.LeftTrigger - b.LeftTrigger) <= 3 &&
        Math.Abs(a.RightTrigger - b.RightTrigger) <= 3 &&
        Math.Abs((int)a.LeftX - b.LeftX) <= 768 && Math.Abs((int)a.LeftY - b.LeftY) <= 768 &&
        Math.Abs((int)a.RightX - b.RightX) <= 768 && Math.Abs((int)a.RightY - b.RightY) <= 768;

    private static int MapButtons(uint buttons)
    {
        var mapped = 0;
        if ((buttons & 0x0001) != 0) mapped |= 0x0010; // Menu / Start
        if ((buttons & 0x0002) != 0) mapped |= 0x0020; // View / Back
        if ((buttons & 0x0004) != 0) mapped |= 0x1000;
        if ((buttons & 0x0008) != 0) mapped |= 0x2000;
        if ((buttons & 0x0010) != 0) mapped |= 0x4000;
        if ((buttons & 0x0020) != 0) mapped |= 0x8000;
        if ((buttons & 0x0040) != 0) mapped |= 0x0001;
        if ((buttons & 0x0080) != 0) mapped |= 0x0002;
        if ((buttons & 0x0100) != 0) mapped |= 0x0004;
        if ((buttons & 0x0200) != 0) mapped |= 0x0008;
        if ((buttons & 0x0400) != 0) mapped |= 0x0100;
        if ((buttons & 0x0800) != 0) mapped |= 0x0200;
        if ((buttons & 0x1000) != 0) mapped |= 0x0040;
        if ((buttons & 0x2000) != 0) mapped |= 0x0080;
        return mapped;
    }

    private static byte UnitByte(float value) => (byte)Math.Clamp((int)MathF.Round(value * 255f), 0, 255);
    private static short UnitShort(float value)
    {
        var scaled = value >= 0 ? value * short.MaxValue : value * 32768f;
        return (short)Math.Clamp((int)MathF.Round(scaled), short.MinValue, short.MaxValue);
    }

    private static string ReadId(nint pointer)
    {
        var bytes = new byte[32];
        Marshal.Copy(pointer, bytes, 0, bytes.Length);
        return Convert.ToHexString(bytes).ToLowerInvariant();
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
                    if (pair.Value is not JsonObject node || node["provider"]?.GetValue<string>() != "GameInput") continue;
                    var id = node["id"]?.GetValue<string>() ?? pair.Key;
                    var deviceId = node["device_id"]?.GetValue<string>() ?? "";
                    var rootId = node["root_id"]?.GetValue<string>() ?? id.Replace("gameinput-", "");
                    var vid = (ushort)(node["vendor_id"]?.GetValue<int>() ?? 0);
                    var pid = (ushort)(node["product_id"]?.GetValue<int>() ?? 0);
                    var name = node["display_name"]?.GetValue<string>() ?? id;
                    var isVirtual = node["is_virtual"]?.GetValue<bool>() ?? false;
                    var slot = node["correlated_xinput_slot"]?.GetValue<int?>();
                    var lastSeen = node["last_seen"]?.GetValue<double>() is double seconds
                        ? DateTime.UnixEpoch.AddSeconds(seconds) : DateTime.UnixEpoch;
                    _entries[id] = new Entry { Device = new NativeGameInputDevice(id, deviceId, rootId, vid, pid,
                        name, false, isVirtual, slot, lastSeen, "Disconnected") };
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
                foreach (var device in _entries.Values.Select(entry => entry.Device))
                {
                    var existing = devices[device.Id] as JsonObject;
                    var firstSeen = existing?["first_seen"]?.GetValue<double>() ?? DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
                    devices[device.Id] = new JsonObject
                    {
                        ["id"] = device.Id,
                        ["kind"] = "controller",
                        ["provider"] = "GameInput",
                        ["native_id"] = device.RootId,
                        ["device_id"] = device.DeviceId,
                        ["root_id"] = device.RootId,
                        ["vendor_id"] = device.VendorId,
                        ["product_id"] = device.ProductId,
                        ["display_name"] = device.Name,
                        ["identity_stability"] = "app_local_root_id",
                        ["assignable"] = !device.IsVirtual,
                        ["is_virtual"] = device.IsVirtual,
                        ["connected"] = device.Connected,
                        ["correlated_xinput_slot"] = device.XInputSlot,
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

    public void Dispose()
    {
        _running = false;
        try { _pollThread?.Join(250); } catch { }
        if (_gameInput != nint.Zero && _callbackToken != 0)
        {
            try { Method<StopCallbackDelegate>(_gameInput, 12)(_gameInput, _callbackToken); } catch { }
            try { Method<UnregisterCallbackDelegate>(_gameInput, 13)(_gameInput, _callbackToken, 1_000_000); } catch { }
        }
        lock (_gate)
        {
            foreach (var entry in _entries.Values)
                if (entry.Pointer != nint.Zero) Release(entry.Pointer);
            PersistRegistryIfDue();
        }
        if (_gameInput != nint.Zero) Release(_gameInput);
        _gameInput = nint.Zero;
    }

    private static T Method<T>(nint instance, int index) where T : Delegate
    {
        var table = Marshal.ReadIntPtr(instance);
        return Marshal.GetDelegateForFunctionPointer<T>(Marshal.ReadIntPtr(table, index * nint.Size));
    }
    private static void AddRef(nint instance) => Method<AddRefDelegate>(instance, 1)(instance);
    private static void Release(nint instance) => Method<ReleaseDelegate>(instance, 2)(instance);

    [DllImport("GameInput.dll")] private static extern int GameInputCreate(out nint gameInput);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] private delegate uint AddRefDelegate(nint self);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] private delegate uint ReleaseDelegate(nint self);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] private delegate nint GetDeviceInfoDelegate(nint self);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] private delegate int GetCurrentReadingDelegate(nint self, uint kind, nint device, out nint reading);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] [return: MarshalAs(UnmanagedType.I1)]
    private delegate bool GetGamepadStateDelegate(nint self, out GamepadState state);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] private delegate int RegisterDeviceCallbackDelegate(
        nint self, nint device, uint kind, uint status, int enumeration, nint context, DeviceCallback callback, out ulong token);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] private delegate void DeviceCallback(
        ulong token, nint context, nint device, ulong timestamp, uint currentStatus, uint previousStatus);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] private delegate void StopCallbackDelegate(nint self, ulong token);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] [return: MarshalAs(UnmanagedType.I1)]
    private delegate bool UnregisterCallbackDelegate(nint self, ulong token, ulong timeoutUs);

    [StructLayout(LayoutKind.Sequential)]
    private struct GamepadState
    {
        public uint Buttons;
        public float LeftTrigger;
        public float RightTrigger;
        public float LeftX;
        public float LeftY;
        public float RightX;
        public float RightY;
    }
}
