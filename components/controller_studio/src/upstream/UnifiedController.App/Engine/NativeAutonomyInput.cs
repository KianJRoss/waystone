using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text.Json;

namespace UnifiedController.App.Engine;

/// <summary>
/// Opt-in local command channel that lets an agent (a game-playing bot) press the virtual controller,
/// ported from the Python engine's autonomy_input.py. Safe by construction:
/// - off unless the active profile has "fishing_autonomy_enabled": true ("Patrol input" in the UI);
/// - loopback UDP only (127.0.0.1:28741), JSON commands that must be under a second old, with a session id
///   and an increasing sequence (no replays);
/// - every command is a short pulse (50-400 ms) that expires by itself, so a hung agent can't hold input;
/// - any physical controller input suspends the session at once (the person always wins); a new session can
///   start only after 2 s without physical input. Ctrl+Alt+F8 (cancel macros) suspends it too.
/// Command: {"session":"s1","sequence":1,"issued_at":&lt;unix seconds&gt;,"action":"move","lx":0.6,"ly":0,"duration_ms":300}
/// Actions: move (left stick lx/ly in -1..1), cast/confirm (B pulse), cancel/close (A pulse), prime (RB pulse), wait/stop,
/// and input: a full controller state for the pulse, so an agent can play the whole game:
/// {"action":"input","buttons":["A","ZR"],"lx":0,"ly":1,"rx":0,"ry":0,"duration_ms":200}. Buttons use Switch names
/// (Citron maps the Xbox pad by position): A B X Y L R ZL ZR PLUS MINUS LS RS UP DOWN LEFT RIGHT. HOME and
/// capture are not available. Re-send before it expires to hold.
/// </summary>
public sealed class NativeAutonomyInput : IDisposable
{
    public const int Port = 28741;
    private const short ManualStickDeadzone = 9000;
    private readonly UdpClient? _udp;
    private readonly Thread? _thread;
    private readonly object _lock = new();
    private volatile bool _running = true;
    private int _buttons;
    private short? _lx, _ly, _rx, _ry;
    private byte? _lt, _rt;

    // Switch button name -> XInput bit on the virtual pad (Citron's positional mapping: Switch A = Xbox B, ...).
    private static readonly Dictionary<string, int> SwitchButtons = new(StringComparer.OrdinalIgnoreCase)
    {
        ["A"] = 0x2000, ["B"] = 0x1000, ["X"] = 0x8000, ["Y"] = 0x4000, ["L"] = 0x0100, ["R"] = 0x0200,
        ["PLUS"] = 0x0010, ["MINUS"] = 0x0020, ["LS"] = 0x0040, ["RS"] = 0x0080,
        ["UP"] = 0x0001, ["DOWN"] = 0x0002, ["LEFT"] = 0x0004, ["RIGHT"] = 0x0008,
    };
    private double _until;
    private string? _session;
    private long _sequence;
    private bool _suspended;
    private double _lastManual = double.NegativeInfinity;
    private double _now;

    public volatile bool Enabled;
    public string LastError { get; private set; } = "";
    public string State
    {
        get
        {
            lock (_lock)
                return !Enabled ? "off" : _suspended ? "suspended" : _now < _until ? "active" : "armed";
        }
    }

    public NativeAutonomyInput()
    {
        try
        {
            _udp = new UdpClient(new IPEndPoint(IPAddress.Loopback, Port));
            _thread = new Thread(ReceiveLoop) { IsBackground = true, Name = "AutonomyInput" };
            _thread.Start();
        }
        catch (SocketException ex)
        {
            LastError = "port in use: " + ex.Message;
        }
    }

    /// <summary>The active profile's "fishing_autonomy_enabled" flag (read on profile reload).</summary>
    public static bool ReadEnabled(string profilesPath)
    {
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(profilesPath));
            var root = document.RootElement;
            var profile = root.GetProperty("profiles").GetProperty(root.GetProperty("active_profile").GetString()!);
            return profile.TryGetProperty("fishing_autonomy_enabled", out var flag) && flag.ValueKind == JsonValueKind.True;
        }
        catch
        {
            return false;
        }
    }

    public void Suspend()
    {
        lock (_lock)
        {
            _suspended = true;
            _until = 0;
        }
    }

    /// <summary>Called once per engine frame, just before the report goes to the virtual controller.</summary>
    public void Apply(ref NativeControllerReport report, in NativeControllerReport physical, double now)
    {
        lock (_lock)
        {
            _now = now;
            var manual = physical.Buttons != 0 || physical.LeftTrigger > 30 || physical.RightTrigger > 30 ||
                         Math.Abs((int)physical.LeftX) > ManualStickDeadzone || Math.Abs((int)physical.LeftY) > ManualStickDeadzone ||
                         Math.Abs((int)physical.RightX) > ManualStickDeadzone || Math.Abs((int)physical.RightY) > ManualStickDeadzone;
            if (manual)
            {
                _lastManual = now;
                if (_session != null) _suspended = true;
            }
            if (!Enabled || _suspended || now >= _until) return;
            report.Buttons |= _buttons;
            if (_lx.HasValue) report.LeftX = _lx.Value;
            if (_ly.HasValue) report.LeftY = _ly.Value;
            if (_rx.HasValue) report.RightX = _rx.Value;
            if (_ry.HasValue) report.RightY = _ry.Value;
            if (_lt.HasValue) report.LeftTrigger = _lt.Value;
            if (_rt.HasValue) report.RightTrigger = _rt.Value;
        }
    }

    private void ReceiveLoop()
    {
        var any = new IPEndPoint(IPAddress.Any, 0);
        while (_running)
        {
            try
            {
                var data = _udp!.Receive(ref any);
                Handle(data);
            }
            catch (SocketException) when (!_running) { }
            catch (ObjectDisposedException) { return; }
            catch (Exception ex) { LastError = ex.Message; }
        }
    }

    private void Handle(byte[] data)
    {
        using var document = JsonDocument.Parse(data);
        var m = document.RootElement;
        var unixNow = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
        var issued = m.GetProperty("issued_at").GetDouble();
        if (!(unixNow - issued is >= -0.05 and <= 1.0)) { LastError = "expired command"; return; }
        var session = m.GetProperty("session").GetString() ?? "";
        var sequence = m.GetProperty("sequence").GetInt64();
        var action = m.GetProperty("action").GetString() ?? "";
        if (session.Length is < 1 or > 64 || sequence < 1) { LastError = "invalid session/sequence"; return; }
        lock (_lock)
        {
            if (!Enabled) { LastError = "disabled in profile"; return; }
            if (session != _session)
            {
                if (_now - _lastManual < 2.0) { LastError = "person is using the controller"; return; }
                _session = session; _sequence = 0; _suspended = false;
            }
            if (_suspended) { LastError = "suspended by physical input"; return; }
            if (sequence <= _sequence) { LastError = "replayed sequence"; return; }
            _sequence = sequence;
            int buttons = 0; short? lx = null, ly = null, rx = null, ry = null; byte? lt = null, rt = null;
            double seconds;
            if (action is "wait" or "stop")
            {
                _until = 0;
                return;
            }
            var duration = m.TryGetProperty("duration_ms", out var d) ? d.GetInt32() : 0;
            if (duration is < 50 or > 400) { LastError = "duration must be 50..400 ms"; return; }
            seconds = duration / 1000.0;
            switch (action)
            {
                case "cast" or "confirm": buttons = 0x2000; seconds = Math.Min(seconds, .15); break;
                case "cancel" or "close": buttons = 0x1000; seconds = Math.Min(seconds, .15); break;
                case "prime": buttons = 0x0200; seconds = Math.Min(seconds, .15); break;
                case "move":
                    var x = m.GetProperty("lx").GetDouble();
                    var y = m.GetProperty("ly").GetDouble();
                    if (!double.IsFinite(x) || !double.IsFinite(y) || Math.Abs(x) > 1 || Math.Abs(y) > 1) { LastError = "invalid axes"; return; }
                    lx = (short)Math.Round(x * 32767); ly = (short)Math.Round(y * 32767);
                    break;
                case "input":
                    if (m.TryGetProperty("buttons", out var names))
                    {
                        if (names.ValueKind != JsonValueKind.Array) { LastError = "buttons must be a list"; return; }
                        foreach (var item in names.EnumerateArray())
                        {
                            var name = item.GetString() ?? "";
                            if (name.Equals("ZL", StringComparison.OrdinalIgnoreCase)) lt = 255;
                            else if (name.Equals("ZR", StringComparison.OrdinalIgnoreCase)) rt = 255;
                            else if (SwitchButtons.TryGetValue(name, out var bit)) buttons |= bit;
                            else { LastError = $"unknown button {name}"; return; }
                        }
                    }
                    if (!Axis(m, "lx", ref lx) || !Axis(m, "ly", ref ly) || !Axis(m, "rx", ref rx) || !Axis(m, "ry", ref ry))
                    {
                        LastError = "invalid axes"; return;
                    }
                    break;
                default: LastError = "action is not allowed"; return;
            }
            _buttons = buttons; _lx = lx; _ly = ly; _rx = rx; _ry = ry; _lt = lt; _rt = rt;
            _until = _now + seconds;
            LastError = "";
        }
    }

    /// <summary>Optional stick axis in -1..1; false only when present and invalid.</summary>
    private static bool Axis(JsonElement m, string name, ref short? value)
    {
        if (!m.TryGetProperty(name, out var axis)) return true;
        if (axis.ValueKind != JsonValueKind.Number) return false;
        var v = axis.GetDouble();
        if (!double.IsFinite(v) || Math.Abs(v) > 1) return false;
        value = (short)Math.Round(v * 32767);
        return true;
    }

    public void Dispose()
    {
        _running = false;
        _udp?.Dispose();
    }
}
