using System.Runtime.InteropServices;

namespace UnifiedController.Linux.Platform.Output;

internal static partial class UInputNative
{
    internal const int EvSyn=0, EvKey=1, EvRel=2, EvAbs=3, EvFf=0x15, EvUinput=0x101;
    internal const int FfRumble=0x50, FfGain=0x60, UiFfUpload=1, UiFfErase=2;
    internal const int SynReport=0, RelX=0, RelY=1, RelWheel=8;
    internal const int AbsX=0, AbsY=1, AbsZ=2, AbsRx=3, AbsRy=4, AbsRz=5, AbsHat0X=16, AbsHat0Y=17;
    // Xbox pads (xpad driver, SDL mappings for 045e:028e) report the left face button as BTN_X (307)
    // and the top one as BTN_Y (308), despite the BTN_NORTH/BTN_WEST aliases for those codes.
    internal const int BtnSouth=304, BtnEast=305, BtnX=307, BtnY=308, BtnTl=310, BtnTr=311,
        BtnSelect=314, BtnStart=315, BtnMode=316, BtnThumbL=317, BtnThumbR=318;
    internal const int BusVirtual=6;
    // File descriptor or LIBEVDEV_UINPUT_OPEN_MANAGED (-2), not a flags value.
    internal const int UinputOpenManaged=-2;

    [StructLayout(LayoutKind.Sequential)] internal struct InputAbsInfo { internal int value, minimum, maximum, fuzz, flat, resolution; }
    [StructLayout(LayoutKind.Sequential)] internal struct TimeVal { internal nint Seconds, Microseconds; }
    [StructLayout(LayoutKind.Sequential)] internal struct InputEvent { internal TimeVal Time; internal ushort Type, Code; internal int Value; }
    [StructLayout(LayoutKind.Sequential)] internal struct InputId { internal ushort BusType, Vendor, Product, Version; }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)] internal unsafe struct UInputSetup { internal InputId Id; internal fixed byte Name[80]; internal uint FfEffectsMax; }
    [StructLayout(LayoutKind.Sequential)] internal struct UInputAbsSetup { internal ushort Code; internal ushort Padding; internal InputAbsInfo AbsInfo; }
    [StructLayout(LayoutKind.Sequential)] internal struct FfReplay { internal ushort Length, Delay; }
    [StructLayout(LayoutKind.Sequential)] internal struct FfTrigger { internal ushort Button, Interval; }
    [StructLayout(LayoutKind.Explicit, Size=32)] internal struct FfUnion { [FieldOffset(0)] internal ushort StrongMagnitude; [FieldOffset(2)] internal ushort WeakMagnitude; }
    // ff_effect's union is 8-byte aligned on x86_64, leaving two bytes after replay.
    [StructLayout(LayoutKind.Sequential)] internal struct FfEffect { internal ushort Type; internal short Id; internal ushort Direction; internal FfTrigger Trigger; internal FfReplay Replay; internal ushort Padding; internal FfUnion Data; }
    [StructLayout(LayoutKind.Sequential)] internal struct UInputFfUpload { internal uint RequestId; internal int Retval; internal FfEffect Effect, Old; }
    [StructLayout(LayoutKind.Sequential)] internal struct UInputFfErase { internal uint RequestId; internal int Retval, EffectId; }

    internal const int ORdwr=2, ONonblock=0x800;
    [LibraryImport("libc", SetLastError=true, StringMarshalling=StringMarshalling.Utf8)] internal static partial int open(string path, int flags);
    [LibraryImport("libc", SetLastError=true)] internal static partial int close(int fd);
    [LibraryImport("libc", SetLastError=true)] internal static partial nint read(int fd, nint buffer, nuint count);
    [LibraryImport("libc", SetLastError=true)] internal static partial nint write(int fd, nint buffer, nuint count);
    [LibraryImport("libc", SetLastError=true)] internal static partial int ioctl(int fd, nuint request, nint arg);

    [LibraryImport("libevdev.so.2")] internal static partial nint libevdev_new();
    [LibraryImport("libevdev.so.2")] internal static partial void libevdev_free(nint dev);
    [LibraryImport("libevdev.so.2", StringMarshalling=StringMarshalling.Utf8)] internal static partial void libevdev_set_name(nint dev, string name);
    [LibraryImport("libevdev.so.2", StringMarshalling=StringMarshalling.Utf8)] internal static partial void libevdev_set_phys(nint dev, string phys);
    [LibraryImport("libevdev.so.2", StringMarshalling=StringMarshalling.Utf8)] internal static partial void libevdev_set_uniq(nint dev, string uniq);
    [LibraryImport("libevdev.so.2")] internal static partial void libevdev_set_id_bustype(nint dev, int value);
    [LibraryImport("libevdev.so.2")] internal static partial void libevdev_set_id_vendor(nint dev, int value);
    [LibraryImport("libevdev.so.2")] internal static partial void libevdev_set_id_product(nint dev, int value);
    [LibraryImport("libevdev.so.2")] internal static partial void libevdev_set_id_version(nint dev, int value);
    [LibraryImport("libevdev.so.2")] internal static partial int libevdev_enable_event_type(nint dev, uint type);
    [LibraryImport("libevdev.so.2")] internal static partial int libevdev_enable_event_code(nint dev, uint type, uint code, nint data);
    [LibraryImport("libevdev.so.2")] internal static partial void libevdev_set_num_slots(nint dev, int numSlots);
    [LibraryImport("libevdev.so.2")] internal static partial int libevdev_uinput_create_from_device(nint dev, int flags, out nint uinput);
    [LibraryImport("libevdev.so.2")] internal static partial void libevdev_uinput_destroy(nint uinput);
    [LibraryImport("libevdev.so.2")] internal static partial int libevdev_uinput_write_event(nint uinput, uint type, uint code, int value);

    internal static void Check(int result, string operation) { if (result < 0) throw new IOException($"{operation} failed: errno {-result}."); }
    internal static unsafe void EnableAbs(nint device, int code, int min, int max)
    {
        var info = new InputAbsInfo { minimum=min, maximum=max };
        Check(libevdev_enable_event_code(device, EvAbs, (uint)code, (nint)(&info)), $"enable ABS {code}");
    }
}
