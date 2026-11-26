using System.Runtime.InteropServices;

namespace UnifiedController.Linux.Platform.Input;

/// <summary>SDL 2.32 ABI used by sdl2-compat. Only SDL2-exported entry points are declared.</summary>
internal static class SdlNative
{
    internal const string Library = "libSDL2-2.0.so.0";
    internal const uint InitJoystick = 0x00000200;
    internal const uint InitGameController = 0x00002000;
    internal const uint InitSensor = 0x00008000;
    internal const int SensorInvalid = -1;
    internal const int SensorAccel = 1;
    internal const int SensorGyro = 2;

    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern uint SDL_WasInit(uint flags);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_InitSubSystem(uint flags);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern void SDL_QuitSubSystem(uint flags);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_SetHint([MarshalAs(UnmanagedType.LPUTF8Str)] string name, [MarshalAs(UnmanagedType.LPUTF8Str)] string value);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern IntPtr SDL_GetError();
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_NumJoysticks();
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_IsGameController(int joystickIndex);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern IntPtr SDL_GameControllerOpen(int joystickIndex);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern void SDL_GameControllerClose(IntPtr controller);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern IntPtr SDL_GameControllerGetJoystick(IntPtr controller);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern IntPtr SDL_GameControllerName(IntPtr controller);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern short SDL_GameControllerGetAxis(IntPtr controller, int axis);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern byte SDL_GameControllerGetButton(IntPtr controller, int button);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_GameControllerHasSensor(IntPtr controller, int sensorType);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_GameControllerSetSensorEnabled(IntPtr controller, int sensorType, int enabled);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_GameControllerGetSensorData(IntPtr controller, int sensorType, [Out] float[] data, int numValues);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_GameControllerGetSensorDataWithTimestamp(IntPtr controller, int sensorType, out ulong timestamp, [Out] float[] data, int numValues);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_GameControllerRumble(IntPtr controller, ushort lowFrequency, ushort highFrequency, uint durationMs);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern void SDL_GameControllerUpdate();
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern void SDL_PumpEvents();
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern void SDL_FlushEvents(uint minType, uint maxType);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_JoystickInstanceID(IntPtr joystick);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern ushort SDL_JoystickGetVendor(IntPtr joystick);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern ushort SDL_JoystickGetProduct(IntPtr joystick);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern IntPtr SDL_JoystickGetSerial(IntPtr joystick);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern IntPtr SDL_JoystickPath(IntPtr joystick);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern Guid SDL_JoystickGetGUID(IntPtr joystick);
    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)] internal static extern int SDL_JoystickGetAttached(IntPtr joystick);

    internal static string Utf8(IntPtr value) => value == IntPtr.Zero ? string.Empty : Marshal.PtrToStringUTF8(value) ?? string.Empty;
    internal static string Error() => Utf8(SDL_GetError());
}
