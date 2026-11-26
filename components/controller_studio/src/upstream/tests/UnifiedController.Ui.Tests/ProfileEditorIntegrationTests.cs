using System.Text.Json.Nodes;
using UnifiedController.App.Engine;
using UnifiedController.Core;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Ui.Tests;

public sealed class ProfileEditorIntegrationTests
{
    [Fact]
    public void Edits_are_consumed_by_the_linked_native_parser_and_extensions_survive()
    {
        var profile = new JsonObject { ["routes"] = new JsonObject(), ["future_profile"] = new JsonObject { ["keep"] = 7 } };
        ProfileEditorService.SetStick(profile, "left", true, ".1", "1", ".2", "1.7");
        ProfileEditorService.SetMotion(profile, "custom", right: new JsonObject
        {
            ["enabled"] = true, ["mode"] = "rate", ["x_axis"] = "yaw", ["y_axis"] = "pitch",
            ["full_scale"] = 90, ["deadzone"] = 3, ["future_side"] = "keep"
        });
        ProfileEditorService.SetMacro(profile, "Burst", 4, true, new JsonArray(new JsonObject { ["type"] = "press", ["target"] = "Controller:A" }));
        ProfileEditorService.SetRoute(profile, "Controller:B", "Controller:X", null, [], "while-held", "turbo", 3, 15, .4);
        profile["enabled_output_slots"] = new JsonArray(1, 3);
        profile["device_output_slots"] = new JsonObject { ["pad-a"] = 2 };
        profile["source_merge_policy"] = "all_connected";

        var path = WriteDocument(profile);
        try
        {
            var native = NativeProfileRuntime.Load(path);
            Assert.True(native.LeftStick.Enabled); Assert.Equal(1.7, native.LeftStick.Curve);
            Assert.Equal("custom", native.Motion.Preset); Assert.True(native.Motion.Right.Enabled); Assert.Equal(90, native.Motion.Right.FullScale);
            Assert.Equal(4, native.Macros["Burst"].RepeatCount); Assert.True(native.Macros["Burst"].CancelOnRelease);
            Assert.Equal("all_connected", native.SourceMergePolicy); Assert.Contains(3, native.EnabledOutputSlots); Assert.Equal(2, native.DeviceOutputSlots["pad-a"]);
            Assert.Equal(7, profile["future_profile"]!["keep"]!.GetValue<int>());
            Assert.Equal("keep", profile["motion"]!["right"]!["future_side"]!.GetValue<string>());
            Assert.Equal(15, profile["route_effects"]!["Controller:B"]!["rate_hz"]!.GetValue<double>());
        }
        finally { File.Delete(path); }
    }

    [Theory]
    [InlineData(0x1000, "Controller:A")]
    [InlineData(0x2000, "Controller:B")]
    [InlineData(0x4000, "Controller:X")]
    [InlineData(0x8000, "Controller:Y")]
    public void Listener_uses_real_non_contiguous_xinput_masks(int mask, string expected)
    {
        var snapshot = Snapshot(new ControllerFrame(mask, 0, 0, 0, 0, 0, 0));
        Assert.Equal(expected, ProfileEditorService.Listen(snapshot, 0));
    }

    [Fact]
    public void Listener_prefers_fresh_per_device_keyboard_or_mouse_source()
    {
        var snapshot = Snapshot(default) with { LastInputSource = "KeyboardDevice:raw-kbd:Space", LastInputTimestamp = 42 };
        Assert.Equal("KeyboardDevice:raw-kbd:Space", ProfileEditorService.Listen(snapshot, 41));
        Assert.Null(ProfileEditorService.Listen(snapshot, 42));
    }

    [Fact]
    public void Macro_edit_preserves_unknown_macro_fields()
    {
        var profile = new JsonObject { ["macros"] = new JsonObject { ["M"] = new JsonObject { ["vendor_extension"] = 9, ["steps"] = new JsonArray() } } };
        ProfileEditorService.SetMacro(profile, "M", 2, false, new JsonArray(new JsonObject { ["type"] = "delay", ["duration_ms"] = 10 }));
        Assert.Equal(9, profile["macros"]!["M"]!["vendor_extension"]!.GetValue<int>());
        Assert.Equal(2, profile["macros"]!["M"]!["repeat_count"]!.GetValue<int>());
        Assert.Null(profile["macros"]!["M"]!["repeats"]);
    }

    private static RuntimeSnapshot Snapshot(ControllerFrame input) => new("Default", true, "ok", [], null, input, default, null, 0, 0, 0, null, []);
    private static string WriteDocument(JsonObject profile)
    {
        var path = Path.Combine(Path.GetTempPath(), $"uc-ui-{Guid.NewGuid():N}.json");
        File.WriteAllText(path, new JsonObject { ["version"] = 1, ["active_profile"] = "P", ["profiles"] = new JsonObject { ["P"] = profile } }.ToJsonString());
        return path;
    }
}
