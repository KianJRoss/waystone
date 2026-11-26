using System.Security.Cryptography;
using System.Text.Json.Nodes;
using UnifiedController.Core;

namespace UnifiedController.Linux.App.Services;

/// <summary>Typed output operations, serialized by the existing socket handler. File edits preserve all extension fields.</summary>
public sealed class OutputControlService(ProfileDocumentService documents, IOutputTypeController output)
{
    public static bool Handles(string? operation) => operation is "output_get" or "output_set" or "output_game_state";
    public static string ConfiguredType(JsonObject document) => document["virtual_gamepad_driver"] is null
        ? "xbox360" : VirtualOutputTypes.Normalize(document["virtual_gamepad_driver"]!.GetValue<string>());
    private string Revision() => Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(documents.Path)));
    private string? _persistenceError;
    public JsonObject Read()
    {
        documents.ReloadFromDisk();
        var state = output.OutputStatus;
        return new() {
            ["current_type"]=state.CurrentType, ["default_type"]=ConfiguredType(documents.Document),
            ["supported_types"]=new JsonArray(VirtualOutputTypes.Supported.Select(x=>(JsonNode?)JsonValue.Create(x)).ToArray()),
            ["owned_devices"]=new JsonArray(output.OwnedDevices.Where(d=>d.Connected&&!d.IsVirtual).Take(64).Select(d=>(JsonNode?)new JsonObject {
                ["stable_id"]=d.StableId,["vendor_id"]=d.VendorId,["product_id"]=d.ProductId,["connected"]=d.Connected,["virtual"]=d.IsVirtual }).ToArray()),
            ["slots"]=new JsonArray(state.Slots.Select(s=>(JsonNode?)new JsonObject {
                ["slot"]=s.Slot,["type"]=s.Type,["connected"]=s.Connected,["state"]=s.State,["error"]=s.Error }).ToArray()),
            ["last_switch_error"]=_persistenceError ?? state.LastSwitchError,["partial_failure"]=_persistenceError is not null || state.PartialFailure,
            ["game_state"]=state.GameState,["game_observation_scope"]="waystone_tracked_games",
            ["game_observation_age_seconds"]=state.GameObservationAgeSeconds,
            ["active_game_ids"]=new JsonArray(state.ActiveGameIds.Select(x=>(JsonNode?)JsonValue.Create(x)).ToArray()),
            ["revision"]=Revision()
        };
    }
    private static bool Boolean(JsonObject request,string key,bool fallback)
    {
        if(!request.ContainsKey(key)) return fallback;
        if(request[key] is not JsonValue value || !value.TryGetValue<bool>(out var result)) throw new ControllerOperationException("ERR_INVALID_REQUEST");
        return result;
    }
    private static void Fields(JsonObject request,params string[] fields)
    {
        var allowed=fields.Concat(new[]{"version","request_id","operation"}).ToHashSet(StringComparer.Ordinal);
        if(request.Any(p=>!allowed.Contains(p.Key))) throw new ControllerOperationException("ERR_INVALID_REQUEST");
    }
    public async Task<JsonObject> HandleAsync(JsonObject request)
    {
        var operation=request["operation"]?.GetValue<string>();
        if(operation=="output_get") { Fields(request); return Read(); }
        if(operation=="output_game_state")
        {
            Fields(request,"active_game_ids","observation_known");
            var known=Boolean(request,"observation_known",true);
            if(request["active_game_ids"] is not JsonArray ids || ids.Count>256) throw new ControllerOperationException("ERR_INVALID_REQUEST");
            var values=new List<string>();
            foreach(var id in ids)
            {
                if(id is not JsonValue value || !value.TryGetValue<string>(out var text) || string.IsNullOrWhiteSpace(text) ||
                    text.Length>512 || text.Any(char.IsControl)) throw new ControllerOperationException("ERR_INVALID_REQUEST");
                values.Add(text);
            }
            if(!known && values.Count!=0) throw new ControllerOperationException("ERR_INVALID_REQUEST");
            output.ObserveGames(values,known); return Read();
        }
        if(operation!="output_set") throw new ControllerOperationException("ERR_UNSUPPORTED_OPERATION");
        Fields(request,"type","force","persist_default","expected_revision");
        if(request["type"] is not JsonValue typeValue || !typeValue.TryGetValue<string>(out var rawType)) throw new ControllerOperationException("ERR_INVALID_REQUEST");
        string type;
        try { type=VirtualOutputTypes.Normalize(rawType); }
        catch(ArgumentException) { throw new ControllerOperationException("ERR_INVALID_OUTPUT_TYPE"); }
        var force=Boolean(request,"force",false);
        var persist=Boolean(request,"persist_default",true);
        documents.ReloadFromDisk();
        var revision=Revision();
        if(request.ContainsKey("expected_revision"))
        {
            if(request["expected_revision"] is not JsonValue expected || !expected.TryGetValue<string>(out var value) ||
                value.Length!=64 || !value.All(Uri.IsHexDigit)) throw new ControllerOperationException("ERR_INVALID_REQUEST");
            if(!string.Equals(value,revision,StringComparison.OrdinalIgnoreCase)) throw new ControllerOperationException("ERR_REVISION_CONFLICT");
        }
        bool changed;
        try { changed=await output.SetOutputTypeAsync(type,force).ConfigureAwait(false); }
        catch(OutputSwitchException ex) { throw new ControllerOperationException(ex.Code); }
        catch(Exception ex) when(ex is IOException or InvalidOperationException or AggregateException)
        { throw new ControllerOperationException("ERR_OUTPUT_SWITCH_FAILED"); }
        if(persist)
        {
            try
            {
                // Re-read after async device work; reject an intervening writer rather than clobbering its extensions.
                documents.ReloadFromDisk();
                if(Revision()!=revision)
                {
                    _persistenceError="Output applied, but profile revision changed before default persistence.";
                    throw new ControllerOperationException("ERR_OUTPUT_PERSISTENCE_FAILED");
                }
                if(ConfiguredType(documents.Document)!=type) documents.MutateDocument(doc=>doc["virtual_gamepad_driver"]=type);
                _persistenceError=null;
            }
            catch(ControllerOperationException) { throw; }
            catch(Exception ex) when(ex is IOException or UnauthorizedAccessException or InvalidOperationException or InvalidDataException)
            {
                _persistenceError="Output applied, but configured default persistence failed: "+ex.Message;
                throw new ControllerOperationException("ERR_OUTPUT_PERSISTENCE_FAILED");
            }
        }
        var result=Read(); result["changed"]=changed; return result;
    }
}
