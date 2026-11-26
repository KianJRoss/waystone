namespace UnifiedController.Core;

public readonly record struct ControllerFrame(int Buttons, byte LeftTrigger, byte RightTrigger,
    short LeftX, short LeftY, short RightX, short RightY);
public readonly record struct MotionFrame(float[] GyroDps, float[] AccelG, ulong TimestampUs);
public sealed record InputDeviceInfo(string StableId, string Name, ushort VendorId, ushort ProductId,
    string PhysicalPath, bool IsVirtual, bool Connected);
public sealed record InputSample(string StableId, ControllerFrame Controller, MotionFrame? Motion,
    IReadOnlySet<string> ExtendedButtons, long MonotonicTimestamp);

public interface IControllerInputProvider : IAsyncDisposable
{
    event Action<InputDeviceInfo>? DeviceChanged;
    IReadOnlyList<InputDeviceInfo> Devices { get; }
    ValueTask<IReadOnlyList<InputSample>> PollAsync(CancellationToken token);
    void SetRumble(string stableId, byte low, byte high);
}

public sealed record JoyConPairInfo(string Id, string LeftId, string RightId, string Name);
public interface IConfigurableControllerInput
{
    IReadOnlyList<JoyConPairInfo> JoyConPairs { get; }
    void PairJoyCons(string leftId, string rightId);
    void UnpairJoyCons(string pairId);
    bool ExclusiveCapture { get; set; }
}

public interface IVirtualControllerOutput : IAsyncDisposable
{
    bool Connected { get; }
    ValueTask StartAsync(CancellationToken token);
    ValueTask SubmitAsync(ControllerFrame frame, CancellationToken token);
    ValueTask SubmitNeutralAsync(CancellationToken token);
    event Action<byte, byte>? Rumble;
}

/// <summary>Optional richer input for virtual HID pads; legacy outputs keep their original frame path.</summary>
public interface IMotionVirtualControllerOutput : IVirtualControllerOutput
{
    ValueTask SubmitSampleAsync(ControllerFrame frame, MotionFrame? motion,
        IReadOnlySet<string>? extendedButtons, CancellationToken token);
}

public sealed record InputSnapshot(IReadOnlySet<int> Keys, IReadOnlySet<string> MouseButtons,
    IReadOnlySet<string> MousePulses, int MouseX, int MouseY,
    IReadOnlyDictionary<string, HashSet<int>> Keyboards,
    IReadOnlyDictionary<string, DesktopMouseState> Mice);
public sealed record DesktopMouseState(int DeltaX, int DeltaY, HashSet<string> Buttons, HashSet<string> Pulses);
public sealed record DesktopFrame(IReadOnlySet<string> Actions, short PointerX = 0, short PointerY = 0,
    double Sensitivity = 18, double Deadzone = 0.16);
public interface IDesktopInputSource : IDisposable
{
    bool KeyboardEnabled { get; set; }
    bool MouseEnabled { get; set; }
    InputSnapshot Drain();
}
public interface IDesktopInputDiagnostics
{
    string? LastInputSource { get; }
    long LastInputTimestamp { get; }
    IReadOnlyList<string> Limitations { get; }
}
public interface IDesktopOutputSink : IDisposable
{
    void Apply(DesktopFrame frame);
    void ReleaseAll();
}
public interface IMonotonicClock { long Timestamp { get; } double Seconds { get; } }
public interface IMonotonicWaiter { void WaitUntil(long timestamp, CancellationToken token); }

public sealed record RuntimeSnapshot(string ActiveProfile, bool Running, string Health,
    IReadOnlyList<InputDeviceInfo> Devices, string? SelectedSourceId, ControllerFrame Input,
    ControllerFrame Output, MotionFrame? Motion, double AverageLoopMs, double MaximumLoopMs,
    int ActiveMacros, string? Error, IReadOnlyList<string> Limitations)
{
    public string? LastInputSource { get; init; }
    public long LastInputTimestamp { get; init; }
    public bool ControllerMouseModeEnabled { get; init; }
    public bool ModActive { get; init; }
    public IReadOnlySet<string> ActiveDesktopControls { get; init; } = new HashSet<string>();
    public IReadOnlySet<string> ExtendedButtons { get; init; } = new HashSet<string>();
    public IReadOnlyDictionary<int, ControllerFrame> OutputSlots { get; init; } = new Dictionary<int, ControllerFrame>();
    /// <summary>Fused orientation of the motion source, as on the Windows 3D GYRO tab; null without motion.</summary>
    public MotionOrientation? Orientation { get; init; }
    /// <summary>Cemuhook/DSU server state for emulators (Citron/Eden), null when the runtime never started it.</summary>
    public DsuServerStatus? Dsu { get; init; }
    /// <summary>Last rumble forwarded to the physical source (0–255 per motor, after profile scaling).</summary>
    public RumbleState Rumble { get; init; }
    /// <summary>Runtime-owned modes that hotkeys/actions toggle (desktop mode, keyboard controller mode, mouse aim).</summary>
    public IReadOnlySet<string> ActiveModes { get; init; } = new HashSet<string>();
}

public sealed record MotionOrientation(double PitchDeg, double RollDeg, double YawDeg,
    double AngularVelocityDps, double TotalAccelG, bool Still);
public sealed record DsuServerStatus(bool Running, int Port, int PreviewPort, int Clients, string? Error);
public readonly record struct RumbleState(byte Heavy, byte Light, bool FromTest);

/// <summary>Named runtime actions shared by hotkeys, routes ("Action:..."), the CLI and the UI.</summary>
public static class RuntimeActions
{
    public const string OpenControlCenter = "Open Control Center";
    public const string KeyboardControllerMode = "Keyboard Controller Mode";
    public const string MouseAimMode = "Mouse Aim Mode";
    public const string DesktopMode = "Desktop Mode";
    public const string CancelMacros = "Cancel Macros";
    public const string FishingHelperToggle = "Fishing Helper Toggle";
    public static readonly IReadOnlyList<string> All =
        [OpenControlCenter, KeyboardControllerMode, MouseAimMode, DesktopMode, CancelMacros, FishingHelperToggle];
}

public interface IControllerRuntime : IAsyncDisposable
{
    IControllerInputProvider ControllerInput { get; }
    RuntimeSnapshot Snapshot { get; }
    bool KeyboardCaptureEnabled { get; set; }
    bool MouseCaptureEnabled { get; set; }
    ValueTask StartAsync(CancellationToken token = default);
    ValueTask StopAsync();
    void StopAllMacros();
    void RecenterMotion();
    void CalibrateMotion();
    /// <summary>Pulse the selected physical source's motors (0–1 each) for durationMs; profile scaling is not applied.</summary>
    void TestRumble(double heavy, double light, int durationMs) => throw new NotSupportedException("Rumble test is not available.");
    void StopRumble() { }
    /// <summary>Run a <see cref="RuntimeActions"/> name; false when unknown or unsupported here.
    /// OpenControlCenter is raised through <see cref="ActionRequested"/> for the UI to handle.</summary>
    bool InvokeAction(string action) => false;
    event Action<string>? ActionRequested { add { } remove { } }
}
