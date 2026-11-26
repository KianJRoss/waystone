using UnifiedController.Core;
using System.Text.Json.Nodes;
using UnifiedController.Linux.Platform.Desktop;
using UnifiedController.Linux.Platform.Output;
using UnifiedController.Linux.Platform.Timing;

namespace UnifiedController.Linux.Platform;
public static class PlatformServices
{
    public static IControllerRuntime CreateRuntime(string rootDirectory)
    {
        var document = JsonNode.Parse(File.ReadAllText(Path.Combine(rootDirectory,"profiles.json")))!.AsObject();
        var configured = document["virtual_gamepad_driver"] is null ? "xbox360" : VirtualOutputTypes.Normalize(document["virtual_gamepad_driver"]!.GetValue<string>());
        var type=Type.GetType("UnifiedController.Linux.Platform.Input.SdlControllerProvider, UnifiedController.Linux.Platform",throwOnError:true)!;
        var input=(IControllerInputProvider?)Activator.CreateInstance(type,rootDirectory) ?? throw new InvalidOperationException("SDL controller provider could not be created.");
        try { return new ControllerRuntime(rootDirectory,input,VirtualOutputFactory.Create,configured,new EvdevKeyboardMouseSource(),new UInputDesktopOutput(),new LinuxMonotonicClock()); }
        catch { input.DisposeAsync().AsTask().GetAwaiter().GetResult(); throw; }
    }
}
