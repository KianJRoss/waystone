namespace UnifiedController.Linux.Platform.Output;

internal readonly record struct RumbleEffect(ushort Strong, ushort Weak, ushort LengthMs);

/// <summary>Stateful, device-independent decoder for the uinput FF event protocol.</summary>
internal sealed class UInputForceFeedbackProcessor
{
    private readonly Dictionary<int, RumbleEffect> _effects = [];
    private int _gain = 0xffff;
    internal Action<byte, byte, int>? Rumble { get; set; }
    internal void Upload(int id, ushort strong, ushort weak, ushort lengthMs) => _effects[id] = new(strong, weak, lengthMs);
    internal void Erase(int id) => _effects.Remove(id);
    internal void Process(ushort type, ushort code, int value)
    {
        if (type != UInputNative.EvFf) return;
        if (code == UInputNative.FfGain) { _gain = Math.Clamp(value, 0, 0xffff); return; }
        if (!_effects.TryGetValue(code, out var effect)) return;
        if (value <= 0) { Rumble?.Invoke(0, 0, 0); return; }
        static byte Scale(ushort magnitude, int gain) => (byte)Math.Clamp((int)Math.Round(magnitude / 65535d * gain / 65535d * 255), 0, 255);
        Rumble?.Invoke(Scale(effect.Strong, _gain), Scale(effect.Weak, _gain), effect.LengthMs);
    }
    internal void Stop() => Rumble?.Invoke(0, 0, 0);
}
