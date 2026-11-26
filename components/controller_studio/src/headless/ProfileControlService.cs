using System.Security.Cryptography;
using System.Text.Json.Nodes;
using UnifiedController.Core;

namespace UnifiedController.Linux.App.Services;

/// <summary>Native parser-backed profile operations, never on the forwarding thread.</summary>
public sealed class ProfileControlService(ProfileDocumentService documents,
    Func<RuntimeSnapshot> snapshot, Action cancelMacros, Action? committed = null, Func<JsonObject>? outputStatus = null)
{
    private static readonly string[] Operations = ["status","profiles","profile_get","profile_select","profile_update","cancel_macros"];
    private string Revision() => Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(documents.Path)));
    private static JsonObject SerializeFrame(ControllerFrame f) => new JsonObject
    {
        ["buttons"] = (int)(f.Buttons & 0xFFFF),
        ["left_x"] = (int)f.LeftX,
        ["left_y"] = (int)f.LeftY,
        ["right_x"] = (int)f.RightX,
        ["right_y"] = (int)f.RightY,
        ["left_trigger"] = (int)f.LeftTrigger,
        ["right_trigger"] = (int)f.RightTrigger
    };
    private static string Name(JsonObject request)
    {
        var name=request["name"]?.GetValue<string>();
        if(string.IsNullOrWhiteSpace(name)||name.Length>256||name.Any(char.IsControl))
            throw new ControllerOperationException("ERR_INVALID_PROFILE_NAME");
        return name;
    }
    public JsonObject Handle(JsonObject request)
    {
        bool mutated=false;
        try
        {
            var operation=request["operation"]?.GetValue<string>();
            if(operation is null || !Operations.Contains(operation))throw new ControllerOperationException("ERR_UNSUPPORTED_OPERATION");
            if(operation=="status")
            {
                var s=snapshot();
                var virtualOutput=outputStatus?.Invoke();
                return new JsonObject {["running"]=s.Running,["health"]=s.Health,["active_profile"]=s.ActiveProfile,
                    ["modes"]=new JsonObject {["keyboard"]=s.ActiveModes.Contains(RuntimeActions.KeyboardControllerMode),
                        ["mouse"]=s.ActiveModes.Contains(RuntimeActions.MouseAimMode),["desktop"]=s.ActiveModes.Contains(RuntimeActions.DesktopMode)},
                    ["virtual_gamepad"]=virtualOutput,
                    ["virtual_gamepad_type"]=virtualOutput?["current_type"]?.DeepClone(),
                    ["capabilities"]=new JsonArray(Operations.Concat(new[]{"output_get","output_set","output_game_state"}).Select(x=>(JsonNode?)JsonValue.Create(x)).ToArray()),
                    ["devices"]=new JsonArray(s.Devices.Take(64).Select(d=>(JsonNode?)new JsonObject {
                        ["id"]=d.StableId, ["name"]=d.Name, ["connected"]=d.Connected, ["virtual"]=d.IsVirtual
                    }).ToArray()),
                    ["live"]=new JsonObject {
                        ["schema_version"]=1,
                        ["input"]=SerializeFrame(s.Input),
                        ["output"]=SerializeFrame(s.Output),
                        ["selected_source_id"]=s.SelectedSourceId,
                        ["extended_buttons"]=new JsonArray(s.ExtendedButtons.Take(32).Select(b=>(JsonNode?)JsonValue.Create(b)).ToArray()),
                        ["mod_active"]=s.ModActive,
                        ["controller_mouse_mode"]=s.ControllerMouseModeEnabled
                    }};
            }
            if(operation=="cancel_macros") {cancelMacros();return new JsonObject {["canceled"]=true};}
            documents.ReloadFromDisk();
            var revision=Revision();
            if(operation=="profiles")return new JsonObject {["active_profile"]=documents.ActiveName,
                ["names"]=new JsonArray(documents.Names.Select(x=>(JsonNode?)JsonValue.Create(x)).ToArray()),["revision"]=revision};
            var name=Name(request);
            var profiles=documents.Document["profiles"]!.AsObject();
            if(profiles[name] is not JsonObject profile)throw new ControllerOperationException("ERR_PROFILE_NOT_FOUND");
            if(operation=="profile_get")return new JsonObject {["name"]=name,["profile"]=profile.DeepClone(),["revision"]=revision};
            if(request["expected_revision"]?.GetValue<string>()!=revision)throw new ControllerOperationException("ERR_REVISION_CONFLICT");
            if(operation=="profile_select")
                documents.MutateDocument(doc=>doc["active_profile"]=name);
            else
            {
                if(request["profile"] is not JsonObject replacement)throw new ControllerOperationException("ERR_INVALID_PROFILE");
                var copy=profile.DeepClone().AsObject();
                foreach(var field in replacement)copy[field.Key]=field.Value?.DeepClone();
                documents.ValidateReplacement(name,copy);
                documents.MutateDocument(doc=>doc["profiles"]![name]=copy);
            }
            mutated=true;
            try { committed?.Invoke(); }
            catch { Console.Error.WriteLine("unified-controller: profile saved; optional editor refresh failed"); }
            return new JsonObject {["name"]=name,["active_profile"]=documents.ActiveName,["revision"]=Revision()};
        }
        catch(ControllerOperationException){throw;}
        catch(Exception e) when(e is ArgumentException or InvalidOperationException or System.Text.Json.JsonException or InvalidDataException)
        {throw new ControllerOperationException("ERR_INVALID_PROFILE");}
        catch(IOException){throw new ControllerOperationException(mutated?"ERR_OUTCOME_PENDING":"ERR_PROFILE_STORAGE_FAILED");}
        catch(UnauthorizedAccessException){throw new ControllerOperationException(mutated?"ERR_OUTCOME_PENDING":"ERR_PROFILE_STORAGE_FAILED");}
    }
}
