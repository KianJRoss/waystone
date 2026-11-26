using System.Diagnostics;
using UnifiedController.Core;
using UnifiedController.Linux.Platform.Output;
namespace UnifiedController.Linux.Platform.Desktop;

public sealed class UInputDesktopOutput : IDesktopOutputSink
{
    private nint _device; private readonly HashSet<int> _held=[]; private readonly object _gate=new();
    private HashSet<string> _previous=new(StringComparer.OrdinalIgnoreCase); private long _lastMove,_moveStarted;
    private double _remainderX,_remainderY;
    public string? Limitation { get; private set; }
    public UInputDesktopOutput() { try { Create(); } catch(Exception ex) when(ex is IOException or UnauthorizedAccessException or DllNotFoundException) { Limitation=$"Desktop injection unavailable: {ex.Message}"; } }
    public void Apply(DesktopFrame frame)
    {
        lock(_gate) try
        {
            var wanted=new HashSet<int>();
            foreach(var action in frame.Actions)
                if(action.StartsWith("Desktop:Key:",StringComparison.OrdinalIgnoreCase)&&TryResolveKey(action[12..],out var key)) wanted.Add(key);
                else if(action.StartsWith("Desktop:Mouse:",StringComparison.OrdinalIgnoreCase)&&MouseKeys.TryGetValue(action[14..],out key)) wanted.Add(key);
            foreach(var key in _held.Except(wanted).ToArray()) Write(UInputNative.EvKey,key,0);
            foreach(var key in wanted.Except(_held)) Write(UInputNative.EvKey,key,1);
            _held.Clear(); _held.UnionWith(wanted);
            if(frame.Actions.Contains("Desktop:Wheel:Up")&&!_previous.Contains("Desktop:Wheel:Up")) Write(UInputNative.EvRel,UInputNative.RelWheel,1);
            if(frame.Actions.Contains("Desktop:Wheel:Down")&&!_previous.Contains("Desktop:Wheel:Down")) Write(UInputNative.EvRel,UInputNative.RelWheel,-1);
            var now=Stopwatch.GetTimestamp(); var elapsed=_lastMove==0?double.MaxValue:(now-_lastMove)/(double)Stopwatch.Frequency;
            var digitalX=(frame.Actions.Contains("Desktop:Move:Right")?1:0)-(frame.Actions.Contains("Desktop:Move:Left")?1:0);
            var digitalY=(frame.Actions.Contains("Desktop:Move:Down")?1:0)-(frame.Actions.Contains("Desktop:Move:Up")?1:0);
            if((digitalX!=0||digitalY!=0)&&elapsed>=.008)
            { if(_moveStarted==0)_moveStarted=now; var speed=3+(int)Math.Round(Math.Min(21,(now-_moveStarted)/(double)Stopwatch.Frequency*14)); if(digitalX!=0&&digitalY!=0)speed=Math.Max(2,(int)Math.Round(speed*.707)); Move(digitalX*speed,digitalY*speed);_lastMove=now; }
            else if(digitalX==0&&digitalY==0)_moveStarted=0;
            if((frame.PointerX!=0||frame.PointerY!=0)&&elapsed>=.008)
            { var (x,y)=AnalogDelta(frame.PointerX,frame.PointerY,frame.Sensitivity,frame.Deadzone,ref _remainderX,ref _remainderY);Move(x,y);_lastMove=now; }
            else if(frame.PointerX==0&&frame.PointerY==0){_remainderX=_remainderY=0;}
            _previous=new(frame.Actions,StringComparer.OrdinalIgnoreCase); Write(UInputNative.EvSyn,UInputNative.SynReport,0);
        }
        catch(Exception ex) when(ex is IOException or UnauthorizedAccessException) { Limitation=$"Desktop injection failed: {ex.Message}"; ReleaseAllCore(); }
    }
    public static (int X,int Y) AnalogDelta(short rawX,short rawY,double sensitivity,double deadzone,ref double remainderX,ref double remainderY)
    { var x=rawX/(rawX<0?32768d:32767d);var y=rawY/(rawY<0?32768d:32767d);var magnitude=Math.Sqrt(x*x+y*y);if(magnitude<=deadzone){remainderX=remainderY=0;return(0,0);}var scaled=Math.Min(1,(magnitude-deadzone)/Math.Max(.01,1-deadzone));scaled*=scaled;remainderX+=x/magnitude*sensitivity*scaled;remainderY+=-y/magnitude*sensitivity*scaled;var mx=(int)Math.Truncate(remainderX);var my=(int)Math.Truncate(remainderY);remainderX-=mx;remainderY-=my;return(mx,my);}
    public static bool TryResolveKey(string name,out int key)
    { key=0;if(name.Length==1){var ch=char.ToUpperInvariant(name[0]);if(ch is >= 'A' and <= 'Z'){var i="QWERTYUIOP".IndexOf(ch);if(i>=0)key=16+i;else if((i="ASDFGHJKL".IndexOf(ch))>=0)key=30+i;else key=44+"ZXCVBNM".IndexOf(ch);return true;}if(ch is >= '0' and <= '9'){key=ch=='0'?11:2+ch-'1';return true;}}if(name.StartsWith('F')&&int.TryParse(name[1..],out var f)&&f is >=1 and <=12){key=f<=10?58+f:f==11?87:88;return true;}if(name.StartsWith("Numpad",StringComparison.OrdinalIgnoreCase)&&name.Length==7&&name[6] is >= '0' and <= '9'){key=NumpadDigits[name[6]-'0'];return true;}return KeyNames.TryGetValue(name,out key);}
    public void ReleaseAll(){lock(_gate)ReleaseAllCore();}
    private void ReleaseAllCore(){foreach(var key in _held.ToArray())Write(UInputNative.EvKey,key,0);_held.Clear();_previous.Clear();_moveStarted=_lastMove=0;_remainderX=_remainderY=0;if(_device!=0)Write(UInputNative.EvSyn,UInputNative.SynReport,0);}
    public void Dispose(){lock(_gate){if(_device==0)return;ReleaseAllCore();UInputNative.libevdev_uinput_destroy(_device);_device=0;}}
    private void Move(int x,int y){if(x!=0)Write(UInputNative.EvRel,UInputNative.RelX,x);if(y!=0)Write(UInputNative.EvRel,UInputNative.RelY,y);}
    private void Create(){var d=UInputNative.libevdev_new();if(d==0)throw new IOException("libevdev_new failed for desktop output.");try{UInputNative.libevdev_set_name(d,"Unified Controller Desktop Output");UInputNative.libevdev_set_phys(d,"unified-controller/desktop/1");UInputNative.libevdev_set_uniq(d,"unified-controller-desktop");UInputNative.libevdev_set_id_bustype(d,UInputNative.BusVirtual);UInputNative.libevdev_set_id_vendor(d,0x1209);UInputNative.libevdev_set_id_product(d,0x5543);UInputNative.Check(UInputNative.libevdev_enable_event_type(d,UInputNative.EvKey),"enable desktop keys");foreach(var key in KeyNames.Values.Concat(MouseKeys.Values).Concat(Enumerable.Range(2,10)).Concat(Enumerable.Range(16,10)).Concat(Enumerable.Range(30,9)).Concat(Enumerable.Range(44,7)).Distinct())UInputNative.Check(UInputNative.libevdev_enable_event_code(d,UInputNative.EvKey,(uint)key,0),$"enable desktop key {key}");UInputNative.Check(UInputNative.libevdev_enable_event_type(d,UInputNative.EvRel),"enable desktop relative events");foreach(var rel in new[]{0,1,8})UInputNative.Check(UInputNative.libevdev_enable_event_code(d,UInputNative.EvRel,(uint)rel,0),$"enable desktop rel {rel}");UInputNative.Check(UInputNative.libevdev_uinput_create_from_device(d,UInputNative.UinputOpenManaged,out _device),"create desktop uinput");}finally{UInputNative.libevdev_free(d);}}
    private void Write(int type,int code,int value){if(_device!=0)UInputNative.Check(UInputNative.libevdev_uinput_write_event(_device,(uint)type,(uint)code,value),"write desktop event");}
    private static readonly Dictionary<string,int> MouseKeys=new(StringComparer.OrdinalIgnoreCase){{"Left",272},{"Right",273},{"Middle",274},{"X1",275},{"X2",276}};
    private static readonly int[] NumpadDigits=[82,79,80,81,75,76,77,71,72,73];
    private static readonly Dictionary<string,int> KeyNames=new(StringComparer.OrdinalIgnoreCase){{"Escape",1},{"Esc",1},{"Tab",15},{"Enter",28},{"Space",57},{"Backspace",14},{"Delete",111},{"Insert",110},{"Home",102},{"End",107},{"Page Up",104},{"Page Down",109},{"Left",105},{"Up",103},{"Right",106},{"Down",108},{"Left Shift",42},{"Right Shift",54},{"Left Ctrl",29},{"Right Ctrl",97},{"Left Alt",56},{"Right Alt",100},{"Windows",125},{"Left Win",125},{"Right Win",126},{"Menu",127},{"Menu Key",127},{"Print Screen",99},{"Scroll Lock",70},{"Pause",119},{"Num Lock",69},{"Numpad Divide",98},{"Numpad Multiply",55},{"Numpad Minus",74},{"Numpad Plus",78},{"Numpad Enter",96},{"Numpad Decimal",83},{"Volume Mute",113},{"Volume Down",114},{"Volume Up",115},{"Media Previous",165},{"Media Next",163},{"Media Play Pause",164},{"F1",59},{"F2",60},{"F3",61},{"F4",62},{"F5",63},{"F6",64},{"F7",65},{"F8",66},{"F9",67},{"F10",68},{"F11",87},{"F12",88}};
}
