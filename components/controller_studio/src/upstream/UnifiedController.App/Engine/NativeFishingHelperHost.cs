using System.Diagnostics;
using System.IO;

namespace UnifiedController.App.Engine;

public sealed class NativeFishingHelperHost : IDisposable
{
    private readonly string _rootDirectory;
    private readonly CancellationTokenSource _stop = new();
    private Thread? _supervisorThread;
    private Process? _process;

    public bool IsRunning
    {
        get
        {
            try { return _process is { HasExited: false }; }
            catch { return false; }
        }
    }

    public int? ProcessId => IsRunning ? _process?.Id : null;
    public string? LastError { get; private set; }

    public NativeFishingHelperHost(string rootDirectory)
    {
        _rootDirectory = rootDirectory;
    }

    public void Start()
    {
        if (_supervisorThread is { IsAlive: true }) return;
        _supervisorThread = new Thread(Supervise)
        {
            IsBackground = true,
            Name = "FishingHelperSupervisor",
        };
        _supervisorThread.Start();
    }

    private void Supervise()
    {
        while (!_stop.IsCancellationRequested)
        {
            if (!IsRunning)
            {
                DisposeProcess();
                StartWorker();
            }

            _stop.Token.WaitHandle.WaitOne(IsRunning ? 1000 : 3000);
        }
    }

    private void StartWorker()
    {
        try
        {
            var python = @"C:\Python314\pythonw.exe";
            var script = Path.Combine(_rootDirectory, "modules", "fishing", "fishing_helper.py");
            if (!File.Exists(python)) throw new FileNotFoundException("Python runtime is unavailable", python);
            if (!File.Exists(script)) throw new FileNotFoundException("Fishing Helper worker is unavailable", script);

            var start = new ProcessStartInfo(python)
            {
                WorkingDirectory = _rootDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            start.ArgumentList.Add(script);
            start.ArgumentList.Add("--headless");
            start.ArgumentList.Add("--parent-pid");
            start.ArgumentList.Add(Environment.ProcessId.ToString());
            _process = Process.Start(start) ?? throw new InvalidOperationException("Fishing Helper did not start");
            LastError = null;
        }
        catch (Exception ex)
        {
            LastError = ex.Message;
            Debug.WriteLine($"Fishing Helper start failed: {ex}");
        }
    }

    private void DisposeProcess()
    {
        try { _process?.Dispose(); } catch { }
        _process = null;
    }

    public void Dispose()
    {
        _stop.Cancel();
        try { _supervisorThread?.Join(1500); } catch { }

        try
        {
            if (_process is { HasExited: false })
                _process.Kill(entireProcessTree: true);
        }
        catch { }

        DisposeProcess();
        _stop.Dispose();
    }
}
