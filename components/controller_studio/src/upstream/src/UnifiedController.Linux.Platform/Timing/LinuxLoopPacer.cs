using System.Diagnostics;
using System.Runtime.InteropServices;
using UnifiedController.Core;

namespace UnifiedController.Linux.Platform.Timing;

public sealed class LinuxMonotonicClock : IMonotonicClock, IMonotonicWaiter
{
    public long Timestamp => Stopwatch.GetTimestamp();
    public double Seconds => Timestamp / (double)Stopwatch.Frequency;
    public void WaitUntil(long timestamp, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        var remaining = timestamp - Stopwatch.GetTimestamp();
        if (remaining <= 0) return;
        if (clock_gettime(1, out var deadline) != 0)
            throw new IOException("clock_gettime failed: " + Marshal.GetLastPInvokeError());
        var total = (long)deadline.Nanoseconds + (long)Math.Ceiling(remaining * 1_000_000_000d / Stopwatch.Frequency);
        deadline.Seconds += (nint)(total / 1_000_000_000);
        deadline.Nanoseconds = (nint)(total % 1_000_000_000);
        int result;
        do { token.ThrowIfCancellationRequested(); result = clock_nanosleep(1, 1, in deadline, 0); }
        while (result == 4); // EINTR: retain the absolute deadline.
        if (result != 0) throw new IOException("clock_nanosleep failed: " + result);
    }
    [StructLayout(LayoutKind.Sequential)] private struct Timespec { public nint Seconds, Nanoseconds; }
    [DllImport("libc", SetLastError = true)] private static extern int clock_gettime(int clock, out Timespec value);
    [DllImport("libc")] private static extern int clock_nanosleep(int clock, int flags, in Timespec value, nint remaining);
}
