using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;

namespace UnifiedController.App.Engine;

public sealed class NativeDsuServer : IDisposable
{
    private readonly int _port;
    private const uint MagicServer = 0x53555344; // "DSUS"
    private const ushort ProtocolVersion = 1001;
    private const int SioUdpConnReset = -1744830452; // 0x9800000C

    private Socket? _socket;
    private Thread? _listenThread;
    private Thread? _streamThread;
    private volatile bool _running;
    private string? _lastError;
    private uint _packetNumber;
    private readonly HashSet<EndPoint> _subscribers = [];
    private readonly object _subLock = new();

    private readonly Func<float[]> _getGyro;
    private readonly Func<float[]> _getAccel;
    private readonly Func<ulong> _getTimestampUs;

    public bool IsRunning => _running && _socket != null && _socket.IsBound;
    public int ClientCount { get { lock (_subLock) return _subscribers.Count; } }
    public string? LastError => _lastError;
    public int Port => _port;

    public NativeDsuServer(Func<float[]> getGyro, Func<float[]> getAccel, Func<ulong> getTimestampUs, int port = 26760)
    {
        _getGyro = getGyro;
        _getAccel = getAccel;
        _getTimestampUs = getTimestampUs;
        _port = port;
    }

    public void Start()
    {
        if (_running) return;
        _running = true;

        _listenThread = new Thread(ListenLoop)
        {
            IsBackground = true,
            Name = "DsuServerListenThread"
        };
        _listenThread.Start();

        _streamThread = new Thread(StreamLoop)
        {
            IsBackground = true,
            Name = "DsuServerStreamThread"
        };
        _streamThread.Start();
    }

    private readonly object _socketLock = new();

    private void ListenLoop()
    {
        var recvBuffer = new byte[256];

        while (_running)
        {
            if (_socket == null || !_socket.IsBound)
            {
                try
                {
                    var sock = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
                    sock.ExclusiveAddressUse = false;
                    sock.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
                    try
                    {
                        sock.IOControl(SioUdpConnReset, new byte[] { 0, 0, 0, 0 }, null);
                    }
                    catch { }
                    sock.Bind(new IPEndPoint(IPAddress.Any, _port));
                    lock (_socketLock)
                    {
                        _socket = sock;
                    }
                    _lastError = null;
                    try { File.AppendAllText("app.log", $"{DateTime.Now:O} NativeDsuServer: Bound to 0.0.0.0:{_port}\n"); } catch { }
                }
                catch (Exception ex)
                {
                    _lastError = ex.Message;
                    try { File.AppendAllText("app.log", $"{DateTime.Now:O} NativeDsuServer: Failed to bind {_port}: {ex.Message}\n"); } catch { }
                    Thread.Sleep(1000);
                    continue;
                }
            }

            try
            {
                EndPoint remoteEp = new IPEndPoint(IPAddress.Any, 0);
                int received = _socket.ReceiveFrom(recvBuffer, ref remoteEp);
                if (received < 16) continue;

                // Check magic "DSUC" (0x43555344) or "DSUS" (0x53555344)
                uint magic = BitConverter.ToUInt32(recvBuffer, 0);
                if (magic != 0x43555344 && magic != MagicServer) continue;

                uint msgType = BitConverter.ToUInt32(recvBuffer, 16);

                if (msgType == 0x100000) // Version
                {
                    SendVersion(remoteEp);
                }
                else if (msgType == 0x100001) // Controller Info
                {
                    SendControllerInfo(remoteEp);
                }
                else if (msgType == 0x100002) // Data subscription
                {
                    lock (_subLock)
                    {
                        _subscribers.Add(remoteEp);
                    }
                    // Immediately reply with first data report
                    var firstReport = new byte[100];
                    BuildDataReport(firstReport);
                    SafeSendTo(firstReport, remoteEp);
                }
            }
            catch (SocketException ex)
            {
                if (ex.SocketErrorCode == SocketError.ConnectionReset || ex.SocketErrorCode == SocketError.TimedOut)
                    continue;
                if (!_running) break;
                Thread.Sleep(50);
            }
            catch (Exception ex)
            {
                if (!_running) break;
                Debug.WriteLine($"Dsu listen error: {ex.Message}");
                Thread.Sleep(50);
            }
        }
    }

    private void SafeSendTo(byte[] buffer, EndPoint ep)
    {
        try
        {
            lock (_socketLock)
            {
                if (_socket != null && _socket.IsBound)
                {
                    _socket.SendTo(buffer, ep);
                }
            }
        }
        catch { }
    }

    private void StreamLoop()
    {
        var packet = new byte[100];

        while (_running)
        {
            try
            {
                List<EndPoint> targets;
                lock (_subLock)
                {
                    targets = [.. _subscribers];
                }

                if (targets.Count > 0)
                {
                    BuildDataReport(packet);
                    foreach (var ep in targets)
                    {
                        SafeSendTo(packet, ep);
                    }
                }

                Thread.Sleep(10); // 100Hz streaming
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"Dsu stream error: {ex.Message}");
            }
        }
    }

    private void SendVersion(EndPoint ep)
    {
        var packet = new byte[22];
        WriteHeader(packet, 0x100000, 2);
        BitConverter.GetBytes(ProtocolVersion).CopyTo(packet, 20);
        FinalizeAndSend(packet, ep);
    }

    private void SendControllerInfo(EndPoint ep)
    {
        var packet = new byte[32];
        WriteHeader(packet, 0x100001, 12);
        packet[20] = 0;    // Slot
        packet[21] = 2;    // Slot state: connected
        packet[22] = 2;    // Device model: full gyro
        packet[23] = 1;    // Connection type: USB
        packet[29] = 1;    // MAC: 00:00:00:00:00:01
        packet[30] = 5;    // Battery: full
        FinalizeAndSend(packet, ep);
    }

    private void BuildDataReport(byte[] packet)
    {
        Array.Clear(packet, 0, packet.Length);
        WriteHeader(packet, 0x100002, 80);

        // Body starts at packet offset 20
        packet[20] = 0;    // Slot 0
        packet[21] = 2;    // Connected
        packet[22] = 2;    // Full gyro
        packet[23] = 1;    // USB
        packet[29] = 1;    // MAC
        packet[30] = 5;    // Battery: full
        packet[31] = 1;    // State: connected

        // Packet number at body[12] -> offset 32
        _packetNumber++;
        BitConverter.GetBytes(_packetNumber).CopyTo(packet, 32);

        // Neutral buttons and sticks at body[20..24] -> offset 40
        packet[40] = 0x80;
        packet[41] = 0x80;
        packet[42] = 0x80;
        packet[43] = 0x80;

        // Timestamp at body[48] -> offset 68
        ulong timestamp = _getTimestampUs();
        BitConverter.GetBytes(timestamp).CopyTo(packet, 68);

        var accel = _getAccel();
        var gyro = _getGyro();

        // Accel at body[56] -> offset 76 (X, Y, Z in Gs)
        BitConverter.GetBytes(accel[0]).CopyTo(packet, 76);
        BitConverter.GetBytes(accel[1]).CopyTo(packet, 80);
        BitConverter.GetBytes(accel[2]).CopyTo(packet, 84);

        // Gyro at body[68] -> offset 88 (Pitch, Yaw, Roll in deg/s)
        BitConverter.GetBytes(gyro[0]).CopyTo(packet, 88);
        BitConverter.GetBytes(gyro[1]).CopyTo(packet, 92);
        BitConverter.GetBytes(gyro[2]).CopyTo(packet, 96);

        CalculateCrc(packet);
    }

    private static void WriteHeader(byte[] packet, uint msgType, ushort payloadLength)
    {
        BitConverter.GetBytes(MagicServer).CopyTo(packet, 0);
        BitConverter.GetBytes(ProtocolVersion).CopyTo(packet, 4);
        BitConverter.GetBytes((ushort)(payloadLength + 4)).CopyTo(packet, 6);
        BitConverter.GetBytes(0u).CopyTo(packet, 8); // CRC placeholder
        BitConverter.GetBytes(0x00010001u).CopyTo(packet, 12); // Server ID
        BitConverter.GetBytes(msgType).CopyTo(packet, 16);
    }

    private void FinalizeAndSend(byte[] packet, EndPoint ep)
    {
        CalculateCrc(packet);
        try
        {
            _socket?.SendTo(packet, ep);
        }
        catch { }
    }

    private static void CalculateCrc(byte[] packet)
    {
        packet[8] = 0;
        packet[9] = 0;
        packet[10] = 0;
        packet[11] = 0;

        uint crc = ComputeCrc32(packet);
        BitConverter.GetBytes(crc).CopyTo(packet, 8);
    }

    private static uint ComputeCrc32(byte[] data)
    {
        uint crc = 0xFFFFFFFF;
        for (int i = 0; i < data.Length; i++)
        {
            crc ^= data[i];
            for (int j = 0; j < 8; j++)
            {
                crc = (crc >> 1) ^ ((crc & 1) != 0 ? 0xEDB88320u : 0);
            }
        }
        return ~crc;
    }

    public void Dispose()
    {
        _running = false;
        try
        {
            _socket?.Close();
            _socket?.Dispose();
        }
        catch { }
        _socket = null;

        try { _listenThread?.Join(300); } catch { }
        try { _streamThread?.Join(300); } catch { }
    }
}
