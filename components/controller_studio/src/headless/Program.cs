using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using UnifiedController.Core;
using UnifiedController.Linux.App;
using UnifiedController.Linux.App.Services;
using UnifiedController.Linux.Platform;

namespace UnifiedController.Headless;

internal static class Program
{
    private static readonly object _shutdownLock = new();
    private static TaskCompletionSource<int> _shutdownTcs = new();
    private static volatile bool _shuttingDown;
    private static string? _activeSocketPath;

    public static async Task<int> Main(string[] args)
    {
        // 1. Argument parsing and validation
        var isFixture = args.Contains("--fixture", StringComparer.Ordinal);
        var dataDir = GetOption(args, "--data-dir");
        var legacyDataDir = GetOption(args, "--legacy-data-dir");
        var runtimeDir = GetOption(args, "--runtime-dir");

        if (string.IsNullOrWhiteSpace(dataDir))
        {
            Console.Error.WriteLine("Error: --data-dir is required.");
            return 2;
        }

        if (isFixture && !string.IsNullOrWhiteSpace(legacyDataDir))
        {
            Console.Error.WriteLine("Error: --fixture and --legacy-data-dir are mutually exclusive.");
            return 2;
        }

        var canonicalDataRoot = Path.GetFullPath(dataDir);
        string? canonicalLegacyDir = null;
        if (!string.IsNullOrWhiteSpace(legacyDataDir))
        {
            canonicalLegacyDir = Path.GetFullPath(legacyDataDir);
            if (!Directory.Exists(canonicalLegacyDir))
            {
                Console.Error.WriteLine($"Error: Legacy data directory not found: {canonicalLegacyDir}");
                return 5;
            }
        }

        // Never seed production defaults: profiles.json must exist in data-dir
        var profilesPath = Path.Combine(canonicalDataRoot, "profiles.json");
        if (!isFixture && !File.Exists(profilesPath))
        {
            Console.Error.WriteLine($"Error: profiles.json not found in data directory: {canonicalDataRoot}");
            return 4;
        }
        if (isFixture && !File.Exists(profilesPath))
        {
            EnsureFixtureProfilesFile(canonicalDataRoot);
        }

        // Determine control socket runtime directory
        var canonicalRuntimeDir = !string.IsNullOrWhiteSpace(runtimeDir)
            ? Path.GetFullPath(runtimeDir)
            : (Environment.GetEnvironmentVariable("XDG_RUNTIME_DIR") ?? $"/run/user/{geteuid()}");

        // 2. Deterministic sorted locking via LocalInstanceCoordinator
        var lockPaths = new HashSet<string>(StringComparer.Ordinal) { canonicalDataRoot };
        if (canonicalLegacyDir is not null)
        {
            lockPaths.Add(canonicalLegacyDir);
        }

        var sortedPaths = lockPaths.OrderBy(p => p, StringComparer.Ordinal).ToList();
        var coordinators = new List<LocalInstanceCoordinator>();

        try
        {
            foreach (var path in sortedPaths)
            {
                var coord = new LocalInstanceCoordinator(path);
                if (!coord.IsPrimary)
                {
                    Console.Error.WriteLine($"Error: An active instance already owns directory: {path}");
                    foreach (var c in coordinators)
                    {
                        try { await c.DisposeAsync(); } catch { }
                    }
                    try { await coord.DisposeAsync(); } catch { }
                    return 3;
                }
                coordinators.Add(coord);
            }

            // 3. Initialize Profile Documents
            var documents = new ProfileDocumentService(canonicalDataRoot);

            // 4. Initialize Engine Runtime with guaranteed disposal covering all subsequent steps
            IControllerRuntime runtime = isFixture
                ? new FakeRuntime(OutputControlService.ConfiguredType(documents.Document))
                : PlatformServices.CreateRuntime(canonicalDataRoot);

            try
            {
                await runtime.StartAsync();

                // 5. Initialize Controller Control Service and IPC Server
                var outputCommands = new OutputControlService(documents,(IOutputTypeController)runtime);
                var runtimeCommands = new ControllerCommandService(runtime);
            var profileCommands = new ControllerProfileCommandService(documents);
            var deviceCommands = new ControllerDeviceCommandService(runtime);
            var controlService = new ProfileControlService(
                    documents,
                    () => runtime.Snapshot,
                    () => runtime.StopAllMacros(),
                    committed: () =>
                    {
                        // Profile updated and committed to disk
                    },
                    outputStatus: outputCommands.Read
                );

                ControllerControlServer? server = null;
                try
                {
                    server = new ControllerControlServer(
                        canonicalRuntimeDir,
                        request =>
                        {
                            if (_shuttingDown) throw new ControllerOperationException("ERR_SHUTTING_DOWN");
                            var operation = request["operation"]?.GetValue<string>();
                    if (OutputControlService.Handles(operation)) return outputCommands.HandleAsync(request);
                    return Task.FromResult(ControllerCommandService.Handles(operation) ? runtimeCommands.Handle(request) :
                        ControllerProfileCommandService.Handles(operation) ? profileCommands.Handle(request) :
                        ControllerDeviceCommandService.Handles(operation) ? deviceCommands.Handle(request) : controlService.Handle(request));
                        }
                    );

                    _activeSocketPath = server.SocketPath;

                    // 6. Signal handling: PosixSignalRegistration with context.Cancel = true
                    using var sigtermReg = PosixSignalRegistration.Create(
                        PosixSignal.SIGTERM,
                        ctx =>
                        {
                            ctx.Cancel = true;
                            TriggerShutdown(0);
                        }
                    );
                    using var sigintReg = PosixSignalRegistration.Create(
                        PosixSignal.SIGINT,
                        ctx =>
                        {
                            ctx.Cancel = true;
                            TriggerShutdown(0);
                        }
                    );
                    using var sigquitReg = PosixSignalRegistration.Create(
                        PosixSignal.SIGQUIT,
                        ctx =>
                        {
                            ctx.Cancel = true;
                            TriggerShutdown(0);
                        }
                    );

                    Console.CancelKeyPress += (s, e) =>
                    {
                        e.Cancel = true;
                        TriggerShutdown(0);
                    };
                    AppDomain.CurrentDomain.ProcessExit += (s, e) =>
                    {
                        if (_activeSocketPath is not null)
                        {
                            try { unlink(_activeSocketPath); } catch { }
                        }
                    };

                    // 7. Signal readiness
                    Console.WriteLine("WAYSTONE_ENGINE_READY=1");
                    Console.Out.Flush();

                    // 8. Lifecycle listeners: stdin EOF / shutdown command
                    StartStdinListener();

                    // Wait for shutdown trigger
                    var exitCode = await _shutdownTcs.Task;

                    // 9. Orderly teardown:
                    // Step A: Mark shutting down to reject incoming mutations
                    _shuttingDown = true;

                    // Step B: Drain and dispose server FIRST to close listener and unlink socket
                    if (server is not null)
                    {
                        try { await server.DisposeAsync(); } catch { }
                        server = null;
                        _activeSocketPath = null;
                    }

                    return exitCode;
                }
                finally
                {
                    // Ensure server is disposed if an unhandled exception occurred before or during main loop
                    if (server is not null)
                    {
                        try { await server.DisposeAsync(); } catch { }
                        _activeSocketPath = null;
                    }
                }
            }
            finally
            {
                // Guaranteed runtime shutdown and disposal covering server bind failures
                _shuttingDown = true;
                try
                {
                    runtime.StopAllMacros();
                    await runtime.StopAsync();
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine($"[headless] Error stopping runtime: {ex.Message}");
                }
                finally
                {
                    try { await runtime.DisposeAsync(); } catch { }
                }
            }
        }
        finally
        {
            // Guaranteed lock release in finally block
            foreach (var c in coordinators)
            {
                try { await c.DisposeAsync(); } catch { }
            }
        }
    }

    private static void StartStdinListener()
    {
        var thread = new Thread(() =>
        {
            try
            {
                using var reader = new StreamReader(Console.OpenStandardInput(), Console.InputEncoding);
                while (true)
                {
                    var line = reader.ReadLine();
                    if (line is null) // EOF
                    {
                        TriggerShutdown(0);
                        break;
                    }

                    var trimmed = line.Trim();
                    if (trimmed.Equals("shutdown", StringComparison.OrdinalIgnoreCase) ||
                        trimmed.Equals("exit", StringComparison.OrdinalIgnoreCase) ||
                        trimmed.Equals("quit", StringComparison.OrdinalIgnoreCase))
                    {
                        TriggerShutdown(0);
                        break;
                    }
                }
            }
            catch
            {
                TriggerShutdown(0);
            }
        })
        {
            IsBackground = true,
            Name = "HeadlessStdinListener"
        };
        thread.Start();
    }

    private static void TriggerShutdown(int exitCode)
    {
        lock (_shutdownLock)
        {
            _shuttingDown = true;
            _shutdownTcs.TrySetResult(exitCode);
        }
    }

    private static string? GetOption(string[] args, string name)
    {
        for (var i = 0; i < args.Length - 1; i++)
        {
            if (string.Equals(args[i], name, StringComparison.Ordinal))
            {
                return args[i + 1];
            }
        }
        return null;
    }

    private static void EnsureFixtureProfilesFile(string root)
    {
        var profilesPath = Path.Combine(root, "profiles.json");
        if (!File.Exists(profilesPath))
        {
            var initialDoc = new JsonObject
            {
                ["version"] = 1,
                ["active_profile"] = "Default",
                ["profiles"] = new JsonObject
                {
                    ["Default"] = new JsonObject
                    {
                        ["routes"] = new JsonObject(),
                        ["enabled_output_slots"] = new JsonArray(1)
                    }
                }
            };
            Directory.CreateDirectory(root);
            File.WriteAllText(profilesPath, initialDoc.ToJsonString(new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
        }
    }

    [DllImport("libc", SetLastError = true)]
    private static extern uint geteuid();

    [DllImport("libc", SetLastError = true)]
    private static extern int unlink(string pathname);
}
