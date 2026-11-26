using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using UnifiedController.Core;
using UnifiedController.App.Engine;

namespace UnifiedController.Linux.Platform.Desktop;

/// <summary>Optional, non-exclusive physical keyboard/mouse capture through libevdev.</summary>
public sealed partial class EvdevKeyboardMouseSource : IDesktopInputSource, IDesktopInputDiagnostics
{
    private readonly object _gate = new();
    private readonly Dictionary<string, Device> _devices = [];
    private readonly Dictionary<string, HashSet<int>> _keys = [];
    private readonly Dictionary<string, DesktopMouseState> _mice = [];
    private readonly List<string> _limitations = [];
    private readonly CancellationTokenSource _stop = new();
    private readonly Task _worker;
    private bool _keyboardEnabled, _mouseEnabled, _disposed;
    private int _dx, _dy;
    private readonly HashSet<string> _pulses = new(StringComparer.OrdinalIgnoreCase);

    public EvdevKeyboardMouseSource()
    {
        _limitations.Add("Desktop capture is disabled by default; enable keyboard or mouse capture explicitly.");
        _worker = Task.Run(WorkerAsync);
    }
    public bool KeyboardEnabled { get { lock (_gate) return _keyboardEnabled; } set { lock (_gate) { _keyboardEnabled = value; if (!value) ClearKeyboards(); } } }
    public bool MouseEnabled { get { lock (_gate) return _mouseEnabled; } set { lock (_gate) { _mouseEnabled = value; if (!value) ClearMice(); } } }
    public string? LastInputSource { get; private set; }
    public long LastInputTimestamp { get; private set; }
    public IReadOnlyList<string> Limitations { get { lock (_gate) return _limitations.ToArray(); } }
    public string? Limitation => Limitations.Count == 0 ? null : string.Join("; ", Limitations);

    public InputSnapshot Drain()
    {
        lock (_gate)
        {
            var allKeys = new HashSet<int>(_keys.Values.SelectMany(x => x));
            var allButtons = new HashSet<string>(_mice.Values.SelectMany(x => x.Buttons), StringComparer.OrdinalIgnoreCase);
            var result = new InputSnapshot(allKeys, allButtons, new HashSet<string>(_pulses, StringComparer.OrdinalIgnoreCase), _dx, _dy,
                _keys.ToDictionary(x => x.Key, x => new HashSet<int>(x.Value)),
                _mice.ToDictionary(x => x.Key, x => new DesktopMouseState(x.Value.DeltaX, x.Value.DeltaY,
                    new HashSet<string>(x.Value.Buttons, StringComparer.OrdinalIgnoreCase), new HashSet<string>(x.Value.Pulses, StringComparer.OrdinalIgnoreCase))));
            _dx = _dy = 0; _pulses.Clear();
            foreach (var id in _mice.Keys.ToArray()) _mice[id] = new(0, 0, _mice[id].Buttons, []);
            return result;
        }
    }

    private async Task WorkerAsync()
    {
        try
        {
            var nextDiscovery = 0L;
            while (!_stop.IsCancellationRequested)
            {
                if (!KeyboardEnabled && !MouseEnabled)
                { await Task.Delay(100, _stop.Token).ConfigureAwait(false); continue; }
                var now = Stopwatch.GetTimestamp();
                if (now >= nextDiscovery) { Discover(); nextDiscovery = now + Stopwatch.Frequency; }
                PollDevices();
                await Task.Delay(4, _stop.Token).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException) when (_stop.IsCancellationRequested) { }
    }

    // Discovery and filesystem access never occur on Drain(), the runtime polling path.
    private void Discover()
    {
        string[] paths;
        try { paths = Directory.GetFiles("/dev/input", "event*"); }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or DirectoryNotFoundException)
        { lock (_gate) SetLimitation("/dev/input", $"evdev discovery unavailable: {ex.Message}"); return; }
        lock (_gate)
        {
            var present = paths.ToHashSet(StringComparer.Ordinal);
            foreach (var old in _devices.Where(x => !present.Contains(x.Value.Path)).Select(x => x.Key).ToArray()) Remove(old, "disconnected");
            foreach (var path in paths)
            {
                if (_devices.Values.Any(x => x.Path == path)) continue;
                try
                {
                    var device = Device.Open(path);
                    if (device is null) continue;
                    if (device.IsInjected) { device.Dispose(); continue; }
                    _devices[device.Id] = device;
                    _limitations.RemoveAll(x => x.StartsWith(path + ":", StringComparison.Ordinal));
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
                { SetLimitation(path, $"{path}: cannot read evdev input ({ex.Message})"); }
            }
            if (!_keyboardEnabled && !_mouseEnabled) SetLimitation("disabled", "Desktop capture is disabled by default; enable keyboard or mouse capture explicitly.");
            else _limitations.RemoveAll(x => x.StartsWith("Desktop capture is disabled", StringComparison.Ordinal));
        }
    }

    private void PollDevices()
    {
        lock (_gate)
        {
            foreach (var pair in _devices.ToArray())
            {
                try
                {
                    while (pair.Value.Next(out var ev, out var sync))
                    {
                        if (sync == Evdev.ReadStatusSync)
                        {
                            Clear(pair.Key);
                            ProcessEvent(pair.Key, pair.Value.Keyboard && _keyboardEnabled, pair.Value.Mouse && _mouseEnabled,
                                ev.Type, ev.Code, ev.Value);
                            while (pair.Value.NextSync(out var changed))
                                ProcessEvent(pair.Key, pair.Value.Keyboard && _keyboardEnabled, pair.Value.Mouse && _mouseEnabled,
                                    changed.Type, changed.Code, changed.Value);
                            continue;
                        }
                        ProcessEvent(pair.Key, pair.Value.Keyboard && _keyboardEnabled, pair.Value.Mouse && _mouseEnabled, ev.Type, ev.Code, ev.Value);
                    }
                }
                catch (IOException ex) { Remove(pair.Key, ex.Message); }
            }
        }
    }

    internal void ProcessEvent(string id, bool keyboard, bool mouse, ushort type, ushort code, int value)
    {
        if (type == Evdev.EvSyn && code == Evdev.SynDropped) { Clear(id); return; }
        var accepted = false;
        string? control = null;
        if (keyboard && type == Evdev.EvKey && EvdevKeyToVirtualKey(code, out var vk))
        { if (!_keys.TryGetValue(id, out var set)) _keys[id] = set = []; if (value == 0) set.Remove(vk); else set.Add(vk); accepted = value != 0; if (NativeProfileRuntime.KeyName(vk) is { } name) control = $"KeyboardDevice:{id}:{name}"; }
        if (mouse)
        {
            if (!_mice.TryGetValue(id, out var state)) _mice[id] = state = new(0, 0, [], []);
            var buttons = state.Buttons; var pulses = state.Pulses; var x = state.DeltaX; var y = state.DeltaY;
            if (type == Evdev.EvRel && code == Evdev.RelX) { x += value; _dx += value; accepted = true; control = $"MouseDevice:{id}:Movement"; }
            else if (type == Evdev.EvRel && code == Evdev.RelY) { y += value; _dy += value; accepted = true; control = $"MouseDevice:{id}:Movement"; }
            else if (type == Evdev.EvRel && code is Evdev.RelWheel or Evdev.RelHWheel)
            { var prefix = code == Evdev.RelWheel ? "Wheel" : "HWheel"; var pulse = prefix + (value > 0 ? " Up" : " Down"); if (value != 0) { pulses.Add(pulse); _pulses.Add(pulse); } accepted = value != 0; control = $"MouseDevice:{id}:{pulse}"; }
            else if (type == Evdev.EvKey && MouseButton(code) is { } button)
            { if (value == 0) buttons.Remove(button); else buttons.Add(button); accepted = value != 0; control = $"MouseDevice:{id}:{button}"; }
            _mice[id] = new(x, y, buttons, pulses);
        }
        if (accepted && control is not null) { LastInputSource = control; LastInputTimestamp = Stopwatch.GetTimestamp(); }
    }

    public static bool EvdevKeyToVirtualKey(ushort code, out int vk)
    {
        vk = code switch
        {
            >= 2 and <= 10 => '1' + code - 2, 11 => '0', >= 16 and <= 25 => "QWERTYUIOP"[code-16],
            >= 30 and <= 38 => "ASDFGHJKL"[code-30], >= 44 and <= 50 => "ZXCVBNM"[code-44],
            1=>0x1B, 14=>0x08, 15=>0x09, 28=>0x0D, 57=>0x20, 111=>0x2E, 110=>0x2D,
            102=>0x24, 107=>0x23, 104=>0x21, 109=>0x22, 105=>0x25, 103=>0x26, 106=>0x27, 108=>0x28,
            42=>0xA0, 54=>0xA1, 29=>0xA2, 97=>0xA3, 56=>0xA4, 100=>0xA5, 125=>0x5B, 126=>0x5C,
            12=>0xBD, 13=>0xBB, 26=>0xDB, 27=>0xDD, 39=>0xBA, 40=>0xDE, 41=>0xC0,
            43=>0xDC, 51=>0xBC, 52=>0xBE, 53=>0xBF, 58=>0x14,
            99=>0x2C, 70=>0x91, 119=>0x13, 69=>0x90, 127=>0x5D,
            98=>0x6F, 55=>0x6A, 74=>0x6D, 78=>0x6B, 96=>0x0D, 83=>0x6E,
            82=>0x60, 79=>0x61, 80=>0x62, 81=>0x63, 75=>0x64, 76=>0x65,
            77=>0x66, 71=>0x67, 72=>0x68, 73=>0x69,
            >= 59 and <= 68 => 0x70 + code-59, 87=>0x7A, 88=>0x7B,
            113=>0xAD, 114=>0xAE, 115=>0xAF, 165=>0xB1, 163=>0xB0, 164=>0xB3, _=>0
        };
        return vk != 0;
    }
    private static string? MouseButton(ushort code) => code switch { 272=>"Left",273=>"Right",274=>"Middle",275=>"X1",276=>"X2",_=>null };
    private void SetLimitation(string key, string text) { _limitations.RemoveAll(x => x.StartsWith(key + ":", StringComparison.Ordinal)); if (!_limitations.Contains(text)) _limitations.Add(text); }
    private void Clear(string id) { _keys.Remove(id); _mice.Remove(id); }
    private void ClearKeyboards() => _keys.Clear();
    private void ClearMice() { _mice.Clear(); _dx=_dy=0; _pulses.Clear(); }
    private void Remove(string id, string reason) { if (_devices.Remove(id, out var d)) { d.Dispose(); Clear(id); SetLimitation(d.Path, $"{d.Path}: disconnected ({reason})"); } }

    public void Dispose()
    {
        lock (_gate) { if (_disposed) return; _disposed=true; ClearKeyboards(); ClearMice(); }
        _stop.Cancel(); try { _worker.GetAwaiter().GetResult(); } catch (OperationCanceledException) { }
        lock (_gate) { foreach (var d in _devices.Values) d.Dispose(); _devices.Clear(); }
        _stop.Dispose();
    }

    private sealed class Device : IDisposable
    {
        private int _fd; private nint _dev;
        public string Id { get; } public string Path { get; } public bool Keyboard { get; } public bool Mouse { get; }
        public bool IsInjected { get; }
        private Device(int fd,nint dev,string id,string path,bool keyboard,bool mouse,bool injected) { _fd=fd;_dev=dev;Id=id;Path=path;Keyboard=keyboard;Mouse=mouse;IsInjected=injected; }
        public static Device? Open(string path)
        {
            var fd=Evdev.open(path,Evdev.O_RDONLY|Evdev.O_NONBLOCK); if(fd<0) throw new IOException($"errno {Marshal.GetLastPInvokeError()}");
            if(Evdev.libevdev_new_from_fd(fd,out var dev)<0) { Evdev.close(fd); throw new IOException("libevdev rejected device"); }
            var keyboard=Enumerable.Range(1,255).Count(k=>Evdev.libevdev_has_event_code(dev,Evdev.EvKey,(uint)k)!=0)>20;
            var mouse=Evdev.libevdev_has_event_code(dev,Evdev.EvRel,Evdev.RelX)!=0 && Evdev.libevdev_has_event_code(dev,Evdev.EvRel,Evdev.RelY)!=0;
            if(!keyboard&&!mouse) { Evdev.libevdev_free(dev);Evdev.close(fd);return null; }
            var name=Evdev.String(Evdev.libevdev_get_name(dev)); var phys=Evdev.String(Evdev.libevdev_get_phys(dev)); var uniq=Evdev.String(Evdev.libevdev_get_uniq(dev));
            var injected=name.StartsWith("Unified Controller",StringComparison.OrdinalIgnoreCase)||phys.Contains("unified-controller/",StringComparison.OrdinalIgnoreCase)||uniq.Contains("unified-controller",StringComparison.OrdinalIgnoreCase)||name.Contains("LAN Mouse",StringComparison.OrdinalIgnoreCase);
            var topology = uniq.Length > 0 ? "uniq:"+uniq : phys.Length > 0 ? "phys:"+phys : "path:"+path;
            var material=$"{Evdev.libevdev_get_id_vendor(dev):x4}:{Evdev.libevdev_get_id_product(dev):x4}|{topology}|{name}";
            var id="raw-"+Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(material))).ToLowerInvariant()[..24];
            return new(fd,dev,id,path,keyboard,mouse,injected);
        }
        public bool Next(out Evdev.InputEvent ev,out int status) { status=Evdev.libevdev_next_event(_dev,Evdev.ReadFlagNormal,out ev); return status is Evdev.ReadStatusSuccess or Evdev.ReadStatusSync; }
        public bool NextSync(out Evdev.InputEvent ev) => Evdev.libevdev_next_event(_dev,Evdev.ReadFlagSync,out ev)==Evdev.ReadStatusSync;
        public void Dispose(){if(_dev!=0)Evdev.libevdev_free(_dev);if(_fd>=0)Evdev.close(_fd);_dev=0;_fd=-1;}
    }

    private static partial class Evdev
    {
        internal const int O_RDONLY=0,O_NONBLOCK=0x800,ReadFlagSync=1,ReadFlagNormal=2,ReadStatusSuccess=0,ReadStatusSync=1;
        internal const ushort EvSyn=0,EvKey=1,EvRel=2,SynDropped=3,RelX=0,RelY=1,RelHWheel=6,RelWheel=8;
        [StructLayout(LayoutKind.Sequential)] internal struct TimeVal { internal nint Sec, Usec; }
        [StructLayout(LayoutKind.Sequential)] internal struct InputEvent { internal TimeVal Time; internal ushort Type,Code; internal int Value; }
        [LibraryImport("libc",SetLastError=true,StringMarshalling=StringMarshalling.Utf8)] internal static partial int open(string path,int flags);
        [LibraryImport("libc")] internal static partial int close(int fd);
        [LibraryImport("libevdev.so.2")] internal static partial int libevdev_new_from_fd(int fd,out nint dev);
        [LibraryImport("libevdev.so.2")] internal static partial void libevdev_free(nint dev);
        [LibraryImport("libevdev.so.2")] internal static partial int libevdev_next_event(nint dev,uint flags,out InputEvent ev);
        [LibraryImport("libevdev.so.2")] internal static partial int libevdev_has_event_code(nint dev,uint type,uint code);
        [LibraryImport("libevdev.so.2")] internal static partial nint libevdev_get_name(nint dev);
        [LibraryImport("libevdev.so.2")] internal static partial nint libevdev_get_phys(nint dev);
        [LibraryImport("libevdev.so.2")] internal static partial nint libevdev_get_uniq(nint dev);
        [LibraryImport("libevdev.so.2")] internal static partial int libevdev_get_id_vendor(nint dev);
        [LibraryImport("libevdev.so.2")] internal static partial int libevdev_get_id_product(nint dev);
        internal static string String(nint value)=>Marshal.PtrToStringUTF8(value)??"";
    }
}
