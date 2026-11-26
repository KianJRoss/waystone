using System.Diagnostics;
using System.Runtime.InteropServices;

namespace UnifiedController.App.Engine;

public sealed class NativeInputHookManager : IDisposable
{
    private const int WH_KEYBOARD_LL = 13;
    private const int WH_MOUSE_LL = 14;

    private const int WM_KEYDOWN = 0x0100;
    private const int WM_KEYUP = 0x0101;
    private const int WM_SYSKEYDOWN = 0x0104;
    private const int WM_SYSKEYUP = 0x0105;

    private const int WM_MOUSEMOVE = 0x0200;
    private const int WM_LBUTTONDOWN = 0x0201;
    private const int WM_LBUTTONUP = 0x0202;
    private const int WM_RBUTTONDOWN = 0x0204;
    private const int WM_RBUTTONUP = 0x0205;
    private const int WM_MBUTTONDOWN = 0x0207;
    private const int WM_MBUTTONUP = 0x0208;
    private const int WM_MOUSEWHEEL = 0x020A;
    private const int WM_XBUTTONDOWN = 0x020B;
    private const int WM_XBUTTONUP = 0x020C;

    private const int LLKHF_INJECTED = 0x10;
    private const int LLMHF_INJECTED = 0x01;
    private const uint InjectMarker = 0x55434D50;

    private const int VK_CONTROL = 0x11;
    private const int VK_LCONTROL = 0xA2;
    private const int VK_RCONTROL = 0xA3;
    private const int VK_MENU = 0x12; // Alt
    private const int VK_LMENU = 0xA4;
    private const int VK_RMENU = 0xA5;

    [StructLayout(LayoutKind.Sequential)]
    private struct KBDLLHOOKSTRUCT
    {
        public uint vkCode;
        public uint scanCode;
        public uint flags;
        public uint time;
        public nuint dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT
    {
        public int x;
        public int y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MSLLHOOKSTRUCT
    {
        public POINT pt;
        public uint mouseData;
        public uint flags;
        public uint time;
        public nuint dwExtraInfo;
    }

    private delegate nint HookProc(int nCode, nint wParam, nint lParam);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern nint SetWindowsHookExW(int idHook, HookProc lpfn, nint hMod, uint dwThreadId);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnhookWindowsHookEx(nint hhk);

    [DllImport("user32.dll")]
    private static extern nint CallNextHookEx(nint hhk, int nCode, nint wParam, nint lParam);

    [DllImport("user32.dll")]
    private static extern short GetAsyncKeyState(int vKey);

    [DllImport("kernel32.dll")]
    private static extern nint GetModuleHandleW(string? lpModuleName);

    [DllImport("user32.dll")]
    private static extern bool GetMessageW(out nint msg, nint hWnd, uint wMsgFilterMin, uint wMsgFilterMax);

    [DllImport("user32.dll")]
    private static extern bool PostThreadMessageW(uint idThread, uint msg, nint wParam, nint lParam);

    private HookProc? _kbProc;
    private HookProc? _mouseProc;
    private nint _hKbHook = nint.Zero;
    private nint _hMouseHook = nint.Zero;
    private Thread? _hookThread;
    private uint _hookThreadId;
    private volatile bool _running;

    private readonly HashSet<int> _activeKeys = [];
    private readonly HashSet<int> _swallowedKeys = [];
    private readonly object _keyLock = new();

    private readonly HashSet<string> _activeMouseButtons = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> _mousePulses = new(StringComparer.OrdinalIgnoreCase);
    private int _mouseDeltaX;
    private int _mouseDeltaY;
    private POINT? _mouseAnchor;
    private readonly object _mouseLock = new();

    public volatile bool KeyboardCaptureEnabled;
    public volatile bool MouseCaptureEnabled;

    public void Start()
    {
        if (_running) return;
        _running = true;

        _hookThread = new Thread(HookLoop)
        {
            IsBackground = true,
            Name = "LowLevelInputHookThread"
        };
        _hookThread.Start();
    }

    public HashSet<int> GetActiveKeys()
    {
        lock (_keyLock)
        {
            return new HashSet<int>(_activeKeys);
        }
    }

    public (int dx, int dy, HashSet<string> buttons, HashSet<string> pulses) GetAndResetMouseState()
    {
        lock (_mouseLock)
        {
            int dx = _mouseDeltaX;
            int dy = _mouseDeltaY;
            _mouseDeltaX = 0;
            _mouseDeltaY = 0;
            var buttons = new HashSet<string>(_activeMouseButtons, StringComparer.OrdinalIgnoreCase);
            var pulses = new HashSet<string>(_mousePulses, StringComparer.OrdinalIgnoreCase);
            _mousePulses.Clear();
            return (dx, dy, buttons, pulses);
        }
    }

    private void HookLoop()
    {
        [DllImport("kernel32.dll")]
        static extern uint GetCurrentThreadId();

        _hookThreadId = GetCurrentThreadId();
        _kbProc = LowLevelKeyboardHandler;
        _mouseProc = LowLevelMouseHandler;

        var hMod = GetModuleHandleW(null);
        _hKbHook = SetWindowsHookExW(WH_KEYBOARD_LL, _kbProc, hMod, 0);
        _hMouseHook = SetWindowsHookExW(WH_MOUSE_LL, _mouseProc, hMod, 0);

        if (_hKbHook == nint.Zero || _hMouseHook == nint.Zero)
        {
            Debug.WriteLine("NativeInputHookManager: Failed to install low-level hooks!");
        }
        else
        {
            Debug.WriteLine("NativeInputHookManager: Low-level keyboard and mouse hooks installed.");
        }

        while (_running && GetMessageW(out _, nint.Zero, 0, 0))
        {
        }

        if (_hKbHook != nint.Zero) UnhookWindowsHookEx(_hKbHook);
        if (_hMouseHook != nint.Zero) UnhookWindowsHookEx(_hMouseHook);
        _hKbHook = nint.Zero;
        _hMouseHook = nint.Zero;
    }

    private nint LowLevelKeyboardHandler(int nCode, nint wParam, nint lParam)
    {
        if (nCode >= 0)
        {
            var kb = Marshal.PtrToStructure<KBDLLHOOKSTRUCT>(lParam);
            if ((kb.flags & LLKHF_INJECTED) != 0 && kb.dwExtraInfo == (nuint)InjectMarker)
                return CallNextHookEx(_hKbHook, nCode, wParam, lParam);

            int vk = (int)kb.vkCode;
            int msg = (int)wParam;
            bool isDown = msg is WM_KEYDOWN or WM_SYSKEYDOWN;
            bool isUp = msg is WM_KEYUP or WM_SYSKEYUP;

            bool suppress = false;

            lock (_keyLock)
            {
                if (isDown)
                {
                    _activeKeys.Add(vk);

                    // Global Ctrl+Alt shortcuts are owned by MainWindow's
                    // RegisterHotKey/WM_HOTKEY path. Handling them here as
                    // well caused one key press to execute an action twice.
                    if (KeyboardCaptureEnabled)
                    {
                        // In keyboard controller mode, swallow game keys
                        if (vk is not (VK_CONTROL or VK_LCONTROL or VK_RCONTROL or VK_MENU or VK_LMENU or VK_RMENU))
                        {
                            _swallowedKeys.Add(vk);
                            suppress = true;
                        }
                    }
                }
                else if (isUp)
                {
                    _activeKeys.Remove(vk);
                    if (_swallowedKeys.Remove(vk))
                        suppress = true;
                }
            }

            if (suppress) return 1;
        }

        return CallNextHookEx(_hKbHook, nCode, wParam, lParam);
    }

    private nint LowLevelMouseHandler(int nCode, nint wParam, nint lParam)
    {
        if (nCode >= 0)
        {
            var ms = Marshal.PtrToStructure<MSLLHOOKSTRUCT>(lParam);
            if ((ms.flags & LLMHF_INJECTED) != 0 && ms.dwExtraInfo == (nuint)InjectMarker)
                return CallNextHookEx(_hMouseHook, nCode, wParam, lParam);

            int msg = (int)wParam;
            if (MouseCaptureEnabled)
            {
                lock (_mouseLock)
                {
                    if (msg == WM_MOUSEMOVE)
                    {
                        if (_mouseAnchor == null)
                        {
                            _mouseAnchor = ms.pt;
                        }
                        else
                        {
                            _mouseDeltaX += ms.pt.x - _mouseAnchor.Value.x;
                            _mouseDeltaY += ms.pt.y - _mouseAnchor.Value.y;
                            _mouseAnchor = ms.pt;
                        }
                    }
                    else if (msg == WM_LBUTTONDOWN) _activeMouseButtons.Add("Left");
                    else if (msg == WM_LBUTTONUP) _activeMouseButtons.Remove("Left");
                    else if (msg == WM_RBUTTONDOWN) _activeMouseButtons.Add("Right");
                    else if (msg == WM_RBUTTONUP) _activeMouseButtons.Remove("Right");
                    else if (msg == WM_MBUTTONDOWN) _activeMouseButtons.Add("Middle");
                    else if (msg == WM_MBUTTONUP) _activeMouseButtons.Remove("Middle");
                    else if (msg == WM_MOUSEWHEEL)
                    {
                        short delta = (short)((ms.mouseData >> 16) & 0xFFFF);
                        _mousePulses.Add(delta > 0 ? "Wheel Up" : "Wheel Down");
                    }
                    else if (msg is WM_XBUTTONDOWN or WM_XBUTTONUP)
                    {
                        string name = ((ms.mouseData >> 16) & 0xFFFF) == 1 ? "X1" : "X2";
                        if (msg == WM_XBUTTONDOWN) _activeMouseButtons.Add(name);
                        else _activeMouseButtons.Remove(name);
                    }
                }
                return 1; // Suppress cursor movement while aiming
            }
            else
            {
                lock (_mouseLock)
                {
                    _mouseAnchor = null;
                }
            }
        }

        return CallNextHookEx(_hMouseHook, nCode, wParam, lParam);
    }

    public void Dispose()
    {
        _running = false;
        if (_hookThreadId != 0)
        {
            PostThreadMessageW(_hookThreadId, 0x0012, nint.Zero, nint.Zero); // WM_QUIT
        }
        try
        {
            _hookThread?.Join(500);
        }
        catch { }
    }
}
