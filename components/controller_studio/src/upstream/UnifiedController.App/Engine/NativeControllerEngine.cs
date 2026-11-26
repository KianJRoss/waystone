using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace UnifiedController.App.Engine;

public readonly record struct NativePhysicalSnapshot(
    bool Connected, int Buttons, byte LeftTrigger, byte RightTrigger,
    short LeftX, short LeftY, short RightX, short RightY, string[] ExtendedButtons);

public readonly record struct FishingHelperToggleState(
    bool Available, bool Enabled, bool Learning, bool AudioReady, string Error);

public readonly record struct FishingHelperPrimeState(
    bool Primed, int RemainingSeconds, double BestBiteScore);

public sealed class NativeControllerEngine : IDisposable
{
    private readonly NativeViGEmOutput _vigem = new();
    private readonly NativeXInputReader _xinput = new();
    private readonly NativeGameSirHidReader _gameSir = new();
    private readonly NativeJoyConManager _joyCons;
    private readonly NativeInputHookManager _hooks = new();
    private readonly NativeRawInputManager? _rawInput;
    private readonly NativeGameInputManager _gameInput;
    private readonly NativeDsuServer _dsu;
    private readonly NativeDsuServer _previewDsu;
    private readonly NativeFishingHelperHost _fishingHelper;
    private readonly EventWaitHandle _catchEvent;
    // Bounded stick/button pulses from a local game-playing agent (opt-in per profile; see NativeAutonomyInput).
    private readonly NativeAutonomyInput _autonomy = new();
    private readonly EventWaitHandle _fishingToggleEvent;
    private readonly EventWaitHandle _fishingPrimeEvent;
    private readonly NativeMotionStickMapper _motionMapper = new();
    private readonly NativeMotionBearing _motionBearing = new();
    private readonly NativeMotionCalibration _motionCalibration;
    private readonly NativeMacroScheduler _macroScheduler = new();
    private readonly NativeDesktopOutput _desktopOutput = new();
    private readonly object _dsuMotionLock = new();
    private float[] _dsuGyro = [0, 0, 0];
    private float[] _dsuAccel = [0, 0, 1];
    private ulong _dsuTimestampUs;

    private readonly string _profilesPath;
    private readonly string _statusPath;
    private readonly string _bearingRecenterPath;
    private readonly string _gyroRecenterPath;
    private readonly string _fishingTelemetryPath;
    private DateTime _profileLastWrite;
    private volatile NativeProfileRuntime _profile = NativeProfileRuntime.Empty;
    private string? _profileError;
    private long _catchUntilTimestamp;
    private HashSet<string> _activeRouteActionSources = new(StringComparer.OrdinalIgnoreCase);
    private ulong _lastMotionTimestampUs;
    private long _shareVisualUntilTimestamp;
    private long _homeVisualUntilTimestamp;
    private bool _lastFishingPrimed;
    private int _lastFishingRemainingSeconds = -1;
    private double _nextFishingPrimeAt;
    private bool? _lastNotifiedModActive;
    private string _lastNotifiedModSource = string.Empty;
    private bool _controllerMouseToggleWasDown;

    private Thread? _engineThread;
    private volatile bool _running;
    private int _disposeState;
    private long _nextStatusErrorLogAt;
    private readonly object _stateLock = new();
    private Dictionary<int, int> _outputSourceSlots = [];
    private readonly Dictionary<int, NativeSourceSelector> _sourceSelectors = [];

    // Live state
    public int LiveButtons { get; private set; }
    public byte LiveLt { get; private set; }
    public byte LiveRt { get; private set; }
    public short LiveLx { get; private set; }
    public short LiveLy { get; private set; }
    public short LiveRx { get; private set; }
    public short LiveRy { get; private set; }
    public bool PhysicalConnected { get; private set; }
    public int PhysicalSlot { get; private set; } = -1;
    public int VirtualSlot { get; private set; } = -1;
    public double AverageLoopMs { get; private set; }
    public double MaximumLoopMs { get; private set; }
    public int PollIntervalMs { get; private set; } = 10;
    public HashSet<string> ExtendedButtons { get; private set; } = new(StringComparer.OrdinalIgnoreCase);
    private NativePhysicalSnapshot _physicalSnapshot = new(false, 0, 0, 0, 0, 0, 0, 0, []);

    public bool KeyboardModeEnabled => _hooks.KeyboardCaptureEnabled;
    public bool MouseAimingEnabled => _hooks.MouseCaptureEnabled;
    public bool DesktopModeEnabled { get; private set; }
    public bool ControllerMouseModeEnabled { get; private set; }
    public IReadOnlyList<NativeGameInputDevice> GameInputDevices => _gameInput.Devices;
    public IReadOnlyList<NativeJoyConDevice> JoyConDevices => _joyCons.Devices;
    public IReadOnlyList<NativeJoyConPair> JoyConPairs => _joyCons.Pairs;

    public event Action<bool>? KeyboardModeToggled;
    public event Action<bool>? MouseModeToggled;
    public event Action<bool>? DesktopModeToggled;
    public event Action<FishingHelperToggleState>? FishingToggleCompleted;
    public event Action<FishingHelperPrimeState>? FishingHelperPrimeStateChanged;
    public event Action<bool, string, string>? ModStateChanged;
    public event Action<bool>? ControllerMouseModeToggled;
    public event Action? OpenControlCenterRequested;

    public NativePhysicalSnapshot GetPhysicalSnapshot()
    {
        lock (_stateLock) return _physicalSnapshot with { ExtendedButtons = [.. _physicalSnapshot.ExtendedButtons] };
    }

    public NativeControllerEngine(string rootDirectory, NativeRawInputManager? rawInput = null)
    {
        _rawInput = rawInput;
        _gameInput = new NativeGameInputManager(rootDirectory);
        _joyCons = new NativeJoyConManager(rootDirectory);
        _profilesPath = Path.Combine(rootDirectory, "profiles.json");
        _statusPath = Path.Combine(rootDirectory, "status.json");
        _bearingRecenterPath = Path.Combine(rootDirectory, "motion_bearing_recenter.flag");
        _gyroRecenterPath = Path.Combine(rootDirectory, "recenter_gyro.flag");
        _fishingTelemetryPath = Path.Combine(rootDirectory, "modules", "fishing", "telemetry.json");
        _motionCalibration = new NativeMotionCalibration(rootDirectory);
        _fishingHelper = new NativeFishingHelperHost(rootDirectory);
        _catchEvent = new EventWaitHandle(
            false,
            EventResetMode.AutoReset,
            @"Local\CitronUnifiedControllerCatch");
        _fishingToggleEvent = new EventWaitHandle(
            false,
            EventResetMode.AutoReset,
            @"Local\UnifiedControllerToggleFishing");
        _fishingPrimeEvent = new EventWaitHandle(
            false,
            EventResetMode.AutoReset,
            @"Local\CitronFishingHelperPrime");

        _dsu = new NativeDsuServer(
            GetCitronDsuGyro,
            GetCitronDsuAccel,
            GetDsuTimestampUs
        );
        // Keep diagnostics in the controller's intuitive Cartesian frame.
        // Citron's Cemuhook client applies a BetterJoy-specific permutation,
        // so the emulator feed uses port 26760 and previews use 26761.
        _previewDsu = new NativeDsuServer(
            GetPreviewDsuGyro,
            GetPreviewDsuAccel,
            GetDsuTimestampUs,
            26761
        );

        _vigem.RumbleReceived += (logicalSlot, large, small) =>
        {
            int sourceSlot;
            lock (_stateLock) sourceSlot = _outputSourceSlots.GetValueOrDefault(logicalSlot, -1);
            if (sourceSlot >= 0)
            {
                var scaled = _profile.ScaleRumble(large, small);
                _xinput.SetVibration((uint)sourceSlot, scaled.Large, scaled.Small);
            }
        };
    }

    public void Start()
    {
        if (_running) return;

        LoadProfile();
        _vigem.Initialize(_profile.EnabledOutputSlots);

        // ViGEm reports the XInput user index assigned to this target. It may
        // arrive just after Connect(), so allow a short bounded discovery
        // window before the input loop begins.
        for (var attempt = 0; attempt < 20 && _vigem.UserIndex is null; attempt++)
            Thread.Sleep(25);
        VirtualSlot = _vigem.UserIndex ?? -1;

        _gameSir.Start();
        _joyCons.Start();
        _hooks.Start();
        _dsu.Start();
        _previewDsu.Start();
        _fishingHelper.Start();

        _running = true;
        _engineThread = new Thread(EngineLoop)
        {
            IsBackground = true,
            Name = "NativeControllerEngineLoop",
            Priority = ThreadPriority.AboveNormal
        };
        _engineThread.Start();
    }

    public void ToggleKeyboardMode()
    {
        _hooks.KeyboardCaptureEnabled = !_hooks.KeyboardCaptureEnabled;
        KeyboardModeToggled?.Invoke(_hooks.KeyboardCaptureEnabled);
    }

    public void ToggleMouseAiming()
    {
        _hooks.MouseCaptureEnabled = !_hooks.MouseCaptureEnabled;
        MouseModeToggled?.Invoke(_hooks.MouseCaptureEnabled);
    }

    public void ToggleDesktopMode()
    {
        DesktopModeEnabled = !DesktopModeEnabled;
        if (!DesktopModeEnabled) _desktopOutput.ReleaseAll();
        DesktopModeToggled?.Invoke(DesktopModeEnabled);
    }

    public void CancelAllMacros()
    {
        _macroScheduler.CancelAll();
        _autonomy.Suspend();
    }

    public void ToggleFishingHelper()
    {
        _fishingToggleEvent.Set();
        ThreadPool.QueueUserWorkItem(_ =>
        {
            // The headless worker processes its event and publishes telemetry
            // every 75 ms. Read the resulting state after it has completed a
            // few cycles instead of reporting only that a request was sent.
            Thread.Sleep(300);
            FishingToggleCompleted?.Invoke(ReadFishingHelperState());
        });
    }

    private FishingHelperToggleState ReadFishingHelperState()
    {
        try
        {
            using var stream = new FileStream(
                _fishingTelemetryPath, FileMode.Open, FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete);
            using var document = JsonDocument.Parse(stream);
            var root = document.RootElement;
            var enabled = root.TryGetProperty("enabled", out var enabledValue) && enabledValue.GetBoolean();
            var learning = root.TryGetProperty("learning", out var learningValue) && learningValue.GetBoolean();
            var audioReady = root.TryGetProperty("audio_ready", out var readyValue) && readyValue.GetBoolean();
            var error = root.TryGetProperty("audio_error", out var errorValue)
                ? errorValue.GetString() ?? string.Empty
                : string.Empty;
            return new FishingHelperToggleState(true, enabled, learning, audioReady, error);
        }
        catch (Exception ex)
        {
            return new FishingHelperToggleState(false, false, false, false, ex.Message);
        }
    }

    private void EngineLoop()
    {
        [DllImport("winmm.dll")]
        static extern uint timeBeginPeriod(uint uMilliseconds);
        [DllImport("winmm.dll")]
        static extern uint timeEndPeriod(uint uMilliseconds);
        [DllImport("kernel32.dll", EntryPoint = "CreateWaitableTimerExW", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr CreateWaitableTimerEx(IntPtr attributes, string? name, uint flags, uint desiredAccess);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool SetWaitableTimer(IntPtr timer, ref long dueTime, int period, IntPtr completionRoutine, IntPtr argument, bool resume);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool CloseHandle(IntPtr handle);

        timeBeginPeriod(1);
        // A 1 ms Thread.Sleep adds its full duration after the work, often
        // stretching a nominal 1 kHz loop to 1.5–2 ms. Use a one-shot high
        // resolution timer so the wait covers only the remainder of the tick.
        const uint CreateWaitableTimerHighResolution = 0x00000002;
        const uint TimerAllAccess = 0x001F0003;
        var loopTimer = CreateWaitableTimerEx(IntPtr.Zero, null,
            CreateWaitableTimerHighResolution, TimerAllAccess);
        var nextTick = Stopwatch.GetTimestamp();
        var tickPeriod = Stopwatch.Frequency / 100;

        var statusStopwatch = Stopwatch.StartNew();
        var profileStopwatch = Stopwatch.StartNew();
        var recenterStopwatch = Stopwatch.StartNew();
        long performanceSamples = 0;
        double performanceTotalMs = 0;
        double performanceMaximumMs = 0;
        var connectedPhysicalSlots = Array.Empty<int>();

        int virtualButtons = 0;
        byte virtualLt = 0, virtualRt = 0;
        short virtualLx = 0, virtualLy = 0, virtualRx = 0, virtualRy = 0;

        while (_running)
        {
            var tickStart = Stopwatch.GetTimestamp();
            var loopNow = tickStart / (double)Stopwatch.Frequency;

            if (_catchEvent.WaitOne(0))
                _catchUntilTimestamp = tickStart + Stopwatch.Frequency / 10;

            var motionSample = _gameSir.GetMotionSample();
            var motionTimestamp = motionSample.TimestampUs;
            var motionChanged = motionTimestamp != 0 && motionTimestamp != _lastMotionTimestampUs;
            if (motionTimestamp != 0 && motionTimestamp != _lastMotionTimestampUs)
            {
                _lastMotionTimestampUs = motionTimestamp;
                var motionNow = loopNow;
                var corrected = _motionCalibration.Update(
                    motionSample.Gyro, motionSample.Accel, Stopwatch.GetTimestamp());
                _motionMapper.Update(corrected.Gyro, corrected.Accel, motionNow);
                var gyro = corrected.Gyro;
                var accel = corrected.Accel;
                if (_motionBearing.Enabled)
                {
                    var generated = _motionBearing.Update(
                        _motionMapper.Pitch, _motionMapper.Roll,
                        _profile.Motion.LevelReference, motionNow, gyro[1]);
                    gyro = generated.Gyro;
                    accel = generated.Accel;
                }
                lock (_dsuMotionLock)
                {
                    _dsuGyro = [.. gyro];
                    _dsuAccel = [.. accel];
                    _dsuTimestampUs = motionTimestamp;
                }
            }

            if (recenterStopwatch.ElapsedMilliseconds >= 50)
            {
                recenterStopwatch.Restart();
                if (File.Exists(_bearingRecenterPath) || File.Exists(_gyroRecenterPath))
                {
                    _motionBearing.RecenterHeading();
                    TryDelete(_bearingRecenterPath);
                    TryDelete(_gyroRecenterPath);
                }
            }

            // 1. Reload profile if modified (check every 500ms)
            if (profileStopwatch.ElapsedMilliseconds > 500)
            {
                profileStopwatch.Restart();
                CheckReloadProfile();
            }

            // 2. Poll physical XInput gamepads (slots 0..3), excluding the
            // ViGEm target owned by this engine.
            VirtualSlot = _vigem.UserIndex ?? -1;
            var virtualIndices = _vigem.UserIndices;
            bool foundPhysical = false;
            int foundSlot = -1;
            var physicalSlots = new List<int>(4);
            var physicalInputs = new List<(int XInputSlot, string? DeviceId, int OutputSlot, NativeControllerReport Report)>(4);
            var originalPhysicalReport = new NativeControllerReport();
            var additionalOutputReports = new Dictionary<int, NativeControllerReport>();

            for (uint i = 0; i < 4; i++)
            {
                if (virtualIndices.Contains((int)i) || _xinput.IsUnifiedVirtualSlot(i)) continue;

                if (_xinput.Poll(i, out var state))
                {
                    physicalSlots.Add((int)i);
                    var physicalReport = new NativeControllerReport
                    {
                        Buttons = state.Gamepad.Buttons,
                        LeftTrigger = state.Gamepad.LeftTrigger,
                        RightTrigger = state.Gamepad.RightTrigger,
                        LeftX = state.Gamepad.ThumbLX,
                        LeftY = state.Gamepad.ThumbLY,
                        RightX = state.Gamepad.ThumbRX,
                        RightY = state.Gamepad.ThumbRY,
                    };
                    _gameInput.ObserveXInput((int)i, physicalReport);
                    var deviceId = _gameInput.DeviceIdForXInputSlot((int)i);
                    physicalInputs.Add(((int)i, deviceId, _profile.OutputSlotForDevice(deviceId), physicalReport));
                    if (!foundPhysical)
                    {
                        foundPhysical = true;
                        foundSlot = (int)i;
                    }
                }
            }

            // Nintendo controllers use their native Bluetooth HID protocol and
            // therefore never appear in XInput. Feed their logical single or
            // paired reports into the same device assignment and merge path.
            foreach (var joyCon in _joyCons.GetLogicalInputs())
            {
                physicalInputs.Add((-1, joyCon.Id, _profile.OutputSlotForDevice(joyCon.Id), joyCon.Report));
                if (!foundPhysical)
                {
                    foundPhysical = true;
                    foundSlot = -1;
                }
            }

            connectedPhysicalSlots = physicalSlots.ToArray();

            PhysicalConnected = foundPhysical;
            PhysicalSlot = foundSlot;

            // 3. Extended buttons (GameSir Cyclone 2)
            var extBtns = _gameSir.GetButtons();
            ExtendedButtons = extBtns;
            var extendedButtonNow = Stopwatch.GetTimestamp();
            var visualHoldTicks = Stopwatch.Frequency / 5; // Keep short HID pulses visible for 200 ms.
            if (extBtns.Contains("Share"))
                _shareVisualUntilTimestamp = extendedButtonNow + visualHoldTicks;
            if (extBtns.Contains("Home"))
                _homeVisualUntilTimestamp = extendedButtonNow + visualHoldTicks;

            // 4. Assign complete physical devices to their configured virtual
            // player before per-control routes are evaluated.
            var primaryReport = new NativeControllerReport();
            var controllerOutputSlot = 1;
            var selectedSourceSlots = new Dictionary<int, int>();
            if (foundPhysical)
            {
                controllerOutputSlot = physicalInputs[0].OutputSlot;
                foreach (var group in physicalInputs.GroupBy(input => input.OutputSlot))
                {
                    var groupedInputs = group.ToArray();
                    if (!_sourceSelectors.TryGetValue(group.Key, out var selector))
                        _sourceSelectors[group.Key] = selector = new NativeSourceSelector();
                    var selectedIndex = selector.Select(groupedInputs.Select(input =>
                        (input.DeviceId ?? $"xinput:{input.XInputSlot}", input.Report)).ToArray());
                    var groupedReports = groupedInputs.Select(input => input.Report).ToArray();
                    var selected = _profile.SourceMergePolicy == "all_connected"
                        ? NativeReportMerger.Merge(groupedReports)
                        : groupedReports[selectedIndex];
                    selectedSourceSlots[group.Key] = groupedInputs[selectedIndex].XInputSlot;
                    if (group.Key == controllerOutputSlot)
                    {
                        originalPhysicalReport = selected;
                        PhysicalSlot = groupedInputs[selectedIndex].XInputSlot;
                    }
                    if (group.Key == 1) primaryReport = selected;
                    else additionalOutputReports[group.Key] = selected;
                }
                lock (_stateLock)
                    _outputSourceSlots = selectedSourceSlots;
            }
            else
            {
                lock (_stateLock) _outputSourceSlots = [];
            }

            // Preserve the controller report exactly as XInput supplied it
            // before extended-button disambiguation and profile routing.
            var rawPhysicalReport = originalPhysicalReport;

            // This controller's firmware also raises XInput View when its
            // separate HID Share control is down. Remove that duplicate bit
            // so Share can act as a layer modifier without opening the game's
            // Back/View screen.
            if (extBtns.Contains("Share")) originalPhysicalReport.Buttons &= ~0x0020;
            if (extBtns.Contains("Home")) originalPhysicalReport.Buttons |= 0x0400;
            if (controllerOutputSlot == 1) primaryReport = originalPhysicalReport;
            else additionalOutputReports[controllerOutputSlot] = originalPhysicalReport;

            // A tap primes once. While RB remains down, keep extending the
            // helper's listening deadline at 10 Hz so its display stays at 17
            // seconds until release. Use the authoritative physical report
            // instead of making the helper rediscover the controller.
            if ((originalPhysicalReport.Buttons & 0x0200) != 0)
            {
                if (loopNow >= _nextFishingPrimeAt)
                {
                    _fishingPrimeEvent.Set();
                    _nextFishingPrimeAt = loopNow + 0.1;
                }
            }
            else
            {
                _nextFishingPrimeAt = 0;
            }

            var report = primaryReport;

            IReadOnlySet<int> activeKeys = _hooks.KeyboardCaptureEnabled
                ? _hooks.GetActiveKeys()
                : new HashSet<int>();
            var mouseDx = 0;
            var mouseDy = 0;
            IReadOnlySet<string> mouseButtons = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            IReadOnlySet<string> mousePulses = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (_hooks.MouseCaptureEnabled)
            {
                var mouse = _hooks.GetAndResetMouseState();
                mouseDx = mouse.dx;
                mouseDy = mouse.dy;
                mouseButtons = mouse.buttons;
                mousePulses = mouse.pulses;
            }
            var rawSnapshot = _rawInput?.DrainSnapshot() ?? new NativeRawInputSnapshot(
                new Dictionary<string, HashSet<int>>(), new Dictionary<string, NativeRawMouseState>());
            var enabledRawSnapshot = new NativeRawInputSnapshot(
                _hooks.KeyboardCaptureEnabled ? rawSnapshot.Keyboards : new Dictionary<string, HashSet<int>>(),
                _hooks.MouseCaptureEnabled ? rawSnapshot.Mice : new Dictionary<string, NativeRawMouseState>());
            var activeSources = _profile.BuildActiveSources(
                activeKeys, mouseButtons, mousePulses, originalPhysicalReport, extBtns);
            _profile.AddRawActiveSources(enabledRawSnapshot, activeSources);
            _profile.UpdateLayers(activeSources, loopNow);
            var controllerMouse = _profile.ControllerMouse;
            var controllerMouseToggleDown = controllerMouse.Enabled &&
                _profile.IsSourceActive(controllerMouse.ToggleSource, originalPhysicalReport, extBtns, activeSources) &&
                (!controllerMouse.RequiresMod || _profile.ModActive);
            var controllerMouseChanged = false;
            if (controllerMouseToggleDown && !_controllerMouseToggleWasDown)
            {
                ControllerMouseModeEnabled = !ControllerMouseModeEnabled;
                controllerMouseChanged = true;
            }
            else if (!controllerMouse.Enabled && ControllerMouseModeEnabled)
            {
                ControllerMouseModeEnabled = false;
                controllerMouseChanged = true;
            }
            _controllerMouseToggleWasDown = controllerMouseToggleDown;

            List<NativeRouteAction> routeActions;
            if (controllerOutputSlot == 1)
            {
                routeActions = _profile.ApplyControllerRoutes(
                    originalPhysicalReport, extBtns, ref report, loopNow, activeSources,
                    additionalOutputReports, 1).ToList();
            }
            else
            {
                additionalOutputReports.TryGetValue(controllerOutputSlot, out var assignedReport);
                routeActions = _profile.ApplyControllerRoutes(
                    originalPhysicalReport, extBtns, ref assignedReport, loopNow, activeSources,
                    additionalOutputReports, controllerOutputSlot).ToList();
                additionalOutputReports[controllerOutputSlot] = assignedReport;
            }

            // Guide + Menu is always available as the desktop-mode escape chord.
            if (DesktopModeEnabled && (originalPhysicalReport.Buttons & 0x0410) == 0x0410)
                ToggleDesktopMode();

            // 5. Apply Keyboard and Mouse inputs if capture is active
            if (_hooks.KeyboardCaptureEnabled)
            {
                routeActions.AddRange(_profile.ApplyKeyboard(activeKeys, ref report, loopNow, activeSources, additionalOutputReports));
            }

            if (_hooks.MouseCaptureEnabled)
            {
                routeActions.AddRange(_profile.ApplyMouse(
                    mouseDx, mouseDy, mouseButtons, mousePulses, ref report, loopNow, activeSources, additionalOutputReports));
            }
            routeActions.AddRange(_profile.ApplyRawInputs(enabledRawSnapshot, ref report, loopNow, activeSources,
                _hooks.KeyboardCaptureEnabled, _hooks.MouseCaptureEnabled, additionalOutputReports));

            var activeActionSources = routeActions.Select(action => action.Source)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
            foreach (var action in routeActions)
            {
                if (_activeRouteActionSources.Contains(action.Source)) continue;
                if (action.Action.StartsWith("Macro:", StringComparison.OrdinalIgnoreCase))
                    _macroScheduler.Start(action.Action[6..], action.Source, loopNow);
                else if (action.Action.StartsWith("Desktop:", StringComparison.OrdinalIgnoreCase)) { }
                else ExecuteAction(action.Action);
            }
            _activeRouteActionSources = activeActionSources;
            var activeMacroSources = routeActions
                .Where(action => action.Action.StartsWith("Macro:", StringComparison.OrdinalIgnoreCase))
                .Select(action => action.Source).ToHashSet(StringComparer.OrdinalIgnoreCase);
            _macroScheduler.CancelReleased(activeMacroSources);
            var desktopActions = routeActions.Where(action => action.Action.StartsWith("Desktop:", StringComparison.OrdinalIgnoreCase))
                .Select(action => action.Action).ToHashSet(StringComparer.OrdinalIgnoreCase);
            if (ControllerMouseModeEnabled)
            {
                if (_profile.IsSourceActive(controllerMouse.LeftClickSource, originalPhysicalReport, extBtns, activeSources))
                    desktopActions.Add("Desktop:Mouse:Left");
                if (_profile.IsSourceActive(controllerMouse.RightClickSource, originalPhysicalReport, extBtns, activeSources))
                    desktopActions.Add("Desktop:Mouse:Right");
                if (_profile.IsSourceActive(controllerMouse.ScrollUpSource, originalPhysicalReport, extBtns, activeSources))
                    desktopActions.Add("Desktop:Wheel:Up");
                if (_profile.IsSourceActive(controllerMouse.ScrollDownSource, originalPhysicalReport, extBtns, activeSources))
                    desktopActions.Add("Desktop:Wheel:Down");
            }
            if (DesktopModeEnabled || ControllerMouseModeEnabled) _desktopOutput.Update(desktopActions, loopNow);
            else _desktopOutput.Update(new HashSet<string>(StringComparer.OrdinalIgnoreCase), loopNow);
            if (ControllerMouseModeEnabled)
            {
                var pointerX = controllerMouse.Stick == "left" ? originalPhysicalReport.LeftX : originalPhysicalReport.RightX;
                var pointerY = controllerMouse.Stick == "left" ? originalPhysicalReport.LeftY : originalPhysicalReport.RightY;
                _desktopOutput.UpdateAnalogPointer(pointerX, pointerY, controllerMouse.Sensitivity, controllerMouse.Deadzone, loopNow);

                void SuppressMouseControls(ref NativeControllerReport target)
                {
                    _profile.SuppressSource(controllerMouse.LeftClickSource, ref target);
                    _profile.SuppressSource(controllerMouse.RightClickSource, ref target);
                    _profile.SuppressSource(controllerMouse.ScrollUpSource, ref target);
                    _profile.SuppressSource(controllerMouse.ScrollDownSource, ref target);
                    if (controllerMouse.Stick == "left") { target.LeftX = 0; target.LeftY = 0; }
                    else { target.RightX = 0; target.RightY = 0; }
                }
                if (controllerOutputSlot == 1) SuppressMouseControls(ref report);
                else
                {
                    additionalOutputReports.TryGetValue(controllerOutputSlot, out var assigned);
                    SuppressMouseControls(ref assigned);
                    additionalOutputReports[controllerOutputSlot] = assigned;
                }
            }
            if (controllerMouseToggleDown)
            {
                if (controllerOutputSlot == 1) _profile.SuppressSource(controllerMouse.ToggleSource, ref report);
                else
                {
                    additionalOutputReports.TryGetValue(controllerOutputSlot, out var assigned);
                    _profile.SuppressSource(controllerMouse.ToggleSource, ref assigned);
                    additionalOutputReports[controllerOutputSlot] = assigned;
                }
            }
            _profile.FinishInputFrame(activeSources);
            var modSource = _profile.Mod.Source;
            var modActive = _profile.ModActive;
            if (!string.Equals(modSource, _lastNotifiedModSource, StringComparison.OrdinalIgnoreCase))
            {
                _lastNotifiedModSource = modSource;
                _lastNotifiedModActive = modActive;
            }
            else if (_lastNotifiedModActive.HasValue && _lastNotifiedModActive.Value != modActive)
            {
                _lastNotifiedModActive = modActive;
                ModStateChanged?.Invoke(modActive, modSource, _profile.Mod.Behavior);
            }
            else if (!_lastNotifiedModActive.HasValue)
            {
                _lastNotifiedModActive = modActive;
            }
            if (controllerMouseChanged)
            {
                if (!ControllerMouseModeEnabled && !DesktopModeEnabled) _desktopOutput.ReleaseAll();
                ControllerMouseModeToggled?.Invoke(ControllerMouseModeEnabled);
            }

            _motionMapper.Apply(ref report, Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency);
            if (ControllerMouseModeEnabled)
            {
                if (controllerMouse.Stick == "left") { report.LeftX = 0; report.LeftY = 0; }
                else { report.RightX = 0; report.RightY = 0; }
            }
            _profile.ApplyStickProcessing(ref report);
            foreach (var slot in additionalOutputReports.Keys.ToArray())
            {
                var slotReport = additionalOutputReports[slot];
                _profile.ApplyStickProcessing(ref slotReport);
                additionalOutputReports[slot] = slotReport;
            }
            _macroScheduler.Apply(loopNow, ref report);
            foreach (var action in _macroScheduler.DrainActions()) ExecuteAction(action);

            _autonomy.Apply(ref report, rawPhysicalReport, loopNow);
            if (Stopwatch.GetTimestamp() < _catchUntilTimestamp)
                report.Buttons |= 0x2000; // XInput B

            virtualButtons = report.Buttons;
            virtualLt = report.LeftTrigger;
            virtualRt = report.RightTrigger;
            virtualLx = report.LeftX;
            virtualLy = report.LeftY;
            virtualRx = report.RightX;
            virtualRy = report.RightY;

            // 6. Submit virtual report to ViGEm
            _vigem.SubmitReport(1, report);
            foreach (var slot in _vigem.EnabledSlots.Where(value => value != 1))
                _vigem.SubmitReport(slot, additionalOutputReports.GetValueOrDefault(slot));

            // 7. Update live properties
            lock (_stateLock)
            {
                _physicalSnapshot = new NativePhysicalSnapshot(
                    foundPhysical, rawPhysicalReport.Buttons,
                    rawPhysicalReport.LeftTrigger, rawPhysicalReport.RightTrigger,
                    rawPhysicalReport.LeftX, rawPhysicalReport.LeftY,
                    rawPhysicalReport.RightX, rawPhysicalReport.RightY, [.. extBtns]);
                LiveButtons = virtualButtons;
                LiveLt = virtualLt;
                LiveRt = virtualRt;
                LiveLx = virtualLx;
                LiveLy = virtualLy;
                LiveRx = virtualRx;
                LiveRy = virtualRy;
            }

            // 8. Periodically write status.json (every 100ms) for external tool compatibility
            if (statusStopwatch.ElapsedMilliseconds >= 100)
            {
                statusStopwatch.Restart();
                _vigem.MaintainOutputs();
                VirtualSlot = _vigem.UserIndex ?? -1;
                CheckFishingHelperTelemetry();
                if (performanceSamples > 0)
                {
                    AverageLoopMs = performanceTotalMs / performanceSamples;
                    MaximumLoopMs = performanceMaximumMs;
                    performanceSamples = 0;
                    performanceTotalMs = 0;
                    performanceMaximumMs = 0;
                }
                WriteStatusJson(connectedPhysicalSlots);
            }

            // Keep fast input when a controller/capture/macro is in use, but
            // avoid doing thousands of idle routing operations per second.
            PollIntervalMs = NativeLoopPacing.IntervalMilliseconds(foundPhysical,
                _hooks.KeyboardCaptureEnabled || _hooks.MouseCaptureEnabled,
                motionChanged || _macroScheduler.ActiveCount > 0 || tickStart < _catchUntilTimestamp);
            tickPeriod = Stopwatch.Frequency * PollIntervalMs / 1000;
            var elapsedUs = (Stopwatch.GetTimestamp() - tickStart) * 1_000_000.0 / Stopwatch.Frequency;
            var elapsedMs = elapsedUs / 1000.0;
            performanceSamples++;
            performanceTotalMs += elapsedMs;
            performanceMaximumMs = Math.Max(performanceMaximumMs, elapsedMs);
            nextTick = NativeLoopPacing.NextDeadline(nextTick, Stopwatch.GetTimestamp(), tickPeriod, Stopwatch.Frequency);
            var remainingTicks = nextTick - Stopwatch.GetTimestamp();
            if (remainingTicks > 0)
            {
                if (loopTimer != IntPtr.Zero)
                {
                    var dueTime = -Math.Max(1, remainingTicks * 10_000_000L / Stopwatch.Frequency);
                    if (SetWaitableTimer(loopTimer, ref dueTime, 0, IntPtr.Zero, IntPtr.Zero, false))
                        WaitForSingleObject(loopTimer, 10);
                    else
                        Thread.Sleep(1);
                }
                else
                {
                    Thread.Sleep(1);
                }
            }
            else Thread.Sleep(1);
        }

        if (loopTimer != IntPtr.Zero) CloseHandle(loopTimer);
        timeEndPeriod(1);
    }

    private void CheckFishingHelperTelemetry()
    {
        try
        {
            // Python publishes this file with os.replace(). Delete sharing is
            // required or a reader can intermittently block the atomic swap.
            using var stream = new FileStream(
                _fishingTelemetryPath, FileMode.Open, FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete);
            using var document = JsonDocument.Parse(stream);
            var root = document.RootElement;
            var primed = root.TryGetProperty("primed", out var primedValue) && primedValue.GetBoolean();
            var remaining = root.TryGetProperty("listen_remaining_seconds", out var remainingValue)
                && remainingValue.TryGetDouble(out var remainingSeconds)
                ? remainingSeconds
                : 0.0;
            // Match the original helper display: a freshly refreshed 18 second
            // window is presented as 17 because a fraction has already elapsed.
            var displaySeconds = primed ? Math.Max(0, (int)Math.Floor(remaining)) : 0;
            if (primed != _lastFishingPrimed || (primed && displaySeconds != _lastFishingRemainingSeconds))
            {
                var score = root.TryGetProperty("best_bite_score", out var scoreValue)
                    && scoreValue.TryGetDouble(out var bestScore)
                    ? bestScore
                    : 0.0;
                FishingHelperPrimeStateChanged?.Invoke(
                    new FishingHelperPrimeState(primed, displaySeconds, score));
            }
            _lastFishingPrimed = primed;
            _lastFishingRemainingSeconds = displaySeconds;
        }
        catch
        {
            // The helper atomically replaces telemetry.json. A missing or
            // temporarily unavailable file should not affect input handling.
        }
    }

    private void CheckReloadProfile()
    {
        try
        {
            if (File.Exists(_profilesPath))
            {
                var writeTime = File.GetLastWriteTimeUtc(_profilesPath);
                if (writeTime != _profileLastWrite)
                {
                    LoadProfile();
                }
            }
        }
        catch { }
    }

    private void LoadProfile()
    {
        try
        {
            if (File.Exists(_profilesPath))
            {
                _profileLastWrite = File.GetLastWriteTimeUtc(_profilesPath);
                _desktopOutput.ReleaseAll();
                _profile = NativeProfileRuntime.Load(_profilesPath);
                _autonomy.Enabled = NativeAutonomyInput.ReadEnabled(_profilesPath);
                _vigem.ConfigureSlots(_profile.EnabledOutputSlots);
                _motionMapper.Configure(_profile.Motion);
                _motionBearing.Configure(_profile.MotionBearing);
                _macroScheduler.Configure(_profile.Macros);
                _hooks.MouseCaptureEnabled = _profile.MouseMovementEnabled;
                _profileError = null;
            }
        }
        catch (Exception ex)
        {
            // Keep the last valid immutable snapshot if an invalid or
            // partially-written profile is observed.
            _profileError = ex.Message;
        }
    }

    private void WriteStatusJson(IReadOnlyCollection<int> connectedPhysicalSlots)
    {
        try
        {
            NativePhysicalSnapshot physical;
            lock (_stateLock) physical = _physicalSnapshot;
            var health = !_running ? "stopped" : !_vigem.IsConnected
                ? "output_unavailable"
                : VirtualSlot < 0
                    ? "virtual_slot_unknown"
                    : !_vigem.ReportsVerified
                        ? _vigem.Health == "healthy" ? "output_verifying" : _vigem.Health
                        : PhysicalConnected ? "healthy" : "waiting_for_controller";

            var doc = new
            {
                running = _running,
                process_id = Environment.ProcessId,
                source_present = physical.Connected,
                updated = DateTimeOffset.UtcNow.ToString("O"),
                passthrough_health = health,
                virtual_output_error = _vigem.LastError,
                reports_match = _vigem.ReportsVerified,
                output_recovery_count = _vigem.RecoveryCount,
                virtual_xinput_index = VirtualSlot >= 0 ? (int?)VirtualSlot : null,
                active_source_index = PhysicalConnected ? (int?)PhysicalSlot : null,
                active_source_id = PhysicalConnected ? $"xinput:{PhysicalSlot}" : null,
                source_indices = connectedPhysicalSlots,
                active_source_state = new int[]
                {
                    physical.Buttons, physical.LeftTrigger, physical.RightTrigger,
                    physical.LeftX, physical.LeftY, physical.RightX, physical.RightY
                },
                virtual_outputs = _vigem.Outputs.Select(output => new
                {
                    logical_slot = output.LogicalSlot,
                    xinput_index = output.XInputIndex,
                    connected = output.Connected,
                    verified = output.Verified,
                    health = output.Health
                }).ToArray(),
                raw_input_devices = _rawInput?.Devices.Select(device => new
                {
                    id = device.Id,
                    kind = device.Kind,
                    name = device.Name,
                    connected = device.Connected,
                    last_seen = device.LastSeenUtc.ToString("O"),
                    last_input = device.LastInput
                }).ToArray() ?? [],
                gameinput_available = _gameInput.Available,
                gameinput_error = _gameInput.Error,
                gameinput_devices = _gameInput.Devices.Select(device => new
                {
                    id = device.Id,
                    device_id = device.DeviceId,
                    root_id = device.RootId,
                    vendor_id = device.VendorId,
                    product_id = device.ProductId,
                    name = device.Name,
                    connected = device.Connected,
                    is_virtual = device.IsVirtual,
                    xinput_slot = device.XInputSlot,
                    last_seen = device.LastSeenUtc.ToString("O"),
                    last_input = device.LastInput
                }).ToArray(),
                joycon_devices = _joyCons.Devices.Select(device => new
                {
                    id = device.Id,
                    name = device.Name,
                    serial = device.Serial,
                    side = device.Side.ToString().ToLowerInvariant(),
                    connected = device.Connected,
                    battery_percent = device.BatteryPercent,
                    last_seen = device.LastSeenUtc.ToString("O"),
                    last_input = device.LastInput
                }).ToArray(),
                joycon_pairs = _joyCons.Pairs.Select(pair => new
                {
                    id = pair.Id,
                    name = pair.Name,
                    left_id = pair.LeftId,
                    right_id = pair.RightId
                }).ToArray(),
                source_merge_policy = _profile.SourceMergePolicy,
                device_output_assignments = _profile.DeviceOutputSlots,
                active_profile = _profile.ActiveProfileName,
                configured_route_count = _profile.RouteCount,
                configured_effect_route_count = _profile.EffectRouteCount,
                configured_activator_route_count = _profile.ActivatorRouteCount,
                active_layers = _profile.ActiveLayers,
                mod_key_source = _profile.Mod.Source,
                mod_key_behavior = _profile.Mod.Behavior,
                mod_key_active = _profile.ModActive,
                autonomy_state = _autonomy.State,
                autonomy_last_error = _autonomy.LastError,
                configured_macro_count = _profile.Macros.Count,
                active_macro_count = _macroScheduler.ActiveCount,
                active_macros = _macroScheduler.ActiveNames,
                desktop_mode_enabled = DesktopModeEnabled,
                controller_mouse_mode_enabled = ControllerMouseModeEnabled,
                controller_mouse_configured = _profile.ControllerMouse.Enabled,
                desktop_held_key_count = _desktopOutput.HeldKeyCount,
                desktop_held_mouse_button_count = _desktopOutput.HeldMouseButtonCount,
                profile_error = _profileError,
                mouse_sensitivity = _profile.MouseSensitivity,
                mouse_movement_enabled = _profile.MouseMovementEnabled,
                fishing_helper_worker_running = _fishingHelper.IsRunning,
                fishing_helper_worker_pid = _fishingHelper.ProcessId,
                fishing_helper_worker_error = _fishingHelper.LastError,
                fishing_helper_catch_active = Stopwatch.GetTimestamp() < _catchUntilTimestamp,
                virtual_state = new int[] { LiveButtons, LiveLt, LiveRt, LiveLx, LiveLy, LiveRx, LiveRy },
                motion_dsu_running = _dsu.IsRunning,
                motion_preview_dsu_running = _previewDsu.IsRunning,
                motion_sample_fresh = _gameSir.IsConnected,
                motion_is_still = _motionCalibration.IsStill,
                motion_pitch = Math.Round(_motionMapper.Pitch, 3),
                motion_roll = Math.Round(_motionMapper.Roll, 3),
                motion_preset = _profile.Motion.Preset,
                motion_bearing_enabled = _motionBearing.Enabled,
                motion_bearing_rates = new[]
                {
                    Math.Round(_motionBearing.PitchRate, 3),
                    Math.Round(_motionBearing.YawRate, 3),
                    Math.Round(_motionBearing.RollRate, 3)
                },
                motion_gyro = GetPreviewDsuGyro(),
                motion_accel = GetPreviewDsuAccel(),
                motion_citron_gyro = GetCitronDsuGyro(),
                motion_citron_accel = GetCitronDsuAccel(),
                raw_hid_button_bytes = _gameSir.GetLatestButtonBytes(),
                raw_hid_report_hex = _gameSir.GetLatestReportHex(),
                active_extended_buttons = ExtendedButtons.ToArray(),
                visual_extended_buttons = GetVisualExtendedButtons(),
                performance = new
                {
                    poll_interval_ms = PollIntervalMs,
                    average_loop_ms = Math.Round(AverageLoopMs, 4),
                    maximum_loop_ms = Math.Round(MaximumLoopMs, 4)
                }
            };

            var tmp = _statusPath + ".tmp";
            File.WriteAllText(tmp, JsonSerializer.Serialize(doc, new JsonSerializerOptions { WriteIndented = true }));
            for (var attempt = 0; ; attempt++)
            {
                try { File.Move(tmp, _statusPath, true); break; }
                catch (IOException) when (!_running && attempt < 4) { Thread.Sleep(20); }
            }
        }
        catch (Exception ex)
        {
            if (Environment.TickCount64 >= _nextStatusErrorLogAt)
            {
                _nextStatusErrorLogAt = Environment.TickCount64 + 5000;
                App.Log($"Could not publish controller status: {ex.Message}");
            }
        }
    }

    private string[] GetVisualExtendedButtons()
    {
        var now = Stopwatch.GetTimestamp();
        var buttons = new List<string>(2);
        if (now < _shareVisualUntilTimestamp)
            buttons.Add("Share");
        if (now < _homeVisualUntilTimestamp)
            buttons.Add("Home");
        return buttons.ToArray();
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposeState, 1) != 0) return;
        _running = false;
        try { _engineThread?.Join(500); } catch { }

        _vigem.Dispose();
        PhysicalConnected = false;
        PhysicalSlot = VirtualSlot = -1;
        lock (_stateLock) _physicalSnapshot = new(false, 0, 0, 0, 0, 0, 0, 0, []);
        WriteStatusJson(Array.Empty<int>());
        _gameInput.Dispose();
        _gameSir.Dispose();
        _joyCons.Dispose();
        _hooks.Dispose();
        _dsu.Dispose();
        _previewDsu.Dispose();
        _fishingHelper.Dispose();
        _desktopOutput.Dispose();
        _catchEvent.Dispose();
        _autonomy.Dispose();
        _fishingToggleEvent.Dispose();
        _fishingPrimeEvent.Dispose();
    }

    private float[] GetPreviewDsuGyro()
    {
        lock (_dsuMotionLock) return [.. _dsuGyro];
    }

    private float[] GetPreviewDsuAccel()
    {
        lock (_dsuMotionLock) return [.. _dsuAccel];
    }

    private ulong GetDsuTimestampUs()
    {
        lock (_dsuMotionLock) return _dsuTimestampUs;
    }

    private float[] GetCitronDsuGyro()
    {
        lock (_dsuMotionLock)
        {
            // Citron converts DSU (pitch, yaw, roll) into its internal
            // (pitch, roll, -yaw) frame. With the acceleration permutation
            // below, pitch and yaw must retain the same handedness through
            // Citron's MotionInput integration.
            return [_dsuGyro[0], -_dsuGyro[1], _dsuGyro[2]];
        }
    }

    private float[] GetCitronDsuAccel()
    {
        lock (_dsuMotionLock)
        {
            // Citron maps DSU acceleration (x, y, z) to (x, -z, y), while its
            // MotionInput fusion expects a level controller to read (0, 0, -1).
            // Pre-permute the calibrated controller vector so Citron receives
            // the same physical axes: DSU (x, -z, y) -> Citron (x, -y, -z).
            return [_dsuAccel[0], -_dsuAccel[2], _dsuAccel[1]];
        }
    }

    private static void TryDelete(string path)
    {
        try { File.Delete(path); } catch (IOException) { } catch (UnauthorizedAccessException) { }
    }

    private void ExecuteAction(string action)
    {
        if (action.Equals("Fishing Helper Toggle", StringComparison.OrdinalIgnoreCase))
            ToggleFishingHelper();
        else if (action.Equals("Keyboard Controller Mode", StringComparison.OrdinalIgnoreCase))
            ToggleKeyboardMode();
        else if (action.Equals("Mouse Aim Mode", StringComparison.OrdinalIgnoreCase))
            ToggleMouseAiming();
        else if (action.Equals("Open Control Center", StringComparison.OrdinalIgnoreCase))
            OpenControlCenterRequested?.Invoke();
        else if (action.Equals("Desktop Mode", StringComparison.OrdinalIgnoreCase))
            ToggleDesktopMode();
        else if (action.Equals("Cancel Macros", StringComparison.OrdinalIgnoreCase))
            CancelAllMacros();
    }
}
