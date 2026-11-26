namespace UnifiedController.Core;

/// <summary>Small complementary IMU filter. Gyro order is pitch, yaw, roll; accel is Cartesian X/Y/Z.</summary>
public sealed class MotionOrientationEstimator
{
    private const double GyroWeight = .98;
    private readonly Queue<(double Gyro, double Accel)> _window = new();
    private double _pitch, _roll, _yaw;
    private ulong _lastTimestamp;
    private bool _initialized;

    public MotionOrientation Update(IReadOnlyList<float> gyroDps, IReadOnlyList<float> accelG,
        ulong timestampUs, bool calibrationStill = false)
    {
        if (gyroDps.Count < 3 || accelG.Count < 3) throw new ArgumentException("Motion samples require three axes.");
        var accelMagnitude = Math.Sqrt(accelG.Sum(v => v * v));
        var angular = Math.Sqrt(gyroDps.Sum(v => v * v));
        var accelPitch = Math.Atan2(-accelG[0], Math.Sqrt(accelG[1] * accelG[1] + accelG[2] * accelG[2])) * 180 / Math.PI;
        var accelRoll = Math.Atan2(accelG[1], accelG[2]) * 180 / Math.PI;
        var dt = _lastTimestamp != 0 && timestampUs > _lastTimestamp ? Math.Min(.1, (timestampUs - _lastTimestamp) / 1_000_000d) : 0;
        if (!_initialized) { _pitch = accelPitch; _roll = accelRoll; _initialized = true; }
        else if (dt > 0)
        {
            _pitch = GyroWeight * (_pitch + gyroDps[0] * dt) + (1 - GyroWeight) * accelPitch;
            _roll = GyroWeight * (_roll + gyroDps[2] * dt) + (1 - GyroWeight) * accelRoll;
            _yaw = Normalize(_yaw + gyroDps[1] * dt);
        }
        _lastTimestamp = timestampUs;
        _window.Enqueue((angular, accelMagnitude)); while (_window.Count > 40) _window.Dequeue();
        var still = calibrationStill || (_window.Count >= 20 && Spread(_window.Select(v => v.Gyro)) < .6 &&
            Spread(_window.Select(v => v.Accel)) < .04 && _window.Average(v => v.Gyro) < .8 && Math.Abs(_window.Average(v => v.Accel) - 1) < .12);
        return new(_pitch, _roll, _yaw, angular, accelMagnitude, still);
    }

    public void Recenter() { _pitch = _roll = _yaw = 0; _lastTimestamp = 0; _initialized = false; _window.Clear(); }
    private static double Spread(IEnumerable<double> values) { var a = values.ToArray(); return a.Max() - a.Min(); }
    private static double Normalize(double value) { while (value > 180) value -= 360; while (value < -180) value += 360; return value; }
}
