using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App.Models;

public static class VisualSurfaceActivity
{
    public static bool IsActive(this VisualSurfaceState state, string code)
    {
        if (state.ActiveDesktopControls.Contains(code) || state.ExtendedButtons.Contains(code)) return true;
        var separator = code.IndexOf(':');
        var name = separator >= 0 ? code[(separator + 1)..] : code;
        if (state.ActiveDesktopControls.Contains(name) || state.ExtendedButtons.Contains(name)) return true;
        if (!code.StartsWith("Controller:", StringComparison.Ordinal)) return false;
        if (name == "LT") return state.Input.LeftTrigger > 20;
        if (name == "RT") return state.Input.RightTrigger > 20;
        return ProfileEditorService.ControllerButtons.Any(pair => pair.Value == name && (state.Input.Buttons & pair.Key) != 0);
    }
}
