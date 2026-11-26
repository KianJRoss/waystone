using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Security.Cryptography;
using UnifiedController.Core;
using UnifiedController.Linux.Platform.Output;

// Standalone, no packages, no SDL/libevdev/services. Every output instance below receives FakeWriter.
var count = 0;
void Check(string name, Action run) { run(); count++; Console.WriteLine($"PASS {name}"); }
static void Equal<T>(T expected, T actual) { if (!EqualityComparer<T>.Default.Equals(expected, actual)) throw new Exception($"Expected {expected}; got {actual}."); }
static void Bytes(ReadOnlySpan<byte> expected, ReadOnlySpan<byte> actual)
{ if (!expected.SequenceEqual(actual)) throw new Exception($"Byte mismatch. Expected {Convert.ToHexString(expected)}, got {Convert.ToHexString(actual)}."); }
static void Throws<T>(Action run) where T : Exception { try { run(); } catch (T) { return; } throw new Exception($"Expected {typeof(T).Name}."); }
static uint Type(byte[] packet) => BinaryPrimitives.ReadUInt32LittleEndian(packet);
static ushort U16(byte[] packet, int offset) => BinaryPrimitives.ReadUInt16LittleEndian(packet.AsSpan(offset));
static short I16(byte[] packet, int offset) => BinaryPrimitives.ReadInt16LittleEndian(packet.AsSpan(offset));
static uint U32(byte[] packet, int offset) => BinaryPrimitives.ReadUInt32LittleEndian(packet.AsSpan(offset));
static byte[] Event(uint kind) { var packet = new byte[UhidSonyOutput.MaximumEventSize]; BinaryPrimitives.WriteUInt32LittleEndian(packet, kind); return packet; }
static byte[] OutputEvent(byte[] report)
{ var packet = Event(6); report.CopyTo(packet, 4); BinaryPrimitives.WriteUInt16LittleEndian(packet.AsSpan(4100), (ushort)report.Length); packet[4102] = 1; return packet; }
static byte[] GetEvent(byte report, byte type = 0)
{ var packet = Event(9); BinaryPrimitives.WriteUInt32LittleEndian(packet.AsSpan(4), 0x12345678); packet[8] = report; packet[9] = type; return packet; }
static byte[] SetEvent(byte[] report)
{ var packet = Event(13); BinaryPrimitives.WriteUInt32LittleEndian(packet.AsSpan(4), 0x12345678); packet[8] = report[0]; packet[9] = 1; BinaryPrimitives.WriteUInt16LittleEndian(packet.AsSpan(10), (ushort)report.Length); report.CopyTo(packet, 12); return packet; }
static UhidSonyOutput Create(SonyPadType type, FakeWriter writer, int slot = 1) => type == SonyPadType.DualSense ? new UhidDualSenseOutput(slot, writer) : new UhidDualShock4Output(slot, writer);
static byte[] Input(SonyPadType type, ControllerFrame frame = default, MotionFrame? motion = null,
    IReadOnlySet<string>? buttons = null, byte seq = 0) => SonyHidReports.EncodeInput(type, frame, motion, buttons, seq);
static Dictionary<(int tag, int id), int> ReportBits(ReadOnlySpan<byte> descriptor)
{
    var result = new Dictionary<(int,int),int>(); var size = 0; var count = 0; var id = 0;
    for (var i = 0; i < descriptor.Length;)
    {
        var prefix = descriptor[i++]; if (prefix == 0xfe) throw new Exception("Unexpected HID long item.");
        var bytes = (prefix & 3) == 3 ? 4 : prefix & 3;
        var value = 0; for (var n = 0; n < bytes; n++) value |= descriptor[i++] << (8*n);
        switch (prefix & 0xfc)
        {
            case 0x74: size = value; break;
            case 0x94: count = value; break;
            case 0x84: id = value; break;
            case 0x80: case 0x90: case 0xb0:
                var key = (prefix & 0xfc, id); result[key] = result.GetValueOrDefault(key) + size*count; break;
        }
    }
    return result;
}

Check("DS4 descriptor exact captured hash/length", () => {
    var d = SonyHidDescriptors.Get(SonyPadType.DualShock4); Equal(507, d.Length);
    Equal("68B83F0CA188487E879670A0CEB22A73675BA4B51AC2887277F4F534A515FCDF", Convert.ToHexString(SHA256.HashData(d))); });
Check("DualSense descriptor exact captured hash/length", () => {
    var d = SonyHidDescriptors.Get(SonyPadType.DualSense); Equal(257, d.Length);
    Equal("2806CCA91B601FEA4515C5F9F9C4FC8DBAD47C49E9FEA9B8A090C71C5EE8B7D0", Convert.ToHexString(SHA256.HashData(d))); });
foreach (var type in Enum.GetValues<SonyPadType>())
{
    var ds5 = type == SonyPadType.DualSense; var b = ds5 ? 8 : 5; var g = ds5 ? 16 : 13;
    Check($"{type} descriptor report sizes independently decoded", () => {
        var bits = ReportBits(SonyHidDescriptors.Get(type)); Equal(63*8, bits[(0x80,1)]);
        Equal(ds5 ? 47*8 : 31*8, bits[(0x90, ds5 ? 2 : 5)]);
        Equal(ds5 ? 40*8 : 36*8, bits[(0xb0,ds5 ? 5 : 2)]); });
    Check($"{type} neutral byte-exact golden", () => {
        var expected = new byte[64]; expected[0]=1; expected[1]=expected[2]=expected[3]=expected[4]=128; expected[b]=8;
        if(ds5) { expected[33]=expected[37]=128; expected[53]=0x2a; }
        else { expected[30]=0x1b;expected[33]=1;foreach(var offset in new[]{35,39,44,48,53,57})expected[offset]=128; }
        Bytes(expected, Input(type)); });
    Check($"{type} face/system/shoulder/stick/trigger/touch buttons", () => {
        var f = new ControllerFrame(0xf7f0, 1, 255,0,0,0,0);
        var r = Input(type,f,buttons:new HashSet<string>{"Touchpad","MicMute"},seq:63);
        Equal((byte)0xf8,r[b]); Equal((byte)0xff,r[b+1]); Equal((byte)(ds5 ? 7 : 255),r[b+2]);
        Equal((byte)1,r[ds5?5:8]);Equal((byte)255,r[ds5?6:9]); if(ds5)Equal((byte)63,r[7]); });
    Check($"{type} each button maps independently", () => {
        foreach(var (mask,offset,value) in new[]{(0x4000,b,0x10),(0x1000,b,0x20),(0x2000,b,0x40),(0x8000,b,0x80),
            (0x100,b+1,1),(0x200,b+1,2),(0x20,b+1,16),(0x10,b+1,32),(0x40,b+1,64),(0x80,b+1,128),(0x400,b+2,1)})
        { var r=Input(type,new ControllerFrame(mask,0,0,0,0,0,0));Equal((byte)value,(byte)(r[offset] & (offset==b?0xf0:255))); }
    });
    Check($"{type} all eight hat directions and opposing cancellation", () => {
        var masks = new[]{1,9,8,10,2,6,4,5};for(var i=0;i<8;i++)Equal((byte)i,Input(type,new(masks[i],0,0,0,0,0,0))[b]);
        foreach(var mask in new[]{0,3,12,15})Equal((byte)8,Input(type,new(mask,0,0,0,0,0,0))[b]); });
    Check($"{type} signed axes endpoints/inverted Y", () => {
        var r=Input(type,new(0,0,0,short.MinValue,short.MaxValue,short.MaxValue,short.MinValue));
        Bytes(new byte[]{0,0,255,255},r.AsSpan(1,4)); });
    Check($"{type} calibrated XYZ sensor bytes and timestamp", () => {
        var r=Input(type,motion:new MotionFrame([1.25f,-2.5f,100],[1,-1,0.5f],16000));
        Bytes(new byte[]{20,0,216,255,64,6,0,32,0,224,0,16},r.AsSpan(g,12));
        if(ds5)Equal(48000u,U32(r,28));else Equal((ushort)3000,U16(r,10)); });
    Check($"{type} missing/nonfinite/clamped motion", () => {
        var r=Input(type,motion:new MotionFrame([float.NaN,float.PositiveInfinity,999999],[-999999],ulong.MaxValue));
        Equal((short)0,I16(r,g));Equal((short)0,I16(r,g+2));Equal(short.MaxValue,I16(r,g+4));
        Equal(short.MinValue,I16(r,g+6));Equal((short)0,I16(r,g+8));Equal((short)0,I16(r,g+10));
        var nullArrays = Input(type,motion:new MotionFrame(null!,null!,0)); Bytes(new byte[12],nullArrays.AsSpan(g,12)); });
    Check($"{type} timestamp rollover", () => {
        var time=ds5 ? (1UL<<32)+7 : 16UL*65536+160;
        var r=Input(type,motion:new MotionFrame([],[],time));
        if(ds5)Equal(21u,U32(r,28));else Equal((ushort)30,U16(r,10)); });
    Check($"{type} synthetic calibration roundtrips Linux and SDL units", () => {
        var feature=SonyHidReports.Feature(type,(byte)(ds5?5:2),new byte[6])!;
        Equal(ds5?41:37,feature.Length);Equal((byte)(ds5?5:2),feature[0]);
        var gyroScale=(I16(feature,19)+I16(feature,21))*1024/(I16(feature,7)-I16(feature,9));Equal(64,gyroScale);
        for(var axis=0;axis<3;axis++) {
            Equal((short)0,I16(feature,1+axis*2)); Equal((short)8192,I16(feature,7+axis*4));Equal((short)-8192,I16(feature,9+axis*4));
            var range=I16(feature,23+axis*4)-I16(feature,25+axis*4);Equal(16384,range); }
        var r=Input(type,motion:new MotionFrame([123,0,0],[1,0,0],0));Equal(123*1024,I16(r,g)*gyroScale);Equal(8192,(int)I16(r,g+6)); });
    Check($"{type} pairing/firmware feature lengths and unknown rejected", () => {
        byte[] id=[2,3,4,5,6,7];var p=SonyHidReports.Feature(type,(byte)(ds5?9:0x12),id)!;
        Equal(ds5?20:16,p.Length);Bytes(id,p.AsSpan(1,6));Equal(ds5?64:49,SonyHidReports.Feature(type,(byte)(ds5?0x20:0xa3),id)!.Length);
        Equal<byte[]?>(null,SonyHidReports.Feature(type,0xff,id)); });
    Check($"{type} output rumble/lightbar flags and exact bytes", () => {
        var r=new byte[ds5?48:32];r[0]=(byte)(ds5?2:5);r[1]=1;if(ds5)r[2]=4;else r[1]|=2;
        var m=ds5?3:4;var rgb=ds5?45:6;r[m]=20;r[m+1]=90;r[rgb]=1;r[rgb+1]=2;r[rgb+2]=3;
        Equal(true,SonyHidReports.TryParseOutput(type,r,out var parsed));Equal((byte?)90,parsed!.Heavy);Equal((byte?)20,parsed.Light);
        Equal((byte?)1,parsed.Red);Equal((byte?)2,parsed.Green);Equal((byte?)3,parsed.Blue);
        r[1]=r[2]=0;SonyHidReports.TryParseOutput(type,r,out parsed);Equal<byte?>(null,parsed!.Heavy);Equal<byte?>(null,parsed.Red); });
    Check($"{type} malformed output lengths and report ids rejected", () => {
        for(var length=0;length<=5000;length++) {
            if(length==(ds5?48:32) || ds5&&length==63)continue;
            var r=new byte[length];if(length>0)r[0]=(byte)(ds5?2:5);Equal(false,SonyHidReports.TryParseOutput(type,r,out _)); }
        Equal(false,SonyHidReports.TryParseOutput(type,new byte[ds5?48:32],out _)); });
    Check($"{type} construction inert and CREATE2 exact ABI/descriptor", () => {
        var writer=new FakeWriter();var output=Create(type,writer,4);Equal(0,writer.Opens);Equal(false,output.Connected);
        output.StartAsync(default).GetAwaiter().GetResult();Equal(true,output.Connected);Equal(1,writer.Opens);
        var r=writer.Packets.Single();Equal(4376,r.Length);Equal(11u,Type(r));Equal((ushort)3,U16(r,262));Equal(0x054cu,U32(r,264));
        Equal(ds5?0x0ce6u:0x05c4u,U32(r,268));Equal((ushort)(ds5?257:507),U16(r,260));Bytes(SonyHidDescriptors.Get(type),r.AsSpan(280,ds5?257:507));
        output.DisposeAsync().GetAwaiter().GetResult();Equal(1,writer.Disposals); });
    Check($"{type} repeated start, sample INPUT2 and neutral/destroy lifecycle", () => {
        var writer=new FakeWriter();var output=Create(type,writer);output.StartAsync(default).GetAwaiter().GetResult();output.StartAsync(default).GetAwaiter().GetResult();Equal(1,writer.Opens);
        var f=new ControllerFrame(0x1000,20,30,1,2,3,4);var motion=new MotionFrame([4,5,6],[1,2,3],123456);
        output.SubmitSampleAsync(f,motion,new HashSet<string>{"Touchpad"},default).GetAwaiter().GetResult();
        var p=writer.Packets.Last();Equal(12u,Type(p));Equal((ushort)64,U16(p,4));Bytes(Input(type,f,motion,new HashSet<string>{"Touchpad"}),p.AsSpan(6,64));
        output.SubmitNeutralAsync(default).GetAwaiter().GetResult();var neutral=writer.Packets.Last();Equal((byte)8,neutral[6+b]);Equal((byte)0,neutral[6+b+1]);Equal((byte)128,neutral[7]);Bytes(new byte[12],neutral.AsSpan(6+g,12));
        output.DisposeAsync().GetAwaiter().GetResult();output.DisposeAsync().GetAwaiter().GetResult();Equal(1,writer.Disposals);Equal(1u,Type(writer.Packets.Last()));Equal(false,output.Connected);
        Throws<ObjectDisposedException>(()=>output.StartAsync(default).GetAwaiter().GetResult()); });
    Check($"{type} feature GET request id/type/unsupported reply", () => {
        var writer=new FakeWriter();var output=Create(type,writer);output.StartAsync(default).GetAwaiter().GetResult();
        Equal(true,output.ProcessKernelEvent(GetEvent((byte)(ds5?5:2))));var p=writer.Packets.Last();Equal(10u,Type(p));Equal(0x12345678u,U32(p,4));Equal((ushort)0,U16(p,8));Equal((ushort)(ds5?41:37),U16(p,10));Equal((byte)(ds5?5:2),p[12]);
        output.ProcessKernelEvent(GetEvent(0xff));p=writer.Packets.Last();Equal((ushort)95,U16(p,8));Equal((ushort)0,U16(p,10));
        output.ProcessKernelEvent(GetEvent((byte)(ds5?5:2),1));Equal((ushort)95,U16(writer.Packets.Last(),8));
        output.DisposeAsync().GetAwaiter().GetResult(); });
    Check($"{type} OUTPUT and SET_REPORT fake dispatch (no physical writes)", () => {
        var writer=new FakeWriter();var output=Create(type,writer);output.StartAsync(default).GetAwaiter().GetResult();var calls=0;
        output.Rumble+=(heavy,light)=> {Equal((byte)67,heavy);Equal((byte)45,light);calls++;};
        var report=new byte[ds5?48:32];report[0]=(byte)(ds5?2:5);report[1]=1;report[ds5?3:4]=45;report[ds5?4:5]=67;
        Equal(true,output.ProcessKernelEvent(OutputEvent(report)));Equal(1,calls);
        Equal(true,output.ProcessKernelEvent(SetEvent(report)));Equal(2,calls);var p=writer.Packets.Last();Equal(14u,Type(p));Equal(0x12345678u,U32(p,4));Equal((ushort)0,U16(p,8));
        var bad=SetEvent(report);bad[8]=0xff;Equal(false,output.ProcessKernelEvent(bad));Equal((ushort)22,U16(writer.Packets.Last(),8));Equal(2,calls);
        output.DisposeAsync().GetAwaiter().GetResult(); });
    Check($"{type} malformed kernel events bounded, no output callbacks", () => {
        var writer=new FakeWriter();var output=Create(type,writer);output.StartAsync(default).GetAwaiter().GetResult();var calls=0;output.Rumble+=(_,_)=>calls++;
        foreach(var n in new[]{0,1,3,4377,10000})Equal(false,output.ProcessKernelEvent(new byte[n]));
        Equal(false,output.ProcessKernelEvent(Event(999)));
        var p=Event(6);BinaryPrimitives.WriteUInt16LittleEndian(p.AsSpan(4100),65535);p[4102]=1;Equal(false,output.ProcessKernelEvent(p));
        p=Event(13);BinaryPrimitives.WriteUInt16LittleEndian(p.AsSpan(10),65535);Equal(false,output.ProcessKernelEvent(p));Equal((ushort)22,U16(writer.Packets.Last(),8));
        Equal(false,output.ProcessKernelEvent(Event(9).AsSpan(0,9)));Equal(false,output.ProcessKernelEvent(Event(13).AsSpan(0,11)));
        var random=new Random(42);for(var n=0;n<300;n++){var data=new byte[random.Next(0,4377)];random.NextBytes(data);if(data.Length>=4)data.AsSpan(0,4).Clear();Equal(false,output.ProcessKernelEvent(data));}
        Equal(0,calls);output.DisposeAsync().GetAwaiter().GetResult(); });
    Check($"{type} kernel open/close/start/stop semantics", () => {
        var writer=new FakeWriter();var output=Create(type,writer);output.StartAsync(default).GetAwaiter().GetResult();
        output.ProcessKernelEvent(Event(5));Equal(true,output.Connected);output.ProcessKernelEvent(Event(3));Equal(false,output.Connected);
        Throws<InvalidOperationException>(()=>output.SubmitAsync(default,default).GetAwaiter().GetResult());output.ProcessKernelEvent(Event(2));Equal(true,output.Connected);
        output.DisposeAsync().GetAwaiter().GetResult();Equal(false,output.ProcessKernelEvent(Event(2))); });
}
Check("DualSense padded63 output and rumble-v2", () => {
    var p=new byte[63];p[0]=2;p[39]=4;p[3]=55;p[4]=77;Equal(true,SonyHidReports.TryParseOutput(SonyPadType.DualSense,p,out var parsed));Equal((byte?)77,parsed!.Heavy);Equal((byte?)55,parsed.Light); });
Check("DualSense adaptive triggers safely copied, gated independently", () => {
    var p=new byte[48];p[0]=2;p[1]=12;for(var i=0;i<22;i++)p[11+i]=(byte)(i+1);
    Equal(true,SonyHidReports.TryParseOutput(SonyPadType.DualSense,p,out var parsed));Bytes(p.AsSpan(11,11),parsed!.RightTrigger.Span);Bytes(p.AsSpan(22,11),parsed.LeftTrigger.Span);
    p[11]=255;Equal((byte)1,parsed.RightTrigger.Span[0]);p[1]=4;SonyHidReports.TryParseOutput(SonyPadType.DualSense,p,out parsed);Equal(0,parsed!.LeftTrigger.Length); });
Check("invalid enum/slot/identity rejected without open", () => {
    Throws<ArgumentOutOfRangeException>(()=>SonyHidDescriptors.Get((SonyPadType)99).ToArray());Throws<ArgumentOutOfRangeException>(()=>Input((SonyPadType)99));
    Throws<ArgumentException>(()=>SonyHidReports.Feature(SonyPadType.DualSense,5,[]));var w=new FakeWriter();Throws<ArgumentOutOfRangeException>(()=>new UhidDualSenseOutput(0,w));Equal(0,w.Opens); });
Check("cancelled start and pre-start submit cannot open", () => {
    var w=new FakeWriter();var o=new UhidDualSenseOutput(writer:w);Throws<OperationCanceledException>(()=>o.StartAsync(new CancellationToken(true)).GetAwaiter().GetResult());Equal(0,w.Opens);
    Throws<InvalidOperationException>(()=>o.SubmitAsync(default,default).GetAwaiter().GetResult());o.SubmitNeutralAsync(default).GetAwaiter().GetResult();Equal(0,w.Packets.Count);
    o.DisposeAsync().GetAwaiter().GetResult();Equal(1,w.Disposals); });
foreach(var written in new[]{0,7,4375})Check($"short CREATE2 ({written}) fails once and closes", () => {
    var w=new FakeWriter{NextWriteLength=written};var o=new UhidDualSenseOutput(writer:w);Throws<IOException>(()=>o.StartAsync(default).GetAwaiter().GetResult());Equal(1,w.Packets.Count);Equal(1,w.Disposals);Equal(false,o.Connected);o.DisposeAsync().GetAwaiter().GetResult();Equal(1,w.Disposals); });
Check("partial INPUT2 never retries suffix and closes on disposal", () => {
    var w=new FakeWriter();var o=new UhidDualShock4Output(writer:w);o.StartAsync(default).GetAwaiter().GetResult();w.NextWriteLength=50;
    Throws<IOException>(()=>o.SubmitAsync(default,default).GetAwaiter().GetResult());Equal(2,w.Packets.Count);Equal(false,o.Connected);
    o.DisposeAsync().GetAwaiter().GetResult();Equal(3,w.Packets.Count);Equal(1u,Type(w.Packets.Last()));Equal(1,w.Disposals); });
Check("failed native-equivalent open releases fake transport", () => {
    var w=new FakeWriter{FailOpen=true};var o=new UhidDualSenseOutput(writer:w);Throws<IOException>(()=>o.StartAsync(default).GetAwaiter().GetResult());Equal(1,w.Disposals);Equal(0,w.Packets.Count); });
Check("reader services fake queued GET request", () => {
    var w=new FakeWriter();w.Incoming.Enqueue(GetEvent(5));var o=new UhidDualSenseOutput(writer:w);o.StartAsync(default).GetAwaiter().GetResult();
    Equal(true,SpinWait.SpinUntil(()=>w.Packets.Any(p=>Type(p)==10),TimeSpan.FromSeconds(2)));o.DisposeAsync().GetAwaiter().GetResult();Equal<Exception?>(null,o.LastReaderError); });
Check("idle UHID reader waits for readiness instead of spinning", () => {
    var w=new FakeWriter();var o=new UhidDualSenseOutput(writer:w);o.StartAsync(default).GetAwaiter().GetResult();
    Thread.Sleep(260);var waits=w.ReadinessWaits;o.DisposeAsync().GetAwaiter().GetResult();
    Equal(true,waits<=4); });
Check("reader transport failure is observable and blocks input", () => {
    var w=new FakeWriter{FailRead=true};var o=new UhidDualSenseOutput(writer:w);o.StartAsync(default).GetAwaiter().GetResult();
    Equal(true,SpinWait.SpinUntil(()=>!o.Connected,TimeSpan.FromSeconds(2)));Equal(true,o.LastReaderError is IOException);
    Throws<InvalidOperationException>(()=>o.SubmitAsync(default,default).GetAwaiter().GetResult());o.DisposeAsync().GetAwaiter().GetResult();Equal(1u,Type(w.Packets.Last())); });
Check("throwing rumble callback is captured by fake reader, not process fatal", () => {
    var w=new FakeWriter();var o=new UhidDualSenseOutput(writer:w);o.Rumble+=(_,_)=>throw new InvalidOperationException("fake sink failure");
    o.StartAsync(default).GetAwaiter().GetResult();var report=new byte[48];report[0]=2;report[1]=1;w.Incoming.Enqueue(OutputEvent(report));
    Equal(true,SpinWait.SpinUntil(()=>!o.Connected,TimeSpan.FromSeconds(2)));Equal(true,o.LastReaderError is InvalidOperationException);
    o.DisposeAsync().GetAwaiter().GetResult();Equal(1,w.Disposals); });
Check("failed transport close is retryable and cannot reconnect", () => {
    var w=new FakeWriter{DisposeFailures=1};var o=new UhidDualSenseOutput(writer:w);o.StartAsync(default).GetAwaiter().GetResult();
    Throws<IOException>(()=>o.DisposeAsync().GetAwaiter().GetResult());Equal(false,o.Connected);Equal(1,w.Disposals);
    Throws<ObjectDisposedException>(()=>o.StartAsync(default).GetAwaiter().GetResult());Equal(false,o.ProcessKernelEvent(Event(2)));
    o.DisposeAsync().GetAwaiter().GetResult();Equal(2,w.Disposals);o.DisposeAsync().GetAwaiter().GetResult();Equal(2,w.Disposals); });
Check("failed startup close is retryable", () => {
    var w=new FakeWriter{FailOpen=true,DisposeFailures=1};var o=new UhidDualShock4Output(writer:w);
    try { o.StartAsync(default).GetAwaiter().GetResult(); throw new Exception("Expected startup and close failures"); }
    catch(AggregateException ex) { Equal(true,ex.InnerExceptions.Any(e=>e.Message=="fake open failure")&&ex.InnerExceptions.Any(e=>e.Message=="fake close failure")); }
    Equal(1,w.Disposals);
    o.DisposeAsync().GetAwaiter().GetResult();Equal(2,w.Disposals); });
Check("failed destroy packet still closes transport", () => {
    var w=new FakeWriter();var o=new UhidDualSenseOutput(writer:w);o.StartAsync(default).GetAwaiter().GetResult();
    o.ProcessKernelEvent(Event(3));w.NextWriteLength=0;o.DisposeAsync().GetAwaiter().GetResult();Equal(1,w.Disposals);Equal(false,o.Connected); });
Console.WriteLine($"{count} fake-only Sony output checks passed; zero real devices opened.");

sealed class FakeWriter : IUhidWriter
{
    public int Opens, Disposals, ReadinessWaits;
    public int? NextWriteLength;
    public bool FailOpen, FailRead;
    public int DisposeFailures;
    public ConcurrentQueue<byte[]> Packets { get; } = new();
    public ConcurrentQueue<byte[]> Incoming { get; } = new();
    public void Open() { Opens++; if(FailOpen)throw new IOException("fake open failure"); }
    public int Write(ReadOnlySpan<byte> packet) { Packets.Enqueue(packet.ToArray());var length=NextWriteLength;NextWriteLength=null;return length??packet.Length; }
    public bool WaitReadable(int timeoutMilliseconds)
    {
        Interlocked.Increment(ref ReadinessWaits);
        if(FailRead||!Incoming.IsEmpty)return true;
        Thread.Sleep(timeoutMilliseconds);
        return FailRead||!Incoming.IsEmpty;
    }
    public int Read(Span<byte> packet) { if(FailRead)throw new IOException("fake read failure");if(!Incoming.TryDequeue(out var data))return 0;data.CopyTo(packet);return data.Length; }
    public void Dispose() { Disposals++; if(DisposeFailures-->0)throw new IOException("fake close failure"); }
}
