using Avalonia;
using UnifiedController.Core;
using UnifiedController.Linux.Platform;
using System.Runtime.InteropServices;

namespace UnifiedController.Linux.App;

internal static class Program
{
    public static string DataRoot { get; private set; } = "";
    public static IControllerRuntime? Runtime { get; private set; }
    public static LocalInstanceCoordinator? Instance { get; private set; }

    [STAThread]
    public static async Task<int> Main(string[] args)
    {
        string? action;
        try { DataRoot = GetOption(args, "--data-dir") ?? DefaultDataRoot(); action = GetAction(args); }
        catch (ArgumentException e) { Console.Error.WriteLine($"unified-controller: {e.Message}"); return 2; }
        Directory.CreateDirectory(DataRoot);
        Instance = new LocalInstanceCoordinator(DataRoot);
        if (!Instance.IsPrimary)
        {
            if (action is not null) await Instance.RequestActionAsync(action);
            else if (!args.Contains("--headless", StringComparer.Ordinal)) await Instance.RequestShowAsync();
            await Instance.DisposeAsync();
            return 0;
        }
        Runtime = PlatformServices.CreateRuntime(DataRoot);
        Instance.ActionRequested += name => Runtime.InvokeAction(name);
        if (action is not null) Runtime.InvokeAction(action);

        if (args.Contains("--headless", StringComparer.Ordinal))
        {
            using var stop = new CancellationTokenSource();
            Console.CancelKeyPress += (_, e) => { e.Cancel = true; stop.Cancel(); };
            using var term = PosixSignalRegistration.Create(PosixSignal.SIGTERM, context => { context.Cancel = true; stop.Cancel(); });
            using var interrupt = PosixSignalRegistration.Create(PosixSignal.SIGINT, context => { context.Cancel = true; stop.Cancel(); });
            try { await Runtime.StartAsync(stop.Token); await Task.Delay(Timeout.Infinite, stop.Token); }
            catch (OperationCanceledException) { }
            finally { try { Runtime.StopAllMacros(); await Runtime.StopAsync(); } finally { await Runtime.DisposeAsync(); await Instance.DisposeAsync(); } }
            return 0;
        }
        var avaloniaArgs = StripApplicationOptions(args);
        try { return BuildAvaloniaApp().StartWithClassicDesktopLifetime(avaloniaArgs); }
        catch (Exception e) { Console.Error.WriteLine($"unified-controller: the window failed to start: {e}"); return 1; }
        finally { SafeShutdown(); }
    }

    // Blocking callers sit on the UI thread, whose dispatcher is not pumping while they wait,
    // so the shutdown runs on the thread pool and never resumes on the UI context.
    internal static void SafeShutdown() => Task.Run(SafeShutdownAsync).GetAwaiter().GetResult();

    internal static async Task SafeShutdownAsync()
    {
        if (Runtime is not null)
            try { Runtime.StopAllMacros(); await Runtime.StopAsync().ConfigureAwait(false); }
            finally { await Runtime.DisposeAsync().ConfigureAwait(false); Runtime = null; }
        if (Instance is not null) { await Instance.DisposeAsync().ConfigureAwait(false); Instance = null; }
    }

    private static string DefaultDataRoot()
    {
        var xdg = Environment.GetEnvironmentVariable("XDG_DATA_HOME");
        return Path.Combine(!string.IsNullOrWhiteSpace(xdg) ? xdg : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".local", "share"), "unified-controller");
    }
    private static string[] StripApplicationOptions(string[] args)
    {
        var result = new List<string>();
        for (var i = 0; i < args.Length; i++) { if (args[i] is "--data-dir" or "--action") { i++; continue; } if (args[i] != "--headless") result.Add(args[i]); }
        return result.ToArray();
    }

    private static string? GetAction(string[] args)
    {
        var value = GetRawOption(args, "--action");
        if (value is null) return null;
        var canonical = RuntimeActions.All.FirstOrDefault(x => x.Equals(value, StringComparison.OrdinalIgnoreCase));
        return canonical ?? throw new ArgumentException($"Unknown action '{value}'. Expected one of: {string.Join(", ", RuntimeActions.All)}");
    }
    private static string? GetRawOption(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        if (index < 0) return null;
        if (index + 1 >= args.Length || string.IsNullOrWhiteSpace(args[index + 1])) throw new ArgumentException($"{name} requires a value");
        return args[index + 1];
    }

    private static string? GetOption(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        if (index < 0) return null;
        if (index + 1 >= args.Length || string.IsNullOrWhiteSpace(args[index + 1]))
            throw new ArgumentException($"{name} requires a directory");
        return Path.GetFullPath(args[index + 1]);
    }

    public static AppBuilder BuildAvaloniaApp() => AppBuilder.Configure<App>()
        .UsePlatformDetect().WithInterFont().LogToTrace();
}
