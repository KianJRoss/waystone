using System.Runtime.InteropServices;

namespace UnifiedController.App.Engine;

public sealed class NativeDesktopOutput : IDisposable
{
    private const nuint InjectMarker = 0x55434D50;
    private readonly HashSet<ushort> _heldKeys = [];
    private readonly HashSet<string> _heldButtons = new(StringComparer.OrdinalIgnoreCase);
    private HashSet<string> _previousActions = new(StringComparer.OrdinalIgnoreCase);
    private double _movementStarted;
    private double _lastMovement;
    private double _analogRemainderX;
    private double _analogRemainderY;

    public int HeldKeyCount => _heldKeys.Count;
    public int HeldMouseButtonCount => _heldButtons.Count;

    public void Update(IReadOnlySet<string> actions, double now)
    {
        var desiredKeys = new HashSet<ushort>();
        var desiredButtons = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var action in actions)
        {
            if (action.StartsWith("Desktop:Key:", StringComparison.OrdinalIgnoreCase) &&
                TryResolveKey(action[12..], out var key)) desiredKeys.Add(key);
            else if (action.StartsWith("Desktop:Mouse:", StringComparison.OrdinalIgnoreCase))
                desiredButtons.Add(action[14..]);
        }

        foreach (var key in _heldKeys.Except(desiredKeys).ToArray()) SendKey(key, false);
        foreach (var key in desiredKeys.Except(_heldKeys).ToArray()) SendKey(key, true);
        _heldKeys.Clear();
        _heldKeys.UnionWith(desiredKeys);

        foreach (var button in _heldButtons.Except(desiredButtons).ToArray()) SendMouseButton(button, false);
        foreach (var button in desiredButtons.Except(_heldButtons).ToArray()) SendMouseButton(button, true);
        _heldButtons.Clear();
        _heldButtons.UnionWith(desiredButtons);

        if (actions.Contains("Desktop:Wheel:Up") && !_previousActions.Contains("Desktop:Wheel:Up")) SendWheel(120);
        if (actions.Contains("Desktop:Wheel:Down") && !_previousActions.Contains("Desktop:Wheel:Down")) SendWheel(-120);

        var x = (actions.Contains("Desktop:Move:Right") ? 1 : 0) - (actions.Contains("Desktop:Move:Left") ? 1 : 0);
        var y = (actions.Contains("Desktop:Move:Down") ? 1 : 0) - (actions.Contains("Desktop:Move:Up") ? 1 : 0);
        if (x != 0 || y != 0)
        {
            if (_movementStarted <= 0) _movementStarted = now;
            if (now - _lastMovement >= 0.008)
            {
                var speed = 3 + (int)Math.Round(Math.Min(21, (now - _movementStarted) * 14));
                if (x != 0 && y != 0) speed = Math.Max(2, (int)Math.Round(speed * 0.707));
                SendMouseMove(x * speed, y * speed);
                _lastMovement = now;
            }
        }
        else _movementStarted = 0;

        _previousActions = new HashSet<string>(actions, StringComparer.OrdinalIgnoreCase);
    }

    public void UpdateAnalogPointer(short rawX, short rawY, double sensitivity, double deadzone, double now)
    {
        if (now - _lastMovement < 0.008) return;
        var x = rawX / (rawX < 0 ? 32768.0 : 32767.0);
        var y = rawY / (rawY < 0 ? 32768.0 : 32767.0);
        var magnitude = Math.Sqrt(x * x + y * y);
        if (magnitude <= deadzone)
        {
            _analogRemainderX = 0;
            _analogRemainderY = 0;
            return;
        }
        var scaled = Math.Min(1.0, (magnitude - deadzone) / Math.Max(0.01, 1.0 - deadzone));
        scaled *= scaled;
        var pixels = sensitivity * scaled;
        _analogRemainderX += x / magnitude * pixels;
        _analogRemainderY += -y / magnitude * pixels;
        var moveX = (int)Math.Truncate(_analogRemainderX);
        var moveY = (int)Math.Truncate(_analogRemainderY);
        if (moveX != 0 || moveY != 0)
        {
            SendMouseMove(moveX, moveY);
            _analogRemainderX -= moveX;
            _analogRemainderY -= moveY;
        }
        _lastMovement = now;
    }

    public void ReleaseAll()
    {
        foreach (var key in _heldKeys.ToArray()) SendKey(key, false);
        foreach (var button in _heldButtons.ToArray()) SendMouseButton(button, false);
        _heldKeys.Clear();
        _heldButtons.Clear();
        _previousActions.Clear();
        _movementStarted = 0;
        _analogRemainderX = 0;
        _analogRemainderY = 0;
    }

    public static bool TryResolveKey(string name, out ushort key)
    {
        key = 0;
        if (name.Length == 1)
        {
            var ch = char.ToUpperInvariant(name[0]);
            if (ch is >= 'A' and <= 'Z' or >= '0' and <= '9') { key = ch; return true; }
        }
        if (name.StartsWith('F') && int.TryParse(name[1..], out var function) && function is >= 1 and <= 24)
        { key = (ushort)(0x6F + function); return true; }
        return KeyNames.TryGetValue(name, out key);
    }

    private static readonly Dictionary<string, ushort> KeyNames = new(StringComparer.OrdinalIgnoreCase)
    {
        ["Escape"] = 0x1B, ["Esc"] = 0x1B, ["Tab"] = 0x09, ["Enter"] = 0x0D,
        ["Space"] = 0x20, ["Backspace"] = 0x08, ["Delete"] = 0x2E, ["Insert"] = 0x2D,
        ["Home"] = 0x24, ["End"] = 0x23, ["Page Up"] = 0x21, ["Page Down"] = 0x22,
        ["Left"] = 0x25, ["Up"] = 0x26, ["Right"] = 0x27, ["Down"] = 0x28,
        ["Left Shift"] = 0xA0, ["Right Shift"] = 0xA1, ["Left Ctrl"] = 0xA2, ["Right Ctrl"] = 0xA3,
        ["Left Alt"] = 0xA4, ["Right Alt"] = 0xA5, ["Windows"] = 0x5B,
        ["Volume Mute"] = 0xAD, ["Volume Down"] = 0xAE, ["Volume Up"] = 0xAF,
        ["Media Previous"] = 0xB1, ["Media Next"] = 0xB0, ["Media Play Pause"] = 0xB3
    };

    private static void SendKey(ushort key, bool down) => Send(new INPUT
    {
        type = 1,
        U = new InputUnion { ki = new KEYBDINPUT { wVk = key, dwFlags = down ? 0u : 0x0002u, dwExtraInfo = InjectMarker } }
    });

    private static void SendMouseButton(string button, bool down)
    {
        var flag = button.ToLowerInvariant() switch
        {
            "left" => down ? 0x0002u : 0x0004u,
            "right" => down ? 0x0008u : 0x0010u,
            "middle" => down ? 0x0020u : 0x0040u,
            "x1" or "x2" => down ? 0x0080u : 0x0100u,
            _ => 0u
        };
        if (flag == 0) return;
        Send(new INPUT { type = 0, U = new InputUnion { mi = new MOUSEINPUT { dwFlags = flag, mouseData = button.Equals("x2", StringComparison.OrdinalIgnoreCase) ? 2u : 1u, dwExtraInfo = InjectMarker } } });
    }

    private static void SendWheel(int amount) => Send(new INPUT
    { type = 0, U = new InputUnion { mi = new MOUSEINPUT { dwFlags = 0x0800, mouseData = unchecked((uint)amount), dwExtraInfo = InjectMarker } } });
    private static void SendMouseMove(int x, int y) => Send(new INPUT
    { type = 0, U = new InputUnion { mi = new MOUSEINPUT { dx = x, dy = y, dwFlags = 0x0001, dwExtraInfo = InjectMarker } } });
    private static void Send(INPUT input) => SendInput(1, [input], Marshal.SizeOf<INPUT>());

    public void Dispose() => ReleaseAll();

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint count, INPUT[] inputs, int size);
    [StructLayout(LayoutKind.Sequential)] private struct INPUT { public uint type; public InputUnion U; }
    [StructLayout(LayoutKind.Explicit)] private struct InputUnion
    { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
    [StructLayout(LayoutKind.Sequential)] private struct MOUSEINPUT
    { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public nuint dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)] private struct KEYBDINPUT
    { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public nuint dwExtraInfo; }
}
