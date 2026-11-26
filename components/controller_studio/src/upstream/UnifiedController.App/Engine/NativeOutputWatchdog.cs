namespace UnifiedController.App.Engine;

public enum NativeOutputRecovery { None, Reassert, Reconnect }

/// <summary>Allows normal delivery latency, but detects persistent stale output.</summary>
public sealed class NativeOutputWatchdog
{
    private readonly Queue<(long At, NativeControllerReport Report)> _recent = new();
    private NativeControllerReport? _lastRecorded;
    private long? _mismatchSince;
    private long? _reassertedAt;
    public bool Verified { get; private set; }
    public string Health { get; private set; } = "output_verifying";

    public void Record(NativeControllerReport report, long now)
    {
        if (_recent.Count == 0 || !Matches(_lastRecorded!.Value, report))
            _recent.Enqueue((now, report));
        _lastRecorded = report;
        while (_recent.Count > 1 && (_recent.Peek().At < now - 250 || _recent.Count > 512))
            _recent.Dequeue();
    }

    public NativeOutputRecovery Observe(NativeControllerReport? actual, long now)
    {
        // Retain the current report even if the source has been idle for hours.
        while (_recent.Count > 1 && _recent.Peek().At < now - 250) _recent.Dequeue();
        if (actual.HasValue && _recent.Any(value => Matches(value.Report, actual.Value)))
        {
            Verified = true;
            Health = "healthy";
            _mismatchSince = _reassertedAt = null;
            return NativeOutputRecovery.None;
        }
        Verified = false;
        Health = actual.HasValue ? "output_mismatch" : "output_unavailable";
        _mismatchSince ??= now;
        if (_reassertedAt.HasValue)
            return now - _reassertedAt.Value >= 500 ? NativeOutputRecovery.Reconnect : NativeOutputRecovery.None;
        if (now - _mismatchSince.Value < 300) return NativeOutputRecovery.None;
        _reassertedAt = now;
        return actual.HasValue ? NativeOutputRecovery.Reassert : NativeOutputRecovery.Reconnect;
    }

    public static bool Matches(NativeControllerReport a, NativeControllerReport b) =>
        (a.Buttons & 0xffff) == (b.Buttons & 0xffff) &&
        a.LeftTrigger == b.LeftTrigger && a.RightTrigger == b.RightTrigger &&
        a.LeftX == b.LeftX && a.LeftY == b.LeftY && a.RightX == b.RightX && a.RightY == b.RightY;

    public static NativeControllerReport RefreshReport(NativeControllerReport report)
    {
        // Change one axis unit to bypass ViGEm's duplicate-report cache without
        // releasing buttons or clearing an active user's intended state.
        report.LeftX = (short)(report.LeftX ^ 1);
        return report;
    }
}
