using System.Buffers.Binary;
using System.Diagnostics;
using System.Text;
using UnifiedController.Core;

namespace UnifiedController.Linux.Platform.Output;

public sealed class UhidDualShock4Output(int slot = 1, IUhidWriter? writer = null)
    : UhidSonyOutput(SonyPadType.DualShock4, slot, writer);
public sealed class UhidDualSenseOutput(int slot = 1, IUhidWriter? writer = null)
    : UhidSonyOutput(SonyPadType.DualSense, slot, writer);

/// <summary>USB Sony virtual controller. Construction is inert; production /dev/uhid opens only in StartAsync.
/// Kernel feature requests are serviced without waiting for probe completion. No physical trigger actuator is exposed.</summary>
public class UhidSonyOutput : IMotionVirtualControllerOutput
{
    // linux/include/uapi/linux/uhid.h: packed u32 type followed by largest union member (CREATE2).
    public const int MaximumEventSize = 4376;
    private readonly SonyPadType _type;
    private readonly int _slot;
    private readonly IUhidWriter _writer;
    private readonly object _gate = new();
    private readonly byte[] _identity;
    private bool _created, _connected, _disposed, _stopping;
    private byte _sequence;
    private long _startedAt;
    private CancellationTokenSource? _readerStop;
    private Thread? _reader;
    public event Action<byte, byte>? Rumble;
    public event Action<SonyOutputReport>? OutputReportReceived;
    public Exception? LastReaderError { get; private set; }
    public bool Connected { get { lock (_gate) return _connected; } }

    protected UhidSonyOutput(SonyPadType type, int slot, IUhidWriter? writer)
    {
        if (slot is < 1 or > 4) throw new ArgumentOutOfRangeException(nameof(slot));
        _ = SonyHidDescriptors.Get(type);
        _type = type; _slot = slot; _writer = writer ?? new LinuxUhidWriter();
        _identity = Guid.NewGuid().ToByteArray()[..6];
        _identity[0] = (byte)((_identity[0] & 0xfc) | 2); // locally administered, unicast; never copy a physical MAC
    }

    public ValueTask StartAsync(CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        lock (_gate)
        {
            ObjectDisposedException.ThrowIf(_disposed || _stopping, this);
            if (_created) return ValueTask.CompletedTask;
            try
            {
                _writer.Open();
                var packet = Event(11); // UHID_CREATE2
                var label = _type == SonyPadType.DualSense ? "DualSense" : "DualShock 4";
                Text(packet.AsSpan(4, 128), $"Unified Controller {label} Slot {_slot}");
                Text(packet.AsSpan(132, 64), $"waystone/sony/{_slot}");
                Text(packet.AsSpan(196, 64), string.Join(":", _identity.Reverse().Select(b => b.ToString("x2"))));
                var descriptor = SonyHidDescriptors.Get(_type);
                BinaryPrimitives.WriteUInt16LittleEndian(packet.AsSpan(260), (ushort)descriptor.Length);
                BinaryPrimitives.WriteUInt16LittleEndian(packet.AsSpan(262), 3); // BUS_USB, not BUS_VIRTUAL
                BinaryPrimitives.WriteUInt32LittleEndian(packet.AsSpan(264), 0x054c);
                BinaryPrimitives.WriteUInt32LittleEndian(packet.AsSpan(268), _type == SonyPadType.DualSense ? 0x0ce6u : 0x05c4u);
                BinaryPrimitives.WriteUInt32LittleEndian(packet.AsSpan(272), 0x0100);
                descriptor.CopyTo(packet.AsSpan(280));
                Write(packet);
                _created = _connected = true; _startedAt = Stopwatch.GetTimestamp();
                _readerStop = new CancellationTokenSource();
                var readerToken = _readerStop.Token;
                _reader = new Thread(() => ReadEvents(readerToken)) { IsBackground = true, Name = $"SonyUHID-{_slot}" };
                _reader.Start();
            }
            catch (Exception startupError)
            {
                if (_created) { try { Write(Event(1)); } catch { } }
                _connected = false; _stopping = true;
                _readerStop?.Cancel(); _readerStop?.Dispose(); _readerStop = null;
                // Mark disposed only after close succeeds: the manager must be able to retry a failed close.
                try { _writer.Dispose(); }
                catch (Exception closeError)
                {
                    throw new AggregateException("UHID startup failed and its transport could not be closed.", startupError, closeError);
                }
                _created = false; _disposed = true;
                throw;
            }
        }
        return ValueTask.CompletedTask;
    }

    public ValueTask SubmitAsync(ControllerFrame frame, CancellationToken token) => SubmitSampleAsync(frame, null, null, token);
    public ValueTask SubmitSampleAsync(ControllerFrame frame, MotionFrame? motion,
        IReadOnlySet<string>? extendedButtons, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        lock (_gate)
        {
            ObjectDisposedException.ThrowIf(_disposed || _stopping, this);
            if (!_connected) throw new InvalidOperationException("Output is not started or its UHID transport failed.");
            SendInput(frame, motion, extendedButtons);
        }
        return ValueTask.CompletedTask;
    }
    public ValueTask SubmitNeutralAsync(CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        lock (_gate) { if (_connected && !_stopping) SendInput(default, null, null); }
        return ValueTask.CompletedTask;
    }

    /// <summary>Process one complete packed UHID event; also a deterministic fake transport test seam.
    /// Malformed/unknown events return false. Only feature/output report requests are accepted.</summary>
    public bool ProcessKernelEvent(ReadOnlySpan<byte> packet)
    {
        if (packet.Length < 4 || packet.Length > MaximumEventSize) return false;
        SonyOutputReport? output = null;
        lock (_gate)
        {
            if (!_created || _disposed || _stopping) return false;
            var kind = BinaryPrimitives.ReadUInt32LittleEndian(packet);
            switch (kind)
            {
                case 2: _connected = true; return true; // UHID_START
                case 3: _connected = false; return true; // UHID_STOP
                case 4: case 5: return true; // UHID_OPEN/CLOSE (no readers isn't a disconnect)
                case 6: // UHID_OUTPUT: data[4096], u16 size, u8 rtype
                    if (packet.Length < 4103 || packet[4102] != 1) return false;
                    var size = BinaryPrimitives.ReadUInt16LittleEndian(packet[4100..]);
                    if (size > 4096 || !SonyHidReports.TryParseOutput(_type, packet.Slice(4, size), out output)) return false;
                    break;
                case 9: // UHID_GET_REPORT: u32 id, u8 rnum, u8 rtype
                    if (packet.Length < 10) return false;
                    var data = packet[9] == 0 ? SonyHidReports.Feature(_type, packet[8], _identity) : null;
                    var reply = Event(10);
                    packet.Slice(4, 4).CopyTo(reply.AsSpan(4));
                    BinaryPrimitives.WriteUInt16LittleEndian(reply.AsSpan(8), data is null ? (ushort)95 : (ushort)0); // EOPNOTSUPP
                    BinaryPrimitives.WriteUInt16LittleEndian(reply.AsSpan(10), (ushort)(data?.Length ?? 0));
                    data?.CopyTo(reply, 12);
                    Write(reply); return true;
                case 13: // UHID_SET_REPORT: u32 id,u8 rnum,u8 rtype,u16 size,data[]
                    if (packet.Length < 12) return false;
                    var length = BinaryPrimitives.ReadUInt16LittleEndian(packet[10..]);
                    var accepted = length <= 4096 && packet.Length >= 12 + length && packet[9] == 1 &&
                        length > 0 && packet[8] == packet[12] &&
                        SonyHidReports.TryParseOutput(_type, packet.Slice(12, length), out output);
                    var setReply = Event(14);
                    packet.Slice(4, 4).CopyTo(setReply.AsSpan(4));
                    BinaryPrimitives.WriteUInt16LittleEndian(setReply.AsSpan(8), accepted ? (ushort)0 : (ushort)22);
                    Write(setReply);
                    if (!accepted) return false;
                    break;
                default: return false;
            }
        }
        if (output is not null)
        {
            if (output.Heavy is byte heavy && output.Light is byte light) Rumble?.Invoke(heavy, light);
            OutputReportReceived?.Invoke(output);
        }
        return true;
    }

    public ValueTask DisposeAsync()
    {
        CancellationTokenSource? stop; Thread? reader;
        lock (_gate)
        {
            if (_disposed) return ValueTask.CompletedTask;
            _stopping = true; stop = _readerStop; reader = _reader; stop?.Cancel();
        }
        if (reader is not null && reader != Thread.CurrentThread) reader.Join(); // Read is required to be nonblocking.
        lock (_gate)
        {
            if (_disposed) return ValueTask.CompletedTask;
            if (_created)
            {
                if (_connected) { try { SendInput(default, null, null); } catch { } }
                try { Write(Event(1)); } catch { } // UHID_DESTROY; close is the final guaranteed cleanup.
            }
            _connected = false;
            _writer.Dispose(); // May throw; retain _created and retryable disposal until close is confirmed.
            _created = false; _disposed = true;
            stop?.Dispose(); _readerStop = null; _reader = null;
        }
        return ValueTask.CompletedTask;
    }

    private void SendInput(ControllerFrame frame, MotionFrame? motion, IReadOnlySet<string>? extendedButtons)
    {
        var nowUs = (ulong)(Stopwatch.GetElapsedTime(_startedAt).Ticks / 10);
        var report = SonyHidReports.EncodeInput(_type, frame, motion, extendedButtons, _sequence++, nowUs);
        var packet = Event(12); // UHID_INPUT2: u16 size, u8 data[4096]
        BinaryPrimitives.WriteUInt16LittleEndian(packet.AsSpan(4), (ushort)report.Length);
        report.CopyTo(packet, 6); Write(packet);
    }
    private void ReadEvents(CancellationToken token)
    {
        var packet = new byte[MaximumEventSize];
        try
        {
            while (!token.IsCancellationRequested)
            {
                if (!_writer.WaitReadable(100)) continue;
                var count = _writer.Read(packet);
                if (count < 0 || count > packet.Length) throw new IOException("Invalid UHID event length.");
                if (count > 0) ProcessKernelEvent(packet.AsSpan(0, count));
                else token.WaitHandle.WaitOne(2);
            }
        }
        catch (Exception error)
        {
            if (!token.IsCancellationRequested)
                lock (_gate) { LastReaderError = error; _connected = false; }
        }
    }
    private void Write(ReadOnlySpan<byte> packet)
    {
        try
        {
            var count = _writer.Write(packet);
            if (count != packet.Length) throw new IOException($"UHID short packet write: {count}/{packet.Length}; not retried as a stream.");
        }
        catch { _connected = false; throw; }
    }
    private static byte[] Event(uint kind)
    { var packet = new byte[MaximumEventSize]; BinaryPrimitives.WriteUInt32LittleEndian(packet, kind); return packet; }
    private static void Text(Span<byte> destination, string text)
    { var bytes = Encoding.UTF8.GetBytes(text); bytes.AsSpan(0, Math.Min(bytes.Length, destination.Length - 1)).CopyTo(destination); }
}
