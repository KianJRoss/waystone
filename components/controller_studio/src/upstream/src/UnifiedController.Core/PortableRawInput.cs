namespace UnifiedController.App.Engine;

// Contract-compatible counterparts to the records in the Windows Raw Input manager.
// Keeping them here lets the shared profile runtime consume per-device input without
// linking any Win32 registration or message-pump code.
public sealed record NativeRawMouseState(int DeltaX, int DeltaY, HashSet<string> Buttons, HashSet<string> Pulses);
public sealed record NativeRawInputSnapshot(
    IReadOnlyDictionary<string, HashSet<int>> Keyboards,
    IReadOnlyDictionary<string, NativeRawMouseState> Mice);
