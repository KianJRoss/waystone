using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace UnifiedController.Linux.App;

public class ControllerOperationException : Exception
{
    public string Code { get; }

    public ControllerOperationException(string code) : base(code)
    {
        Code = code;
    }

    public ControllerOperationException(string code, string message) : base(message)
    {
        Code = code;
    }
}

public sealed class ControllerControlServer : IAsyncDisposable, IDisposable
{
    public const int ProtocolVersion = 1;
    public const int MaxRequestBytes = 131072;
    public const int MaxResponseBytes = 131072;
    private const int MaxCacheEntries = 128;

    private static readonly HashSet<string> AllowedOperations = new(StringComparer.Ordinal)
    {
        "status",
        "output_get",
        "output_set",
        "output_game_state",
        "profiles",
        "profile_get",
        "profile_select",
        "profile_update",
        "cancel_macros",
        "command_capabilities",
        "motion_recenter",
        "motion_calibrate",
        "rumble_test",
        "rumble_stop",
        "profile_command_capabilities",
        "profile_create",
        "profile_copy",
        "profile_rename",
        "profile_delete",
        "profile_import",
        "profile_export",
        "profile_set_field",
        "device_command_capabilities",
        "joycon_pair",
        "joycon_unpair",
        "device_capture"
    };

    private static readonly Regex SafeErrorCodeRegex = new(@"^ERR_[A-Z0-9_]{1,60}$", RegexOptions.Compiled);
    private static readonly Regex Hex32Regex = new(@"^[0-9a-fA-F]{32}$", RegexOptions.Compiled);

    private readonly Socket _listener;
    private readonly string _path;
    private readonly Func<JsonObject, Task<JsonObject>> _handler;
    private readonly CancellationTokenSource _stop = new();
    private readonly Task _worker;
    private readonly SemaphoreSlim _handlerLock = new(1, 1);

    private readonly record struct CachedResponse(byte[] RequestHash, byte[] ResponseBytes);
    private readonly record struct PendingRequest(byte[] RequestHash, TaskCompletionSource<byte[]> Completion);

    private readonly Dictionary<string, CachedResponse> _responseCache = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, PendingRequest> _pendingRequests = new(StringComparer.OrdinalIgnoreCase);
    private readonly LinkedList<string> _lruKeys = new();
    private readonly object _cacheLock = new();
    private int _disposeStarted;

    public ControllerControlServer(string runtimeDirectory, Func<JsonObject, Task<JsonObject>> handler)
    {
        if (!OperatingSystem.IsLinux()) throw new PlatformNotSupportedException();
        ArgumentException.ThrowIfNullOrWhiteSpace(runtimeDirectory);
        ArgumentNullException.ThrowIfNull(handler);

        RequirePrivateDirectory(runtimeDirectory);
        var directory = Path.Combine(runtimeDirectory, "waystone-unified-controller");

        // Validate existing dir first without modifying permissions; create 0700 only if absent.
        if (Path.Exists(directory))
        {
            RequirePrivateDirectory(directory);
        }
        else
        {
            Directory.CreateDirectory(directory, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
            RequirePrivateDirectory(directory);
        }

        _path = Path.Combine(directory, "control.sock");
        _handler = handler;
        _listener = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);

        // Never remove an existing/stale endpoint: lifecycle owner must resolve it explicitly.
        try
        {
            _listener.Bind(new UnixDomainSocketEndPoint(_path));
            File.SetUnixFileMode(_path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
            _listener.Listen(16);
        }
        catch
        {
            _listener.Dispose();
            throw;
        }

        _worker = ServeAsync();
    }

    public string SocketPath => _path;

    private async Task ServeAsync()
    {
        while (!_stop.IsCancellationRequested)
        {
            Socket peer;
            try
            {
                peer = await _listener.AcceptAsync(_stop.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) { break; }
            catch (ObjectDisposedException) { break; }

            _ = HandleClientAsync(peer);
        }
    }

    private async Task HandleClientAsync(Socket peer)
    {
        using (peer)
        using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(_stop.Token))
        {
            deadline.CancelAfter(TimeSpan.FromSeconds(5));
            try
            {
                if (!SameUser(peer)) return;

                var bytes = new byte[MaxRequestBytes + 1];
                var length = 0;
                while (length <= MaxRequestBytes)
                {
                    var n = await peer.ReceiveAsync(bytes.AsMemory(length, bytes.Length - length), SocketFlags.None, deadline.Token).ConfigureAwait(false);
                    if (n == 0) break;
                    length += n;
                    if (Array.IndexOf(bytes, (byte)'\n', 0, length) >= 0) break;
                }

                if (length == 0 || length > MaxRequestBytes) return;
                var newlineIndex = Array.IndexOf(bytes, (byte)'\n', 0, length);
                if (newlineIndex < 0) return;

                var requestSpan = bytes.AsSpan(0, newlineIndex);
                if (requestSpan.EndsWith((ReadOnlySpan<byte>)"\r"u8))
                {
                    requestSpan = requestSpan[..^1];
                }

                var requestHash = SHA256.HashData(requestSpan);

                JsonNode? parsedNode;
                try
                {
                    parsedNode = JsonNode.Parse(requestSpan);
                }
                catch (JsonException)
                {
                    var err = CreateErrorResponse("00000000000000000000000000000000", "ERR_INVALID_REQUEST");
                    await SendResponseAsync(peer, err, deadline.Token).ConfigureAwait(false);
                    return;
                }

                if (parsedNode is not JsonObject requestObj)
                {
                    var err = CreateErrorResponse("00000000000000000000000000000000", "ERR_INVALID_REQUEST");
                    await SendResponseAsync(peer, err, deadline.Token).ConfigureAwait(false);
                    return;
                }

                var versionNode = requestObj["version"];
                if (versionNode is null || (int?)versionNode != ProtocolVersion)
                {
                    var err = CreateErrorResponse("00000000000000000000000000000000", "ERR_INVALID_REQUEST");
                    await SendResponseAsync(peer, err, deadline.Token).ConfigureAwait(false);
                    return;
                }

                var requestIdStr = requestObj["request_id"]?.GetValue<string>();
                if (string.IsNullOrWhiteSpace(requestIdStr) || !Hex32Regex.IsMatch(requestIdStr))
                {
                    var err = CreateErrorResponse("00000000000000000000000000000000", "ERR_INVALID_REQUEST_ID");
                    await SendResponseAsync(peer, err, deadline.Token).ConfigureAwait(false);
                    return;
                }

                var operation = requestObj["operation"]?.GetValue<string>();
                if (string.IsNullOrWhiteSpace(operation) || !AllowedOperations.Contains(operation))
                {
                    var err = CreateErrorResponse(requestIdStr, "ERR_INVALID_OPERATION");
                    await SendResponseAsync(peer, err, deadline.Token).ConfigureAwait(false);
                    return;
                }

                byte[]? immediateResponse = null;
                Task<byte[]>? pendingWaitTask = null;

                lock (_cacheLock)
                {
                    // 1. Check completed response cache
                    if (_responseCache.TryGetValue(requestIdStr, out var cached))
                    {
                        immediateResponse = CryptographicOperations.FixedTimeEquals(cached.RequestHash, requestHash)
                            ? cached.ResponseBytes
                            : CreateErrorResponse(requestIdStr, "ERR_REQUEST_ID_CONFLICT");
                    }
                    // 2. Check active in-flight request
                    else if (_pendingRequests.TryGetValue(requestIdStr, out var pending))
                    {
                        if (!CryptographicOperations.FixedTimeEquals(pending.RequestHash, requestHash))
                        {
                            immediateResponse = CreateErrorResponse(requestIdStr, "ERR_REQUEST_ID_CONFLICT");
                        }
                        else
                        {
                            pendingWaitTask = pending.Completion.Task;
                        }
                    }
                    else if (_pendingRequests.Count >= MaxCacheEntries)
                    {
                        immediateResponse=CreateErrorResponse(requestIdStr,"ERR_BUSY");
                    }
                    else
                    {
                        var tcs = new TaskCompletionSource<byte[]>(TaskCreationOptions.RunContinuationsAsynchronously);
                        _pendingRequests[requestIdStr] = new PendingRequest(requestHash, tcs);
                    }
                }

                if (immediateResponse is not null)
                {
                    await SendResponseAsync(peer, immediateResponse, deadline.Token).ConfigureAwait(false);
                    return;
                }

                if (pendingWaitTask is not null)
                {
                    byte[] pendingResult;
                    try
                    {
                        pendingResult = await pendingWaitTask.WaitAsync(deadline.Token).ConfigureAwait(false);
                    }
                    catch (OperationCanceledException) when (deadline.IsCancellationRequested && !_stop.IsCancellationRequested)
                    {
                        pendingResult = CreateErrorResponse(requestIdStr, "ERR_TIMEOUT");
                    }
                    catch (TimeoutException)
                    {
                        pendingResult = CreateErrorResponse(requestIdStr, "ERR_TIMEOUT");
                    }
                    catch
                    {
                        pendingResult = CreateErrorResponse(requestIdStr, "ERR_INTERNAL_ERROR");
                    }

                    await SendResponseAsync(peer, pendingResult, deadline.Token).ConfigureAwait(false);
                    return;
                }

                // Primary executor: acquire sequential execution lock
                try { await _handlerLock.WaitAsync(deadline.Token).ConfigureAwait(false); }
                catch(OperationCanceledException)
                {
                    var canceledResponse=CreateErrorResponse(requestIdStr,"ERR_TIMEOUT");
                    lock(_cacheLock)
                    {
                        if(_pendingRequests.Remove(requestIdStr,out var canceledPending))
                            canceledPending.Completion.TrySetResult(canceledResponse);
                        CacheResponse(requestIdStr,requestHash,canceledResponse);
                    }
                    return;
                }

                byte[] finalResponse = Array.Empty<byte>();
                bool timedOut = false;
                Task<JsonObject>? handlerTask = null;

                try
                {
                    JsonObject? result = null;
                    string? errorCode = null;

                    try
                    {
                        handlerTask = _handler(requestObj);
                        result = await handlerTask.WaitAsync(deadline.Token).ConfigureAwait(false);
                    }
                    catch (ControllerOperationException coe)
                    {
                        errorCode = SafeErrorCodeRegex.IsMatch(coe.Code) ? coe.Code : "ERR_OPERATION_FAILED";
                    }
                    catch (TimeoutException)
                    {
                        errorCode = "ERR_TIMEOUT";
                        timedOut = true;
                    }
                    catch (OperationCanceledException) when (_stop.IsCancellationRequested)
                    {
                        errorCode = "ERR_SHUTDOWN";
                    }
                    catch (OperationCanceledException) when (deadline.IsCancellationRequested)
                    {
                        errorCode = "ERR_TIMEOUT";
                        timedOut = true;
                    }
                    catch
                    {
                        errorCode = "ERR_INTERNAL_ERROR";
                    }

                    if (errorCode is not null)
                    {
                        finalResponse = CreateErrorResponse(requestIdStr, errorCode);
                    }
                    else
                    {
                        var successObj = new JsonObject
                        {
                            ["version"] = ProtocolVersion,
                            ["request_id"] = requestIdStr,
                            ["ok"] = true,
                            ["result"] = result ?? new JsonObject()
                        };
                        finalResponse = SerializeWithNewline(successObj);
                    }
                }
                finally
                {
                    if (timedOut && handlerTask is not null)
                    {
                        // If timed out, retain _handlerLock and _pendingRequests until handlerTask settles
                        _ = handlerTask.ContinueWith(t =>
                        {
                            byte[] settled;
                            try
                            {
                                settled = t.IsCompletedSuccessfully && t.Result is not null
                                    ? SerializeWithNewline(new JsonObject {
                                        ["version"]=ProtocolVersion,["request_id"]=requestIdStr,
                                        ["ok"]=true,["result"]=t.Result })
                                    : CreateErrorResponse(requestIdStr,
                                        t.Exception?.GetBaseException() is ControllerOperationException error &&
                                        SafeErrorCodeRegex.IsMatch(error.Code) ? error.Code : "ERR_OPERATION_FAILED");
                            }
                            catch { settled=CreateErrorResponse(requestIdStr,"ERR_OPERATION_FAILED"); }
                            lock (_cacheLock)
                            {
                                if(_pendingRequests.Remove(requestIdStr,out var pending))
                                    pending.Completion.TrySetResult(settled);
                                CacheResponse(requestIdStr,requestHash,settled);
                            }
                            _handlerLock.Release();
                        }, TaskScheduler.Default);
                    }
                    else
                    {
                        lock (_cacheLock)
                        {
                            if (_pendingRequests.TryGetValue(requestIdStr, out var p))
                            {
                                p.Completion.TrySetResult(finalResponse);
                                _pendingRequests.Remove(requestIdStr);
                            }
                            CacheResponse(requestIdStr, requestHash, finalResponse);
                        }
                        _handlerLock.Release();
                    }
                }

                await SendResponseAsync(peer, finalResponse, deadline.Token).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is SocketException or IOException or OperationCanceledException or ObjectDisposedException)
            {
                // Ignored - transport failures or client disconnects
            }
        }
    }

    private void CacheResponse(string requestId, byte[] requestHash, byte[] responseBytes)
    {
        if (!_responseCache.ContainsKey(requestId))
        {
            if (_lruKeys.Count >= MaxCacheEntries)
            {
                var oldest = _lruKeys.First!.Value;
                _lruKeys.RemoveFirst();
                _responseCache.Remove(oldest);
            }
            _responseCache[requestId] = new CachedResponse(requestHash, responseBytes);
            _lruKeys.AddLast(requestId);
        }
    }

    private static byte[] CreateErrorResponse(string requestId, string errorCode)
    {
        var errObj = new JsonObject
        {
            ["version"] = ProtocolVersion,
            ["request_id"] = requestId,
            ["ok"] = false,
            ["error"] = errorCode
        };
        return SerializeWithNewline(errObj);
    }

    private static byte[] SerializeWithNewline(JsonObject obj)
    {
        var jsonBytes = JsonSerializer.SerializeToUtf8Bytes(obj);
        if (jsonBytes.Length + 1 > MaxResponseBytes)
        {
            var fallback = new JsonObject
            {
                ["version"] = ProtocolVersion,
                ["request_id"] = obj["request_id"]?.GetValue<string>() ?? "00000000000000000000000000000000",
                ["ok"] = false,
                ["error"] = "ERR_RESPONSE_TOO_LARGE"
            };
            jsonBytes = JsonSerializer.SerializeToUtf8Bytes(fallback);
        }

        var bytes = new byte[jsonBytes.Length + 1];
        jsonBytes.CopyTo(bytes, 0);
        bytes[^1] = (byte)'\n';
        return bytes;
    }

    private static async Task SendResponseAsync(Socket peer, byte[] response, CancellationToken ct)
    {
        var sent = 0;
        while (sent < response.Length)
        {
            var n = await peer.SendAsync(response.AsMemory(sent), SocketFlags.None, ct).ConfigureAwait(false);
            if (n == 0) break;
            sent += n;
        }
    }

    private static bool SameUser(Socket peer)
    {
        var size = (uint)Marshal.SizeOf<Credentials>();
        return getsockopt(peer.Handle.ToInt32(), 1, 17, out var credentials, ref size) == 0 &&
            size == Marshal.SizeOf<Credentials>() && credentials.Uid == geteuid();
    }

    private static void RequirePrivateDirectory(string path)
    {
        if (!Path.IsPathFullyQualified(path) || statx(-100, path, 0x100, 0x3, out var info) != 0 ||
            (info.Mode & 0xF000) != 0x4000 || info.Uid != geteuid() || (info.Mode & 0x3F) != 0)
            throw new IOException("Control endpoint requires an owner-only real runtime directory.");

        for (var current = new DirectoryInfo(path); current is not null; current = current.Parent)
            if ((current.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Control runtime path must not contain symlinks.");
    }

    [StructLayout(LayoutKind.Sequential)] private struct Credentials { public int Pid; public uint Uid, Gid; }
    [StructLayout(LayoutKind.Explicit, Size = 256)]
    private struct StatxInfo
    {
        [FieldOffset(20)] public uint Uid;
        [FieldOffset(28)] public ushort Mode;
    }
    [DllImport("libc", SetLastError = true)] private static extern uint geteuid();
    [DllImport("libc", SetLastError = true)] private static extern int getsockopt(int fd, int level, int option, out Credentials value, ref uint length);
    [DllImport("libc", SetLastError = true)] private static extern int statx(int dirfd, string path, int flags, uint mask, out StatxInfo value);

    public void Dispose()
    {
        DisposeAsync().AsTask().GetAwaiter().GetResult();
    }

    public async ValueTask DisposeAsync()
    {
        if(Interlocked.Exchange(ref _disposeStarted,1)!=0)return;
        _stop.Cancel();
        _listener.Dispose();
        try
        {
            await _worker.ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is SocketException or OperationCanceledException or ObjectDisposedException) { }

        try
        {
            if (File.Exists(_path))
            {
                File.Delete(_path);
            }
        }
        catch { }

        _stop.Dispose();
        // Pending callbacks retain the managed semaphore until they settle.
        // Disposing it here races their Release(); GC reclaims it afterwards.
    }
}
