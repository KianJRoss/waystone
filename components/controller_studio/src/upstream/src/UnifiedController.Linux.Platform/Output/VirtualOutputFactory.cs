using UnifiedController.Core;

namespace UnifiedController.Linux.Platform.Output;

public static class VirtualOutputFactory
{
    public static IVirtualControllerOutput Create(string type, int slot) => VirtualOutputTypes.Normalize(type) switch
    {
        "xbox360" => new UInputXbox360Output(slot),
        "xboxone" => new UInputXboxOneOutput(slot),
        "xboxseries" => new UInputXboxOneOutput(slot, series: true),
        "dualshock4" => new UhidDualShock4Output(slot),
        "dualsense" => new UhidDualSenseOutput(slot),
        _ => throw new ArgumentException("Unsupported virtual output type.", nameof(type))
    };
}
