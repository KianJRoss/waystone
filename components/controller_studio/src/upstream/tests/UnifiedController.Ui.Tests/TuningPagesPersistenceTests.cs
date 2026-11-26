using System.Text.Json.Nodes;
using UnifiedController.App.Engine;
using UnifiedController.Core;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Ui.Tests;

public sealed class TuningPagesPersistenceTests
{
    [Fact]
    public void Rumble_keys_round_trip_through_native_parser()
    {
        var profile = Profile(); profile["rumble"] = new JsonObject { ["extension"] = "keep" };
        ProfileEditorService.SetRumble(profile, false, 35, 72);
        var path = Write(profile);
        try { var native = NativeProfileRuntime.Load(path); Assert.False(native.RumbleEnabled); Assert.Equal(.35, native.LeftRumbleScale, 3); Assert.Equal(.72, native.RightRumbleScale, 3); Assert.Equal("keep", profile["rumble"]!["extension"]!.GetValue<string>()); }
        finally { File.Delete(path); }
    }

    [Fact]
    public void Stick_curve_and_deadzone_are_native_and_lossless()
    {
        var profile = Profile(); profile["stick_processing"] = new JsonObject { ["left"] = new JsonObject { ["extension"] = 8 } };
        ProfileEditorService.SetStick(profile, "left", true, ".12", ".91", ".08", "1.6");
        var path = Write(profile);
        try { var stick = NativeProfileRuntime.Load(path).LeftStick; Assert.True(stick.Enabled); Assert.Equal(.12, stick.InnerDeadzone); Assert.Equal(.91, stick.OuterDeadzone); Assert.Equal(.08, stick.AntiDeadzone); Assert.Equal(1.6, stick.Curve); Assert.Equal(8, profile["stick_processing"]!["left"]!["extension"]!.GetValue<int>()); }
        finally { File.Delete(path); }
    }

    [Fact]
    public void Macro_step_edit_repeat_and_unknown_fields_survive()
    {
        var profile = Profile(); profile["macros"] = new JsonObject { ["Cast"] = new JsonObject { ["extension"] = true } };
        var steps = new JsonArray(new JsonObject { ["type"] = "press", ["target"] = "Controller:A", ["hold_ms"] = 80, ["delay_ms"] = 20 });
        ProfileEditorService.SetMacro(profile, "Cast", 3, true, steps);
        var path = Write(profile);
        try { var macro = NativeProfileRuntime.Load(path).Macros["Cast"]; Assert.Equal(3, macro.RepeatCount); Assert.True(macro.CancelOnRelease); Assert.True(profile["macros"]!["Cast"]!["extension"]!.GetValue<bool>()); Assert.Equal(80, profile["macros"]!["Cast"]!["steps"]![0]!["hold_ms"]!.GetValue<int>()); }
        finally { File.Delete(path); }
    }

    [Fact]
    public void Macro_editor_emits_only_native_step_fields()
    {
        var steps = new JsonArray(
            ProfileEditorService.CreateMacroStep("press", "Controller:A", 0),
            ProfileEditorService.CreateMacroStep("axis", "Controller:Left Stick X=0.5", 0),
            ProfileEditorService.CreateMacroStep("delay", "", 50),
            ProfileEditorService.CreateMacroStep("action", RuntimeActions.DesktopMode, 0));
        var profile = Profile(); ProfileEditorService.SetMacro(profile, "Native", 1, false, steps);
        var path = Write(profile);
        try
        {
            var macro = NativeProfileRuntime.Load(path).Macros["Native"];
            Assert.Equal(new[] { "press", "axis", "delay", "action" }, macro.Steps.Select(x => x.Type));
            Assert.Equal(.5, macro.Steps[1].Value); Assert.Equal(50, macro.Steps[2].DurationMs);
            Assert.Equal(RuntimeActions.DesktopMode, macro.Steps[3].Action);
        }
        finally { File.Delete(path); }
    }

    [Fact]
    public void Hyprland_content_has_all_defaults_and_install_is_scoped()
    {
        var text = HotkeyBindingService.BuildHyprlandContent();
        Assert.Contains("CTRL ALT, F10", text); Assert.Contains("--action \"Open Control Center\"", text); Assert.Contains("CTRL ALT, F1", text);
        var home = Path.Combine(Path.GetTempPath(), $"uc-hotkeys-{Guid.NewGuid():N}");
        try { var path = HotkeyBindingService.Install(home); Assert.Equal(Path.Combine(home, ".config", "hypr", "unified-controller-hotkeys.conf"), path); Assert.Equal(text, File.ReadAllText(path)); Assert.Single(Directory.GetFiles(Path.Combine(home, ".config", "hypr"))); }
        finally { if (Directory.Exists(home)) Directory.Delete(home, true); }
    }

    [Fact]
    public void Motion_trigger_and_mouse_edits_preserve_unknown_fields()
    {
        var profile = Profile(); profile["motion"] = new JsonObject { ["left"] = new JsonObject { ["future"] = 1 } }; profile["trigger_processing"] = new JsonObject { ["left"] = new JsonObject { ["future"] = 2 } }; profile["controller_mouse"] = new JsonObject { ["future"] = 3 };
        ProfileEditorService.SetMotionSubObject(profile, "left", new JsonObject { ["enabled"] = true });
        ProfileEditorService.SetTriggers(profile, new JsonObject { ["inner_deadzone"] = .1 }, new JsonObject { ["inner_deadzone"] = .2 });
        ProfileEditorService.SetControllerMouse(profile, new JsonObject { ["enabled"] = true });
        Assert.Equal(1, profile["motion"]!["left"]!["future"]!.GetValue<int>()); Assert.Equal(2, profile["trigger_processing"]!["left"]!["future"]!.GetValue<int>()); Assert.Equal(3, profile["controller_mouse"]!["future"]!.GetValue<int>());
    }

    private static JsonObject Profile() => new() { ["routes"] = new JsonObject(), ["enabled_output_slots"] = new JsonArray(1) };
    private static string Write(JsonObject profile) { var path = Path.Combine(Path.GetTempPath(), $"uc-pages-{Guid.NewGuid():N}.json"); File.WriteAllText(path, new JsonObject { ["version"] = 1, ["active_profile"] = "P", ["profiles"] = new JsonObject { ["P"] = profile } }.ToJsonString()); return path; }
}
