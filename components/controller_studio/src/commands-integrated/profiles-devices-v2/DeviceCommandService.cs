using System.Text.Json.Nodes;
using UnifiedController.Core;

namespace UnifiedController.Linux.App;

/// Uses the original provider's pairing/capture APIs. No invented source-selector setter.
public sealed class ControllerDeviceCommandService(IControllerRuntime runtime)
{
    public static readonly string[] AllOperations=["device_command_capabilities","joycon_pair","joycon_unpair","device_capture"];
    public static bool Handles(string? op)=>op is not null && AllOperations.Contains(op);
    private static string Identifier(JsonObject r,string key) {
        if(r[key] is not JsonValue v || !v.TryGetValue<string>(out var s) || string.IsNullOrWhiteSpace(s) || s.Length>512 || s.Any(char.IsControl))
            throw new ControllerOperationException("ERR_INVALID_REQUEST");
        return s;
    }
    private IConfigurableControllerInput? Configuration {
        get {try{return runtime.ControllerInput as IConfigurableControllerInput;}catch(NotSupportedException){return null;}}
    }
    private static JsonArray Pairs(IConfigurableControllerInput config)=>new(config.JoyConPairs.Take(64).Select(p=>(JsonNode?)new JsonObject{
        ["id"]=p.Id,["left_id"]=p.LeftId,["right_id"]=p.RightId,["name"]=p.Name}).ToArray());
    public JsonObject Handle(JsonObject request) {
        var op=Identifier(request,"operation");
        if(!Handles(op))throw new ControllerOperationException("ERR_UNSUPPORTED_OPERATION");
        var allowed=new HashSet<string>(["version","request_id","operation"]);
        if(op=="joycon_pair")allowed.UnionWith(["left_id","right_id"]);
        if(op=="joycon_unpair")allowed.Add("pair_id");
        if(op=="device_capture")allowed.Add("enabled");
        if(request.Any(p=>!allowed.Contains(p.Key)))throw new ControllerOperationException("ERR_INVALID_REQUEST");
        var config=Configuration;
        if(op=="device_command_capabilities")return new JsonObject {
            ["schema_version"]=1,["operations"]=new JsonArray((config is null ? ["device_command_capabilities"] : AllOperations).Select(x=>(JsonNode?)JsonValue.Create(x)).ToArray()),
            ["pairs"]=config is null ? new JsonArray() : Pairs(config),["exclusive_capture"]=config?.ExclusiveCapture,
            ["source_selection_mode"]="automatic",["select_source_supported"]=false};
        if(config is null)throw new ControllerOperationException("ERR_UNSUPPORTED_OPERATION");
        string? left=null,right=null,pair=null;bool enabled=false;
        if(op=="joycon_pair") {
            left=Identifier(request,"left_id");right=Identifier(request,"right_id");
            if(left==right || !runtime.ControllerInput.Devices.Any(d=>d.StableId==left && d.Connected && !d.IsVirtual) ||
                !runtime.ControllerInput.Devices.Any(d=>d.StableId==right && d.Connected && !d.IsVirtual))
                throw new ControllerOperationException("ERR_INVALID_DEVICE_PAIR");
        }
        if(op=="joycon_unpair") {
            pair=Identifier(request,"pair_id");
            if(!config.JoyConPairs.Any(p=>p.Id==pair))throw new ControllerOperationException("ERR_PAIR_NOT_FOUND");
        }
        if(op=="device_capture" && (request["enabled"] is not JsonValue b || !b.TryGetValue<bool>(out enabled)))
            throw new ControllerOperationException("ERR_INVALID_REQUEST");
        try {
            if(op=="joycon_pair")config.PairJoyCons(left!,right!);
            else if(op=="joycon_unpair")config.UnpairJoyCons(pair!);
            else config.ExclusiveCapture=enabled;
        }
        catch(NotSupportedException){throw new ControllerOperationException("ERR_UNSUPPORTED_OPERATION");}
        catch(ArgumentException){throw new ControllerOperationException("ERR_INVALID_DEVICE_PAIR");}
        catch(Exception e) when(e is IOException or UnauthorizedAccessException){throw new ControllerOperationException("ERR_OUTCOME_PENDING");}
        // Re-read provider state. An operation returning without the requested effect is NOT success.
        bool verified=op switch {
            "joycon_pair"=>config.JoyConPairs.Any(p=>p.LeftId==left && p.RightId==right),
            "joycon_unpair"=>!config.JoyConPairs.Any(p=>p.Id==pair),
            _=>config.ExclusiveCapture==enabled };
        if(!verified)throw new ControllerOperationException("ERR_OUTCOME_PENDING");
        return new JsonObject{["accepted"]=true,["operation"]=op,["completion"]="provider_state_verified",["pairs"]=Pairs(config),["exclusive_capture"]=config.ExclusiveCapture};
    }
}
