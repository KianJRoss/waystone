using System.IO.Pipes;
using System.Security.Cryptography;
using System.Text;

namespace UnifiedController.Linux.App;

internal sealed class LocalInstanceCoordinator : IAsyncDisposable
{
    private readonly FileStream? _lock;
    private readonly string _pipeName;
    private readonly CancellationTokenSource _stop = new();
    private Task? _server;
    public bool IsPrimary => _lock is not null;
    public event Action? ShowRequested;
    public event Action<string>? ActionRequested;

    public LocalInstanceCoordinator(string dataRoot)
    {
        _pipeName = "unified-controller-" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Path.GetFullPath(dataRoot))))[..20];
        try { _lock = new FileStream(Path.Combine(dataRoot, ".app.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException) { return; }
        _server = ServeAsync();
    }

    public async Task RequestShowAsync()
        => await SendAsync("show");
    public async Task RequestActionAsync(string action)
        => await SendAsync("action:" + action);
    private async Task SendAsync(string message)
    {
        try { using var pipe = new NamedPipeClientStream(".", _pipeName, PipeDirection.Out); await pipe.ConnectAsync(1500); var bytes = Encoding.UTF8.GetBytes(message); await pipe.WriteAsync(bytes); }
        catch (TimeoutException) { }
    }
    private async Task ServeAsync()
    {
        while (!_stop.IsCancellationRequested)
            try
            {
                await using var pipe = new NamedPipeServerStream(_pipeName, PipeDirection.In, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous);
                await pipe.WaitForConnectionAsync(_stop.Token);
                using var reader = new StreamReader(pipe, Encoding.UTF8);
                var message = await reader.ReadToEndAsync(_stop.Token);
                if (message == "show") ShowRequested?.Invoke();
                else if (message.StartsWith("action:", StringComparison.Ordinal)) ActionRequested?.Invoke(message[7..]);
            }
            catch (OperationCanceledException) { break; }
            catch (IOException) when (!_stop.IsCancellationRequested) { }
    }
    public async ValueTask DisposeAsync()
    { _stop.Cancel(); if (_server is not null) try { await _server; } catch (OperationCanceledException) { } _lock?.Dispose(); _stop.Dispose(); }
}
