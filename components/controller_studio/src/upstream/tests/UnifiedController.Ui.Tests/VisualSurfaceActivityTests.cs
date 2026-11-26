using UnifiedController.Core;
using UnifiedController.Linux.App.Models;

namespace UnifiedController.Ui.Tests;

public sealed class VisualSurfaceActivityTests
{
    [Fact]
    public void PhysicalAbxyAndTriggersLightOnlyTheirOwnVisualControls()
    {
        var state = new VisualSurfaceState(new Dictionary<string,string>(), null, null, MapperInteractionMode.Source,
            new ControllerFrame(0x1000 | 0x0001, 255, 0, 0, 0, 0, 0), default,
            new HashSet<string> { "Keyboard:W" }, new HashSet<string> { "Share" }, null);
        Assert.True(state.IsActive("Controller:A"));
        Assert.True(state.IsActive("Controller:D-pad Up"));
        Assert.True(state.IsActive("Controller:LT"));
        Assert.True(state.IsActive("Keyboard:W"));
        Assert.True(state.IsActive("Controller:Share"));
        Assert.False(state.IsActive("Controller:X"));
        Assert.False(state.IsActive("Controller:RT"));
    }
}
