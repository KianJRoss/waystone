using System.Diagnostics;
using System.Runtime.InteropServices;

namespace UnifiedController.Linux.Platform.Input;

/// <summary>
/// Some pads in XInput mode (GameSir Cyclone 2 at 3537:100b) have no Share button in the gamepad
/// report: Share types Windows Game Bar shortcuts (Win+Alt+PrintScreen, Alt+PrintScreen) on a
/// keyboard interface of the same USB device. That keyboard is grabbed while the controller is open,
/// so the desktop (and Lan Mouse) never see the shortcuts. The firmware holds Alt+Super down for as long
/// as Share is held (then taps Alt+PrintScreen on release), so Share is reported held while Super is
/// down; a PrintScreen press without that becomes a short pulse.
/// </summary>
internal sealed class CompanionShareKeys : IDisposable
{
    private const int KeySysRq = 99, KeyPrint = 210, KeyLeftMeta = 125, EvKey = 1;
    private static readonly long PulseTicks = Stopwatch.Frequency / 12; // ~83 ms, several forwarding frames
    private readonly List<int> _fds = new();
    private long _pulseUntil;
    private bool _held;
    private long _releasedAt = long.MinValue / 2;
    private readonly byte[] _buffer = new byte[24 * 32]; // struct input_event is 24 bytes on 64-bit

    private CompanionShareKeys() { }

    /// <summary>Grab the share-key siblings of the controller at eventPath; null when there are none.</summary>
    public static CompanionShareKeys? Open(string eventPath)
    {
        var usb = UsbDevice(Path.GetFileName(eventPath));
        if (usb is null) return null;
        var keys = new CompanionShareKeys();
        foreach (var dir in Directory.EnumerateDirectories("/sys/class/input", "event*"))
        {
            var name = Path.GetFileName(dir);
            if (name == Path.GetFileName(eventPath) || UsbDevice(name) != usb || !HasKey(dir, KeySysRq) && !HasKey(dir, KeyPrint)) continue;
            var fd = open("/dev/input/" + name, ORdonly | ONonblock);
            if (fd < 0) continue;
            if (ioctl(fd, EviocGrab, 1) == 0) keys._fds.Add(fd);
            else close(fd);
        }
        if (keys._fds.Count > 0) return keys;
        keys.Dispose();
        return null;
    }

    /// <summary>Drain pending key events; true while Share is held or a Share pulse is active.</summary>
    public bool Pressed()
    {
        var now = Stopwatch.GetTimestamp();
        foreach (var fd in _fds)
        {
            int count;
            while ((count = (int)read(fd, _buffer, _buffer.Length)) > 0)
                for (var offset = 0; offset + 24 <= count; offset += 24)
                {
                    var type = BitConverter.ToUInt16(_buffer, offset + 16);
                    var code = BitConverter.ToUInt16(_buffer, offset + 18);
                    var value = BitConverter.ToInt32(_buffer, offset + 20);
                    if (type != EvKey) continue;
                    if (code == KeyLeftMeta && value != 2) { if (_held && value == 0) _releasedAt = now; _held = value == 1; }
                    // The Alt+PrintScreen tap that follows a release is part of the same press, not a new one.
                    else if (value == 1 && code is KeySysRq or KeyPrint && !_held && now - _releasedAt > PulseTicks * 3) _pulseUntil = now + PulseTicks;
                }
        }
        return _held || now < _pulseUntil;
    }

    public void Dispose()
    {
        foreach (var fd in _fds) { ioctl(fd, EviocGrab, 0); close(fd); }
        _fds.Clear();
    }

    /// <summary>The USB device directory (the ancestor holding idVendor) of an input event node.</summary>
    private static string? UsbDevice(string eventName)
    {
        var resolved = realpath($"/sys/class/input/{eventName}/device/device", 0);
        if (resolved == 0) return null;
        try
        {
            for (var d = new DirectoryInfo(Marshal.PtrToStringUTF8(resolved)!); d is not null; d = d.Parent)
                if (File.Exists(Path.Combine(d.FullName, "idVendor"))) return d.FullName;
        }
        finally { free(resolved); }
        return null;
    }

    /// <summary>Whether capabilities/key (hex words, most significant first) advertises the key code.</summary>
    private static bool HasKey(string inputDir, int code)
    {
        try
        {
            var words = File.ReadAllText(Path.Combine(inputDir, "device/capabilities/key")).Trim().Split(' ');
            var index = words.Length - 1 - code / 64;
            return index >= 0 && (Convert.ToUInt64(words[index], 16) >> (code % 64) & 1) != 0;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or FormatException) { return false; }
    }

    private const int ORdonly = 0, ONonblock = 0x800;
    private const ulong EviocGrab = 0x40044590;
    [DllImport("libc", SetLastError = true)] private static extern int open(string path, int flags);
    [DllImport("libc", SetLastError = true)] private static extern int ioctl(int fd, ulong request, int value);
    [DllImport("libc", SetLastError = true)] private static extern int close(int fd);
    [DllImport("libc", SetLastError = true)] private static extern nint read(int fd, byte[] buffer, nint count);
    [DllImport("libc", SetLastError = true)] private static extern nint realpath(string path, nint resolved);
    [DllImport("libc")] private static extern void free(nint pointer);
}
