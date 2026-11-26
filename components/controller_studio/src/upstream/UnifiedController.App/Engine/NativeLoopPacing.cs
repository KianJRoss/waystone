namespace UnifiedController.App.Engine;

public static class NativeLoopPacing
{
    public static int IntervalMilliseconds(bool controllerConnected, bool captureEnabled, bool pendingWork) =>
        controllerConnected || captureEnabled || pendingWork ? 1 : 10;

    public static long NextDeadline(long previousDeadline, long now, long intervalTicks, long frequency)
    {
        // An overloaded iteration must still yield; repeatedly missing a 1 ms
        // deadline must not turn an input thread into a CPU-saturating loop.
        var minimumWait = Math.Max(1, frequency / 10000);
        return Math.Max(previousDeadline + intervalTicks, now + minimumWait);
    }
}
