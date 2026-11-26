using System.Security.Cryptography;
using System.Text.Json.Nodes;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Linux.App;

/// Durable profile lifecycle commands. Never accepts a client-supplied filesystem path.
public sealed class ControllerProfileCommandService(ProfileDocumentService documents)
{
    public static readonly string[] AllOperations = ["profile_command_capabilities", "profile_create", "profile_copy", "profile_rename", "profile_delete", "profile_import", "profile_export", "profile_set_field"];
    public static bool Handles(string? op) => op is not null && AllOperations.Contains(op);
    private string Revision() => Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(documents.Path)));
    private static string Text(JsonObject r,string key) {
        if (r[key] is not JsonValue v || !v.TryGetValue<string>(out var value) || string.IsNullOrWhiteSpace(value) || value.Length>256 || value.Any(char.IsControl))
            throw new ControllerOperationException("ERR_INVALID_REQUEST");
        return value;
    }
    private static JsonObject RequireProfile(JsonObject profiles,string name) => profiles[name] as JsonObject ?? throw new ControllerOperationException("ERR_PROFILE_NOT_FOUND");
    private static void NewName(JsonObject profiles,string name) {
        if (profiles.ContainsKey(name)) throw new ControllerOperationException("ERR_PROFILE_EXISTS");
    }
    private static void SetField(JsonObject profile,string field,JsonNode? value) {
        bool valid=false;
        if (field is "rumble.enabled" or "controller_mouse.enabled")
            valid=value is JsonValue b && b.TryGetValue<bool>(out _);
        else if (field is "rumble.left_intensity" or "rumble.right_intensity")
            valid=value is JsonValue n && n.TryGetValue<double>(out var d) && double.IsFinite(d) && d>=0 && d<=100;
        else if(field=="source_merge_policy")
            valid=value is JsonValue s && s.TryGetValue<string>(out var policy) && policy is "first_active" or "all_connected";
        else if(field=="mouse_sensitivity")
            valid=value is JsonValue i && i.TryGetValue<int>(out var sensitivity) && sensitivity>=300 && sensitivity<=3000;
        if(!valid) throw new ControllerOperationException("ERR_INVALID_SETTING");
        var path=field.Split('.');
        if(path.Length==1) profile[field]=value!.DeepClone();
        else {
            // Do not silently replace a malformed/nonobject nested container, even when parser ignores it.
            if(profile[path[0]] is not null && profile[path[0]] is not JsonObject) throw new ControllerOperationException("ERR_INVALID_SETTING");
            var target=profile[path[0]] as JsonObject;
            if(target is null){target=new JsonObject();profile[path[0]]=target;}
            target[path[1]]=value!.DeepClone();
        }
    }
    public JsonObject Handle(JsonObject request) {
        var op=Text(request,"operation");
        if(!Handles(op))throw new ControllerOperationException("ERR_UNSUPPORTED_OPERATION");
        if(op=="profile_command_capabilities") {
            if(request.Any(p=>p.Key is not ("version" or "request_id" or "operation")))throw new ControllerOperationException("ERR_INVALID_REQUEST");
            return new JsonObject{["schema_version"]=1,["operations"]=new JsonArray(AllOperations.Select(x=>(JsonNode?)JsonValue.Create(x)).ToArray())};
        }
        var allowed=new HashSet<string>(["version","request_id","operation","name","expected_revision"]);
        if(op is "profile_copy" or "profile_rename")allowed.Add("source");
        if(op=="profile_import")allowed.Add("document");
        if(op=="profile_set_field")allowed.UnionWith(["field","value"]);
        if(request.Any(p=>!allowed.Contains(p.Key)))throw new ControllerOperationException("ERR_INVALID_REQUEST");
        var name=Text(request,"name");
        documents.ReloadFromDisk();
        var revision=Revision();
        if(request["expected_revision"] is not JsonValue rv || !rv.TryGetValue<string>(out var expected) || expected!=revision)
            throw new ControllerOperationException("ERR_REVISION_CONFLICT");
        var profiles=documents.Document["profiles"]!.AsObject();
        if(op=="profile_export") {
            var profile=RequireProfile(profiles,name);
            var export=documents.Document.DeepClone().AsObject();
            export["active_profile"]=name;export["profiles"]=new JsonObject{[name]=profile.DeepClone()};
            return new JsonObject{["name"]=name,["revision"]=revision,["document"]=export};
        }
        JsonObject? replacement=null;JsonObject? rootExtras=null;string? source=null;
        switch(op) {
            case "profile_create":
                NewName(profiles,name);replacement=new JsonObject{["routes"]=new JsonObject(),["enabled_output_slots"]=new JsonArray(1)};break;
            case "profile_copy": case "profile_rename":
                NewName(profiles,name);source=Text(request,"source");replacement=RequireProfile(profiles,source).DeepClone().AsObject();break;
            case "profile_delete":
                RequireProfile(profiles,name);
                if(profiles.Count<=1)throw new ControllerOperationException("ERR_LAST_PROFILE");break;
            case "profile_set_field":
                replacement=RequireProfile(profiles,name).DeepClone().AsObject();SetField(replacement,Text(request,"field"),request["value"]);break;
            case "profile_import":
                NewName(profiles,name);
                if(request["document"] is not JsonObject imported || imported["version"] is not JsonValue iv || !iv.TryGetValue<int>(out var ver) || ver!=1 ||
                    imported["profiles"] is not JsonObject incoming || incoming.Count!=1 || incoming.First().Value is not JsonObject importedProfile ||
                    (imported["active_profile"] is not JsonValue av || !av.TryGetValue<string>(out var importedActive) || importedActive!=incoming.First().Key))
                    throw new ControllerOperationException("ERR_INVALID_PROFILE");
                rootExtras=new JsonObject();
                foreach(var pair in imported.Where(p=>p.Key is not ("version" or "profiles" or "active_profile"))) {
                    if(documents.Document.ContainsKey(pair.Key) && !JsonNode.DeepEquals(documents.Document[pair.Key],pair.Value))
                        throw new ControllerOperationException("ERR_IMPORT_CONFLICT");
                    if(!documents.Document.ContainsKey(pair.Key))rootExtras[pair.Key]=pair.Value?.DeepClone();
                }
                replacement=importedProfile.DeepClone().AsObject();break;
        }
        try {
            if(replacement is not null)documents.ValidateReplacement(name,replacement);
            documents.MutateDocument(doc=> {
                var p=doc["profiles"]!.AsObject();
                if(op=="profile_delete") {
                    p.Remove(name);
                    if(doc["active_profile"]!.GetValue<string>()==name)doc["active_profile"]=p.First().Key;
                } else {
                    p[name]=replacement!.DeepClone();
                    if(op=="profile_rename") {
                        p.Remove(source!);
                        if(doc["active_profile"]!.GetValue<string>()==source)doc["active_profile"]=name;
                    }
                    if(rootExtras is not null)foreach(var pair in rootExtras)doc[pair.Key]=pair.Value?.DeepClone();
                }
            });
        }
        catch(ControllerOperationException){throw;}
        catch(Exception e) when(e is ArgumentException or InvalidOperationException or System.Text.Json.JsonException or InvalidDataException)
        {throw new ControllerOperationException("ERR_INVALID_PROFILE");}
        catch(Exception e) when(e is IOException or UnauthorizedAccessException) {throw new ControllerOperationException("ERR_OUTCOME_PENDING");}
        string finalRevision;
        try { finalRevision=Revision(); }
        catch(Exception e) when(e is IOException or UnauthorizedAccessException) {throw new ControllerOperationException("ERR_OUTCOME_PENDING");}
        return new JsonObject{["name"]=name,["active_profile"]=documents.ActiveName,["revision"]=finalRevision,["committed"]=true};
    }
}
