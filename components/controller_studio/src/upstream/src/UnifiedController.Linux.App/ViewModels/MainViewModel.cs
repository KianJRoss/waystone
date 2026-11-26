using System.ComponentModel;
using System.Runtime.CompilerServices;
using Avalonia.Threading;
using UnifiedController.Core;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.ViewModels;

public sealed class MainViewModel : INotifyPropertyChanged, IDisposable
{
    public ProfileDocumentService Profiles { get; }
    public RuntimeUiAdapter Runtime { get; }
    public RuntimeSnapshot Snapshot { get; private set; }
    public string Status => $"{Snapshot.Health} · {Snapshot.Devices.Count} device(s)";
    public string InputSummary => Snapshot.Devices.Count == 0 ? "Waiting for controller" : string.Join(" · ", Snapshot.Devices.Where(d => d.Connected && !d.IsVirtual).Select(d => d.Name).Distinct());
    public string OutputSummary => Snapshot.OutputSlots.Count == 0 ? "Output idle" : string.Join(" · ", Snapshot.OutputSlots.Keys.Order().Select(slot => $"Xbox controller · player {slot}"));
    public string InputButtons => Buttons(Snapshot.Input.Buttons);
    public string OutputButtons => Buttons(Snapshot.Output.Buttons);
    public string InputAxes => Axes(Snapshot.Input);
    public string OutputAxes => Axes(Snapshot.Output);
    public double InputActivity => Activity(Snapshot.Input);
    public double OutputActivity => Activity(Snapshot.Output);
    public string LoopTiming => $"Average {Snapshot.AverageLoopMs:F2} ms · maximum {Snapshot.MaximumLoopMs:F2} ms";
    public string DsuRumble => $"DSU: {(Snapshot.Dsu is null ? "not started" : Snapshot.Dsu.Running ? $"running · {Snapshot.Dsu.Clients} client(s)" : Snapshot.Dsu.Error ?? "stopped")}\nRumble: {Snapshot.Rumble.Heavy} / {Snapshot.Rumble.Light}";
    public string ActiveModes => Snapshot.ActiveModes.Count == 0 ? "None" : string.Join(" · ", Snapshot.ActiveModes);
    public string Telemetry => $"Source: {Snapshot.SelectedSourceId ?? "none"}\nLast desktop input: {Snapshot.LastInputSource ?? "none"} @ {Snapshot.LastInputTimestamp}\nInput: {Frame(Snapshot.Input)}\nOutput: {Frame(Snapshot.Output)}\nOutput slots: {string.Join(" | ", Snapshot.OutputSlots.OrderBy(x => x.Key).Select(x => $"{x.Key}: {Frame(x.Value)}"))}\nController mouse: {(Snapshot.ControllerMouseModeEnabled ? "enabled" : "disabled")}\nLoop avg/max: {Snapshot.AverageLoopMs:F2}/{Snapshot.MaximumLoopMs:F2} ms · Macros: {Snapshot.ActiveMacros}";
    public string Motion => Snapshot.Motion is null ? "No motion sample" : $"Gyro dps: {string.Join(", ", Snapshot.Motion.Value.GyroDps.Select(v => v.ToString("F2")))}\nAccel g: {string.Join(", ", Snapshot.Motion.Value.AccelG.Select(v => v.ToString("F3")))}";
    public event PropertyChangedEventHandler? PropertyChanged;
    private readonly DispatcherTimer _timer;

    public MainViewModel()
    {
        Profiles = new(Program.DataRoot);
        Runtime = new(Program.Runtime ?? throw new InvalidOperationException("Runtime unavailable"));
        Snapshot = Runtime.Snapshot;
        _timer = new DispatcherTimer(TimeSpan.FromMilliseconds(33), DispatcherPriority.Background, (_, _) => Refresh());
        _timer.Start();
    }
    public void Refresh()
    {
        Snapshot = Runtime.Snapshot;
        Changed(nameof(Snapshot)); Changed(nameof(Status)); Changed(nameof(Telemetry)); Changed(nameof(Motion));
        Changed(nameof(InputSummary)); Changed(nameof(OutputSummary)); Changed(nameof(InputButtons)); Changed(nameof(OutputButtons));
        Changed(nameof(InputAxes)); Changed(nameof(OutputAxes)); Changed(nameof(InputActivity)); Changed(nameof(OutputActivity));
        Changed(nameof(LoopTiming)); Changed(nameof(DsuRumble)); Changed(nameof(ActiveModes));
    }
    public void Changed(string? name = null) => PropertyChanged?.Invoke(this, new(name));
    public void Dispose() => _timer.Stop();
    private static string Frame(ControllerFrame f) => $"buttons 0x{f.Buttons:X} LT/RT {f.LeftTrigger}/{f.RightTrigger} LS {f.LeftX},{f.LeftY} RS {f.RightX},{f.RightY}";
    private static string Buttons(int mask) => mask == 0
        ? "Buttons: none"
        : "Buttons: " + string.Join(" · ", ProfileEditorService.ControllerButtons.Where(button => (mask & button.Key) != 0).Select(button => button.Value));
    private static string Axes(ControllerFrame f) => $"LS {f.LeftX,6},{f.LeftY,6}   RS {f.RightX,6},{f.RightY,6}   LT/RT {f.LeftTrigger,3}/{f.RightTrigger,3}";
    private static double Activity(ControllerFrame f) => Math.Min(100, new[] { Math.Abs((double)f.LeftX) / 327.67, Math.Abs((double)f.LeftY) / 327.67, Math.Abs((double)f.RightX) / 327.67, Math.Abs((double)f.RightY) / 327.67, f.LeftTrigger / 2.55, f.RightTrigger / 2.55 }.Max());
}
