using System.Runtime.InteropServices;
using UnifiedController.Core;

namespace UnifiedController.Linux.Platform.Output;

public sealed class UInputXbox360Output : IVirtualControllerOutput
{
    private readonly int _slot; private nint _device; private int _fd=-1; private readonly object _gate = new();
    private readonly UInputForceFeedbackProcessor _forceFeedback = new();
    private CancellationTokenSource? _readerStop; private Thread? _reader; private CancellationTokenSource? _effectStop;
    public UInputXbox360Output(int slot = 1) { if (slot is < 1 or > 4) throw new ArgumentOutOfRangeException(nameof(slot)); _slot=slot; }
    public bool Connected => _device != 0;
    public event Action<byte, byte>? Rumble;

    public ValueTask StartAsync(CancellationToken token)
    {
        token.ThrowIfCancellationRequested(); lock (_gate) { if (_device != 0) return ValueTask.CompletedTask;
            try {
                _fd=UInputNative.open("/dev/uinput",UInputNative.ORdwr|UInputNative.ONonblock);
                if(_fd<0) throw new IOException($"open /dev/uinput failed: errno {Marshal.GetLastPInvokeError()}.");
                ConfigureDevice(); _device=1;
                _forceFeedback.Rumble=(heavy,light,length)=> { Rumble?.Invoke(heavy,light); _effectStop?.Cancel(); _effectStop?.Dispose(); _effectStop=null; if(length>0&&(heavy!=0||light!=0)) { var stop=_effectStop=new(); _=Task.Delay(length,stop.Token).ContinueWith(t=>{ if(!t.IsCanceled){ Rumble?.Invoke(0,0); } },CancellationToken.None,TaskContinuationOptions.ExecuteSynchronously,TaskScheduler.Default); } };
                _readerStop=new(); _reader=new Thread(()=>ReadForceFeedback(_readerStop.Token)){IsBackground=true,Name=$"UInputFF-{_slot}"}; _reader.Start();
            } catch { if(_fd>=0){UInputNative.close(_fd);_fd=-1;} _device=0; throw; }
        } return ValueTask.CompletedTask;
    }
    public ValueTask SubmitAsync(ControllerFrame f, CancellationToken token) { token.ThrowIfCancellationRequested(); lock(_gate) { if(_device==0) throw new InvalidOperationException("Output is not started.");
        for(var i=0;i<Buttons.Length;i++) Write(UInputNative.EvKey,Buttons[i],(f.Buttons & Masks[i])!=0?1:0);
        Write(UInputNative.EvAbs,UInputNative.AbsX,f.LeftX); Write(UInputNative.EvAbs,UInputNative.AbsY,Invert(f.LeftY)); Write(UInputNative.EvAbs,UInputNative.AbsRx,f.RightX); Write(UInputNative.EvAbs,UInputNative.AbsRy,Invert(f.RightY));
        Write(UInputNative.EvAbs,UInputNative.AbsZ,f.LeftTrigger); Write(UInputNative.EvAbs,UInputNative.AbsRz,f.RightTrigger);
        Write(UInputNative.EvAbs,UInputNative.AbsHat0X,(f.Buttons&4)!=0?-1:(f.Buttons&8)!=0?1:0); Write(UInputNative.EvAbs,UInputNative.AbsHat0Y,(f.Buttons&1)!=0?-1:(f.Buttons&2)!=0?1:0); Write(UInputNative.EvSyn,UInputNative.SynReport,0);
    } return ValueTask.CompletedTask; }
    public ValueTask SubmitNeutralAsync(CancellationToken token) => _device==0 ? ValueTask.CompletedTask : SubmitAsync(default,token);
    public async ValueTask DisposeAsync() { _readerStop?.Cancel(); _effectStop?.Cancel(); _forceFeedback.Stop(); if(_device!=0) { try { await SubmitNeutralAsync(CancellationToken.None); } catch { } lock(_gate) { if(_device!=0) UInputNative.ioctl(_fd,IoctlNone(2),0); _device=0; } } try{_reader?.Join(300);}catch{} if(_fd>=0){UInputNative.close(_fd);_fd=-1;} _readerStop?.Dispose();_readerStop=null;_effectStop?.Dispose();_effectStop=null; }
    private unsafe void ConfigureDevice()
    {
        SetBit(100,UInputNative.EvKey);SetBit(100,UInputNative.EvAbs);SetBit(100,UInputNative.EvFf);
        foreach(var code in Buttons)SetBit(101,code);
        foreach(var code in new[]{UInputNative.AbsX,UInputNative.AbsY,UInputNative.AbsRx,UInputNative.AbsRy})SetupAbs(code,-32768,32767);
        SetupAbs(UInputNative.AbsZ,0,255);SetupAbs(UInputNative.AbsRz,0,255);SetupAbs(UInputNative.AbsHat0X,-1,1);SetupAbs(UInputNative.AbsHat0Y,-1,1);
        SetBit(107,UInputNative.FfRumble);SetBit(107,UInputNative.FfGain);
        var setup=new UInputNative.UInputSetup{Id=new(){BusType=UInputNative.BusVirtual,Vendor=0x045e,Product=0x028e,Version=0x0114},FfEffectsMax=16};
        var name=System.Text.Encoding.UTF8.GetBytes($"Unified Controller Xbox 360 Slot {_slot}");byte* target=setup.Name;fixed(byte* source=name)Buffer.MemoryCopy(source,target,80,Math.Min(name.Length,79));
        CheckIoctl(UInputNative.ioctl(_fd,IoctlWrite(3,Marshal.SizeOf<UInputNative.UInputSetup>()),(nint)(&setup)),"UI_DEV_SETUP");
        CheckIoctl(UInputNative.ioctl(_fd,IoctlNone(1),0),"UI_DEV_CREATE");
    }
    private unsafe void SetupAbs(int code,int min,int max){SetBit(103,code);var setup=new UInputNative.UInputAbsSetup{Code=(ushort)code,AbsInfo=new(){minimum=min,maximum=max}};CheckIoctl(UInputNative.ioctl(_fd,IoctlWrite(4,Marshal.SizeOf<UInputNative.UInputAbsSetup>()),(nint)(&setup)),"UI_ABS_SETUP");}
    // UI_SET_EVBIT/KEYBIT/ABSBIT/FFBIT take the bit number itself as the ioctl argument, not a pointer to it.
    private void SetBit(int number,int value)=>CheckIoctl(UInputNative.ioctl(_fd,IoctlWrite(number,sizeof(int)),value),$"uinput capability {number}:{value}");
    private static void CheckIoctl(int result,string operation){if(result<0)throw new IOException($"{operation} failed: errno {Marshal.GetLastPInvokeError()}.");}
    private unsafe void ReadForceFeedback(CancellationToken token)
    {
        var size=Marshal.SizeOf<UInputNative.InputEvent>(); var buffer=stackalloc byte[size];
        while(!token.IsCancellationRequested)
        {
            var read=UInputNative.read(_fd,(nint)buffer,(nuint)size);
            if(read!=size){Thread.Sleep(2);continue;}
            var ev=Marshal.PtrToStructure<UInputNative.InputEvent>((nint)buffer);
            if(ev.Type==UInputNative.EvUinput && ev.Code==UInputNative.UiFfUpload) HandleUpload(ev.Value);
            else if(ev.Type==UInputNative.EvUinput && ev.Code==UInputNative.UiFfErase) HandleErase(ev.Value);
            else _forceFeedback.Process(ev.Type,ev.Code,ev.Value);
        }
    }
    private unsafe void HandleUpload(int requestId)
    {
        var upload=new UInputNative.UInputFfUpload{RequestId=(uint)requestId};
        var request=IoctlReadWrite(200,Marshal.SizeOf<UInputNative.UInputFfUpload>());
        if(UInputNative.ioctl(_fd,request,(nint)(&upload))<0)return;
        upload.Retval=upload.Effect.Type==UInputNative.FfRumble?0:-22;
        if(upload.Retval==0)_forceFeedback.Upload(upload.Effect.Id,upload.Effect.Data.StrongMagnitude,upload.Effect.Data.WeakMagnitude,upload.Effect.Replay.Length);
        UInputNative.ioctl(_fd,IoctlWrite(201,Marshal.SizeOf<UInputNative.UInputFfUpload>()),(nint)(&upload));
    }
    private unsafe void HandleErase(int requestId)
    {
        var erase=new UInputNative.UInputFfErase{RequestId=(uint)requestId};
        if(UInputNative.ioctl(_fd,IoctlReadWrite(202,Marshal.SizeOf<UInputNative.UInputFfErase>()),(nint)(&erase))<0)return;
        _forceFeedback.Erase(erase.EffectId); erase.Retval=0;
        UInputNative.ioctl(_fd,IoctlWrite(203,Marshal.SizeOf<UInputNative.UInputFfErase>()),(nint)(&erase));
    }
    private static nuint IoctlReadWrite(int number,int size)=>(nuint)((3u<<30)|((uint)size<<16)|((uint)'U'<<8)|(uint)number);
    private static nuint IoctlWrite(int number,int size)=>(nuint)((1u<<30)|((uint)size<<16)|((uint)'U'<<8)|(uint)number);
    private static nuint IoctlNone(int number)=>(nuint)(((uint)'U'<<8)|(uint)number);
    private unsafe void Write(int type,int code,int value){var ev=new UInputNative.InputEvent{Type=(ushort)type,Code=(ushort)code,Value=value};if(UInputNative.write(_fd,(nint)(&ev),(nuint)Marshal.SizeOf<UInputNative.InputEvent>())<0)throw new IOException($"write event {type}:{code} failed: errno {Marshal.GetLastPInvokeError()}.");}
    private static int Invert(short value) => value == short.MinValue ? short.MaxValue : -value;
    private static readonly int[] Buttons=[UInputNative.BtnSouth,UInputNative.BtnEast,UInputNative.BtnX,UInputNative.BtnY,UInputNative.BtnTl,UInputNative.BtnTr,UInputNative.BtnThumbL,UInputNative.BtnThumbR,UInputNative.BtnStart,UInputNative.BtnSelect,UInputNative.BtnMode];
    private static readonly int[] Masks=[0x1000,0x2000,0x4000,0x8000,0x0100,0x0200,0x0040,0x0080,0x0010,0x0020,0x0400];
}
