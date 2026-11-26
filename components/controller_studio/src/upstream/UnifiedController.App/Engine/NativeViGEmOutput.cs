using System.Diagnostics;
using System.IO;
using Nefarius.ViGEm.Client;
using Nefarius.ViGEm.Client.Targets;
using Nefarius.ViGEm.Client.Targets.Xbox360;

namespace UnifiedController.App.Engine;

public sealed record NativeVirtualOutputInfo(int LogicalSlot, int? XInputIndex, bool Connected, bool Verified, string Health, int RecoveryCount);

/// <summary>Owns up to four independent Xbox 360 virtual outputs.</summary>
public sealed class NativeViGEmOutput : IDisposable
{
    private const ushort VirtualVid = 0x1209;
    private const ushort VirtualPid = 0xC710;
    private readonly object _gate = new();
    private readonly object _connectionGate = new();
    private readonly NativeXInputReader _outputReader = new();
    private readonly Func<int, NativeControllerReport?> _readOutput;
    private readonly Func<int, IXbox360Controller>? _targetFactory;
    private int[] _requestedSlots = [1];
    private Task? _recoveryTask;
    private long _nextRecoveryAt;
    private bool _disposed;
    private int _recoveryCount;

    public NativeViGEmOutput(Func<int, NativeControllerReport?>? readOutput = null,
        Func<int, IXbox360Controller>? targetFactory = null)
    {
        _readOutput = readOutput ?? ReadOutput;
        _targetFactory = targetFactory;
    }

    private NativeControllerReport? ReadOutput(int index)
    {
        if (!_outputReader.Poll((uint)index, out var state)) return null;
        return new NativeControllerReport {
            Buttons = state.Gamepad.Buttons,
            LeftTrigger = state.Gamepad.LeftTrigger, RightTrigger = state.Gamepad.RightTrigger,
            LeftX = state.Gamepad.ThumbLX, LeftY = state.Gamepad.ThumbLY,
            RightX = state.Gamepad.ThumbRX, RightY = state.Gamepad.ThumbRY
        };
    }
    private readonly Dictionary<int, OutputTarget> _targets = [];
    private ViGEmClient? _client;
    private static readonly string ErrorLogPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "UnifiedController",
        "vigem.log");

    private sealed class OutputTarget
    {
        public required IXbox360Controller Controller;
        public bool Connected;
        public NativeControllerReport LatestReport;
        public int? CachedIndex;
        public long IndexCheckedAt;
        public readonly NativeOutputWatchdog Watchdog = new();
    }

    public event Action<int, byte, byte>? RumbleReceived;
    public string LastError { get; private set; } = string.Empty;
    public bool IsConnected { get { lock (_gate) return _targets.TryGetValue(1, out var target) && target.Connected; } }
    public int? UserIndex => UserIndexForSlot(1);
    public bool ReportsVerified { get { lock (_gate) return _requestedSlots.All(slot => _targets.TryGetValue(slot, out var t) && t.Connected && t.Watchdog.Verified); } }
    public string Health
    {
        get
        {
            lock (_gate)
            {
                foreach (var slot in _requestedSlots)
                    if (!_targets.TryGetValue(slot, out var target) || !target.Connected) return "output_unavailable";
                    else if (!target.Watchdog.Verified) return target.Watchdog.Health;
                return "healthy";
            }
        }
    }
    public int RecoveryCount { get { lock (_gate) return _recoveryCount; } }
    public IReadOnlyList<int> EnabledSlots { get { lock (_gate) return _targets.Keys.OrderBy(value => value).ToArray(); } }
    public IReadOnlySet<int> UserIndices
    {
        get
        {
            lock (_gate)
                return _targets.Values.Select(TryGetIndex).Where(value => value.HasValue)
                    .Select(value => value!.Value).ToHashSet();
        }
    }
    public IReadOnlyList<NativeVirtualOutputInfo> Outputs
    {
        get
        {
            lock (_gate)
                return _targets.OrderBy(pair => pair.Key)
                    .Select(pair => new NativeVirtualOutputInfo(pair.Key, TryGetIndex(pair.Value), pair.Value.Connected, pair.Value.Watchdog.Verified, pair.Value.Watchdog.Health, _recoveryCount)).ToArray();
        }
    }

    public bool Initialize(IEnumerable<int>? enabledSlots = null)
    {
        lock (_connectionGate)
        {
            lock (_gate) if (_disposed) return false;
            try
            {
                // A dead driver/client handle must not be reused forever.
                bool hasConnectedTarget;
                lock (_gate) hasConnectedTarget = _targets.Values.Any(t => t.Connected);
                if (_client != null && !hasConnectedTarget)
                {
                    OutputTarget[] old;
                    lock (_gate) { old = _targets.Values.ToArray(); _targets.Clear(); }
                    foreach (var target in old) Disconnect(target);
                    _client.Dispose();
                    _client = null;
                }
                if (_targetFactory == null) _client ??= new ViGEmClient();
                ConfigureSlots(enabledSlots ?? [1]);
                LastError = string.Empty;
                return IsConnected;
            }
            catch (Exception ex)
            {
                LastError = ex.ToString();
                Log($"Initialization failed: {ex}");
                return false;
            }
        }
    }

    public void ConfigureSlots(IEnumerable<int> requestedSlots)
    {
        var desired = requestedSlots.Where(slot => slot is >= 1 and <= 4).Append(1).Distinct().Order().ToArray();
        lock (_connectionGate)
        {
            lock (_gate)
            {
                if (_disposed) return;
                _requestedSlots = desired;
            }
            if (_client == null && _targetFactory == null) return;
            var removed = new List<OutputTarget>();
            lock (_gate)
                foreach (var slot in _targets.Keys.Where(slot => !desired.Contains(slot) || !_targets[slot].Connected).ToArray())
                {
                    removed.Add(_targets[slot]);
                    _targets.Remove(slot);
                }
            foreach (var target in removed) Disconnect(target);
            foreach (var slot in desired)
            {
                lock (_gate) if (_targets.ContainsKey(slot)) continue;
                var target = CreateTarget(slot);
                lock (_gate) _targets[slot] = target;
            }
        }
    }

    private OutputTarget CreateTarget(int slot)
    {
        Exception? failure = null;
        for (var connectionAttempt = 0; connectionAttempt < 2; connectionAttempt++)
        {
            var controller = _targetFactory?.Invoke(slot) ?? _client!.CreateXbox360Controller(VirtualVid, VirtualPid);
            controller.AutoSubmitReport = false;
            var target = new OutputTarget { Controller = controller };
            controller.FeedbackReceived += (_, args) => RumbleReceived?.Invoke(slot, args.LargeMotor, args.SmallMotor);
            try
            {
                try { controller.Connect(); }
                catch (Exception ex)
                {
                    // PnP readiness may lag Connect; retain only a usable target.
                    target.Connected = true;
                    for (var attempt = 0; attempt < 20 && TryGetIndex(target) == null; attempt++) Thread.Sleep(50);
                    if (TryGetIndex(target) == null) throw;
                    Log($"Logical output {slot} recovered after Connect reported: {ex.Message}");
                }
                target.Connected = true;
                InitializeNeutralReport(controller);
                target.Watchdog.Record(new NativeControllerReport(), Environment.TickCount64);
                return target;
            }
            catch (Exception ex)
            {
                failure = ex;
                // Disconnect even when our Connected flag was never set. The
                // library may have connected the base target before throwing.
                Disconnect(target);
                Log($"Logical output {slot} attempt {connectionAttempt + 1} failed: {ex.Message}");
            }
        }
        throw new InvalidOperationException($"ViGEm logical output {slot} did not become ready after two connection attempts.", failure);
    }

    // Called at 10 Hz; compare against recently submitted output rather than
    // physical input, because routing and stick processing intentionally differ.
    public void MaintainOutputs()
    {
        lock (_gate)
        {
            if (_disposed) return;
            var now = Environment.TickCount64;
            foreach (var (slot, target) in _targets)
            {
                if (!target.Connected) continue;
                var index = TryGetIndex(target);
                var actual = index.HasValue ? _readOutput(index.Value) : null;
                if (!actual.HasValue) target.IndexCheckedAt = 0;
                var recovery = target.Watchdog.Observe(actual, now);
                if (recovery == NativeOutputRecovery.Reassert)
                {
                    try
                    {
                        WriteReport(target.Controller, NativeOutputWatchdog.RefreshReport(target.LatestReport));
                        WriteReport(target.Controller, target.LatestReport);
                        _recoveryCount++;
                        Log($"Reasserted logical output {slot} after XInput report mismatch.");
                    }
                    catch (Exception ex) { MarkFailed(slot, target, ex); }
                }
                else if (recovery == NativeOutputRecovery.Reconnect)
                    MarkFailed(slot, target, new IOException("Virtual XInput reports did not recover."));
            }
            if (_requestedSlots.Any(slot => !_targets.TryGetValue(slot, out var t) || !t.Connected) &&
                now >= _nextRecoveryAt && (_recoveryTask == null || _recoveryTask.IsCompleted))
            {
                _nextRecoveryAt = now + 5000;
                var requested = _requestedSlots.ToArray();
                _recoveryTask = Task.Run(() =>
                {
                    if (Initialize(requested))
                    {
                        lock (_gate) _recoveryCount++;
                        Log("Virtual output connection recovered.");
                    }
                });
            }
        }
    }

    private void MarkFailed(int slot, OutputTarget target, Exception ex)
    {
        target.Connected = false;
        LastError = $"Logical output {slot}: {ex.Message}";
        Log(LastError);
    }

    public int? UserIndexForSlot(int logicalSlot)
    {
        lock (_gate) return _targets.TryGetValue(logicalSlot, out var target) ? TryGetIndex(target) : null;
    }

    private static void InitializeNeutralReport(IXbox360Controller controller)
    {
        // ViGEm suppresses a first all-zero report because its report cache is
        // already zero. XInput can still expose a previous incarnation's state
        // until a changed report arrives. Prime with one axis unit (below any
        // meaningful stick movement), then explicitly publish neutral.
        controller.ResetReport();
        controller.SetAxisValue(Xbox360Axis.LeftThumbX, 1);
        controller.SubmitReport();
        controller.ResetReport();
        controller.SubmitReport();
    }

    private static int? TryGetIndex(OutputTarget target)
    {
        if (!target.Connected) return null;
        var now = Environment.TickCount64;
        if (target.IndexCheckedAt != 0 && now - target.IndexCheckedAt < 250) return target.CachedIndex;
        target.IndexCheckedAt = now;
        try
        {
            var index = target.Controller.UserIndex;
            return target.CachedIndex = index is >= 0 and < 4 ? index : null;
        }
        catch { return target.CachedIndex = null; }
    }

    public void SubmitReport(int logicalSlot, NativeControllerReport report)
    {
        lock (_gate)
        {
            if (!_targets.TryGetValue(logicalSlot, out var target) || !target.Connected) return;
            try
            {
                WriteReport(target.Controller, report);
                target.LatestReport = report;
                target.Watchdog.Record(report, Environment.TickCount64);
            }
            catch (Exception ex) { MarkFailed(logicalSlot, target, ex); }
        }
    }

    private static void WriteReport(IXbox360Controller controller, NativeControllerReport report)
    {
        var buttons = report.Buttons;
        controller.SetButtonState(Xbox360Button.Up, (buttons & 0x0001) != 0);
        controller.SetButtonState(Xbox360Button.Down, (buttons & 0x0002) != 0);
        controller.SetButtonState(Xbox360Button.Left, (buttons & 0x0004) != 0);
        controller.SetButtonState(Xbox360Button.Right, (buttons & 0x0008) != 0);
        controller.SetButtonState(Xbox360Button.Start, (buttons & 0x0010) != 0);
        controller.SetButtonState(Xbox360Button.Back, (buttons & 0x0020) != 0);
        controller.SetButtonState(Xbox360Button.LeftThumb, (buttons & 0x0040) != 0);
        controller.SetButtonState(Xbox360Button.RightThumb, (buttons & 0x0080) != 0);
        controller.SetButtonState(Xbox360Button.LeftShoulder, (buttons & 0x0100) != 0);
        controller.SetButtonState(Xbox360Button.RightShoulder, (buttons & 0x0200) != 0);
        controller.SetButtonState(Xbox360Button.Guide, (buttons & 0x0400) != 0);
        controller.SetButtonState(Xbox360Button.A, (buttons & 0x1000) != 0);
        controller.SetButtonState(Xbox360Button.B, (buttons & 0x2000) != 0);
        controller.SetButtonState(Xbox360Button.X, (buttons & 0x4000) != 0);
        controller.SetButtonState(Xbox360Button.Y, (buttons & 0x8000) != 0);
        controller.SetSliderValue(Xbox360Slider.LeftTrigger, report.LeftTrigger);
        controller.SetSliderValue(Xbox360Slider.RightTrigger, report.RightTrigger);
        controller.SetAxisValue(Xbox360Axis.LeftThumbX, report.LeftX);
        controller.SetAxisValue(Xbox360Axis.LeftThumbY, report.LeftY);
        controller.SetAxisValue(Xbox360Axis.RightThumbX, report.RightX);
        controller.SetAxisValue(Xbox360Axis.RightThumbY, report.RightY);
        controller.SubmitReport();
    }

    public void SubmitReport(int buttons, byte leftTrigger, byte rightTrigger, short leftX, short leftY, short rightX, short rightY) =>
        SubmitReport(1, new NativeControllerReport
        {
            Buttons = buttons, LeftTrigger = leftTrigger, RightTrigger = rightTrigger,
            LeftX = leftX, LeftY = leftY, RightX = rightX, RightY = rightY
        });

    private static void Disconnect(OutputTarget target)
    {
        try { InitializeNeutralReport(target.Controller); } catch { }
        try { target.Controller.Disconnect(); } catch { }
        target.Connected = false;
        try { if (target.Controller is IDisposable disposable) disposable.Dispose(); }
        catch (Exception ex) { Log($"Virtual target disposal failed: {ex.Message}"); }
    }

    private static void Log(string message)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(ErrorLogPath)!);
            File.AppendAllText(ErrorLogPath, $"{DateTimeOffset.Now:O} {message}{Environment.NewLine}");
        }
        catch { }
    }

    public void Dispose()
    {
        lock (_connectionGate)
        {
            OutputTarget[] old;
            lock (_gate)
            {
                if (_disposed) return;
                _disposed = true;
                old = _targets.Values.ToArray();
                _targets.Clear();
            }
            foreach (var target in old) Disconnect(target);
            _client?.Dispose();
            _client = null;
        }
    }
}
