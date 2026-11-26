using System.Diagnostics;
using System.IO;
using System.Text.Json.Nodes;

namespace UnifiedController.App.Engine;

/// <summary>
/// Shared IMU correction used before motion reaches DSU, the visualizer, or
/// gyro-to-stick mapping. This ports the stillness bias and desk-level tare
/// behavior from the Python GyroCalibrator.
/// </summary>
public sealed class NativeMotionCalibration
{
    private const int WindowSize = 85;
    private const float DeadbandDps = 0.15f;
    private readonly string _settingsPath;
    private readonly string _calibrateFlagPath;
    private readonly Queue<float[]> _gyroWindow = new();
    private readonly Queue<float[]> _accelWindow = new();
    // Bias in the reader's DSU order: pitch, yaw, roll.
    private readonly double[] _bias = [-30.0 / 16.384, 105.0 / 16.384, 10.0 / 16.384];
    private readonly double[,] _level = new double[3, 3];
    private double _gravityNorm = 1.0;
    private bool _calibrateLevel;
    private DateTime _settingsWriteTime;
    private long _nextSettingsCheck;
    private int _stillSamples;
    private readonly bool _fileStorage;
    private bool _levelRequested;
    private double[] _gravityTare = [0, 0, 1];

    public sealed record CalibrationState(double[] Bias, double[] GravityTare, bool CalibrateLevel);

    public bool IsStill { get; private set; }

    public NativeMotionCalibration(string root, bool fileStorage = true)
    {
        _fileStorage = fileStorage;
        _settingsPath = Path.Combine(root, "gyro_settings.json");
        _calibrateFlagPath = Path.Combine(root, "gyro_calibrate_level.flag");
        SetIdentity(_level);
        if (fileStorage) LoadSettings();
        else Array.Clear(_bias); // SDL supplies engineering units, without the GameSir raw bias.
    }

    public void RequestLevelCalibration() => _levelRequested = true;
    public CalibrationState CaptureState() => new(_bias.ToArray(), _gravityTare.ToArray(), _calibrateLevel);
    public void RestoreState(CalibrationState state)
    {
        if (state.Bias is not { Length: 3 } || state.GravityTare is not { Length: 3 } ||
            state.Bias.Any(v => !double.IsFinite(v) || Math.Abs(v) > 1000) ||
            state.GravityTare.Any(v => !double.IsFinite(v))) return;
        Array.Copy(state.Bias, _bias, 3);
        _calibrateLevel = state.CalibrateLevel;
        if (_calibrateLevel) ComputeLevelMatrix(state.GravityTare);
    }

    public (float[] Gyro, float[] Accel) Update(
        IReadOnlyList<float> rawGyro, IReadOnlyList<float> rawAccel, long nowTicks)
    {
        if (_fileStorage && nowTicks >= _nextSettingsCheck)
        {
            _nextSettingsCheck = nowTicks + Stopwatch.Frequency / 2;
            LoadSettingsIfChanged();
        }

        Push(_gyroWindow, rawGyro);
        Push(_accelWindow, rawAccel);
        UpdateBias();
        TryCalibrateLevel();

        var gyro = new[]
        {
            (float)(rawGyro[0] - _bias[0]),
            (float)(rawGyro[1] - _bias[1]),
            (float)(rawGyro[2] - _bias[2])
        };
        if (IsStill)
            for (var i = 0; i < 3; i++) if (Math.Abs(gyro[i]) < DeadbandDps) gyro[i] = 0;

        var accel = new[] { rawAccel[0], rawAccel[1], rawAccel[2] };
        if (_calibrateLevel)
        {
            // Rotation matrix is Cartesian X/Y/Z. DSU gyro order is
            // pitch(X), yaw(Z), roll(Y), so permute before and after.
            var rotatedAccel = Rotate(accel[0], accel[1], accel[2]);
            accel[0] = (float)(rotatedAccel[0] / _gravityNorm);
            accel[1] = (float)(rotatedAccel[1] / _gravityNorm);
            accel[2] = (float)(rotatedAccel[2] / _gravityNorm);

            var rotatedGyro = Rotate(gyro[0], gyro[2], gyro[1]);
            gyro[0] = (float)rotatedGyro[0];
            gyro[1] = (float)rotatedGyro[2];
            gyro[2] = (float)rotatedGyro[1];
        }
        return (gyro, accel);
    }

    private static void Push(Queue<float[]> queue, IReadOnlyList<float> value)
    {
        queue.Enqueue([value[0], value[1], value[2]]);
        while (queue.Count > WindowSize) queue.Dequeue();
    }

    private void UpdateBias()
    {
        if (_gyroWindow.Count < WindowSize || _accelWindow.Count < WindowSize)
        {
            IsStill = false;
            return;
        }

        var gyroSpread = Enumerable.Range(0, 3)
            .Max(axis => _gyroWindow.Max(v => v[axis]) - _gyroWindow.Min(v => v[axis]));
        var accelMagnitudes = _accelWindow
            .Select(v => Math.Sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])).ToArray();
        var accelSpread = accelMagnitudes.Max() - accelMagnitudes.Min();
        var averageAccel = accelMagnitudes.Average();
        IsStill = gyroSpread < 1.0 && accelSpread < 0.15 && averageAccel is > 0.75 and < 1.25;
        if (!IsStill)
        {
            _stillSamples = 0;
            return;
        }

        _stillSamples++;
        var alpha = _stillSamples < 60 ? 0.1 : 0.02;
        for (var axis = 0; axis < 3; axis++)
        {
            var mean = _gyroWindow.Average(v => v[axis]);
            _bias[axis] += alpha * (mean - _bias[axis]);
        }
    }

    private void TryCalibrateLevel()
    {
        if ((!_levelRequested && (!_fileStorage || !File.Exists(_calibrateFlagPath))) ||
            _accelWindow.Count < WindowSize || !IsStill) return;
        var tare = Enumerable.Range(0, 3).Select(axis => _accelWindow.Average(v => v[axis])).ToArray();
        var norm = Math.Sqrt(tare.Sum(v => v * v));
        if (norm is < 0.70 or > 1.30 || tare[2] / norm < 0.65) return;
        if (!_fileStorage)
        {
            ComputeLevelMatrix(tare.Select(v => (double)v).ToArray());
            _calibrateLevel = true;
            _levelRequested = false;
            return;
        }
        try
        {
            JsonObject settings;
            try { settings = JsonNode.Parse(File.ReadAllText(_settingsPath)) as JsonObject ?? new(); }
            catch { settings = new(); }
            settings["gravity_tare"] = new JsonArray(tare.Select(v => JsonValue.Create(Math.Round(v, 4))).ToArray());
            settings["calibrate_level"] = true;
            var temp = _settingsPath + ".tmp";
            File.WriteAllText(temp, settings.ToJsonString(new() { WriteIndented = true }));
            File.Move(temp, _settingsPath, true);
            File.Delete(_calibrateFlagPath);
            _levelRequested = false;
            LoadSettings();
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private void LoadSettingsIfChanged()
    {
        try
        {
            var write = File.GetLastWriteTimeUtc(_settingsPath);
            if (write != _settingsWriteTime) LoadSettings();
        }
        catch (IOException) { }
    }

    private void LoadSettings()
    {
        try
        {
            var settings = JsonNode.Parse(File.ReadAllText(_settingsPath)) as JsonObject;
            _calibrateLevel = settings?["calibrate_level"]?.GetValue<bool>() ?? false;
            var tareNode = settings?["gravity_tare"] as JsonArray;
            if (_calibrateLevel && tareNode is { Count: 3 })
            {
                var tare = tareNode.Select(v => v?.GetValue<double>() ?? 0).ToArray();
                ComputeLevelMatrix(tare);
            }
            else
            {
                SetIdentity(_level);
                _gravityNorm = 1;
            }
            _settingsWriteTime = File.GetLastWriteTimeUtc(_settingsPath);
        }
        catch
        {
            SetIdentity(_level);
            _gravityNorm = 1;
            _calibrateLevel = false;
        }
    }

    private void ComputeLevelMatrix(double[] gravity)
    {
        _gravityTare = gravity.ToArray();
        var norm = Math.Sqrt(gravity.Sum(v => v * v));
        if (norm < 1e-4) { SetIdentity(_level); _gravityNorm = 1; return; }
        var vx = gravity[0] / norm; var vy = gravity[1] / norm; var vz = gravity[2] / norm;
        // axis = cross(v, +Z) = (vy, -vx, 0)
        var ax = vy; var ay = -vx; var az = 0.0;
        var sin = Math.Sqrt(ax * ax + ay * ay);
        if (sin < 1e-6)
        {
            SetIdentity(_level);
            if (vz < 0) { _level[1, 1] = -1; _level[2, 2] = -1; }
            _gravityNorm = norm;
            return;
        }
        ax /= sin; ay /= sin;
        var cos = Math.Clamp(vz, -1, 1);
        var oneMinusCos = 1 - cos;
        // Rodrigues: I + sin*K + (1-cos)*K^2
        _level[0, 0] = cos + ax * ax * oneMinusCos;
        _level[0, 1] = ax * ay * oneMinusCos - az * sin;
        _level[0, 2] = ax * az * oneMinusCos + ay * sin;
        _level[1, 0] = ay * ax * oneMinusCos + az * sin;
        _level[1, 1] = cos + ay * ay * oneMinusCos;
        _level[1, 2] = ay * az * oneMinusCos - ax * sin;
        _level[2, 0] = az * ax * oneMinusCos - ay * sin;
        _level[2, 1] = az * ay * oneMinusCos + ax * sin;
        _level[2, 2] = cos + az * az * oneMinusCos;
        _gravityNorm = norm;
    }

    private double[] Rotate(double x, double y, double z) =>
    [
        _level[0, 0] * x + _level[0, 1] * y + _level[0, 2] * z,
        _level[1, 0] * x + _level[1, 1] * y + _level[1, 2] * z,
        _level[2, 0] * x + _level[2, 1] * y + _level[2, 2] * z
    ];

    private static void SetIdentity(double[,] matrix)
    {
        Array.Clear(matrix);
        matrix[0, 0] = matrix[1, 1] = matrix[2, 2] = 1;
    }
}
