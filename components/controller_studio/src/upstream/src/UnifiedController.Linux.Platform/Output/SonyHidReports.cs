using System.Buffers.Binary;
using UnifiedController.Core;

namespace UnifiedController.Linux.Platform.Output;

/// <summary>Accepted virtual-device output state. Trigger bytes are retained, never applied to physical hardware.</summary>
public sealed record SonyOutputReport(byte? Heavy, byte? Light, byte? Red, byte? Green, byte? Blue,
    ReadOnlyMemory<byte> RightTrigger, ReadOnlyMemory<byte> LeftTrigger);

/// <summary>USB protocol codec, based on Linux hid-playstation.c input/output structures and SDL_hidapi_ps4/ps5.c.
/// See research/w16/hid-report-layouts.md for source URLs, offsets and calibration derivation.</summary>
public static class SonyHidReports
{
    public static byte[] EncodeInput(SonyPadType type, ControllerFrame frame, MotionFrame? motion = null,
        IReadOnlySet<string>? extendedButtons = null, byte sequence = 0, ulong fallbackTimestampUs = 0)
    {
        ValidateType(type);
        var ds5 = type == SonyPadType.DualSense;
        var report = new byte[64];
        report[0] = 1;
        report[1] = Axis(frame.LeftX); report[2] = Axis(-(int)frame.LeftY);
        report[3] = Axis(frame.RightX); report[4] = Axis(-(int)frame.RightY);
        var buttonOffset = ds5 ? 8 : 5;
        report[buttonOffset] = (byte)(Hat(frame.Buttons) |
            (Has(frame, 0x4000) ? 0x10 : 0) | (Has(frame, 0x1000) ? 0x20 : 0) |
            (Has(frame, 0x2000) ? 0x40 : 0) | (Has(frame, 0x8000) ? 0x80 : 0));
        report[buttonOffset + 1] = (byte)((Has(frame, 0x0100) ? 1 : 0) | (Has(frame, 0x0200) ? 2 : 0) |
            (frame.LeftTrigger > 0 ? 4 : 0) | (frame.RightTrigger > 0 ? 8 : 0) |
            (Has(frame, 0x0020) ? 0x10 : 0) | (Has(frame, 0x0010) ? 0x20 : 0) |
            (Has(frame, 0x0040) ? 0x40 : 0) | (Has(frame, 0x0080) ? 0x80 : 0));
        report[buttonOffset + 2] = (byte)((Has(frame, 0x0400) ? 1 : 0) |
            (Extended(extendedButtons, "Touchpad") ? 2 : 0));
        if (ds5)
        {
            report[5] = frame.LeftTrigger; report[6] = frame.RightTrigger; report[7] = sequence;
            if (Extended(extendedButtons, "MicMute")) report[10] |= 4;
            report[33] = report[37] = 0x80; // Both touch contacts inactive: coordinates aren't in Core's contract.
            report[53] = 0x2a; // Virtual USB supply, full battery.
        }
        else
        {
            report[7] |= (byte)((sequence & 63) << 2);
            report[8] = frame.LeftTrigger; report[9] = frame.RightTrigger;
            report[30] = 0x1b; report[33] = 1; report[34] = sequence;
            report[35] = report[39] = 0x80; // One touch subreport, two inactive contacts.
            report[44] = report[48] = report[53] = report[57] = 0x80;
        }
        var gyroOffset = ds5 ? 16 : 13;
        for (var axis = 0; axis < 3; axis++)
        {
            // Synthetic calibration features below guarantee 16 raw counts/dps, 8192 raw counts/g.
            BinaryPrimitives.WriteInt16LittleEndian(report.AsSpan(gyroOffset + axis * 2),
                Sensor(motion?.GyroDps, axis, 16));
            BinaryPrimitives.WriteInt16LittleEndian(report.AsSpan(gyroOffset + 6 + axis * 2),
                Sensor(motion?.AccelG, axis, 8192));
        }
        var timestamp = motion?.TimestampUs ?? fallbackTimestampUs;
        if (ds5) BinaryPrimitives.WriteUInt32LittleEndian(report.AsSpan(28), unchecked((uint)(timestamp * 3)));
        else BinaryPrimitives.WriteUInt16LittleEndian(report.AsSpan(10), unchecked((ushort)((timestamp / 16 * 3) + timestamp % 16 * 3 / 16)));
        return report;
    }

    public static bool TryParseOutput(SonyPadType type, ReadOnlySpan<byte> report, out SonyOutputReport? parsed)
    {
        ValidateType(type); parsed = null;
        var ds5 = type == SonyPadType.DualSense;
        if (ds5 ? (report.Length is not (48 or 63) || report[0] != 2) : (report.Length != 32 || report[0] != 5)) return false;
        var rumble = ds5 ? ((report[1] & 1) != 0 || (report[39] & 4) != 0) : (report[1] & 1) != 0;
        var rgb = ds5 ? (report[2] & 4) != 0 : (report[1] & 2) != 0;
        var motorOffset = ds5 ? 3 : 4;
        var rgbOffset = ds5 ? 45 : 6;
        // SDL testcontroller.c uses validity bits 04/08 for these exact eleven-byte blocks.
        parsed = new SonyOutputReport(rumble ? report[motorOffset + 1] : null, rumble ? report[motorOffset] : null,
            rgb ? report[rgbOffset] : null, rgb ? report[rgbOffset + 1] : null, rgb ? report[rgbOffset + 2] : null,
            ds5 && (report[1] & 4) != 0 ? report.Slice(11, 11).ToArray() : ReadOnlyMemory<byte>.Empty,
            ds5 && (report[1] & 8) != 0 ? report.Slice(22, 11).ToArray() : ReadOnlyMemory<byte>.Empty);
        return true;
    }

    /// <summary>Minimal virtual-device feature responses required by Linux/SDL's USB probe.
    /// Unknown features are rejected rather than inventing physical capabilities.</summary>
    public static byte[]? Feature(SonyPadType type, byte reportId, ReadOnlySpan<byte> identity)
    {
        ValidateType(type);
        if (identity.Length != 6) throw new ArgumentException("Identity must contain six bytes.", nameof(identity));
        var ds5 = type == SonyPadType.DualSense;
        if (reportId == (ds5 ? 5 : 2))
        {
            var data = new byte[ds5 ? 41 : 37]; data[0] = reportId;
            for (var axis = 0; axis < 3; axis++)
            {
                BinaryPrimitives.WriteInt16LittleEndian(data.AsSpan(7 + axis * 4), 8192);
                BinaryPrimitives.WriteInt16LittleEndian(data.AsSpan(9 + axis * 4), -8192);
                BinaryPrimitives.WriteInt16LittleEndian(data.AsSpan(23 + axis * 4), 8192);
                BinaryPrimitives.WriteInt16LittleEndian(data.AsSpan(25 + axis * 4), -8192);
            }
            BinaryPrimitives.WriteInt16LittleEndian(data.AsSpan(19), 512);
            BinaryPrimitives.WriteInt16LittleEndian(data.AsSpan(21), 512);
            return data;
        }
        if (reportId == (ds5 ? 9 : 0x12) || (!ds5 && reportId == 0x81))
        {
            var data = new byte[ds5 ? 20 : reportId == 0x81 ? 7 : 16]; data[0] = reportId;
            identity.CopyTo(data.AsSpan(1)); return data;
        }
        if (reportId == (ds5 ? 0x20 : 0xa3))
        {
            var data = new byte[ds5 ? 64 : 49]; data[0] = reportId;
            // Deliberately conservative virtual firmware/update version: no audio or physical haptics claims.
            return data;
        }
        return null;
    }
    private static byte Axis(int value) => (byte)Math.Clamp((value + 32768) >> 8, 0, 255);
    private static bool Has(ControllerFrame frame, int mask) => (frame.Buttons & mask) != 0;
    private static bool Extended(IReadOnlySet<string>? buttons, string name) => buttons?.Contains(name) == true;
    private static short Sensor(float[]? values, int axis, int scale)
    {
        if (values is null || axis >= values.Length || !float.IsFinite(values[axis])) return 0;
        return (short)Math.Clamp(Math.Round((double)values[axis] * scale, MidpointRounding.AwayFromZero), short.MinValue, short.MaxValue);
    }
    private static int Hat(int buttons)
    {
        var x = ((buttons & 8) != 0 ? 1 : 0) - ((buttons & 4) != 0 ? 1 : 0);
        var y = ((buttons & 2) != 0 ? 1 : 0) - ((buttons & 1) != 0 ? 1 : 0);
        return (x, y) switch { (0,-1) => 0, (1,-1) => 1, (1,0) => 2, (1,1) => 3,
            (0,1) => 4, (-1,1) => 5, (-1,0) => 6, (-1,-1) => 7, _ => 8 };
    }
    private static void ValidateType(SonyPadType type)
    { if (type is not (SonyPadType.DualShock4 or SonyPadType.DualSense)) throw new ArgumentOutOfRangeException(nameof(type)); }
}
