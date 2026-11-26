using System.Diagnostics;

namespace UnifiedController.Core;

public static class VirtualOutputTypes
{
    public static readonly IReadOnlyList<string> Supported = Array.AsReadOnly(new[] { "xbox360", "xboxone", "xboxseries", "dualshock4", "dualsense" });
    public static string Normalize(string value)
    {
        ArgumentNullException.ThrowIfNull(value);
        var compact = value.Trim().ToLowerInvariant().Replace(" ", "").Replace("-", "").Replace("_", "");
        return compact switch {
            "xbox360" or "360" => "xbox360",
            "xboxone" or "one" or "xboxones" => "xboxone",
            "xboxseries" or "series" or "xboxseriesx" or "xboxseriess" => "xboxseries",
            "dualshock4" or "ds4" or "ps4" => "dualshock4",
            "dualsense" or "ds5" or "ps5" => "dualsense",
            _ => throw new ArgumentException("Unsupported virtual output type.", nameof(value))
        };
    }
}

public sealed record VirtualOutputSlotStatus(int Slot, string Type, bool Connected, string State, string? Error);
public sealed record VirtualOutputStatus(string? CurrentType, IReadOnlyList<VirtualOutputSlotStatus> Slots,
    string? LastSwitchError, bool PartialFailure, string GameState, double? GameObservationAgeSeconds,
    IReadOnlyList<string> ActiveGameIds);
public interface IOutputTypeController
{
    VirtualOutputStatus OutputStatus { get; }
    IReadOnlyList<InputDeviceInfo> OwnedDevices => Array.Empty<InputDeviceInfo>();
    ValueTask<bool> SetOutputTypeAsync(string type, bool force = false, CancellationToken token = default);
    void ObserveGames(IReadOnlyList<string> activeGameIds, bool observationKnown = true);
}
public sealed class OutputSwitchException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}

/// <summary>Serializes all output I/O and removes old pads before creating replacements. No physical input is owned here.</summary>
public sealed class VirtualOutputManager : IAsyncDisposable
{
    private readonly Func<string, int, IVirtualControllerOutput> _factory;
    private readonly Action<byte, byte>? _rumble;
    private readonly Func<double> _now;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly Dictionary<int, IVirtualControllerOutput> _outputs = [];
    private readonly Dictionary<int, VirtualOutputSlotStatus> _slots = [];
    private readonly HashSet<int> _uncertain = [];
    private readonly object _observationGate = new();
    private string[] _games = [];
    private double? _observedAt;
    private int[] _wanted = [];
    private string _type;
    private bool _disposed;
    private sealed record Publication(VirtualOutputStatus Status, IReadOnlyDictionary<int, IVirtualControllerOutput> Outputs);
    private Publication _published = new(new(null, [], null, false, "unknown", null, []), new Dictionary<int, IVirtualControllerOutput>());

    public VirtualOutputManager(Func<string, int, IVirtualControllerOutput> factory, string type = "xbox360",
        Action<byte, byte>? rumble = null, Func<double>? now = null)
    {
        _factory = factory; _type = VirtualOutputTypes.Normalize(type); _rumble = rumble;
        _published = new(new(_type, [], null, false, "unknown", null, []), new Dictionary<int, IVirtualControllerOutput>());
        _now = now ?? (() => Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency);
    }
    public VirtualOutputStatus Status
    {
        get
        {
            var publication = Volatile.Read(ref _published);
            var status = publication.Status;
            var slots = status.Slots.Select(s => s.State == "connected" &&
                (!publication.Outputs.TryGetValue(s.Slot, out var output) || !IsConnected(output))
                ? s with { Connected = false, State = "disconnected", Error = s.Error ?? "Output transport disconnected." } : s).ToArray();
            status = status with {
                Slots = Array.AsReadOnly(slots),
                CurrentType = slots.Length == 0 ? status.CurrentType :
                    slots.All(s => s.Connected && s.State == "connected") && slots.Select(s => s.Type).Distinct().Count() == 1 ? slots[0].Type : null,
                PartialFailure = status.PartialFailure || slots.Any(s => !s.Connected || s.State != "connected")
            };
            lock (_observationGate)
            {
                var age = _observedAt is { } stamp ? Math.Max(0, _now() - stamp) : (double?)null;
                return status with { GameState = age is null or >= 15 ? "unknown" : _games.Length == 0 ? "idle" : "active",
                    GameObservationAgeSeconds = age, ActiveGameIds = Array.AsReadOnly((string[])_games.Clone()) };
            }
        }
    }
    public void ObserveGames(IReadOnlyList<string> ids, bool known = true)
    {
        ArgumentNullException.ThrowIfNull(ids);
        if (ids.Count > 256 || ids.Any(id => string.IsNullOrWhiteSpace(id) || id.Length > 512 || id.Any(char.IsControl)) || (!known && ids.Count != 0))
            throw new ArgumentException("Invalid tracked game observation.");
        lock (_observationGate) { _games = ids.Distinct(StringComparer.Ordinal).ToArray(); _observedAt = known ? _now() : null; }
    }
    private void Publish(string? error = null)
    {
        var slots = _slots.Values.OrderBy(s => s.Slot).ToArray();
        var live = slots.Where(s => s.Connected && s.State == "connected").ToArray();
        var current = _wanted.Length == 0 && _uncertain.Count == 0 ? _type :
            live.Length == _wanted.Length && live.Length > 0 && live.Select(s => s.Type).Distinct().Count() == 1 ? live[0].Type : null;
        Volatile.Write(ref _published, new(new(current, Array.AsReadOnly(slots), error,
            _uncertain.Count != 0 || _wanted.Any(slot => !_slots.TryGetValue(slot, out var s) || !s.Connected || s.State != "connected"),
            "unknown", null, []), new Dictionary<int, IVirtualControllerOutput>(_outputs)));
    }
    public async ValueTask RebuildAsync(IEnumerable<int> requestedSlots, CancellationToken token)
    {
        var wanted = requestedSlots.Distinct().Order().ToArray();
        if (wanted.Any(slot => slot is < 1 or > 4)) throw new ArgumentOutOfRangeException(nameof(requestedSlots));
        await _gate.WaitAsync(token).ConfigureAwait(false);
        try
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            if (wanted.SequenceEqual(_wanted) && _uncertain.Count == 0 &&
                wanted.All(slot => _outputs.TryGetValue(slot, out var output) && IsConnected(output) &&
                    _slots.TryGetValue(slot, out var state) && state.Connected)) return;
            var failures = new List<string>();
            foreach (var slot in _outputs.Keys.Except(wanted).ToArray())
            {
                try { await RemoveAsync(slot).ConfigureAwait(false); }
                catch (OperationCanceledException) when (token.IsCancellationRequested) { throw; }
                catch (Exception ex) { failures.Add($"Output slot {slot} removal failed: {ex.Message}"); }
            }
            foreach (var slot in _slots.Keys.Except(wanted).ToArray())
                if (!_outputs.ContainsKey(slot) && !_uncertain.Contains(slot)) _slots.Remove(slot);
            _wanted = wanted;
            foreach (var slot in wanted)
            {
                try
                {
                    if (_outputs.TryGetValue(slot, out var output) &&
                        (_uncertain.Contains(slot) || !IsConnected(output) || !_slots.TryGetValue(slot,out var state) || !state.Connected))
                        await RemoveAsync(slot).ConfigureAwait(false);
                    if (!_outputs.ContainsKey(slot)) await CreateAsync(slot, _type, token).ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (token.IsCancellationRequested) { throw; }
                catch (Exception ex) { failures.Add($"Output slot {slot} rebuild failed: {ex.Message}"); }
            }
            // A single dead physical/virtual slot must not prevent healthy slots
            // from being created. Keep the failure in status; the runtime retries
            // unhealthy slots with a bounded backoff.
            Publish(failures.Count == 0 ? null : string.Join("; ", failures));
        }
        catch (Exception ex) { Publish(ex.Message); throw; }
        finally { _gate.Release(); }
    }
    private async ValueTask CreateAsync(int slot, string type, CancellationToken token)
    {
        IVirtualControllerOutput? output = null;
        try
        {
            output = _factory(type, slot);
            _outputs.Add(slot, output);
            if (_rumble is not null) output.Rumble += _rumble;
            await output.StartAsync(token).ConfigureAwait(false);
            await output.SubmitNeutralAsync(token).ConfigureAwait(false);
            _slots[slot] = new(slot, type, true, "connected", null);
        }
        catch (Exception ex)
        {
            _slots[slot] = new(slot, type, false, "disconnected", ex.Message);
            if (output is not null)
            {
                try { await RemoveAsync(slot).ConfigureAwait(false); }
                catch (Exception removal) { throw new AggregateException(ex, removal); }
                _slots[slot] = new(slot, type, false, "disconnected", ex.Message);
            }
            throw;
        }
    }
    private async ValueTask RemoveAsync(int slot)
    {
        if (!_outputs.TryGetValue(slot, out var output)) return;
        if (_rumble is not null) output.Rumble -= _rumble;
        try { await output.SubmitNeutralAsync(CancellationToken.None).ConfigureAwait(false); } catch { /* confirmed destruction below releases held input */ }
        try { await output.DisposeAsync().ConfigureAwait(false); }
        catch (Exception ex)
        {
            _uncertain.Add(slot);
            var old = _slots.GetValueOrDefault(slot) ?? new(slot, _type, false, "uncertain", null);
            _slots[slot] = old with { Connected = false, State = "uncertain", Error = ex.Message };
            throw;
        }
        _outputs.Remove(slot); _uncertain.Remove(slot);
        if (_slots.TryGetValue(slot, out var previous)) _slots[slot] = previous with { Connected = false, State = "disconnected" };
    }
    public async ValueTask<bool> SwitchAsync(string value, bool force, CancellationToken token)
    {
        var type = VirtualOutputTypes.Normalize(value);
        await _gate.WaitAsync(token).ConfigureAwait(false);
        try
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            // Same-type requests do not recreate hardware or require an idle game observation.
            if (type == _type && _uncertain.Count == 0 && _wanted.All(s => _slots.TryGetValue(s, out var state) && state.Connected && _outputs.TryGetValue(s,out var output) && IsConnected(output))) return false;
            if (!force)
            {
                var state = Status.GameState;
                if (state == "unknown") throw new OutputSwitchException("ERR_GAME_STATE_UNKNOWN", "Tracked game observation is unknown or stale.");
                if (state == "active") throw new OutputSwitchException("ERR_GAME_ACTIVE", "A tracked game is launching or running.");
            }
            var oldType = _type;
            var removalErrors = new List<Exception>();
            foreach (var slot in _outputs.Keys.ToArray())
                try { await RemoveAsync(slot).ConfigureAwait(false); } catch (Exception ex) { removalErrors.Add(ex); }
            if (removalErrors.Count > 0)
            {
                Publish("Old output removal failed; replacement was not created: " + string.Join("; ", removalErrors.Select(e => e.Message)));
                throw new OutputSwitchException("ERR_OUTPUT_SWITCH_FAILED", _published.Status.LastSwitchError!);
            }
            try
            {
                foreach (var slot in _wanted) await CreateAsync(slot, type, token).ConfigureAwait(false);
                _type = type; Publish(); return true;
            }
            catch (Exception creation)
            {
                var errors = new List<string> { "New output failed: " + creation.Message };
                foreach (var slot in _outputs.Keys.ToArray())
                    try { await RemoveAsync(slot).ConfigureAwait(false); } catch (Exception ex) { errors.Add("Candidate removal failed: " + ex.Message); }
                // Never restore while any failed candidate may still exist.
                if (_outputs.Count == 0)
                {
                    foreach (var slot in _wanted)
                        try { await CreateAsync(slot, oldType, CancellationToken.None).ConfigureAwait(false); }
                        catch (Exception ex) { errors.Add("Rollback failed: " + ex.Message); if (_uncertain.Count > 0) break; }
                }
                Publish(string.Join("; ", errors));
                throw new OutputSwitchException("ERR_OUTPUT_SWITCH_FAILED", _published.Status.LastSwitchError!);
            }
        }
        catch (OutputSwitchException ex) when (ex.Code is "ERR_GAME_ACTIVE" or "ERR_GAME_STATE_UNKNOWN")
        { Publish(ex.Message); throw; }
        finally { _gate.Release(); }
    }
    public async ValueTask<IReadOnlyDictionary<int, ControllerFrame>> SubmitAsync(
        IReadOnlyDictionary<int, ControllerFrame> frames, IReadOnlyDictionary<int, InputSample> samples, CancellationToken token)
    {
        await _gate.WaitAsync(token).ConfigureAwait(false);
        try
        {
            var sent = new Dictionary<int, ControllerFrame>();
            var failures = new List<string>();
            foreach (var (slot, output) in _outputs)
            {
                if (_uncertain.Contains(slot)) continue;
                if (_slots.TryGetValue(slot, out var known) && (!known.Connected || known.State != "connected")) continue;
                var frame = frames.GetValueOrDefault(slot);
                try
                {
                    if (!IsConnected(output)) throw new IOException("Output transport disconnected.");
                    if (output is IMotionVirtualControllerOutput motionOutput)
                    {
                        samples.TryGetValue(slot, out var sample);
                        await motionOutput.SubmitSampleAsync(frame, sample?.Motion, sample?.ExtendedButtons, token).ConfigureAwait(false);
                    }
                    else await output.SubmitAsync(frame, token).ConfigureAwait(false);
                    sent[slot] = frame;
                }
                catch (OperationCanceledException) when (token.IsCancellationRequested) { throw; }
                catch (Exception ex)
                {
                    if (_slots.TryGetValue(slot,out var state)) _slots[slot] = state with { Connected=false,State="disconnected",Error=ex.Message };
                    failures.Add($"Output slot {slot} failed: {ex.Message}");
                }
            }
            if (failures.Count > 0) Publish(string.Join("; ", failures));
            return sent;
        }
        finally { _gate.Release(); }
    }
    private static bool IsConnected(IVirtualControllerOutput output)
    {
        try { return output.Connected; } catch { return false; }
    }
    public async ValueTask NeutralAsync()
    {
        await _gate.WaitAsync().ConfigureAwait(false);
        try { foreach (var output in _outputs.Values) try { await output.SubmitNeutralAsync(CancellationToken.None).ConfigureAwait(false); } catch { } }
        finally { _gate.Release(); }
    }
    public async ValueTask DisposeAsync()
    {
        await _gate.WaitAsync().ConfigureAwait(false);
        try
        {
            if (_disposed && _outputs.Count == 0) return;
            _disposed = true;
            var errors = new List<Exception>();
            foreach (var slot in _outputs.Keys.ToArray())
                try { await RemoveAsync(slot).ConfigureAwait(false); } catch (Exception ex) { errors.Add(ex); }
            Publish(errors.Count == 0 ? _published.Status.LastSwitchError : string.Join("; ", errors.Select(e => e.Message)));
            if (errors.Count > 0) throw new AggregateException(errors);
        }
        finally { _gate.Release(); }
    }
}
