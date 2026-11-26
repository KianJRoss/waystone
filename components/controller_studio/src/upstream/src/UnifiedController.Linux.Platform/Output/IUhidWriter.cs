using System.Runtime.InteropServices;

namespace UnifiedController.Linux.Platform.Output;

/// <summary>Packet-oriented UHID transport. Constructors must not open a device.
/// WaitReadable bounds idle polling; Read returns zero when no event is available. A short Write is a failure.</summary>
public interface IUhidWriter : IDisposable
{
    void Open();
    int Write(ReadOnlySpan<byte> packet);
    bool WaitReadable(int timeoutMilliseconds);
    int Read(Span<byte> packet);
}

/// <summary>Production transport. Only UhidSonyOutput.StartAsync calls Open; fake checks inject another writer.</summary>
public sealed class LinuxUhidWriter : IUhidWriter
{
    private int _fd = -1;
    public void Open()
    {
        if (_fd >= 0) return;
        if (!OperatingSystem.IsLinux() || !BitConverter.IsLittleEndian)
            throw new PlatformNotSupportedException("This UHID transport requires little-endian Linux.");
        _fd = open("/dev/uhid", 2 | 0x800 | 0x80000); // O_RDWR | O_NONBLOCK | O_CLOEXEC
        if (_fd < 0) throw new IOException($"open /dev/uhid failed: errno {Marshal.GetLastPInvokeError()}.");
    }
    public unsafe int Write(ReadOnlySpan<byte> packet)
    {
        if (_fd < 0) throw new ObjectDisposedException(nameof(LinuxUhidWriter));
        fixed (byte* data = packet)
        {
            nint result;
            do { result = write(_fd, (nint)data, (nuint)packet.Length); }
            while (result < 0 && Marshal.GetLastPInvokeError() == 4); // EINTR, no bytes consumed
            if (result < 0) throw new IOException($"UHID write failed: errno {Marshal.GetLastPInvokeError()}.");
            return checked((int)result);
        }
    }
    public unsafe int Read(Span<byte> packet)
    {
        if (_fd < 0) throw new ObjectDisposedException(nameof(LinuxUhidWriter));
        fixed (byte* data = packet)
        {
            var result = read(_fd, (nint)data, (nuint)packet.Length);
            if (result < 0)
            {
                var error = Marshal.GetLastPInvokeError();
                if (error is 11 or 4) return 0; // EAGAIN / EINTR
                throw new IOException($"UHID read failed: errno {error}.");
            }
            if (result == 0) throw new EndOfStreamException("UHID device closed.");
            return checked((int)result);
        }
    }
    public bool WaitReadable(int timeoutMilliseconds)
    {
        var fd = Volatile.Read(ref _fd);
        if (fd < 0) throw new ObjectDisposedException(nameof(LinuxUhidWriter));
        var descriptor = new PollDescriptor { FileDescriptor = fd, Events = PollIn };
        int result;
        do { result = poll(ref descriptor, 1, Math.Clamp(timeoutMilliseconds, 0, 1000)); }
        while (result < 0 && Marshal.GetLastPInvokeError() == 4); // EINTR
        if (result < 0) throw new IOException($"UHID poll failed: errno {Marshal.GetLastPInvokeError()}.");
        return result > 0 && (descriptor.ReturnedEvents & (PollIn | PollError | PollHangup | PollInvalid)) != 0;
    }
    public void Dispose() { var fd = Interlocked.Exchange(ref _fd, -1); if (fd >= 0) close(fd); }
    [StructLayout(LayoutKind.Sequential)]
    private struct PollDescriptor
    {
        public int FileDescriptor;
        public short Events;
        public short ReturnedEvents;
    }
    private const short PollIn = 0x001;
    private const short PollError = 0x008;
    private const short PollHangup = 0x010;
    private const short PollInvalid = 0x020;
    [DllImport("libc", SetLastError = true)] private static extern int open(string path, int flags);
    [DllImport("libc", SetLastError = true)] private static extern int close(int fd);
    [DllImport("libc", SetLastError = true)] private static extern int poll(ref PollDescriptor fds, nuint nfds, int timeout);
    [DllImport("libc", SetLastError = true)] private static extern nint write(int fd, nint data, nuint count);
    [DllImport("libc", SetLastError = true)] private static extern nint read(int fd, nint data, nuint count);
}
