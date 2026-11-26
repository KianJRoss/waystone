using System.Text.Json;

namespace UnifiedController.App.Engine;

public sealed record NativeBearingAxis(
    bool Enabled, string Source, double MaxRateDps, double FullTiltDeg,
    double DeadzoneDeg, double Curve, double ResponseSeconds, bool Invert);

public sealed record NativeBearingConfig(
    bool Enabled, NativeBearingAxis Pitch, NativeBearingAxis Yaw, NativeBearingAxis Roll)
{
    private static NativeBearingAxis Default(string source) =>
        new(false, source, 90, 30, 3, 1, 0.12, false);

    public static NativeBearingConfig Disabled =>
        new(false, Default("pitch"), Default("heading"), Default("roll"));

    public static NativeBearingConfig Parse(JsonElement profile)
    {
        if (!profile.TryGetProperty("motion_bearing", out var raw) || raw.ValueKind != JsonValueKind.Object)
            return Disabled;
        return new(
            Bool(raw, "enabled"),
            ParseAxis(raw, "pitch", "pitch"),
            ParseAxis(raw, "yaw", "heading"),
            ParseAxis(raw, "roll", "roll"));
    }

    private static NativeBearingAxis ParseAxis(JsonElement raw, string name, string fallback)
    {
        if (!raw.TryGetProperty(name, out var axis) || axis.ValueKind != JsonValueKind.Object)
            return Default(fallback);
        var source = Text(axis, "source", fallback);
        if (source is not ("pitch" or "roll" or "heading")) source = fallback;
        var full = Number(axis, "full_tilt_deg", 30, 5, 90);
        return new(
            Bool(axis, "enabled"), source,
            Number(axis, "max_rate_dps", 90, 5, 720), full,
            Number(axis, "deadzone_deg", 3, 0, full * 0.8),
            Number(axis, "curve", 1, 0.5, 3),
            Number(axis, "response_seconds", 0.12, 0, 2),
            Bool(axis, "invert"));
    }

    private static string Text(JsonElement e, string k, string d) =>
        e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? d : d;
    private static bool Bool(JsonElement e, string k, bool d = false) =>
        e.TryGetProperty(k, out var v) && v.ValueKind is JsonValueKind.True or JsonValueKind.False ? v.GetBoolean() : d;
    private static double Number(JsonElement e, string k, double d, double low, double high) =>
        e.TryGetProperty(k, out var v) && v.TryGetDouble(out var n) && double.IsFinite(n) ? Math.Clamp(n, low, high) : d;
}

public readonly record struct NativeBearingSample(float[] Gyro, float[] Accel);

/// <summary>Maps a held physical tilt to sustained, coherent virtual DSU motion.</summary>
public sealed class NativeMotionBearing
{
    private NativeBearingConfig _config = NativeBearingConfig.Disabled;
    private readonly double[] _quat = [1, 0, 0, 0];
    private readonly double[] _rates = [0, 0, 0];
    private double _headingDeg;
    private double _headingNeutralDeg;
    private double? _lastAt;

    public NativeBearingConfig Config => _config;
    public bool Enabled => _config.Enabled;
    public double PitchRate => _rates[0];
    public double YawRate => _rates[1];
    public double RollRate => _rates[2];

    public void Configure(NativeBearingConfig config)
    {
        if (_config == config) return;
        _config = config;
        Reset();
    }

    public void Reset()
    {
        _quat[0] = 1; _quat[1] = _quat[2] = _quat[3] = 0;
        Array.Clear(_rates);
        _headingDeg = _headingNeutralDeg = 0;
        _lastAt = null;
    }

    public void RecenterHeading() => _headingNeutralDeg = _headingDeg;

    public NativeBearingSample Update(
        double pitchDeg, double rollDeg, IReadOnlyList<double> levelReference,
        double now, double physicalYawDps = 0)
    {
        if (!_config.Enabled) throw new InvalidOperationException("Motion bearing is disabled");
        double dt;
        if (!_lastAt.HasValue || now - _lastAt.Value > 0.1 || now < _lastAt.Value)
        {
            Reset();
            dt = 0;
        }
        else dt = Math.Min(0.05, now - _lastAt.Value);
        _lastAt = now;

        if (double.IsFinite(physicalYawDps)) _headingDeg += physicalYawDps * dt;
        var neutral = NativeMotionStickMapper.GravityAngles(levelReference);
        var headingError = Wrap180(_headingDeg - _headingNeutralDeg);
        var settings = new[] { _config.Pitch, _config.Yaw, _config.Roll };
        for (var i = 0; i < settings.Length; i++)
        {
            var setting = settings[i];
            var tilt = !setting.Enabled ? 0 : setting.Source switch
            {
                "pitch" => pitchDeg - neutral.Pitch,
                "roll" => rollDeg - neutral.Roll,
                _ => headingError,
            };
            var magnitude = Math.Abs(tilt);
            var target = 0.0;
            if (magnitude > setting.DeadzoneDeg)
            {
                var fraction = Math.Min(1,
                    (magnitude - setting.DeadzoneDeg) /
                    Math.Max(1e-9, setting.FullTiltDeg - setting.DeadzoneDeg));
                target = Math.CopySign(Math.Pow(fraction, setting.Curve) * setting.MaxRateDps, tilt);
            }
            if (setting.Invert) target = -target;
            var alpha = setting.ResponseSeconds == 0 ? 1 : 1 - Math.Exp(-dt / setting.ResponseSeconds);
            _rates[i] += alpha * (target - _rates[i]);
            if (Math.Abs(_rates[i]) < 0.001) _rates[i] = 0;
        }

        if (dt > 0) IntegrateOrientation(dt);
        return new(
            [(float)_rates[0], (float)_rates[1], (float)_rates[2]],
            [(float)(2 * (_quat[1] * _quat[3] - _quat[0] * _quat[2])),
             (float)(2 * (_quat[2] * _quat[3] + _quat[0] * _quat[1])),
             (float)(1 - 2 * (_quat[1] * _quat[1] + _quat[2] * _quat[2]))]);
    }

    private void IntegrateOrientation(double dt)
    {
        // DSU rates are pitch/yaw/roll. Quaternion axes are pitch/roll/yaw.
        var ax = _rates[0] * Math.PI / 180 * dt;
        var ay = _rates[2] * Math.PI / 180 * dt;
        var az = _rates[1] * Math.PI / 180 * dt;
        var angle = Math.Sqrt(ax * ax + ay * ay + az * az);
        if (angle == 0) return;
        var scale = Math.Sin(angle / 2) / angle;
        var delta = new[] { Math.Cos(angle / 2), ax * scale, ay * scale, az * scale };
        var a = (double[])_quat.Clone();
        _quat[0] = a[0] * delta[0] - a[1] * delta[1] - a[2] * delta[2] - a[3] * delta[3];
        _quat[1] = a[0] * delta[1] + a[1] * delta[0] + a[2] * delta[3] - a[3] * delta[2];
        _quat[2] = a[0] * delta[2] - a[1] * delta[3] + a[2] * delta[0] + a[3] * delta[1];
        _quat[3] = a[0] * delta[3] + a[1] * delta[2] - a[2] * delta[1] + a[3] * delta[0];
        var norm = Math.Sqrt(_quat.Sum(v => v * v));
        for (var i = 0; i < 4; i++) _quat[i] /= norm;
    }

    private static double Wrap180(double angle)
    {
        var wrapped = (angle + 180) % 360;
        if (wrapped < 0) wrapped += 360;
        return wrapped - 180;
    }
}
