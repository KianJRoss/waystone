using System.Text.Json;
using System.Text.Json.Nodes;
using UnifiedController.App.Engine;

namespace UnifiedController.Linux.App.Services;

public sealed class ProfileDocumentService
{
    private readonly string _path;
    public JsonObject Document { get; private set; }
    public string Path => _path;
    public IEnumerable<string> Names => Profiles.Select(p => p.Key);
    public string ActiveName => Document["active_profile"]!.GetValue<string>();
    public JsonObject ActiveProfile => Profiles[ActiveName]!.AsObject();
    private JsonObject Profiles => Document["profiles"]!.AsObject();

    public ProfileDocumentService(string root)
    {
        _path = System.IO.Path.Combine(root, "profiles.json");
        Document = File.Exists(_path) ? Parse(File.ReadAllText(_path)) : NewDocument();
        if (!File.Exists(_path)) Save(); // Never overlays an existing document.
    }

    public void ValidateReplacement(string name, JsonObject profile)
    {
        if (profile["routes"] is JsonNode routesNode)
        {
            if (routesNode is not JsonObject routes) throw new InvalidDataException("Routes must be an object.");
            foreach (var route in routes)
                if (string.IsNullOrWhiteSpace(route.Key) || route.Key.Length > 512 ||
                    route.Value is not JsonValue value || !value.TryGetValue<string>(out var target) ||
                    string.IsNullOrWhiteSpace(target) || target.Length > 512 ||
                    route.Key.Any(char.IsControl) || target.Any(char.IsControl))
                    throw new InvalidDataException("Routes require literal source and destination names.");
        }
        var candidate=Document.DeepClone().AsObject();
        candidate["active_profile"]=name;
        candidate["profiles"]![name]=profile.DeepClone();
        Validate(candidate);
        var path=_path+"."+Guid.NewGuid().ToString("N")+".validation";
        try { File.WriteAllText(path,candidate.ToJsonString(JsonOptions)); _=NativeProfileRuntime.Load(path); }
        finally { if(File.Exists(path))File.Delete(path); }
    }

    public void ReloadFromDisk() { Document = Parse(File.ReadAllText(_path)); }

    public void MutateDocument(Action<JsonObject> mutation)
    {
        var beforeDocument = Document.DeepClone().AsObject();
        var beforePrimary = File.Exists(_path) ? File.ReadAllBytes(_path) : null;
        var backupPath = _path + ".bak";
        var beforeBackup = File.Exists(backupPath) ? File.ReadAllBytes(backupPath) : null;
        try { mutation(Document); Save(); }
        catch
        {
            Document = beforeDocument;
            Restore(_path, beforePrimary); Restore(backupPath, beforeBackup);
            throw;
        }
    }

    public void Select(string name) { RequireName(name); Document["active_profile"] = name; Save(); }
    public void Duplicate(string source, string destination)
    { RequireNewName(destination); Profiles[destination] = Profiles[source]!.DeepClone(); Select(destination); }
    public void Rename(string source, string destination)
    { RequireNewName(destination); var value = Profiles[source]; Profiles.Remove(source); Profiles[destination] = value; Select(destination); }
    public void Delete(string name)
    {
        if (Profiles.Count <= 1) throw new InvalidOperationException("At least one profile is required.");
        Profiles.Remove(name); Document["active_profile"] = Profiles.First().Key; Save();
    }
    public void Import(string path, string? name = null)
    {
        var imported = Parse(File.ReadAllText(path));
        if (imported["profiles"] is JsonObject all)
            foreach (var item in all) { var target = Unique(item.Key); Profiles[target] = item.Value?.DeepClone(); }
        else throw new InvalidDataException("Import must be a version-1 profile document.");
        if (name is not null && all.Count == 1) Rename(Profiles.Last().Key, name); else Save();
    }
    public void Export(string name, string path)
    {
        RequireName(name);
        // Keep document-level extension fields as well as profile-level ones.
        var export = Document.DeepClone().AsObject();
        export["active_profile"] = name;
        export["profiles"] = new JsonObject { [name] = Profiles[name]!.DeepClone() };
        AtomicWrite(path, export.ToJsonString(JsonOptions));
    }
    public void Save()
    {
        Validate(Document);
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(_path)!);
        var payload = Document.ToJsonString(JsonOptions) + Environment.NewLine;
        var candidate = _path + $".{Environment.ProcessId}.candidate";
        try
        {
            File.WriteAllText(candidate, payload);
            _ = NativeProfileRuntime.Load(candidate); // exact production parser, before touching last-good
            if (File.Exists(_path)) File.Copy(_path, _path + ".bak", true);
            AtomicWrite(_path, payload);
        }
        finally { if (File.Exists(candidate)) File.Delete(candidate); }
    }

    /// <summary>Applies an edit only when both native validation and durable persistence succeed.</summary>
    public void MutateActiveProfile(Action<JsonObject> mutation)
    {
        var beforeDocument = Document.DeepClone().AsObject();
        var beforePrimary = File.Exists(_path) ? File.ReadAllBytes(_path) : null;
        var backupPath = _path + ".bak";
        var beforeBackup = File.Exists(backupPath) ? File.ReadAllBytes(backupPath) : null;
        try
        {
            mutation(ActiveProfile);
            Save();
        }
        catch
        {
            Document = beforeDocument;
            Restore(_path, beforePrimary);
            Restore(backupPath, beforeBackup);
            throw;
        }
    }

    private static JsonObject Parse(string json)
    {
        var document = JsonNode.Parse(json)?.AsObject() ?? throw new InvalidDataException("Profile JSON must be an object.");
        Validate(document);
        return document;
    }
    private static JsonObject NewDocument() => new() { ["version"] = 1, ["active_profile"] = "Default",
        ["profiles"] = new JsonObject { ["Default"] = new JsonObject { ["routes"] = new JsonObject(), ["enabled_output_slots"] = new JsonArray(1) } } };
    private static void Validate(JsonObject doc)
    {
        if (doc["version"]?.GetValue<int>() != 1) throw new InvalidDataException("Only profile document version 1 is supported.");
        var profiles = doc["profiles"] as JsonObject;
        var active = doc["active_profile"]?.GetValue<string>();
        if (profiles is null || profiles.Count == 0 || active is null || !profiles.ContainsKey(active))
            throw new InvalidDataException("Profiles must be a non-empty object and active_profile must exist.");
        foreach (var profile in profiles)
        {
            if (profile.Value is not JsonObject p) throw new InvalidDataException($"Profile {profile.Key} must be an object.");
            if (p["routes"] is JsonNode routes && routes is not JsonObject) throw new InvalidDataException($"Routes in {profile.Key} must be an object.");
            if (p["typed_routes"] is JsonNode typed && typed is not JsonArray) throw new InvalidDataException($"typed_routes in {profile.Key} must be an array.");
        }
    }
    private void RequireName(string name) { if (!Profiles.ContainsKey(name)) throw new KeyNotFoundException(name); }
    private void RequireNewName(string name)
    { if (string.IsNullOrWhiteSpace(name) || Profiles.ContainsKey(name)) throw new InvalidOperationException("Choose a unique non-empty profile name."); }
    private string Unique(string name) { var candidate = name; for (var n = 2; Profiles.ContainsKey(candidate); n++) candidate = $"{name} ({n})"; return candidate; }
    private static void AtomicWrite(string path, string text)
    {
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(path))!);
        var temp = path + $".{Environment.ProcessId}.tmp";
        try
        {
            File.WriteAllText(temp, text);
            using (var f = new FileStream(temp, FileMode.Open, FileAccess.Write, FileShare.None)) f.Flush(true);
            File.Move(temp, path, true);
        }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }
    private static void Restore(string path, byte[]? bytes)
    {
        if (bytes is null) { if (File.Exists(path)) File.Delete(path); return; }
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(path))!);
        File.WriteAllBytes(path, bytes);
    }
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
}
