namespace UnifiedController.App.Engine;

/// <summary>Retains an active source; hands off when another source is used.</summary>
public sealed class NativeSourceSelector
{
    private string? _selected;

    public int Select(IReadOnlyList<(string Id, NativeControllerReport Report)> inputs)
    {
        if (inputs.Count == 0) { _selected = null; return -1; }
        var previous = -1;
        for (var i = 0; i < inputs.Count; i++)
            if (inputs[i].Id == _selected) { previous = i; break; }
        var selected = previous >= 0 && IsActive(inputs[previous].Report) ? previous : -1;
        if (selected < 0)
            for (var i = 0; i < inputs.Count; i++)
                if (IsActive(inputs[i].Report)) { selected = i; break; }
        if (selected < 0) selected = previous >= 0 ? previous : 0;
        _selected = inputs[selected].Id;
        return selected;
    }

    private static bool IsActive(NativeControllerReport report) =>
        report.Buttons != 0 || report.LeftTrigger > 8 || report.RightTrigger > 8 ||
        (long)report.LeftX * report.LeftX + (long)report.LeftY * report.LeftY > 4096L * 4096 ||
        (long)report.RightX * report.RightX + (long)report.RightY * report.RightY > 4096L * 4096;
}
