using System.Runtime.InteropServices;

namespace UnifiedController.App.Engine;

[StructLayout(LayoutKind.Sequential)]
public struct XInputGamepad
{
    public ushort Buttons;
    public byte LeftTrigger;
    public byte RightTrigger;
    public short ThumbLX;
    public short ThumbLY;
    public short ThumbRX;
    public short ThumbRY;
}

[StructLayout(LayoutKind.Sequential)]
public struct XInputState
{
    public uint PacketNumber;
    public XInputGamepad Gamepad;
}

[StructLayout(LayoutKind.Sequential)]
public struct XInputVibration
{
    public ushort LeftMotorSpeed;
    public ushort RightMotorSpeed;
}

[StructLayout(LayoutKind.Sequential)]
public struct XInputCapabilitiesEx
{
    public byte Type;
    public byte SubType;
    public ushort Flags;
    public XInputGamepad Gamepad;
    public XInputVibration Vibration;
    public ushort VendorId;
    public ushort ProductId;
    public ushort Version;
    public ushort Reserved;
    public uint Reserved2;
}

public sealed class NativeXInputReader
{
    public static bool IsUnifiedVirtualDevice(ushort vendorId, ushort productId) =>
        vendorId == 0x1209 && productId == 0xC710;

    [DllImport("xinput1_4.dll", EntryPoint = "#108")]
    private static extern uint XInputGetCapabilitiesEx(uint reserved, uint slot, uint flags, out XInputCapabilitiesEx capabilities);

    private readonly Dictionary<uint, (long CheckedAt, bool IsVirtual)> _identities = [];

    // Identify our outputs independently of the ViGEm client handle. An old
    // target can remain enumerated even when initializing a new target fails.
    public bool IsUnifiedVirtualSlot(uint slot)
    {
        var now = Environment.TickCount64;
        if (_identities.TryGetValue(slot, out var cached) && now - cached.CheckedAt < 1000)
            return cached.IsVirtual;
        var isVirtual = false;
        try
        {
            isVirtual = XInputGetCapabilitiesEx(1, slot, 0, out var capabilities) == 0 &&
                IsUnifiedVirtualDevice(capabilities.VendorId, capabilities.ProductId);
        }
        catch (Exception ex) when (ex is DllNotFoundException or EntryPointNotFoundException) { }
        _identities[slot] = (now, isVirtual);
        return isVirtual;
    }
    private const uint ErrorSuccess = 0;
    private const uint ErrorDeviceNotConnected = 1167;

    [DllImport("xinput1_4.dll", EntryPoint = "#100", SetLastError = false)]
    private static extern uint XInputGetStateEx14(uint dwUserIndex, out XInputState pState);

    [DllImport("xinput1_4.dll", EntryPoint = "XInputGetState", SetLastError = false)]
    private static extern uint XInputGetState14(uint dwUserIndex, out XInputState pState);

    [DllImport("xinput1_4.dll", EntryPoint = "XInputSetState", SetLastError = false)]
    private static extern uint XInputSetState14(uint dwUserIndex, ref XInputVibration pVibration);

    [DllImport("xinput1_3.dll", EntryPoint = "#100", SetLastError = false)]
    private static extern uint XInputGetStateEx13(uint dwUserIndex, out XInputState pState);

    [DllImport("xinput1_3.dll", EntryPoint = "XInputGetState", SetLastError = false)]
    private static extern uint XInputGetState13(uint dwUserIndex, out XInputState pState);

    [DllImport("xinput1_3.dll", EntryPoint = "XInputSetState", SetLastError = false)]
    private static extern uint XInputSetState13(uint dwUserIndex, ref XInputVibration pVibration);

    private bool _use14 = true;
    private bool _supportsEx = true;

    public NativeXInputReader()
    {
        try
        {
            XInputGetStateEx14(0, out _);
        }
        catch (EntryPointNotFoundException)
        {
            _supportsEx = false;
        }
        catch (DllNotFoundException)
        {
            _use14 = false;
        }
    }

    public bool Poll(uint slot, out XInputState state)
    {
        uint result;
        try
        {
            if (_use14)
            {
                result = _supportsEx ? XInputGetStateEx14(slot, out state) : XInputGetState14(slot, out state);
            }
            else
            {
                result = _supportsEx ? XInputGetStateEx13(slot, out state) : XInputGetState13(slot, out state);
            }
        }
        catch
        {
            state = default;
            return false;
        }

        if (result != ErrorSuccess) _identities.Remove(slot);
        return result == ErrorSuccess;
    }

    public void SetVibration(uint slot, byte largeMotor, byte smallMotor)
    {
        var vib = new XInputVibration
        {
            LeftMotorSpeed = (ushort)(largeMotor * 257),
            RightMotorSpeed = (ushort)(smallMotor * 257)
        };

        try
        {
            if (_use14)
                XInputSetState14(slot, ref vib);
            else
                XInputSetState13(slot, ref vib);
        }
        catch { }
    }
}
