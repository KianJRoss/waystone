using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using UnifiedController.Core;

namespace UnifiedController.Linux.Platform.Input;

/// <summary>
/// SDL game-controller provider for Linux. SDL owns HIDAPI decoding for DualShock 4,
/// Joy-Cons and Switch Pro controllers; this class owns only the SDL subsystems it starts.
/// </summary>
public sealed class SdlControllerProvider : IControllerInputProvider, IConfigurableControllerInput
{
    private const uint RequiredSubsystems = SdlNative.InitJoystick | SdlNative.InitGameController | SdlNative.InitSensor;
    private readonly object _gate = new();
    private readonly Dictionary<int, OpenController> _controllers = [];
    private readonly List<JoyConPairInfo> _pairs = [];
    private readonly string _pairPath;
    private readonly CancellationTokenSource _shutdown = new();
    private readonly Task _discoveryTask;
    private readonly uint _ownedSubsystems;
    private JsonObject _pairDocument = new() { ["version"] = 1 };
    private bool _exclusiveCapture;
    private bool _disposed;

    public event Action<InputDeviceInfo>? DeviceChanged;

    public SdlControllerProvider(string stateDirectory)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(stateDirectory);
        Directory.CreateDirectory(stateDirectory);
        _pairPath = Path.Combine(stateDirectory, "joycon_pairs.json");
        LoadPairs();

        // SDL documents these HIDAPI hints. They are set before initializing only the
        // missing subsystems; an existing SDL owner remains responsible for shutdown.
        SdlNative.SDL_SetHint("SDL_JOYSTICK_HIDAPI_PS4", "1");
        SdlNative.SDL_SetHint("SDL_JOYSTICK_HIDAPI_JOY_CONS", "1");
        SdlNative.SDL_SetHint("SDL_JOYSTICK_HIDAPI_SWITCH", "1");
        var already = SdlNative.SDL_WasInit(RequiredSubsystems);
        _ownedSubsystems = RequiredSubsystems & ~already;
        if (_ownedSubsystems != 0 && SdlNative.SDL_InitSubSystem(_ownedSubsystems) != 0)
            throw new InvalidOperationException("SDL controller initialization failed: " + SdlNative.Error());

        RefreshDevices();
        _discoveryTask = Task.Run(DiscoveryLoopAsync);
    }

    public IReadOnlyList<InputDeviceInfo> Devices
    {
        get
        {
            lock (_gate)
            {
                var devices = _controllers.Values.Select(c => c.Info).ToList();
                foreach (var pair in _pairs)
                {
                    if (!TryPairMembers(pair, out var left, out var right)) continue;
                    devices.Add(new InputDeviceInfo(pair.Id, pair.Name, 0x057e, 0x2008,
                        $"pair:{left.Info.PhysicalPath}|{right.Info.PhysicalPath}", false, true));
                }
                return devices.OrderBy(d => d.Name, StringComparer.OrdinalIgnoreCase).ToArray();
            }
        }
    }

    public IReadOnlyList<JoyConPairInfo> JoyConPairs
    {
        get { lock (_gate) return _pairs.ToArray(); }
    }

    /// <remarks>
    /// EVIOCGRAB applies to evdev event nodes. SDL HIDAPI may instead open hidraw, where
    /// this setting cannot exclude another hidraw consumer; it remains disabled by default.
    /// </remarks>
    public bool ExclusiveCapture
    {
        get { lock (_gate) return _exclusiveCapture; }
        set
        {
            lock (_gate)
            {
                if (_exclusiveCapture == value) return;
                _exclusiveCapture = value;
                foreach (var controller in _controllers.Values) controller.SetExclusive(value);
            }
        }
    }

    public async ValueTask<IReadOnlyList<InputSample>> PollAsync(CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        await ValueTask.CompletedTask;
        lock (_gate)
        {
            ThrowIfDisposed();
            SdlNative.SDL_GameControllerUpdate();
            var physical = _controllers.Values.Where(c => c.IsAttached).ToDictionary(c => c.Info.StableId, c => c.Read());
            var consumed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var output = new List<InputSample>();
            foreach (var pair in _pairs)
            {
                if (!physical.TryGetValue(pair.LeftId, out var left) || !physical.TryGetValue(pair.RightId, out var right)) continue;
                consumed.Add(pair.LeftId);
                consumed.Add(pair.RightId);
                output.Add(new InputSample(pair.Id, SdlMappings.Merge(left.Upright, right.Upright),
                    SdlMappings.Merge(left.Motion, right.Motion), Union(left.ExtendedButtons, right.ExtendedButtons),
                    Math.Max(left.Timestamp, right.Timestamp)));
            }
            foreach (var item in physical)
            {
                if (consumed.Contains(item.Key)) continue;
                var value = item.Value;
                var report = value.Kind is NintendoControllerKind.JoyConLeft or NintendoControllerKind.JoyConRight
                    ? SdlMappings.Sideways(value.Upright, value.Kind) : value.Upright;
                output.Add(new InputSample(item.Key, report, value.Motion, value.ExtendedButtons, value.Timestamp));
            }
            return output;
        }
    }

    public void PairJoyCons(string leftId, string rightId)
    {
        lock (_gate)
        {
            ThrowIfDisposed();
            var left = Find(leftId);
            var right = Find(rightId);
            if (left?.Kind != NintendoControllerKind.JoyConLeft || right?.Kind != NintendoControllerKind.JoyConRight)
                throw new ArgumentException("PairJoyCons requires one connected left Joy-Con and one connected right Joy-Con.");
            _pairs.RemoveAll(p => p.LeftId.Equals(leftId, StringComparison.OrdinalIgnoreCase) ||
                                  p.RightId.Equals(leftId, StringComparison.OrdinalIgnoreCase) ||
                                  p.LeftId.Equals(rightId, StringComparison.OrdinalIgnoreCase) ||
                                  p.RightId.Equals(rightId, StringComparison.OrdinalIgnoreCase));
            var id = "joycon-pair-" + ShortHash(leftId + "\n" + rightId);
            _pairs.Add(new JoyConPairInfo(id, leftId, rightId, $"Joy-Con Pair {_pairs.Count + 1}"));
            SavePairs();
        }
    }

    public void UnpairJoyCons(string pairId)
    {
        lock (_gate)
        {
            ThrowIfDisposed();
            if (_pairs.RemoveAll(p => p.Id.Equals(pairId, StringComparison.OrdinalIgnoreCase)) > 0) SavePairs();
        }
    }

    public void SetRumble(string stableId, byte low, byte high)
    {
        lock (_gate)
        {
            ThrowIfDisposed();
            var pair = _pairs.FirstOrDefault(p => p.Id.Equals(stableId, StringComparison.OrdinalIgnoreCase));
            if (pair is not null)
            {
                Find(pair.LeftId)?.Rumble(low, high);
                Find(pair.RightId)?.Rumble(low, high);
                return;
            }
            Find(stableId)?.Rumble(low, high);
        }
    }

    private async Task DiscoveryLoopAsync()
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
        try
        {
            while (await timer.WaitForNextTickAsync(_shutdown.Token).ConfigureAwait(false)) RefreshDevices();
        }
        catch (OperationCanceledException) when (_shutdown.IsCancellationRequested) { }
    }

    private void RefreshDevices()
    {
        var notifications = new List<InputDeviceInfo>();
        lock (_gate)
        {
            if (_disposed) return;
            // ControllerUpdate refreshes open handles, but sdl2-compat does not
            // discover added/removed devices until its event queue is pumped.
            // This provider initializes input/sensor subsystems only, never SDL
            // video. Keep this on the serialized discovery path, not the fast
            // forwarding loop. State is polled directly, so discard queued SDL2
            // joystick/controller events rather than accumulating unused input.
            SdlNative.SDL_PumpEvents();
            SdlNative.SDL_FlushEvents(0x600, 0x6ff);
            SdlNative.SDL_GameControllerUpdate();
            var count = Math.Max(0, SdlNative.SDL_NumJoysticks());
            for (var index = 0; index < count; index++)
            {
                if (SdlNative.SDL_IsGameController(index) == 0) continue;
                var handle = SdlNative.SDL_GameControllerOpen(index);
                if (handle == IntPtr.Zero) continue;
                var joystick = SdlNative.SDL_GameControllerGetJoystick(handle);
                var instance = SdlNative.SDL_JoystickInstanceID(joystick);
                if (_controllers.ContainsKey(instance)) { SdlNative.SDL_GameControllerClose(handle); continue; }
                var candidate = OpenController.Create(handle, joystick);
                if (PhysicalDeviceFilter.IsOwnVirtualOutput(candidate.Info.Name, candidate.Info.PhysicalPath,
                        candidate.Identity.UniqueId, candidate.Info.VendorId, candidate.Info.ProductId, candidate.Identity.BusType))
                {
                    candidate.Dispose();
                    continue;
                }
                // SDL2-on-SDL3 can enumerate the same HID through compatibility and HIDAPI.
                // Keep one physical source without globally suppressing controllers by class/name.
                if (_controllers.Values.Any(c => c.Info.StableId.Equals(candidate.Info.StableId, StringComparison.OrdinalIgnoreCase)))
                {
                    candidate.Dispose();
                    continue;
                }
                candidate.SetExclusive(_exclusiveCapture);
                _controllers.Add(instance, candidate);
                notifications.Add(candidate.Info);
            }
            foreach (var entry in _controllers.Where(p => !p.Value.IsAttached).ToArray())
            {
                _controllers.Remove(entry.Key);
                var disconnected = entry.Value.Info with { Connected = false };
                entry.Value.Dispose();
                notifications.Add(disconnected);
            }
        }
        foreach (var notification in notifications) DeviceChanged?.Invoke(notification);
    }

    private OpenController? Find(string stableId) => _controllers.Values.FirstOrDefault(c => c.Info.StableId.Equals(stableId, StringComparison.OrdinalIgnoreCase));
    private bool TryPairMembers(JoyConPairInfo pair, out OpenController left, out OpenController right)
    {
        left = Find(pair.LeftId)!;
        right = Find(pair.RightId)!;
        return left is not null && right is not null && left.IsAttached && right.IsAttached;
    }

    private void LoadPairs()
    {
        try
        {
            _pairDocument = JsonNode.Parse(File.ReadAllText(_pairPath)) as JsonObject ?? _pairDocument;
            if (_pairDocument["pairs"] is not JsonArray pairs) return;
            foreach (var value in pairs.OfType<JsonObject>())
            {
                var id = value["id"]?.GetValue<string>();
                var left = value["left_id"]?.GetValue<string>();
                var right = value["right_id"]?.GetValue<string>();
                if (string.IsNullOrWhiteSpace(id) || string.IsNullOrWhiteSpace(left) || string.IsNullOrWhiteSpace(right)) continue;
                _pairs.Add(new JoyConPairInfo(id, left, right, value["name"]?.GetValue<string>() ?? "Joy-Con Pair"));
            }
        }
        catch (IOException) { }
        catch (JsonException) { }
    }

    private void SavePairs()
    {
        // Retain unknown root fields from the Windows file and use its exact pair vocabulary.
        _pairDocument["version"] ??= 1;
        _pairDocument["pairs"] = new JsonArray(_pairs.Select(p => (JsonNode)new JsonObject
        {
            ["id"] = p.Id, ["left_id"] = p.LeftId, ["right_id"] = p.RightId, ["name"] = p.Name
        }).ToArray());
        var temporary = _pairPath + ".tmp";
        File.WriteAllText(temporary, _pairDocument.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) + Environment.NewLine);
        File.Move(temporary, _pairPath, true);
    }

    public async ValueTask DisposeAsync()
    {
        lock (_gate) { if (_disposed) return; _disposed = true; }
        _shutdown.Cancel();
        try { await _discoveryTask.ConfigureAwait(false); } catch (OperationCanceledException) { }
        lock (_gate)
        {
            foreach (var controller in _controllers.Values) controller.Dispose();
            _controllers.Clear();
            if (_ownedSubsystems != 0) SdlNative.SDL_QuitSubSystem(_ownedSubsystems);
        }
        _shutdown.Dispose();
    }

    private void ThrowIfDisposed() => ObjectDisposedException.ThrowIf(_disposed, this);
    private static string ShortHash(string text) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(text))).ToLowerInvariant()[..20];
    private static IReadOnlySet<string> Union(IReadOnlySet<string> first, IReadOnlySet<string> second) =>
        new HashSet<string>(first.Concat(second), StringComparer.OrdinalIgnoreCase);

    private sealed class OpenController : IDisposable
    {
        private IntPtr _handle;
        private readonly IntPtr _joystick;
        private int _grabFd = -1;
        private CompanionShareKeys? _shareKeys;
        public InputDeviceInfo Info { get; }
        public LinuxDeviceIdentity Identity { get; }
        public NintendoControllerKind Kind { get; }
        public bool IsAttached => _handle != IntPtr.Zero && SdlNative.SDL_JoystickGetAttached(_joystick) != 0;

        private OpenController(IntPtr handle, IntPtr joystick, InputDeviceInfo info, LinuxDeviceIdentity identity, NintendoControllerKind kind)
        { _handle = handle; _joystick = joystick; Info = info; Identity = identity; Kind = kind; }

        public static OpenController Create(IntPtr handle, IntPtr joystick)
        {
            var name = SdlNative.Utf8(SdlNative.SDL_GameControllerName(handle));
            var vendor = SdlNative.SDL_JoystickGetVendor(joystick);
            var product = SdlNative.SDL_JoystickGetProduct(joystick);
            var serial = SdlNative.Utf8(SdlNative.SDL_JoystickGetSerial(joystick));
            var path = SdlNative.Utf8(SdlNative.SDL_JoystickPath(joystick));
            var identity = LinuxDeviceIdentityResolver.Resolve(SdlNative.SDL_JoystickGetGUID(joystick), serial, path, vendor, product);
            var kind = SdlMappings.NintendoKind(vendor, product, name);
            var stableId = kind switch
            {
                // Keep the Windows NativeJoyConManager IDs so its persisted pairs work unchanged.
                NintendoControllerKind.JoyConLeft when serial.Length > 0 => "joycon-left-" + NormalizeSerial(serial),
                NintendoControllerKind.JoyConRight when serial.Length > 0 => "joycon-right-" + NormalizeSerial(serial),
                NintendoControllerKind.SwitchPro when vendor == 0x057e && serial.Length > 0 => "joycon-pro-" + NormalizeSerial(serial),
                _ => identity.StableId
            };
            var controller = new OpenController(handle, joystick,
                new InputDeviceInfo(stableId, name, vendor, product, identity.PhysicalPath, false, true), identity, kind);
            if (path.StartsWith("/dev/input/event", StringComparison.Ordinal)) controller._shareKeys = CompanionShareKeys.Open(path);
            if (SdlNative.SDL_GameControllerHasSensor(handle, SdlNative.SensorGyro) != 0)
                SdlNative.SDL_GameControllerSetSensorEnabled(handle, SdlNative.SensorGyro, 1);
            if (SdlNative.SDL_GameControllerHasSensor(handle, SdlNative.SensorAccel) != 0)
                SdlNative.SDL_GameControllerSetSensorEnabled(handle, SdlNative.SensorAccel, 1);
            return controller;
        }

        public Reading Read()
        {
            var buttons = 0;
            if (Button(0)) buttons |= 0x1000; // A
            if (Button(1)) buttons |= 0x2000; // B
            if (Button(2)) buttons |= 0x4000; // X
            if (Button(3)) buttons |= 0x8000; // Y
            if (Button(4)) buttons |= 0x0020;
            if (Button(5)) buttons |= 0x0400;
            if (Button(6)) buttons |= 0x0010;
            if (Button(7)) buttons |= 0x0040;
            if (Button(8)) buttons |= 0x0080;
            if (Button(9)) buttons |= 0x0100;
            if (Button(10)) buttons |= 0x0200;
            if (Button(11)) buttons |= 0x0001;
            if (Button(12)) buttons |= 0x0002;
            if (Button(13)) buttons |= 0x0004;
            if (Button(14)) buttons |= 0x0008;
            var report = new ControllerFrame(buttons, Trigger(4), Trigger(5), Axis(0), InvertY(1), Axis(2), InvertY(3));
            var extended = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (Button(15) || _shareKeys?.Pressed() == true) extended.Add("Share");
            if (Button(20)) extended.Add("Touchpad");
            MotionFrame? motion = null;
            var gyro = new float[3];
            var accel = new float[3];
            ulong gyroTimestamp = 0, accelTimestamp = 0;
            var hasGyro = SdlNative.SDL_GameControllerHasSensor(_handle, SdlNative.SensorGyro) != 0 &&
                          SdlNative.SDL_GameControllerGetSensorDataWithTimestamp(_handle, SdlNative.SensorGyro, out gyroTimestamp, gyro, 3) == 0;
            var hasAccel = SdlNative.SDL_GameControllerHasSensor(_handle, SdlNative.SensorAccel) != 0 &&
                           SdlNative.SDL_GameControllerGetSensorDataWithTimestamp(_handle, SdlNative.SensorAccel, out accelTimestamp, accel, 3) == 0;
            if (hasGyro || hasAccel)
            {
                motion = new MotionFrame(hasGyro ? SdlMappings.GyroToDegrees(gyro) : new float[3],
                    hasAccel ? SdlMappings.AccelToG(accel) : new float[3], Math.Max(gyroTimestamp, accelTimestamp));
            }
            return new Reading(report, motion, extended, Stopwatch.GetTimestamp(), Kind);
        }

        public void Rumble(byte low, byte high) => SdlNative.SDL_GameControllerRumble(_handle,
            (ushort)(low * 257), (ushort)(high * 257), 250);
        private bool Button(int button) => SdlNative.SDL_GameControllerGetButton(_handle, button) != 0;
        private short Axis(int axis) => SdlNative.SDL_GameControllerGetAxis(_handle, axis);
        private short InvertY(int axis) => SdlMappings.InvertSignedAxis(Axis(axis));
        private byte Trigger(int axis)
        {
            var value = Math.Max(0, (int)SdlNative.SDL_GameControllerGetAxis(_handle, axis));
            return (byte)Math.Clamp((value * 255 + 16383) / 32767, 0, 255);
        }
        private static ulong MonotonicMicroseconds() => (ulong)(Stopwatch.GetTimestamp() * (1_000_000d / Stopwatch.Frequency));
        private static string NormalizeSerial(string value)
        {
            var normalized = new string(value.Where(char.IsLetterOrDigit).ToArray()).ToLowerInvariant();
            return normalized.Length == 0 ? "unknown" : normalized;
        }

        public void SetExclusive(bool enabled)
        {
            ReleaseGrab();
            if (!enabled || !Info.PhysicalPath.StartsWith("/dev/input/event", StringComparison.Ordinal)) return;
            var fd = LibC.open(Info.PhysicalPath, LibC.O_RDONLY | LibC.O_NONBLOCK);
            if (fd < 0) return;
            if (LibC.ioctl(fd, LibC.EVIOCGRAB, 1) == 0) _grabFd = fd;
            else LibC.close(fd);
        }

        private void ReleaseGrab()
        {
            if (_grabFd < 0) return;
            LibC.ioctl(_grabFd, LibC.EVIOCGRAB, 0);
            LibC.close(_grabFd);
            _grabFd = -1;
        }

        public void Dispose()
        {
            ReleaseGrab();
            _shareKeys?.Dispose(); _shareKeys = null;
            if (_handle == IntPtr.Zero) return;
            SdlNative.SDL_GameControllerClose(_handle);
            _handle = IntPtr.Zero;
        }
    }

    private sealed record Reading(ControllerFrame Upright, MotionFrame? Motion,
        IReadOnlySet<string> ExtendedButtons, long Timestamp, NintendoControllerKind Kind);

    private static class LibC
    {
        internal const int O_RDONLY = 0;
        internal const int O_NONBLOCK = 0x800;
        internal const ulong EVIOCGRAB = 0x40044590;
        [DllImport("libc", SetLastError = true)] internal static extern int open(string path, int flags);
        [DllImport("libc", SetLastError = true)] internal static extern int ioctl(int fd, ulong request, int value);
        [DllImport("libc", SetLastError = true)] internal static extern int close(int fd);
    }
}
