using System.Diagnostics;
using System.Collections.Concurrent;
using System.Text.Json;
using UnifiedController.App.Engine;

namespace UnifiedController.Core;

public sealed class ControllerRuntime : IControllerRuntime, IOutputTypeController
{
    private readonly string _root;
    private readonly VirtualOutputManager _outputs;
    private readonly IDesktopInputSource _desktopInput;
    private readonly IDesktopOutputSink _desktopOutput;
    private readonly IMonotonicClock _clock;
    private readonly ProfileService _profiles;
    private readonly NativeSourceSelector _selector = new();
    private readonly NativeMacroScheduler _macros = new();
    private readonly NativeMotionStickMapper _motion = new();
    private readonly MotionOrientationEstimator _orientation = new();
    private CancellationTokenSource? _stop;
    private Task? _loop;
    private FileStream? _instanceLock;
    private RuntimeSnapshot _snapshot;
    private HashSet<string> _activeRouteActions = new(StringComparer.OrdinalIgnoreCase);
    private bool _controllerMouseToggleWasDown;
    private bool _controllerMouseSuppressingToggle;
    private bool _controllerMouseEnabled;
    private readonly ConcurrentQueue<Action> _commands = new();
    private readonly Dictionary<string, NativeMotionCalibration> _calibrators = [];
    private readonly Dictionary<int, NativeSourceSelector> _slotSelectors = [];
    private readonly Dictionary<string, ulong> _lastMotionTimestamps = [];
    private readonly Dictionary<string, double> _nextMotionAt = [];
    private readonly string[] _baseLimitations;
    private string? _motionSource;
    private bool _calibrationRequested;
    // DSU (cemuhook) motion feed, as in the Windows engine: 26760 for emulators (Citron/Eden axes), 26761 raw preview.
    private NativeDsuServer? _dsu, _previewDsu;
    private readonly object _dsuLock = new();
    private float[] _dsuGyro = [0, 0, 0], _dsuAccel = [0, 0, 1];
    private ulong _dsuTimestampUs;
    private MotionOrientation? _currentOrientation;
    private RumbleState _rumble;
    private CancellationTokenSource? _testRumbleStop;
    private bool _desktopMode, _keyboardControllerMode, _mouseAimMode;
    public event Action<string>? ActionRequested;

    public ControllerRuntime(string rootDirectory, IControllerInputProvider input, IVirtualControllerOutput output,
        IDesktopInputSource desktopInput, IDesktopOutputSink desktopOutput, IMonotonicClock clock)
        : this(rootDirectory, input, slot => slot == 1 ? output : throw new InvalidOperationException("The single-output runtime supports only slot 1."), desktopInput, desktopOutput, clock, true)
    { }

    public ControllerRuntime(string rootDirectory, IControllerInputProvider input, Func<int, IVirtualControllerOutput> outputFactory,
        IDesktopInputSource desktopInput, IDesktopOutputSink desktopOutput, IMonotonicClock clock)
        : this(rootDirectory, input, outputFactory, desktopInput, desktopOutput, clock, true)
    { }

    private ControllerRuntime(string rootDirectory, IControllerInputProvider input, Func<int, IVirtualControllerOutput> outputFactory,
        IDesktopInputSource desktopInput, IDesktopOutputSink desktopOutput, IMonotonicClock clock, bool ownsOutputs)
        : this(rootDirectory, input, (_, slot) => outputFactory(slot), "xbox360", desktopInput, desktopOutput, clock)
    { }

    public ControllerRuntime(string rootDirectory, IControllerInputProvider input, Func<string, int, IVirtualControllerOutput> outputFactory,
        string outputType, IDesktopInputSource desktopInput, IDesktopOutputSink desktopOutput, IMonotonicClock clock)
    {
        _root = Path.GetFullPath(rootDirectory); Directory.CreateDirectory(_root);
        ControllerInput = input; _outputs = new(outputFactory, outputType, OnRumble, () => clock.Seconds); _desktopInput = desktopInput; _desktopOutput = desktopOutput; _clock = clock;
        _profiles = new(_root);
        var limitations = new List<string> { "Windows-only audio/module actions are not provided by this Linux version." };
        AddLimitation(desktopOutput, limitations);
        _baseLimitations = limitations.ToArray();
        _snapshot = new("Default", false, "Stopped", [], null, default, default, null, 0, 0, 0, null, limitations);
    }

    public VirtualOutputStatus OutputStatus => _outputs.Status;
    public IReadOnlyList<InputDeviceInfo> OwnedDevices => ControllerInput.Devices
        .Where(device => device.Connected && !device.IsVirtual).Take(64).ToArray();
    public ValueTask<bool> SetOutputTypeAsync(string type, bool force = false, CancellationToken token = default) => _outputs.SwitchAsync(type, force, token);
    public void ObserveGames(IReadOnlyList<string> activeGameIds, bool observationKnown = true) => _outputs.ObserveGames(activeGameIds, observationKnown);

    public IControllerInputProvider ControllerInput { get; }
    public RuntimeSnapshot Snapshot => Volatile.Read(ref _snapshot);
    public bool KeyboardCaptureEnabled { get => _desktopInput.KeyboardEnabled; set { _desktopInput.KeyboardEnabled = value; if (!value) _desktopOutput.ReleaseAll(); } }
    public bool MouseCaptureEnabled { get => _desktopInput.MouseEnabled; set { _desktopInput.MouseEnabled = value; if (!value) _desktopOutput.ReleaseAll(); } }

    public async ValueTask StartAsync(CancellationToken token = default)
    {
        if (_loop is not null) return;
        try { _instanceLock = new FileStream(Path.Combine(_root, ".runtime.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException ex) { throw new InvalidOperationException("Another Unified Controller runtime owns this data directory.", ex); }
        try
        {
            var profile = await _profiles.LoadAsync(token).ConfigureAwait(false);
            await LoadCalibrationsAsync(token).ConfigureAwait(false);
            _macros.Configure(profile.Macros); _motion.Configure(profile.Motion);
            await RebuildOutputsAsync(profile.EnabledOutputSlots, token).ConfigureAwait(false);
            _stop = CancellationTokenSource.CreateLinkedTokenSource(token);
            _loop = Task.Run(() => RunAsync(_stop.Token), CancellationToken.None);
            StartDsu();
        }
        catch (Exception ex)
        {
            var cleanupErrors = new List<Exception>();
            try { await DisposeOutputsAsync().ConfigureAwait(false); }
            catch (Exception cleanup) { cleanupErrors.Add(cleanup); }
            try { _instanceLock?.Dispose(); _instanceLock = null; }
            catch (Exception cleanup) { cleanupErrors.Add(cleanup); }
            var message = cleanupErrors.Count == 0 ? ex.Message :
                ex.Message + " (startup cleanup also failed: " + string.Join("; ", cleanupErrors.Select(e => e.Message)) + ")";
            UpdateSnapshot(Snapshot with { Running = false, Health = "Faulted", Error = message });
            if (cleanupErrors.Count > 0)
                throw new AggregateException("Controller runtime startup failed and cleanup reported errors.", [ex, .. cleanupErrors]);
            throw;
        }
    }

    public async ValueTask StopAsync()
    {
        var loop = _loop;
        _stop?.Cancel();
        if (loop is not null) try { await loop.ConfigureAwait(false); } catch (OperationCanceledException) { } catch { }
        _dsu?.Dispose(); _previewDsu?.Dispose(); _dsu = _previewDsu = null;
        StopRumbleCore();
        _loop = null; _stop?.Dispose(); _stop = null; _macros.CancelAll(); _activeRouteActions.Clear(); _desktopOutput.ReleaseAll();
        await _outputs.NeutralAsync().ConfigureAwait(false);
        try { await SaveCalibrationsAsync().ConfigureAwait(false); }
        finally { _instanceLock?.Dispose(); _instanceLock = null; }
        UpdateSnapshot(Snapshot with { Running = false, Health = Snapshot.Health == "Faulted" ? "Faulted" : "Stopped", Output = default, OutputSlots = new Dictionary<int, ControllerFrame>(), ActiveMacros = 0, ControllerMouseModeEnabled = false });
    }

    public void StopAllMacros() => _commands.Enqueue(() => { _macros.CancelAll(); _desktopOutput.ReleaseAll(); });
    public void RecenterMotion() => _commands.Enqueue(() => { _motion.Reset(); _orientation.Recenter(); _currentOrientation = null; });
    public void CalibrateMotion() => _commands.Enqueue(() => _calibrationRequested = true);
    public void TestRumble(double heavy, double light, int durationMs)
    {
        heavy=Math.Clamp(heavy,0,1); light=Math.Clamp(light,0,1); durationMs=Math.Max(0,durationMs);
        _commands.Enqueue(() => { _testRumbleStop?.Cancel(); _testRumbleStop?.Dispose(); SendRumble((byte)Math.Round(heavy*255),(byte)Math.Round(light*255),true,false); if(durationMs>0){var stop=_testRumbleStop=new(); _=Task.Delay(durationMs,stop.Token).ContinueWith(t=>{if(!t.IsCanceled)_commands.Enqueue(StopRumbleCore);},CancellationToken.None,TaskContinuationOptions.ExecuteSynchronously,TaskScheduler.Default);} });
    }
    public void StopRumble() => _commands.Enqueue(StopRumbleCore);
    public bool InvokeAction(string action)
    {
        if(!RuntimeActions.All.Contains(action,StringComparer.OrdinalIgnoreCase) || action.Equals(RuntimeActions.FishingHelperToggle,StringComparison.OrdinalIgnoreCase)) return false;
        _commands.Enqueue(()=>InvokeActionCore(action)); return true;
    }

    private async Task RunAsync(CancellationToken token)
    {
        var samplesWindow = new Queue<double>(); double max = 0; var nextReload = 0.0; var nextOutputRepair = 0.0; string? error = null;
        Task<bool>? reloadTask = null; var configuredSlots = "";
        try
        {
            while (!token.IsCancellationRequested)
            {
                var began = Stopwatch.GetTimestamp(); var now = _clock.Seconds;
                while (_commands.TryDequeue(out var command)) command();
                // Filesystem probing and parsing stay on a worker; the forwarding loop only
                // observes a completed task and swaps/configures between frames.
                if (reloadTask is { IsCompleted: true })
                {
                    try { if (await reloadTask.ConfigureAwait(false)) { _macros.CancelAll(); _activeRouteActions.Clear(); _desktopOutput.ReleaseAll(); _macros.Configure(_profiles.Current.Macros); _motion.Configure(_profiles.Current.Motion); } error = null; }
                    catch (Exception ex) when (ex is not OperationCanceledException) { error = ex.Message; }
                    reloadTask = null;
                }
                if (reloadTask is null && now >= nextReload) { nextReload = now + .5; reloadTask = Task.Run(() => _profiles.ReloadIfChangedAsync(token), token); }
                var profile = _profiles.Current;
                var slotKey = string.Join(',', profile.EnabledOutputSlots);
                var outputStatus = _outputs.Status;
                var missingOutput = profile.EnabledOutputSlots.Any(slot =>
                    !outputStatus.Slots.Any(state => state.Slot == slot && state.Connected && state.State == "connected"));
                if (slotKey != configuredSlots || missingOutput && now >= nextOutputRepair)
                {
                    await RebuildOutputsAsync(profile.EnabledOutputSlots, token).ConfigureAwait(false);
                    configuredSlots = slotKey;
                    nextOutputRepair = now + 2.0;
                }
                var samples = await ControllerInput.PollAsync(token).ConfigureAwait(false);
                var native = samples.Select(s => (s.StableId, ToNative(s.Controller))).ToArray();
                var selected = profile.SourceMergePolicy == "all_connected" ? -2 : _selector.Select(native);
                NativeControllerReport original = selected == -2 ? NativeReportMerger.Merge(native.Select(x => x.Item2)) : selected >= 0 ? native[selected].Item2 : default;
                string? selectedId = selected >= 0 ? samples[selected].StableId : selected == -2 ? "all_connected" : null;
                var motionSample = selected >= 0 ? samples[selected] : samples.FirstOrDefault(s => s.Motion is not null);
                var chosenMotion = motionSample?.Motion;
                IReadOnlySet<string> selectedExtended = selected >= 0
                    ? samples[selected].ExtendedButtons
                    : selected == -2
                        ? samples.SelectMany(s => s.ExtendedButtons).ToHashSet(StringComparer.OrdinalIgnoreCase)
                        : new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                var reports = new Dictionary<int, NativeControllerReport>();
                var slotSamples = new Dictionary<int, InputSample>();
                foreach (var group in samples.GroupBy(s => profile.OutputSlotForDevice(s.StableId)))
                {
                    var grouped = group.ToArray();
                    var members = grouped.Select(s => (s.StableId, ToNative(s.Controller))).ToArray();
                    if (!_slotSelectors.TryGetValue(group.Key, out var selector))
                        _slotSelectors[group.Key] = selector = new NativeSourceSelector();
                    var selectedMember = selector.Select(members);
                    reports[group.Key] = profile.SourceMergePolicy == "all_connected"
                        ? NativeReportMerger.Merge(members.Select(s => s.Item2)) : members[selectedMember].Item2;
                    slotSamples[group.Key] = profile.SourceMergePolicy == "all_connected"
                        ? new InputSample("all_connected", FromNative(reports[group.Key]), grouped.FirstOrDefault(s => s.Motion is not null)?.Motion,
                            grouped.SelectMany(s => s.ExtendedButtons).ToHashSet(StringComparer.OrdinalIgnoreCase), 0)
                        : grouped[selectedMember];
                }
                reports.TryGetValue(1, out var report);
                var selectedSlot = selected >= 0 ? profile.OutputSlotForDevice(samples[selected].StableId) : 1;
                if (selectedSlot != 1) reports.TryGetValue(selectedSlot, out report);
                var desktop = _desktopInput.Drain();
                var active = profile.BuildActiveSources(desktop.Keys, desktop.MouseButtons, desktop.MousePulses, original, selectedExtended);
                var raw = new NativeRawInputSnapshot(desktop.Keyboards, desktop.Mice.ToDictionary(p => p.Key, p => new NativeRawMouseState(p.Value.DeltaX, p.Value.DeltaY, p.Value.Buttons, p.Value.Pulses)));
                profile.AddRawActiveSources(raw, active); profile.UpdateLayers(active, now);
                var actions = profile.ApplyControllerRoutes(original, selectedExtended, ref report, now, active, reports, selectedSlot).ToList();
                if (KeyboardCaptureEnabled) actions.AddRange(profile.ApplyKeyboard(desktop.Keys, ref report, now, active, reports));
                if (MouseCaptureEnabled) actions.AddRange(profile.ApplyMouse(desktop.MouseX, desktop.MouseY, desktop.MouseButtons, desktop.MousePulses, ref report, now, active, reports));
                actions.AddRange(profile.ApplyRawInputs(raw, ref report, now, active, KeyboardCaptureEnabled, MouseCaptureEnabled, reports));
                var currentActionSources = actions.Select(a => a.Source).ToHashSet(StringComparer.OrdinalIgnoreCase);
                foreach (var action in actions.Where(a => a.Action.StartsWith("Macro:", StringComparison.OrdinalIgnoreCase) && !_activeRouteActions.Contains(a.Source))) _macros.Start(action.Action[6..], action.Source, now);
                foreach (var action in actions.Where(a => !a.Action.StartsWith("Macro:",StringComparison.OrdinalIgnoreCase) && !a.Action.StartsWith("Desktop:",StringComparison.OrdinalIgnoreCase) && !_activeRouteActions.Contains(a.Source))) InvokeActionCore(action.Action);
                _activeRouteActions = currentActionSources;
                _macros.CancelReleased(actions.Where(a => a.Action.StartsWith("Macro:", StringComparison.OrdinalIgnoreCase)).Select(a => a.Source).ToHashSet(StringComparer.OrdinalIgnoreCase));
                var mouse = profile.ControllerMouse;
                var toggleSourceDown = mouse.Enabled && profile.IsSourceActive(mouse.ToggleSource, original, selectedExtended, active);
                if (toggleSourceDown)
                {
                    if (!_controllerMouseToggleWasDown)
                    {
                        _controllerMouseToggleWasDown = true;
                        if (!mouse.RequiresMod || profile.ModActive)
                        {
                            _controllerMouseEnabled = !_controllerMouseEnabled;
                            _controllerMouseSuppressingToggle = true;
                        }
                    }
                }
                else
                {
                    _controllerMouseToggleWasDown = false;
                    _controllerMouseSuppressingToggle = false;
                }
                if (!mouse.Enabled)
                {
                    _controllerMouseEnabled = false;
                    _controllerMouseSuppressingToggle = false;
                }
                var desktopActions = actions.Where(a => a.Action.StartsWith("Desktop:", StringComparison.OrdinalIgnoreCase)).Select(a => a.Action).ToHashSet(StringComparer.OrdinalIgnoreCase);
                short pointerX = 0, pointerY = 0;
                if (_controllerMouseEnabled) { var noExtended = new HashSet<string>(); pointerX = mouse.Stick == "left" ? original.LeftX : original.RightX; pointerY = mouse.Stick == "left" ? original.LeftY : original.RightY; if (profile.IsSourceActive(mouse.LeftClickSource, original, noExtended, active)) desktopActions.Add("Desktop:Mouse:Left"); if (profile.IsSourceActive(mouse.RightClickSource, original, noExtended, active)) desktopActions.Add("Desktop:Mouse:Right"); if (profile.IsSourceActive(mouse.ScrollUpSource, original, noExtended, active)) desktopActions.Add("Desktop:Wheel:Up"); if (profile.IsSourceActive(mouse.ScrollDownSource, original, noExtended, active)) desktopActions.Add("Desktop:Wheel:Down"); profile.SuppressSource(mouse.LeftClickSource, ref report); profile.SuppressSource(mouse.RightClickSource, ref report); profile.SuppressSource(mouse.ScrollUpSource, ref report); profile.SuppressSource(mouse.ScrollDownSource, ref report); if (mouse.Stick == "left") { report.LeftX=0; report.LeftY=0; } else { report.RightX=0; report.RightY=0; } }
                if (_controllerMouseSuppressingToggle) profile.SuppressSource(mouse.ToggleSource, ref report);
                _desktopOutput.Apply(_desktopMode || _controllerMouseEnabled
                    ? new(desktopActions, pointerX, pointerY, mouse.Sensitivity, mouse.Deadzone)
                    : new(new HashSet<string>()));
                if (_motionSource != motionSample?.StableId) { _motion.Reset(); _orientation.Recenter(); _currentOrientation=null; _motionSource = motionSample?.StableId; }
                if (chosenMotion is { } motion && motionSample is not null)
                {
                    if (!_calibrators.TryGetValue(motionSample.StableId, out var calibration))
                        _calibrators[motionSample.StableId] = calibration = new NativeMotionCalibration(_root, fileStorage: false);
                    if (_calibrationRequested) { calibration.RequestLevelCalibration(); _calibrationRequested = false; }
                    if (now >= _nextMotionAt.GetValueOrDefault(motionSample.StableId) &&
                        (motion.TimestampUs == 0 || !_lastMotionTimestamps.TryGetValue(motionSample.StableId, out var lastTimestamp) || lastTimestamp != motion.TimestampUs))
                    {
                        _nextMotionAt[motionSample.StableId] = now + .005;
                        _lastMotionTimestamps[motionSample.StableId] = motion.TimestampUs;
                        var corrected = calibration.Update(motion.GyroDps, motion.AccelG, _clock.Timestamp);
                        _motion.Update(corrected.Gyro, corrected.Accel, now);
                        // SDL currently reports timestamp 0; DSU clients need a rising timestamp.
                        var stamp = motion.TimestampUs != 0 ? motion.TimestampUs : (ulong)(Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency * 1e6);
                        lock (_dsuLock) { _dsuGyro = [.. corrected.Gyro]; _dsuAccel = [.. corrected.Accel]; _dsuTimestampUs = stamp; }
                        _currentOrientation=_orientation.Update(corrected.Gyro,corrected.Accel,stamp,calibration.IsStill);
                    }
                    _motion.Apply(ref report, now);
                }
                profile.ApplyStickProcessing(ref report); _macros.Apply(now, ref report);
                foreach (var macroAction in _macros.DrainActions()) InvokeActionCore(macroAction);
                profile.FinishInputFrame(active); reports[selectedSlot] = report;
                var outputFrames = await _outputs.SubmitAsync(reports.ToDictionary(p => p.Key, p => FromNative(p.Value)), slotSamples, token).ConfigureAwait(false);
                var frame = outputFrames.GetValueOrDefault(1);
                var elapsed = Stopwatch.GetElapsedTime(began).TotalMilliseconds; samplesWindow.Enqueue(elapsed); while (samplesWindow.Count > 500) samplesWindow.Dequeue(); max = Math.Max(max, elapsed);
                var diagnostics = _desktopInput as IDesktopInputDiagnostics;
                var dsuError=_dsu?.LastError??_previewDsu?.LastError;
                UpdateSnapshot(new(profile.ActiveProfileName, true, error is null ? "Running" : "Profile reload failed; using last good", ControllerInput.Devices, selectedId, FromNative(original), frame, chosenMotion, samplesWindow.Average(), max, _macros.ActiveCount, error, Snapshot.Limitations) { OutputSlots = outputFrames, ControllerMouseModeEnabled = _controllerMouseEnabled, ModActive = profile.ModActive, ActiveDesktopControls = desktop.Keys.Select(NativeProfileRuntime.KeyName).Where(n => n is not null).Select(n => "Keyboard:" + n).Concat(desktop.MouseButtons.Select(n => "Mouse:" + n)).Concat(desktop.MousePulses.Select(n => "Mouse:" + n)).ToHashSet(StringComparer.OrdinalIgnoreCase), ExtendedButtons = selectedExtended.ToHashSet(StringComparer.OrdinalIgnoreCase), LastInputSource = diagnostics?.LastInputSource, LastInputTimestamp = diagnostics?.LastInputTimestamp ?? 0, Limitations = diagnostics is null ? _baseLimitations : _baseLimitations.Concat(diagnostics.Limitations).Distinct().ToArray(), Orientation=_currentOrientation, Rumble=_rumble, Dsu=new((_dsu?.IsRunning??false)&&(_previewDsu?.IsRunning??false),26760,26761,(_dsu?.ClientCount??0)+(_previewDsu?.ClientCount??0),dsuError), ActiveModes=BuildActiveModes() });
                await DelayUntilAsync(began, samples.Count == 0 ? 8 : 1, token).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (Exception ex) { UpdateSnapshot(Snapshot with { Running = false, Health = "Faulted", Error = ex.Message }); throw; }
        finally { if (reloadTask is not null) try { await reloadTask.ConfigureAwait(false); } catch { } _macros.CancelAll(); _activeRouteActions.Clear(); _desktopOutput.ReleaseAll(); await _outputs.NeutralAsync().ConfigureAwait(false); }
    }

    private void StartDsu()
    {
        try
        {
            _dsu = new NativeDsuServer(DsuGyro, DsuAccel, DsuTimestamp);
            _previewDsu = new NativeDsuServer(PreviewGyro, PreviewAccel, DsuTimestamp, 26761);
            _dsu.Start(); _previewDsu.Start();
        }
        catch { _dsu?.Dispose(); _previewDsu?.Dispose(); _dsu = _previewDsu = null; }
    }
    private ulong DsuTimestamp() { lock (_dsuLock) return _dsuTimestampUs; }
    // Same permutations as NativeControllerEngine.GetCitronDsuGyro/Accel (Citron's cemuhook client frame).
    private float[] DsuGyro() { lock (_dsuLock) return [_dsuGyro[0], -_dsuGyro[1], _dsuGyro[2]]; }
    private float[] DsuAccel() { lock (_dsuLock) return [_dsuAccel[0], -_dsuAccel[2], _dsuAccel[1]]; }
    private float[] PreviewGyro() { lock (_dsuLock) return [.. _dsuGyro]; }
    private float[] PreviewAccel() { lock (_dsuLock) return [.. _dsuAccel]; }
    private void OnRumble(byte low, byte high) => _commands.Enqueue(()=>SendRumble(low,high,false,true));
    private void SendRumble(byte heavy,byte light,bool fromTest,bool scale)
    {
        if(scale){var scaled=_profiles.Current.ScaleRumble(heavy,light);heavy=scaled.Large;light=scaled.Small;}
        var id=Snapshot.SelectedSourceId;
        if(id=="all_connected") foreach(var device in ControllerInput.Devices.Where(d=>d.Connected&&!d.IsVirtual)) ControllerInput.SetRumble(device.StableId,heavy,light);
        else if(id is not null) ControllerInput.SetRumble(id,heavy,light);
        _rumble=new(heavy,light,fromTest);
    }
    private void StopRumbleCore(){_testRumbleStop?.Cancel();_testRumbleStop?.Dispose();_testRumbleStop=null;SendRumble(0,0,false,false);}
    private void InvokeActionCore(string action)
    {
        if(action.Equals(RuntimeActions.CancelMacros,StringComparison.OrdinalIgnoreCase)){_macros.CancelAll();_desktopOutput.ReleaseAll();}
        else if(action.Equals(RuntimeActions.DesktopMode,StringComparison.OrdinalIgnoreCase)){_desktopMode=!_desktopMode;if(!_desktopMode)_desktopOutput.ReleaseAll();}
        else if(action.Equals(RuntimeActions.KeyboardControllerMode,StringComparison.OrdinalIgnoreCase)){_keyboardControllerMode=!_keyboardControllerMode;KeyboardCaptureEnabled=_keyboardControllerMode;}
        else if(action.Equals(RuntimeActions.MouseAimMode,StringComparison.OrdinalIgnoreCase)){_mouseAimMode=!_mouseAimMode;MouseCaptureEnabled=_mouseAimMode;}
        else if(action.Equals(RuntimeActions.OpenControlCenter,StringComparison.OrdinalIgnoreCase))ActionRequested?.Invoke(RuntimeActions.OpenControlCenter);
    }
    private IReadOnlySet<string> BuildActiveModes(){var modes=new HashSet<string>(StringComparer.OrdinalIgnoreCase);if(_desktopMode)modes.Add(RuntimeActions.DesktopMode);if(_keyboardControllerMode)modes.Add(RuntimeActions.KeyboardControllerMode);if(_mouseAimMode)modes.Add(RuntimeActions.MouseAimMode);return modes;}
    private ValueTask RebuildOutputsAsync(IEnumerable<int> requestedSlots, CancellationToken token) => _outputs.RebuildAsync(requestedSlots, token);
    private ValueTask DisposeOutputsAsync() => _outputs.DisposeAsync();
    private async Task DelayUntilAsync(long began, int periodMs, CancellationToken token)
    {
        var target = began + Stopwatch.Frequency * periodMs / 1000;
        if (_clock is IMonotonicWaiter waiter) { waiter.WaitUntil(target, token); return; }
        var remaining = target - Stopwatch.GetTimestamp();
        if (remaining > 0) await Task.Delay(Math.Max(1, (int)Math.Ceiling(remaining * 1000d / Stopwatch.Frequency)), token).ConfigureAwait(false);
    }
    private async Task LoadCalibrationsAsync(CancellationToken token)
    {
        var path = Path.Combine(_root, "linux-motion-calibration.json");
        if (!File.Exists(path) || _calibrators.Count > 0) return;
        var states = JsonSerializer.Deserialize<Dictionary<string, NativeMotionCalibration.CalibrationState>>(await File.ReadAllTextAsync(path, token).ConfigureAwait(false));
        if (states is null) return;
        foreach (var pair in states)
        {
            var calibration = new NativeMotionCalibration(_root, fileStorage: false);
            calibration.RestoreState(pair.Value); _calibrators[pair.Key] = calibration;
        }
    }
    private async Task SaveCalibrationsAsync()
    {
        if (_calibrators.Count == 0) return;
        var path = Path.Combine(_root, "linux-motion-calibration.json");
        var temp = path + ".tmp";
        try
        {
            var states = _calibrators.ToDictionary(p => p.Key, p => p.Value.CaptureState());
            await File.WriteAllTextAsync(temp, JsonSerializer.Serialize(states)).ConfigureAwait(false);
            File.Move(temp, path, true);
        }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }
    private void UpdateSnapshot(RuntimeSnapshot value) => Volatile.Write(ref _snapshot, value);
    private static void AddLimitation(object service, List<string> limitations)
    {
        if (service.GetType().GetProperty("Limitation")?.GetValue(service) is string { Length: > 0 } text) limitations.Add(text);
    }
    private static NativeControllerReport ToNative(ControllerFrame f) => new() { Buttons=f.Buttons, LeftTrigger=f.LeftTrigger, RightTrigger=f.RightTrigger, LeftX=f.LeftX, LeftY=f.LeftY, RightX=f.RightX, RightY=f.RightY };
    private static ControllerFrame FromNative(NativeControllerReport f) => new(f.Buttons, f.LeftTrigger, f.RightTrigger, f.LeftX, f.LeftY, f.RightX, f.RightY);

    public async ValueTask DisposeAsync()
    {
        try { await StopAsync().ConfigureAwait(false); }
        finally
        {
            try { await DisposeOutputsAsync().ConfigureAwait(false); }
            finally { await ControllerInput.DisposeAsync(); _desktopInput.Dispose(); _desktopOutput.Dispose(); }
        }
    }
}
