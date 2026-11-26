namespace UnifiedController.App.Engine;

/// <summary>Atomic process ownership, released automatically after a crash.</summary>
public sealed class NativeInstanceLock : IDisposable
{
    private readonly Mutex _mutex;
    public bool Acquired { get; private set; }

    public NativeInstanceLock(string name)
    {
        _mutex = new Mutex(false, name);
        try { Acquired = _mutex.WaitOne(0); }
        catch (AbandonedMutexException) { Acquired = true; }
    }

    public void Dispose()
    {
        if (Acquired) { _mutex.ReleaseMutex(); Acquired = false; }
        _mutex.Dispose();
    }
}
