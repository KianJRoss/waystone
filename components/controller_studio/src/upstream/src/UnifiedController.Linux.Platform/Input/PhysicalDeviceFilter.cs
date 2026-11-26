namespace UnifiedController.Linux.Platform.Input;

public static class PhysicalDeviceFilter
{
    public const ushort VirtualXboxVendorId = 0x045e;
    public const ushort VirtualXboxProductId = 0x028e;
    public const ushort BusVirtual = 0x0006;

    public static bool IsOwnVirtualOutput(string? name, string? physicalPath, string? uniqueId,
        ushort vendorId, ushort productId, ushort? busType = null)
    {
        if ((name ?? "").StartsWith("Unified Controller", StringComparison.OrdinalIgnoreCase)) return true;
        if ((physicalPath ?? "").Contains("unified-controller/output/", StringComparison.OrdinalIgnoreCase)) return true;
        if ((uniqueId ?? "").Contains("unified-controller/output/", StringComparison.OrdinalIgnoreCase)) return true;
        return vendorId == VirtualXboxVendorId && productId == VirtualXboxProductId && busType == BusVirtual;
    }
}
