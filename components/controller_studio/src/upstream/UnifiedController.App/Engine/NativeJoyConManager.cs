using HidSharp;
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace UnifiedController.App.Engine;

public enum NativeJoyConSide { Left, Right, Pro }

public sealed record NativeJoyConDevice(
    string Id, string Name, string Serial, NativeJoyConSide Side,
    bool Connected, int BatteryPercent, DateTime LastSeenUtc, string LastInput);

public sealed record NativeJoyConInput(
    string Id, string Name, string[] SourceIds, NativeControllerReport Report);

public sealed record NativeJoyConPair(string Id, string LeftId, string RightId, string Name);

/// <summary>
/// Native Nintendo HID provider. It consumes the standard 0x30 Bluetooth report
/// directly so Joy-Cons do not need BetterJoy or another controller translator.
/// ViGEm remains the common virtual output used by the rest of the application.
/// </summary>
public sealed class NativeJoyConManager : IDisposable
{
    private const int NintendoVendorId = 0x057E;
    private readonly object _gate = new();
    private readonly Dictionary<string, Reader> _readers = new(StringComparer.OrdinalIgnoreCase);
    private readonly List<NativeJoyConPair> _pairs = [];
    private readonly string _pairsPath;
    private Thread? _discoveryThread;
    private volatile bool _running;

    public NativeJoyConManager(string rootDirectory)
    {
        _pairsPath = Path.Combine(rootDirectory, "joycon_pairs.json");
        LoadPairs();
    }

    public IReadOnlyList<NativeJoyConPair> Pairs
    {
        get { lock (_gate) return _pairs.ToArray(); }
    }

    public IReadOnlyList<NativeJoyConDevice> Devices
    {
        get
        {
            lock (_gate)
                return _readers.Values.Select(reader => reader.Device).OrderBy(device => device.Name).ToArray();
        }
    }

    public void Start()
    {
        if (_running) return;
        _running = true;
        Discover();
        _discoveryThread = new Thread(DiscoveryLoop)
        {
            IsBackground = true,
            Name = "NativeJoyConDiscovery",
            Priority = ThreadPriority.BelowNormal
        };
        _discoveryThread.Start();
    }

    public IReadOnlyList<NativeJoyConInput> GetLogicalInputs()
    {
        Reader[] ready;
        lock (_gate) ready = _readers.Values.Where(reader => reader.Device.Connected && reader.HasReport).ToArray();

        DetectPairGesture(ready);

        var inputs = new List<NativeJoyConInput>();
        var left = ready.Where(reader => reader.Side == NativeJoyConSide.Left).OrderBy(reader => reader.Serial).ToList();
        var right = ready.Where(reader => reader.Side == NativeJoyConSide.Right).OrderBy(reader => reader.Serial).ToList();

        NativeJoyConPair[] pairs;
        lock (_gate) pairs = _pairs.ToArray();
        foreach (var pair in pairs)
        {
            var l = left.FirstOrDefault(reader => reader.Id.Equals(pair.LeftId, StringComparison.OrdinalIgnoreCase));
            var r = right.FirstOrDefault(reader => reader.Id.Equals(pair.RightId, StringComparison.OrdinalIgnoreCase));
            if (l == null || r == null) continue;
            left.Remove(l);
            right.Remove(r);
            inputs.Add(new NativeJoyConInput(
                pair.Id, pair.Name, [l.Id, r.Id],
                NativeReportMerger.Merge([l.GetReport(false), r.GetReport(false)])));
        }

        inputs.AddRange(left.Select(reader => new NativeJoyConInput(
            reader.Id, "Joy-Con (L)", [reader.Id], reader.GetReport(true))));
        inputs.AddRange(right.Select(reader => new NativeJoyConInput(
            reader.Id, "Joy-Con (R)", [reader.Id], reader.GetReport(true))));
        inputs.AddRange(ready.Where(reader => reader.Side == NativeJoyConSide.Pro).Select(reader =>
            new NativeJoyConInput(reader.Id, reader.Device.Name, [reader.Id], reader.GetReport(false))));
        return inputs;
    }

    private void DetectPairGesture(IReadOnlyCollection<Reader> ready)
    {
        var pressedLeft = ready.Where(reader => reader.Side == NativeJoyConSide.Left && reader.PairShoulderPressed).ToArray();
        var pressedRight = ready.Where(reader => reader.Side == NativeJoyConSide.Right && reader.PairShoulderPressed).ToArray();
        if (pressedLeft.Length != 1 || pressedRight.Length != 1) return;

        var left = pressedLeft[0];
        var right = pressedRight[0];
        lock (_gate)
        {
            if (_pairs.Any(pair => pair.LeftId.Equals(left.Id, StringComparison.OrdinalIgnoreCase) &&
                                   pair.RightId.Equals(right.Id, StringComparison.OrdinalIgnoreCase))) return;
            _pairs.RemoveAll(pair => pair.LeftId.Equals(left.Id, StringComparison.OrdinalIgnoreCase) ||
                                     pair.RightId.Equals(right.Id, StringComparison.OrdinalIgnoreCase));
            var id = $"joycon-pair-{left.Serial}-{right.Serial}";
            _pairs.Add(new NativeJoyConPair(id, left.Id, right.Id, $"Joy-Con Pair {_pairs.Count + 1}"));
            SavePairs();
        }
    }

    public bool RemovePair(string pairId)
    {
        lock (_gate)
        {
            var removed = _pairs.RemoveAll(pair => pair.Id.Equals(pairId, StringComparison.OrdinalIgnoreCase)) > 0;
            if (removed) SavePairs();
            return removed;
        }
    }

    private void LoadPairs()
    {
        try
        {
            if (!File.Exists(_pairsPath)) return;
            var root = JsonNode.Parse(File.ReadAllText(_pairsPath)) as JsonObject;
            if (root?["pairs"] is not JsonArray pairs) return;
            foreach (var node in pairs.OfType<JsonObject>())
            {
                var id = node["id"]?.GetValue<string>();
                var left = node["left_id"]?.GetValue<string>();
                var right = node["right_id"]?.GetValue<string>();
                if (string.IsNullOrWhiteSpace(id) || string.IsNullOrWhiteSpace(left) || string.IsNullOrWhiteSpace(right)) continue;
                _pairs.Add(new NativeJoyConPair(id, left, right,
                    node["name"]?.GetValue<string>() ?? $"Joy-Con Pair {_pairs.Count + 1}"));
            }
        }
        catch { }
    }

    private void SavePairs()
    {
        try
        {
            var root = new JsonObject
            {
                ["version"] = 1,
                ["pairs"] = new JsonArray(_pairs.Select(pair => (JsonNode)new JsonObject
                {
                    ["id"] = pair.Id,
                    ["left_id"] = pair.LeftId,
                    ["right_id"] = pair.RightId,
                    ["name"] = pair.Name
                }).ToArray())
            };
            var temporary = _pairsPath + ".tmp";
            File.WriteAllText(temporary, root.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
            File.Move(temporary, _pairsPath, true);
        }
        catch { }
    }

    private void DiscoveryLoop()
    {
        while (_running)
        {
            try { Discover(); } catch { }
            Thread.Sleep(1000);
        }
    }

    private void Discover()
    {
        var found = DeviceList.Local.GetHidDevices(NintendoVendorId)
            .Where(device => device.ProductID is 0x2006 or 0x2007 or 0x2009)
            .ToArray();
        var paths = found.Select(device => device.DevicePath).ToHashSet(StringComparer.OrdinalIgnoreCase);

        lock (_gate)
        {
            foreach (var device in found)
            {
                if (_readers.ContainsKey(device.DevicePath)) continue;
                var reader = new Reader(device);
                _readers[device.DevicePath] = reader;
                reader.Start();
            }

            foreach (var reader in _readers.Values.Where(reader => !paths.Contains(reader.Path)).ToArray())
                reader.MarkDisconnected();
        }
    }

    public void Dispose()
    {
        _running = false;
        try { _discoveryThread?.Join(1200); } catch { }
        Reader[] readers;
        lock (_gate) readers = _readers.Values.ToArray();
        foreach (var reader in readers) reader.Dispose();
    }

    private sealed class Reader : IDisposable
    {
        private readonly object _gate = new();
        private readonly HidDevice _hid;
        private Thread? _thread;
        private volatile bool _running;
        private NativeControllerReport _report = new();
        private byte _button3;
        private byte _button4;
        private byte _button5;
        private ushort _stickX = 2048;
        private ushort _stickY = 2048;
        private short _axisX;
        private short _axisY;
        private int _centerSamples;
        private long _centerSumX;
        private long _centerSumY;
        private ushort _lastCenterX;
        private ushort _lastCenterY;
        private double _centerX = 2048;
        private double _centerY = 2048;
        private bool _centerReady;
        private int _battery;
        private DateTime _lastSeen;
        private string _lastInput = "Waiting";

        public string Path => _hid.DevicePath;
        public string Serial { get; }
        public string Id => $"joycon-{Side.ToString().ToLowerInvariant()}-{Serial}";
        public NativeJoyConSide Side { get; }
        public bool HasReport { get; private set; }
        public bool PairShoulderPressed
        {
            get
            {
                lock (_gate) return Side switch
                {
                    NativeJoyConSide.Left => (_button5 & 0x40) != 0,
                    NativeJoyConSide.Right => (_button3 & 0x40) != 0,
                    _ => false
                };
            }
        }
        public NativeJoyConDevice Device
        {
            get
            {
                lock (_gate) return new NativeJoyConDevice(Id, Side switch
                {
                    NativeJoyConSide.Left => "Joy-Con (L)",
                    NativeJoyConSide.Right => "Joy-Con (R)",
                    _ => "Nintendo Switch Pro Controller"
                }, Serial, Side, _running && HasReport, _battery, _lastSeen, _lastInput);
            }
        }

        public Reader(HidDevice hid)
        {
            _hid = hid;
            Side = hid.ProductID switch
            {
                0x2006 => NativeJoyConSide.Left,
                0x2007 => NativeJoyConSide.Right,
                _ => NativeJoyConSide.Pro
            };
            try { Serial = NormalizeSerial(hid.GetSerialNumber()); }
            catch { Serial = Math.Abs(hid.DevicePath.GetHashCode()).ToString("X8"); }
        }

        public void Start()
        {
            _running = true;
            _thread = new Thread(ReadLoop)
            {
                IsBackground = true,
                Name = $"NativeJoyCon-{Side}-{Serial}",
                Priority = ThreadPriority.AboveNormal
            };
            _thread.Start();
        }

        public NativeControllerReport GetReport(bool standalone)
        {
            lock (_gate)
            {
                if (!standalone || Side == NativeJoyConSide.Pro)
                    return Clone(_report);
                return Side == NativeJoyConSide.Left
                    ? DecodeStandaloneLeft(_button3, _button4, _button5, _axisX, _axisY)
                    : DecodeStandaloneRight(_button3, _button4, _button5, _axisX, _axisY);
            }
        }

        public void MarkDisconnected()
        {
            lock (_gate) HasReport = false;
        }

        private void ReadLoop()
        {
            while (_running)
            {
                HidStream? stream = null;
                try
                {
                    if (!_hid.TryOpen(out stream)) { Thread.Sleep(800); continue; }
                    stream.ReadTimeout = 1500;
                    var buffer = new byte[Math.Max(64, _hid.GetMaxInputReportLength())];
                    while (_running)
                    {
                        var read = stream.Read(buffer, 0, buffer.Length);
                        if (read >= 13 && buffer[0] == 0x30) Decode(buffer);
                    }
                }
                catch (TimeoutException) { }
                catch { lock (_gate) HasReport = false; }
                finally { stream?.Dispose(); }
                if (_running) Thread.Sleep(500);
            }
        }

        private void Decode(byte[] data)
        {
            var button3 = data[3];
            var button4 = data[4];
            var button5 = data[5];
            ushort stickX;
            ushort stickY;
            if (Side == NativeJoyConSide.Right)
            {
                stickX = (ushort)(data[9] | ((data[10] & 0x0F) << 8));
                stickY = (ushort)((data[10] >> 4) | (data[11] << 4));
            }
            else
            {
                stickX = (ushort)(data[6] | ((data[7] & 0x0F) << 8));
                stickY = (ushort)((data[7] >> 4) | (data[8] << 4));
            }

            lock (_gate)
            {
                UpdateStickCenter(stickX, stickY);
                _axisX = _centerReady ? Axis(stickX, _centerX) : (short)0;
                _axisY = _centerReady ? Axis(stickY, _centerY) : (short)0;
                var report = DecodeUpright(Side, button3, button4, button5, _axisX, _axisY);
                _button3 = button3;
                _button4 = button4;
                _button5 = button5;
                _stickX = stickX;
                _stickY = stickY;
                _report = report;
                _battery = Math.Clamp(((data[2] >> 4) & 0x0F) * 25 / 2, 0, 100);
                _lastSeen = DateTime.UtcNow;
                _lastInput = Describe(report);
                HasReport = true;
            }
        }

        private static NativeControllerReport DecodeUpright(
            NativeJoyConSide side, byte b3, byte b4, byte b5, short axisX, short axisY)
        {
            var report = new NativeControllerReport();
            if (side is NativeJoyConSide.Right or NativeJoyConSide.Pro)
            {
                if ((b3 & 0x01) != 0) report.Buttons |= 0x8000; // Y
                if ((b3 & 0x02) != 0) report.Buttons |= 0x4000; // X
                if ((b3 & 0x04) != 0) report.Buttons |= 0x2000; // B
                if ((b3 & 0x08) != 0) report.Buttons |= 0x1000; // A
                if ((b3 & 0x40) != 0) report.Buttons |= 0x0200; // R
                if ((b3 & 0x80) != 0) report.RightTrigger = 255; // ZR
                report.RightX = axisX;
                report.RightY = axisY;
            }
            if (side is NativeJoyConSide.Left or NativeJoyConSide.Pro)
            {
                if ((b5 & 0x01) != 0) report.Buttons |= 0x0002; // Down
                if ((b5 & 0x02) != 0) report.Buttons |= 0x0001; // Up
                if ((b5 & 0x04) != 0) report.Buttons |= 0x0008; // Right
                if ((b5 & 0x08) != 0) report.Buttons |= 0x0004; // Left
                if ((b5 & 0x40) != 0) report.Buttons |= 0x0100; // L
                if ((b5 & 0x80) != 0) report.LeftTrigger = 255; // ZL
                report.LeftX = axisX;
                report.LeftY = axisY;
            }
            if ((b4 & 0x01) != 0) report.Buttons |= 0x0020; // Minus/View
            if ((b4 & 0x02) != 0) report.Buttons |= 0x0010; // Plus/Menu
            if ((b4 & 0x04) != 0) report.Buttons |= 0x0080; // R stick
            if ((b4 & 0x08) != 0) report.Buttons |= 0x0040; // L stick
            if ((b4 & 0x10) != 0) report.Buttons |= 0x0400; // Home
            return report;
        }

        private static NativeControllerReport DecodeStandaloneLeft(byte b3, byte b4, byte b5, short axisX, short axisY)
        {
            var report = new NativeControllerReport
            {
                // Left Joy-Con is held horizontally with the rail upward.
                LeftX = axisY,
                LeftY = (short)-axisX
            };
            if ((b5 & 0x02) != 0) report.Buttons |= 0x8000; // Up -> Y
            if ((b5 & 0x04) != 0) report.Buttons |= 0x4000; // Right -> X
            if ((b5 & 0x08) != 0) report.Buttons |= 0x2000; // Left -> B
            if ((b5 & 0x01) != 0) report.Buttons |= 0x1000; // Down -> A
            if ((b5 & 0x10) != 0) report.Buttons |= 0x0100; // SR -> LB
            if ((b5 & 0x20) != 0) report.Buttons |= 0x0200; // SL -> RB
            if ((b4 & 0x01) != 0) report.Buttons |= 0x0020;
            if ((b4 & 0x08) != 0) report.Buttons |= 0x0040;
            return report;
        }

        private static NativeControllerReport DecodeStandaloneRight(byte b3, byte b4, byte b5, short axisX, short axisY)
        {
            var report = new NativeControllerReport
            {
                // Right Joy-Con is held horizontally with the rail upward.
                LeftX = (short)-axisY,
                LeftY = axisX
            };
            if ((b3 & 0x01) != 0) report.Buttons |= 0x8000;
            if ((b3 & 0x02) != 0) report.Buttons |= 0x4000;
            if ((b3 & 0x04) != 0) report.Buttons |= 0x2000;
            if ((b3 & 0x08) != 0) report.Buttons |= 0x1000;
            if ((b3 & 0x10) != 0) report.Buttons |= 0x0100;
            if ((b3 & 0x20) != 0) report.Buttons |= 0x0200;
            if ((b4 & 0x02) != 0) report.Buttons |= 0x0010;
            if ((b4 & 0x04) != 0) report.Buttons |= 0x0040;
            return report;
        }

        private void UpdateStickCenter(ushort x, ushort y)
        {
            if (_centerReady) return;
            if (_centerSamples > 0 && (Math.Abs(x - _lastCenterX) > 24 || Math.Abs(y - _lastCenterY) > 24))
            {
                _centerSamples = 0;
                _centerSumX = 0;
                _centerSumY = 0;
            }
            _lastCenterX = x;
            _lastCenterY = y;
            _centerSumX += x;
            _centerSumY += y;
            _centerSamples++;
            if (_centerSamples < 40) return;
            _centerX = _centerSumX / (double)_centerSamples;
            _centerY = _centerSumY / (double)_centerSamples;
            _centerReady = true;
        }

        private static short Axis(ushort raw, double center)
        {
            var normalized = Math.Clamp((raw - center) / 1750.0, -1.0, 1.0);
            if (Math.Abs(normalized) < 0.08) return 0;
            normalized = Math.Sign(normalized) * (Math.Abs(normalized) - 0.08) / 0.92;
            return (short)Math.Clamp((int)Math.Round(normalized * 32767), -32767, 32767);
        }

        private static NativeControllerReport Clone(NativeControllerReport source) => new()
        {
            Buttons = source.Buttons,
            LeftTrigger = source.LeftTrigger,
            RightTrigger = source.RightTrigger,
            LeftX = source.LeftX,
            LeftY = source.LeftY,
            RightX = source.RightX,
            RightY = source.RightY
        };

        private static string Describe(NativeControllerReport report)
        {
            if (report.Buttons != 0) return $"Buttons 0x{report.Buttons:X4}";
            if (report.LeftTrigger != 0 || report.RightTrigger != 0) return "Trigger";
            if (report.LeftX != 0 || report.LeftY != 0 || report.RightX != 0 || report.RightY != 0) return "Stick";
            return "Idle";
        }

        private static string NormalizeSerial(string? value)
        {
            var normalized = new string((value ?? string.Empty).Where(char.IsLetterOrDigit).ToArray()).ToLowerInvariant();
            return string.IsNullOrWhiteSpace(normalized) ? "unknown" : normalized;
        }

        public void Dispose()
        {
            _running = false;
            try { _thread?.Join(1800); } catch { }
        }
    }
}
