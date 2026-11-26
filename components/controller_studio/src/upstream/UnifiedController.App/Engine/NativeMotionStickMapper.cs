using System.Text.Json;

namespace UnifiedController.App.Engine;

public sealed record NativeMotionSide(
    bool Enabled, string Mode, string XAxis, string YAxis,
    bool XEnabled, bool YEnabled, double FullScale, double Deadzone,
    double Curve, bool InvertX, bool InvertY, string Blend,
    string Activation, double CoastSeconds);

public sealed record NativeMotionConfig(
    string Preset, NativeMotionSide Left, NativeMotionSide Right,
    double[] LevelReference)
{
    private static NativeMotionSide Disabled => new(false, "rate", "yaw", "pitch", true, true,
        120, 2, 1, false, false, "add", "always", 1.2);

    public static NativeMotionConfig Native => new("native", Disabled, Disabled, [0, 0, 1]);

    public static NativeMotionConfig Parse(JsonElement profile)
    {
        if (!profile.TryGetProperty("motion", out var raw) || raw.ValueKind != JsonValueKind.Object)
            return Native;
        var preset = Text(raw, "preset", "native");
        NativeMotionSide left, right;
        if (preset == "gyro_aim")
        {
            left = Disabled;
            right = Disabled with { Enabled = true };
        }
        else if (preset == "tilt_drive")
        {
            left = Disabled with { Enabled = true, Mode = "tilt", XAxis = "roll", FullScale = 30 };
            right = Disabled;
        }
        else if (preset == "dual_motion")
        {
            left = Disabled with { Enabled = true, Mode = "tilt", XAxis = "roll", FullScale = 30 };
            right = Disabled with { Enabled = true };
        }
        else if (preset == "inertia_aim")
        {
            left = Disabled;
            right = Disabled with { Enabled = true, Mode = "inertia" };
        }
        else if (preset == "custom")
        {
            left = raw.TryGetProperty("left", out var l) ? ParseSide(l) : Disabled;
            right = raw.TryGetProperty("right", out var r) ? ParseSide(r) : Disabled;
        }
        else
        {
            preset = "native";
            left = right = Disabled;
        }

        var level = new[] { 0.0, 0.0, 1.0 };
        if (raw.TryGetProperty("level_reference", out var reference) &&
            reference.ValueKind == JsonValueKind.Array && reference.GetArrayLength() == 3)
        {
            var values = reference.EnumerateArray().ToArray();
            for (var i = 0; i < 3; i++)
                level[i] = Math.Clamp(values[i].TryGetDouble(out var v) && double.IsFinite(v) ? v : level[i], -2, 2);
        }
        return new(preset, left, right, level);
    }

    private static NativeMotionSide ParseSide(JsonElement raw)
    {
        if (raw.ValueKind != JsonValueKind.Object) return Disabled;
        var mode = Text(raw, "mode", "rate");
        if (mode is not ("rate" or "tilt" or "inertia")) mode = "rate";
        var xFallback = mode == "tilt" ? "roll" : "yaw";
        var xAxis = Axis(raw, "x_axis", xFallback, mode);
        var yAxis = Axis(raw, "y_axis", "pitch", mode);
        var full = Number(raw, "full_scale", mode == "tilt" ? 30 : 120, 5, 360);
        var activation = Text(raw, "activation", "always");
        if (activation is not ("always" or "LT" or "RT" or "LB" or "RB")) activation = "always";
        var blend = Text(raw, "blend", "add");
        if (blend is not ("add" or "replace")) blend = "add";
        return new(
            Bool(raw, "enabled"), mode, xAxis, yAxis,
            Bool(raw, "x_enabled", true), Bool(raw, "y_enabled", true), full,
            Number(raw, "deadzone", 2, 0, full * 0.8), Number(raw, "curve", 1, 0.5, 3),
            Bool(raw, "invert_x"), Bool(raw, "invert_y"), blend, activation,
            Number(raw, "coast_seconds", 1.2, 0.1, 10));
    }

    private static string Axis(JsonElement raw, string key, string fallback, string mode)
    {
        var value = Text(raw, key, fallback);
        return mode == "tilt"
            ? value is "pitch" or "roll" ? value : fallback
            : value is "pitch" or "yaw" or "roll" ? value : fallback;
    }
    private static string Text(JsonElement e, string k, string d) => e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? d : d;
    private static bool Bool(JsonElement e, string k, bool d = false) => e.TryGetProperty(k, out var v) && v.ValueKind is JsonValueKind.True or JsonValueKind.False ? v.GetBoolean() : d;
    private static double Number(JsonElement e, string k, double d, double low, double high) => e.TryGetProperty(k, out var v) && v.TryGetDouble(out var n) && double.IsFinite(n) ? Math.Clamp(n, low, high) : d;
}

public sealed class NativeMotionStickMapper
{
    private NativeMotionConfig _config = NativeMotionConfig.Native;
    private double? _lastAt;
    private double? _freshAt;
    private bool _initialized;
    private double _pitch, _roll;
    private readonly double[] _gravity = [0, 0, 1];
    private readonly double[] _gyro = [0, 0, 0];
    private readonly double[][] _spin = [[0, 0], [0, 0]];

    public double Pitch => _pitch;
    public double Roll => _roll;

    public void Configure(NativeMotionConfig config)
    {
        if (_config == config) return;
        _config = config;
        Reset();
    }

    public void Reset()
    {
        _lastAt = _freshAt = null;
        _initialized = false;
        _pitch = _roll = 0;
        _gravity[0] = _gravity[1] = 0; _gravity[2] = 1;
        Array.Clear(_spin[0]); Array.Clear(_spin[1]);
    }

    public void Update(IReadOnlyList<float> gyro, IReadOnlyList<float> accel, double now)
    {
        var norm = Math.Sqrt(accel.Sum(v => (double)v * v));
        if (!(norm > 0.5 && norm < 1.5) || gyro.Any(v => !float.IsFinite(v))) return;
        var measured = new[] { accel[0] / norm, accel[1] / norm, accel[2] / norm };
        if (!_initialized || (_lastAt.HasValue && now - _lastAt.Value > 0.1))
        {
            Array.Copy(measured, _gravity, 3);
            (_pitch, _roll) = GravityAngles(measured);
            _initialized = true;
            Array.Clear(_spin[0]); Array.Clear(_spin[1]);
        }
        else
        {
            var dt = Math.Min(0.05, Math.Max(0, now - _lastAt!.Value));
            _pitch += gyro[0] * dt; _roll += gyro[2] * dt;
            var wx = DegreesToRadians(gyro[0]);
            var wy = DegreesToRadians(gyro[2]);
            var wz = DegreesToRadians(gyro[1]);
            var predicted = new[]
            {
                _gravity[0] - dt * (wy * _gravity[2] - wz * _gravity[1]),
                _gravity[1] - dt * (wz * _gravity[0] - wx * _gravity[2]),
                _gravity[2] - dt * (wx * _gravity[1] - wy * _gravity[0]),
            };
            Normalize(predicted);
            var gain = norm is > 0.85 and < 1.15 ? 1 - Math.Exp(-dt / 0.35) : 0;
            for (var i = 0; i < 3; i++) predicted[i] = (1 - gain) * predicted[i] + gain * measured[i];
            Normalize(predicted); Array.Copy(predicted, _gravity, 3);

            var gravityPitch = RadiansToDegrees(Math.Asin(Math.Clamp(_gravity[1], -1, 1)));
            var pitchTarget = new[] { gravityPitch, 180 - gravityPitch, -180 - gravityPitch }
                .Select(v => NearestAngle(v, _pitch)).MinBy(v => Math.Abs(v - _pitch));
            _pitch += gain * (pitchTarget - _pitch);
            if (Math.Sqrt(_gravity[0] * _gravity[0] + _gravity[2] * _gravity[2]) > 0.2)
            {
                var rg = RadiansToDegrees(Math.Atan2(-_gravity[0], _gravity[2]));
                var rt = new[] { -180.0, 0, 180 }.Select(o => NearestAngle(rg + o, _roll)).MinBy(v => Math.Abs(v - _roll));
                _roll += gain * (rt - _roll);
            }
            UpdateInertia(0, _config.Left, gyro, dt);
            UpdateInertia(1, _config.Right, gyro, dt);
        }
        for (var i = 0; i < 3; i++) _gyro[i] = gyro[i];
        _lastAt = _freshAt = now;
    }

    public void Apply(ref NativeControllerReport report, double now)
    {
        if (!_freshAt.HasValue || now - _freshAt.Value > 0.1) return;
        var neutral = GravityAngles(_config.LevelReference);
        ApplySide(0, _config.Left, ref report, neutral, now);
        ApplySide(1, _config.Right, ref report, neutral, now);
    }

    private void ApplySide(int side, NativeMotionSide s, ref NativeControllerReport r, (double Pitch, double Roll) neutral, double now)
    {
        if (!s.Enabled || !Active(s.Activation, r)) return;
        double Value(string axis) => s.Mode == "tilt"
            ? axis == "pitch" ? _pitch - neutral.Pitch : _roll - neutral.Roll
            : axis == "pitch" ? _gyro[0] : axis == "yaw" ? _gyro[1] : _gyro[2];
        var x = s.Mode == "inertia" ? _spin[side][0] : Shape(Value(s.XAxis), s);
        var y = s.Mode == "inertia" ? _spin[side][1] : Shape(Value(s.YAxis), s);
        if (s.InvertX) x = -x; if (s.InvertY) y = -y;
        if (side == 0)
        {
            if (s.XEnabled) r.LeftX = Blend(r.LeftX, x, s.Blend);
            if (s.YEnabled) r.LeftY = Blend(r.LeftY, y, s.Blend);
        }
        else
        {
            if (s.XEnabled) r.RightX = Blend(r.RightX, x, s.Blend);
            if (s.YEnabled) r.RightY = Blend(r.RightY, y, s.Blend);
        }
    }

    private void UpdateInertia(int side, NativeMotionSide s, IReadOnlyList<float> gyro, double dt)
    {
        if (!s.Enabled || s.Mode != "inertia") return;
        string[] axes = [s.XAxis, s.YAxis];
        for (var i = 0; i < 2; i++)
        {
            var raw = axes[i] == "pitch" ? gyro[0] : axes[i] == "yaw" ? gyro[1] : gyro[2];
            var target = Shape(raw, s);
            _spin[side][i] = target != 0
                ? _spin[side][i] + (1 - Math.Exp(-dt / 0.08)) * (target - _spin[side][i])
                : _spin[side][i] * Math.Exp(-dt / s.CoastSeconds);
            if (Math.Abs(_spin[side][i]) < 0.001) _spin[side][i] = 0;
        }
    }

    private static double Shape(double value, NativeMotionSide s)
    {
        if (Math.Abs(value) <= s.Deadzone) return 0;
        var n = Math.Min(1, (Math.Abs(value) - s.Deadzone) / Math.Max(1e-6, s.FullScale - s.Deadzone));
        return Math.CopySign(Math.Pow(n, s.Curve), value);
    }
    private static short Blend(short basis, double value, string blend) => (short)Math.Clamp((blend == "add" ? basis : 0) + (int)Math.Round(value * 32767), -32768, 32767);
    private static bool Active(string a, NativeControllerReport r) => a == "always" || a == "LT" && r.LeftTrigger > 30 || a == "RT" && r.RightTrigger > 30 || a == "LB" && (r.Buttons & 0x100) != 0 || a == "RB" && (r.Buttons & 0x200) != 0;
    public static (double Pitch, double Roll) GravityAngles(IReadOnlyList<double> a) { var n = Math.Max(1e-12, Math.Sqrt(a.Sum(v => v * v))); return (RadiansToDegrees(Math.Asin(Math.Clamp(a[1] / n, -1, 1))), RadiansToDegrees(Math.Atan2(-a[0], a[2]))); }
    private static double NearestAngle(double target, double reference) => target + 360 * Math.Round((reference - target) / 360);
    private static void Normalize(double[] v) { var n = Math.Max(1e-12, Math.Sqrt(v.Sum(x => x * x))); for (var i = 0; i < v.Length; i++) v[i] /= n; }
    private static double DegreesToRadians(double v) => v * Math.PI / 180;
    private static double RadiansToDegrees(double v) => v * 180 / Math.PI;
}
