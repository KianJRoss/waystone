using UnifiedController.Core;

namespace UnifiedController.Linux.Platform.Input;

public enum NintendoControllerKind { None, JoyConLeft, JoyConRight, SwitchPro }

public static class SdlMappings
{
    public const float StandardGravity = 9.80665f;
    public const float RadiansToDegrees = 180f / MathF.PI;

    public static NintendoControllerKind NintendoKind(ushort vendor, ushort product, string? name)
    {
        if (vendor != 0x057e && !(name ?? "").Contains("Joy-Con", StringComparison.OrdinalIgnoreCase) &&
            !(name ?? "").Contains("Nintendo Switch", StringComparison.OrdinalIgnoreCase)) return NintendoControllerKind.None;
        return product switch
        {
            0x2006 => NintendoControllerKind.JoyConLeft,
            0x2007 => NintendoControllerKind.JoyConRight,
            0x2009 => NintendoControllerKind.SwitchPro,
            _ when (name ?? "").Contains("Joy-Con (L)", StringComparison.OrdinalIgnoreCase) => NintendoControllerKind.JoyConLeft,
            _ when (name ?? "").Contains("Joy-Con (R)", StringComparison.OrdinalIgnoreCase) => NintendoControllerKind.JoyConRight,
            _ => NintendoControllerKind.SwitchPro
        };
    }

    public static short InvertSignedAxis(short value) => value == short.MinValue ? short.MaxValue : (short)-value;

    public static ControllerFrame Sideways(ControllerFrame upright, NintendoControllerKind kind)
    {
        if (kind == NintendoControllerKind.JoyConLeft)
        {
            // Same rail-up orientation as NativeJoyConManager: d-pad becomes ABXY.
            var buttons = upright.Buttons & (0x0020 | 0x0040);
            if ((upright.Buttons & 0x0001) != 0) buttons |= 0x8000;
            if ((upright.Buttons & 0x0008) != 0) buttons |= 0x4000;
            if ((upright.Buttons & 0x0004) != 0) buttons |= 0x2000;
            if ((upright.Buttons & 0x0002) != 0) buttons |= 0x1000;
            if ((upright.Buttons & 0x0100) != 0) buttons |= 0x0100;
            if (upright.LeftTrigger != 0) buttons |= 0x0200;
            return new ControllerFrame(buttons, 0, 0, upright.LeftY, InvertSignedAxis(upright.LeftX), 0, 0);
        }
        if (kind == NintendoControllerKind.JoyConRight)
        {
            var buttons = upright.Buttons & (0x0010 | 0x0040 | 0x1000 | 0x2000 | 0x4000 | 0x8000);
            if ((upright.Buttons & 0x0200) != 0) buttons |= 0x0100;
            if (upright.RightTrigger != 0) buttons |= 0x0200;
            return new ControllerFrame(buttons, 0, 0, InvertSignedAxis(upright.RightY), upright.RightX, 0, 0);
        }
        return upright;
    }

    public static ControllerFrame Merge(ControllerFrame left, ControllerFrame right) => new(
        left.Buttons | right.Buttons,
        Math.Max(left.LeftTrigger, right.LeftTrigger), Math.Max(left.RightTrigger, right.RightTrigger),
        Pick(left.LeftX, right.LeftX), Pick(left.LeftY, right.LeftY),
        Pick(left.RightX, right.RightX), Pick(left.RightY, right.RightY));

    public static MotionFrame? Merge(MotionFrame? left, MotionFrame? right)
    {
        if (left is null) return right;
        if (right is null) return left;
        return left.Value.TimestampUs >= right.Value.TimestampUs ? left : right;
    }

    // SDL sensors use the controller-local right/down/toward-player frame. The native
    // engine and DSU calibration use pitch/yaw/roll and Z-up: X, -Z, Y.
    // SDL_GameControllerGetSensorData documentation (SDL 2.32/SDL3 compatibility).
    public static float[] GyroToDegrees(float[] radians) =>
        [radians[0] * RadiansToDegrees, -radians[2] * RadiansToDegrees, radians[1] * RadiansToDegrees];
    public static float[] AccelToG(float[] metresPerSecondSquared) =>
        [metresPerSecondSquared[0] / StandardGravity, -metresPerSecondSquared[2] / StandardGravity, metresPerSecondSquared[1] / StandardGravity];
    private static short Pick(short first, short second) => first != 0 ? first : second;
}
