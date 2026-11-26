using UnifiedController.Core;

namespace UnifiedController.Linux.App.Services;

public sealed class RuntimeUiAdapter(IControllerRuntime runtime)
{
    public RuntimeSnapshot Snapshot => runtime.Snapshot;
    public IConfigurableControllerInput? Configuration => runtime.ControllerInput as IConfigurableControllerInput;
    public Task Start() => runtime.StartAsync().AsTask();
    public Task Stop() => runtime.StopAsync().AsTask();
    public void StopAllMacros() => runtime.StopAllMacros();
    public void Recenter() => runtime.RecenterMotion();
    public void Calibrate() => runtime.CalibrateMotion();
    public bool KeyboardCapture { get => runtime.KeyboardCaptureEnabled; set => runtime.KeyboardCaptureEnabled = value; }
    public bool MouseCapture { get => runtime.MouseCaptureEnabled; set => runtime.MouseCaptureEnabled = value; }
}
