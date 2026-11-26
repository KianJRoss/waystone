using System.Diagnostics;
using System.IO;
using HidSharp;

namespace UnifiedController.App.Engine;

public sealed class NativeGameSirHidReader : IDisposable
{
    private const int VendorId = 0x3537;
    private const byte ReportId = 0x12;

    private HidStream? _stream;
    private Thread? _readThread;
    private volatile bool _running;
    private readonly HashSet<string> _buttons = new(StringComparer.OrdinalIgnoreCase);
    private readonly object _lock = new();
    private byte _latestButtonByte6;
    private byte _latestButtonByte7;
    private string _latestReportHex = string.Empty;

    public float[] LatestGyroDps { get; } = new float[3];
    public float[] LatestAccelG { get; } = new float[3] { 0f, 0f, 1f };
    public ulong LatestTimestampUs { get; private set; }
    public bool IsConnected => _stream != null;

    public void Start()
    {
        if (_running) return;
        _running = true;
        _readThread = new Thread(ReadLoop)
        {
            IsBackground = true,
            Name = "GameSirHidReaderThread",
            Priority = ThreadPriority.BelowNormal
        };
        _readThread.Start();
    }

    public HashSet<string> GetButtons()
    {
        lock (_lock)
        {
            return new HashSet<string>(_buttons, StringComparer.OrdinalIgnoreCase);
        }
    }

    public byte[] GetLatestButtonBytes()
    {
        lock (_lock)
        {
            return [_latestButtonByte6, _latestButtonByte7];
        }
    }

    public string GetLatestReportHex()
    {
        lock (_lock) return _latestReportHex;
    }

    public (float[] Gyro, float[] Accel, ulong TimestampUs) GetMotionSample()
    {
        lock (_lock)
        {
            return (LatestGyroDps.ToArray(), LatestAccelG.ToArray(), LatestTimestampUs);
        }
    }

    private void ReadLoop()
    {
        var heartbeatBuffer = new byte[64];
        heartbeatBuffer[0] = 0x0F;
        heartbeatBuffer[1] = 0xF2;

        var readBuffer = new byte[64];
        var nextHeartbeat = Stopwatch.GetTimestamp();
        var heartbeatTicks = Stopwatch.Frequency * 3 / 4; // 0.75 seconds

        while (_running)
        {
            try
            {
                if (_stream == null)
                {
                    OpenDevice();
                    if (_stream == null)
                    {
                        Thread.Sleep(500);
                        continue;
                    }
                }

                var now = Stopwatch.GetTimestamp();
                if (now >= nextHeartbeat)
                {
                    nextHeartbeat = now + heartbeatTicks;
                    try
                    {
                        _stream.Write(heartbeatBuffer);
                    }
                    catch
                    {
                        CloseDevice();
                        continue;
                    }
                }

                int read = _stream.Read(readBuffer, 0, readBuffer.Length);
                if (read >= 8 && readBuffer[0] == ReportId)
                {
                    var newButtons = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                    // Captured from the Cyclone 2 dongle report: byte 6 bit
                    // 0x10 is Back/View and byte 7 bit 0x02 is Share. Share
                    // must remain separate from the XInput Back signal.
                    if ((readBuffer[7] & 0x02) != 0)
                        newButtons.Add("Share");
                    if ((readBuffer[7] & 0x01) != 0)
                        newButtons.Add("Home");

                    lock (_lock)
                    {
                        _latestButtonByte6 = readBuffer[6];
                        _latestButtonByte7 = readBuffer[7];
                        _latestReportHex = Convert.ToHexString(readBuffer.AsSpan(0, Math.Min(read, 32)));
                        _buttons.Clear();
                        foreach (var b in newButtons) _buttons.Add(b);
                    }

                    if (read >= 25)
                    {
                        short gx = BitConverter.ToInt16(readBuffer, 13);
                        short gy = BitConverter.ToInt16(readBuffer, 15);
                        short gz = BitConverter.ToInt16(readBuffer, 17);
                        short ax = BitConverter.ToInt16(readBuffer, 19);
                        short ay = BitConverter.ToInt16(readBuffer, 21);
                        short az = BitConverter.ToInt16(readBuffer, 23);

                        // GameSir Cyclone 2 IMU decoding (±2000 dps @ 16.384 LSB/dps, ±4G @ 8192 LSB/G)
                        // Gyro: Pitch (X), Yaw (Y), Roll (-Z)
                        lock (_lock)
                        {
                            LatestGyroDps[0] = gx / 16.384f;
                            LatestGyroDps[1] = gy / 16.384f;
                            LatestGyroDps[2] = -gz / 16.384f;

                            // Accel: Lateral X (ax), Longitudinal Y (-az), Vertical Z (ay: +1G resting face up)
                            LatestAccelG[0] = ax / 8192.0f;
                            LatestAccelG[1] = -az / 8192.0f;
                            LatestAccelG[2] = ay / 8192.0f;

                            LatestTimestampUs = (ulong)(DateTime.UtcNow.Ticks / 10);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"GameSir HID read error: {ex.Message}");
                CloseDevice();
                Thread.Sleep(500);
            }
        }
    }

    private void OpenDevice()
    {
        try
        {
            var devices = DeviceList.Local.GetHidDevices(VendorId).ToList();

            // The Cyclone 2 vendor endpoint (usage page 0xFFF0) on Windows is Col04
            var target = devices.FirstOrDefault(d => d.DevicePath.Contains("col04", StringComparison.OrdinalIgnoreCase))
                         ?? devices.FirstOrDefault(d => d.DevicePath.Contains("fff0", StringComparison.OrdinalIgnoreCase));

            if (target != null && target.TryOpen(out _stream))
            {
                try
                {
                    _stream.ReadTimeout = 1500;
                    File.AppendAllText("app.log", $"{DateTime.Now:O} GameSir: Opened vendor HID: {target.DevicePath}\n");
                }
                catch { }
                return;
            }

            _stream = null;
        }
        catch (Exception ex)
        {
            try { File.AppendAllText("app.log", $"{DateTime.Now:O} GameSir: Open error: {ex.Message}\n"); } catch { }
            _stream = null;
        }
    }

    private void CloseDevice()
    {
        try
        {
            _stream?.Dispose();
        }
        catch { }
        _stream = null;
    }

    public void Dispose()
    {
        _running = false;
        CloseDevice();
        try
        {
            _readThread?.Join(500);
        }
        catch { }
        _readThread = null;
    }
}
