using System.Runtime.InteropServices;
using UnifiedController.Core;

namespace UnifiedController.Linux.Platform.Output;

public sealed record XboxUInputAxis(int Code, int Minimum, int Maximum);
public sealed record XboxUInputDevice(string Name, ushort Bus, ushort Vendor, ushort Product, ushort Version,
    IReadOnlyList<int> Buttons, IReadOnlyList<XboxUInputAxis> Axes);
public readonly record struct XboxUInputEvent(ushort Type, ushort Code, int Value);
/// <summary>Inject this seam for tests. The default implementation alone opens /dev/uinput.</summary>
public interface IXboxUInputWriter : IAsyncDisposable
{
    bool Connected { get; }
    event Action<byte, byte>? Rumble;
    ValueTask StartAsync(XboxUInputDevice device, CancellationToken token);
    ValueTask WriteAsync(IReadOnlyList<XboxUInputEvent> events, CancellationToken token);
}

/// <summary>xpad-compatible One S / Series evdev identity, not USB/GIP hardware emulation.</summary>
public sealed class UInputXboxOneOutput : IMotionVirtualControllerOutput
{
    private readonly IXboxUInputWriter _writer;
    private readonly bool _series;
    private bool _disposed;
    private Exception? _rumbleFault;
    public XboxUInputDevice Device { get; }
    public UInputXboxOneOutput(int slot = 1, bool series = false, IXboxUInputWriter? writer = null)
    {
        if (slot is < 1 or > 4) throw new ArgumentOutOfRangeException(nameof(slot));
        _series = series; _writer = writer ?? new NativeXboxUInputWriter();
        var keys = new List<int>(Buttons); if (series) keys.Add(167); // KEY_RECORD, as xpad and SDL Linux's MISC1 mapping
        Device = new(series ? $"Microsoft Xbox Series S|X Controller (Waystone Slot {slot})" : $"Microsoft X-Box One S pad (Waystone Slot {slot})",
            3, 0x045e, series ? (ushort)0x0b12 : (ushort)0x02ea, 0x0500, keys.AsReadOnly(),
            Array.AsReadOnly(new[] { new XboxUInputAxis(0,-32768,32767), new(1,-32768,32767), new(3,-32768,32767), new(4,-32768,32767),
                new(2,0,1023), new(5,0,1023), new(16,-1,1), new(17,-1,1) }));
        _writer.Rumble += ForwardRumble;
    }
    public bool Connected => _writer.Connected && Volatile.Read(ref _rumbleFault) is null;
    public event Action<byte, byte>? Rumble;
    private void ForwardRumble(byte heavy, byte light)
    {
        if (_disposed || Volatile.Read(ref _rumbleFault) is not null) return;
        try { Rumble?.Invoke(heavy, light); }
        catch (Exception ex) { Volatile.Write(ref _rumbleFault, ex); }
    }
    public ValueTask StartAsync(CancellationToken token) { ObjectDisposedException.ThrowIf(_disposed, this); return _writer.StartAsync(Device, token); }
    public ValueTask SubmitAsync(ControllerFrame frame, CancellationToken token) => SubmitSampleAsync(frame, null, null, token);
    public ValueTask SubmitSampleAsync(ControllerFrame frame, MotionFrame? motion, IReadOnlySet<string>? extendedButtons, CancellationToken token)
    {
        token.ThrowIfCancellationRequested(); ObjectDisposedException.ThrowIf(_disposed, this);
        if (Volatile.Read(ref _rumbleFault) is { } fault) throw new IOException("Rumble callback failed; output must be recreated.",fault);
        if (!Connected) throw new InvalidOperationException("Output is not started.");
        return _writer.WriteAsync(Encode(frame, extendedButtons, _series), token);
    }
    public ValueTask SubmitNeutralAsync(CancellationToken token) => Connected ? SubmitAsync(default, token) : ValueTask.CompletedTask;
    public async ValueTask DisposeAsync()
    {
        if (_disposed) return;
        try { await SubmitNeutralAsync(CancellationToken.None).ConfigureAwait(false); } catch { /* closing removes any held input */ }
        _writer.Rumble -= ForwardRumble;
        await _writer.DisposeAsync().ConfigureAwait(false);
        _disposed = true; // only confirmed removal is idempotent; a failed close must be retryable
    }
    public static IReadOnlyList<XboxUInputEvent> Encode(ControllerFrame f, IReadOnlySet<string>? extendedButtons, bool series)
    {
        var events = new List<XboxUInputEvent>();
        for (var i = 0; i < Buttons.Length; ++i) events.Add(new(1, (ushort)Buttons[i], (f.Buttons & Masks[i]) != 0 ? 1 : 0));
        if (series) events.Add(new(1, 167, extendedButtons?.Any(b => b.Equals("Share", StringComparison.OrdinalIgnoreCase) || b.Equals("Capture", StringComparison.OrdinalIgnoreCase)) == true ? 1 : 0));
        events.AddRange(new XboxUInputEvent[] {
            new(3,0,f.LeftX), new(3,1,Invert(f.LeftY)), new(3,3,f.RightX), new(3,4,Invert(f.RightY)),
            new(3,2,(f.LeftTrigger * 1023 + 127) / 255), new(3,5,(f.RightTrigger * 1023 + 127) / 255),
            new(3,16,((f.Buttons & 8)!=0 ? 1:0)-((f.Buttons & 4)!=0 ? 1:0)),
            new(3,17,((f.Buttons & 2)!=0 ? 1:0)-((f.Buttons & 1)!=0 ? 1:0)), new(0,0,0) });
        return events.AsReadOnly();
    }
    private static int Invert(short value) => value == short.MinValue ? short.MaxValue : -value;
    private static readonly int[] Buttons = [304,305,307,308,310,311,317,318,315,314,316];
    private static readonly int[] Masks = [0x1000,0x2000,0x4000,0x8000,0x0100,0x0200,0x0040,0x0080,0x0010,0x0020,0x0400];
}

internal sealed class NativeXboxUInputWriter : IXboxUInputWriter
{
    private int _fd = -1;
    private bool _created;
    private Exception? _fault;
    private readonly object _gate = new();
    private readonly UInputForceFeedbackProcessor _feedback = new();
    private CancellationTokenSource? _stop, _effectStop;
    private Thread? _reader;
    public bool Connected { get { lock (_gate) return _created && Volatile.Read(ref _fault) is null; } }
    public event Action<byte, byte>? Rumble;
    public unsafe ValueTask StartAsync(XboxUInputDevice device, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        lock (_gate)
        {
            if (_created) return ValueTask.CompletedTask;
            try
            {
                _fault = null;
                _fd = UInputNative.open("/dev/uinput", UInputNative.ORdwr | UInputNative.ONonblock);
                if (_fd < 0) throw Error("open /dev/uinput");
                Bit(100,1); Bit(100,3); Bit(100,0x15);
                foreach (var key in device.Buttons) Bit(101,key);
                foreach (var axis in device.Axes)
                {
                    Bit(103,axis.Code);
                    var abs = new UInputNative.UInputAbsSetup { Code=(ushort)axis.Code, AbsInfo=new() { minimum=axis.Minimum, maximum=axis.Maximum } };
                    Check(UInputNative.ioctl(_fd, WriteIoctl(4,Marshal.SizeOf<UInputNative.UInputAbsSetup>()),(nint)(&abs)),"UI_ABS_SETUP");
                }
                Bit(107,0x50); Bit(107,0x60);
                var setup = new UInputNative.UInputSetup { Id=new() { BusType=device.Bus, Vendor=device.Vendor, Product=device.Product, Version=device.Version }, FfEffectsMax=16 };
                var name = System.Text.Encoding.UTF8.GetBytes(device.Name);
                byte* target = setup.Name; fixed(byte* source = name) Buffer.MemoryCopy(source,target,80,Math.Min(name.Length,79));
                Check(UInputNative.ioctl(_fd,WriteIoctl(3,Marshal.SizeOf<UInputNative.UInputSetup>()),(nint)(&setup)),"UI_DEV_SETUP");
                Check(UInputNative.ioctl(_fd,NoneIoctl(1),0),"UI_DEV_CREATE"); _created = true;
                _feedback.Rumble = (heavy,light,length) =>
                {
                    EmitRumble(heavy,light);
                    _effectStop?.Cancel(); _effectStop?.Dispose(); _effectStop=null;
                    if (length>0 && (heavy!=0 || light!=0))
                    {
                        var stop = _effectStop = new();
                        _ = Task.Delay(length,stop.Token).ContinueWith(t => { if(!t.IsCanceled) EmitRumble(0,0); },CancellationToken.None,TaskContinuationOptions.ExecuteSynchronously,TaskScheduler.Default);
                    }
                };
                _stop = new(); var stopToken=_stop.Token;
                _reader = new Thread(() => ReadFeedback(stopToken)) { IsBackground=true,Name="Waystone-XboxOne-FF" }; _reader.Start();
            }
            catch { if(_fd>=0) UInputNative.close(_fd); _fd=-1; _created=false; throw; }
        }
        return ValueTask.CompletedTask;
    }
    public unsafe ValueTask WriteAsync(IReadOnlyList<XboxUInputEvent> events, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        lock (_gate)
        {
            if (Volatile.Read(ref _fault) is { } fault) throw new IOException("uinput feedback failed; output must be recreated.",fault);
            if (!_created) throw new InvalidOperationException("Output is not started.");
            foreach (var item in events)
            {
                var ev = new UInputNative.InputEvent { Type=item.Type,Code=item.Code,Value=item.Value };
                var size=Marshal.SizeOf<UInputNative.InputEvent>();
                if (UInputNative.write(_fd,(nint)(&ev),(nuint)size)!=size) throw Error("uinput event write");
            }
        }
        return ValueTask.CompletedTask;
    }
    public ValueTask DisposeAsync()
    {
        _stop?.Cancel();
        // Nonblocking reader only sleeps 2ms; join before closing to avoid descriptor reuse races.
        if (_reader is not null && !_reader.Join(1000)) throw new IOException("uinput feedback reader did not stop; device removal is unconfirmed.");
        _reader=null;
        lock (_gate)
        {
            _effectStop?.Cancel(); _feedback.Stop();
            if (_created) UInputNative.ioctl(_fd,NoneIoctl(2),0);
            if (_fd>=0) { var result=UInputNative.close(_fd); _fd=-1; _created=false; if(result<0) throw Error("close uinput"); }
            _created=false;
            _stop?.Dispose(); _stop=null; _effectStop?.Dispose(); _effectStop=null;
        }
        return ValueTask.CompletedTask;
    }
    private void EmitRumble(byte heavy,byte light)
    {
        try { Rumble?.Invoke(heavy,light); }
        catch (Exception ex) { Volatile.Write(ref _fault,ex); }
    }
    private void ReadFeedback(CancellationToken token)
    {
        try { ReadFeedbackCore(token); }
        catch (Exception ex) { Volatile.Write(ref _fault,ex); }
    }
    private unsafe void ReadFeedbackCore(CancellationToken token)
    {
        var size=Marshal.SizeOf<UInputNative.InputEvent>(); var buffer=stackalloc byte[size];
        while(!token.IsCancellationRequested && Volatile.Read(ref _fault) is null)
        {
            if(UInputNative.read(_fd,(nint)buffer,(nuint)size)!=size) { Thread.Sleep(2); continue; }
            var ev=Marshal.PtrToStructure<UInputNative.InputEvent>((nint)buffer);
            if(ev.Type==UInputNative.EvUinput && ev.Code==UInputNative.UiFfUpload)
            {
                var upload=new UInputNative.UInputFfUpload { RequestId=(uint)ev.Value };
                if(UInputNative.ioctl(_fd,ReadWriteIoctl(200,Marshal.SizeOf<UInputNative.UInputFfUpload>()),(nint)(&upload))<0) continue;
                upload.Retval=upload.Effect.Type==UInputNative.FfRumble?0:-22;
                if(upload.Retval==0) _feedback.Upload(upload.Effect.Id,upload.Effect.Data.StrongMagnitude,upload.Effect.Data.WeakMagnitude,upload.Effect.Replay.Length);
                UInputNative.ioctl(_fd,WriteIoctl(201,Marshal.SizeOf<UInputNative.UInputFfUpload>()),(nint)(&upload));
            }
            else if(ev.Type==UInputNative.EvUinput && ev.Code==UInputNative.UiFfErase)
            {
                var erase=new UInputNative.UInputFfErase { RequestId=(uint)ev.Value };
                if(UInputNative.ioctl(_fd,ReadWriteIoctl(202,Marshal.SizeOf<UInputNative.UInputFfErase>()),(nint)(&erase))<0) continue;
                _feedback.Erase(erase.EffectId); erase.Retval=0;
                UInputNative.ioctl(_fd,WriteIoctl(203,Marshal.SizeOf<UInputNative.UInputFfErase>()),(nint)(&erase));
            }
            else _feedback.Process(ev.Type,ev.Code,ev.Value);
        }
    }
    private void Bit(int number,int value) => Check(UInputNative.ioctl(_fd,WriteIoctl(number,sizeof(int)),value),"uinput capability");
    private static IOException Error(string operation) => new($"{operation} failed: errno {Marshal.GetLastPInvokeError()}.");
    private static void Check(int result,string operation) { if(result<0) throw Error(operation); }
    private static nuint WriteIoctl(int number,int size) => (nuint)((1u<<30)|((uint)size<<16)|((uint)'U'<<8)|(uint)number);
    private static nuint ReadWriteIoctl(int number,int size) => (nuint)((3u<<30)|((uint)size<<16)|((uint)'U'<<8)|(uint)number);
    private static nuint NoneIoctl(int number) => (nuint)(((uint)'U'<<8)|(uint)number);
}
