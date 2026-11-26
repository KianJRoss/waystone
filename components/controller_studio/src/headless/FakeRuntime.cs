using UnifiedController.Core;

namespace UnifiedController.Linux.App;

public sealed class FakeRuntime : IControllerRuntime, IOutputTypeController
{
    private RuntimeSnapshot _snapshot;
    public event Action<string>? ActionRequested;

    private readonly VirtualOutputManager _outputs;
    public FakeRuntime(string outputType = "xbox360")
    {
        _outputs = new((_,_) => new FixtureOutput(),outputType);
        var inputFrame = new ControllerFrame(
            Buttons: 0x1000,
            LeftTrigger: 180,
            RightTrigger: 45,
            LeftX: -16384,
            LeftY: 20000,
            RightX: 12000,
            RightY: -15000
        );
        var outputFrame = new ControllerFrame(
            Buttons: 0x2000,
            LeftTrigger: 200,
            RightTrigger: 0,
            LeftX: -12000,
            LeftY: 15000,
            RightX: 8000,
            RightY: -10000
        );

        _snapshot = new RuntimeSnapshot(
            "Default",
            true,
            "Running (Fixture)",
            [
                new InputDeviceInfo(
                    "fake-gamepad-1",
                    "Virtual Test Controller",
                    0x045E,
                    0x028E,
                    "/dev/input/js0",
                    true,
                    true
                ),
                new InputDeviceInfo(
                    "fake-keyboard-1",
                    "Virtual Test Keyboard",
                    0x0001,
                    0x0001,
                    "/dev/input/event0",
                    false,
                    true
                )
            ],
            "fake-gamepad-1",
            inputFrame,
            outputFrame,
            null,
            0.001,
            0.002,
            0,
            null,
            ["Fixture Sandbox", "No physical hardware"]
        )
        {
            ExtendedButtons = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "Share" },
            ModActive = true,
            ControllerMouseModeEnabled = true
        };
    }

    public RuntimeSnapshot Current
    {
        get => _snapshot;
        set => _snapshot = value;
    }

    public IControllerInputProvider ControllerInput => null!;
    public RuntimeSnapshot Snapshot => _snapshot;
    public bool KeyboardCaptureEnabled { get; set; }
    public bool MouseCaptureEnabled { get; set; }

    public VirtualOutputStatus OutputStatus => _outputs.Status;
    public ValueTask<bool> SetOutputTypeAsync(string type, bool force = false, CancellationToken token = default) => _outputs.SwitchAsync(type,force,token);
    public void ObserveGames(IReadOnlyList<string> activeGameIds,bool observationKnown=true) => _outputs.ObserveGames(activeGameIds,observationKnown);
    public ValueTask StartAsync(CancellationToken token = default) => _outputs.RebuildAsync(new[]{1},token);
    public ValueTask StopAsync() => ValueTask.CompletedTask;
    public void StopAllMacros() { }
    public void RecenterMotion() { }
    public void CalibrateMotion() { }
    public bool InvokeAction(string action)
    {
        ActionRequested?.Invoke(action);
        return true;
    }
    public ValueTask DisposeAsync() => _outputs.DisposeAsync();

    private sealed class FixtureOutput : IVirtualControllerOutput
    {
        public bool Connected { get; private set; }
        public event Action<byte,byte>? Rumble { add { } remove { } }
        public ValueTask StartAsync(CancellationToken token) { token.ThrowIfCancellationRequested(); Connected=true; return ValueTask.CompletedTask; }
        public ValueTask SubmitAsync(ControllerFrame frame,CancellationToken token) => ValueTask.CompletedTask;
        public ValueTask SubmitNeutralAsync(CancellationToken token) => ValueTask.CompletedTask;
        public ValueTask DisposeAsync() { Connected=false; return ValueTask.CompletedTask; }
    }
}
