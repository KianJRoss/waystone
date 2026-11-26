using System.Text.Json;
using System.Text.Json.Nodes;
using UnifiedController.App.Engine;

namespace UnifiedController.Core;

public sealed class ProfileService
{
    private readonly string _path;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private DateTime _lastWrite;
    private NativeProfileRuntime _current = NativeProfileRuntime.Empty;

    public ProfileService(string rootDirectory) => _path = System.IO.Path.Combine(rootDirectory, "profiles.json");
    public string Path => _path;
    public NativeProfileRuntime Current => Volatile.Read(ref _current);

    public async Task<NativeProfileRuntime> LoadAsync(CancellationToken token = default)
    {
        await _gate.WaitAsync(token).ConfigureAwait(false);
        try
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(_path)!);
            if (!File.Exists(_path)) await WriteAtomicAsync(_path, DefaultDocument(), token).ConfigureAwait(false);
            try { return InstallLoaded(); }
            catch when (File.Exists(_path + ".bak"))
            {
                _ = Validate(await File.ReadAllTextAsync(_path + ".bak", token).ConfigureAwait(false));
                File.Copy(_path + ".bak", _path, true);
                return InstallLoaded();
            }
        }
        finally { _gate.Release(); }
    }

    public async Task<bool> ReloadIfChangedAsync(CancellationToken token = default)
    {
        if (!File.Exists(_path) || File.GetLastWriteTimeUtc(_path) == _lastWrite) return false;
        await LoadAsync(token).ConfigureAwait(false);
        return true;
    }

    public async Task SaveAsync(string json, CancellationToken token = default)
    {
        _ = Validate(json); // validate the full replacement before touching last-good data
        await _gate.WaitAsync(token).ConfigureAwait(false);
        try
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(_path)!);
            if (File.Exists(_path))
            {
                _ = Validate(await File.ReadAllTextAsync(_path, token).ConfigureAwait(false));
                File.Copy(_path, _path + ".bak", true);
            }
            await WriteAtomicAsync(_path, json.EndsWith('\n') ? json : json + "\n", token).ConfigureAwait(false);
            InstallLoaded();
        }
        finally { _gate.Release(); }
    }

    public async Task SelectAsync(string profileName, CancellationToken token = default)
    {
        var root = Validate(await File.ReadAllTextAsync(_path, token).ConfigureAwait(false));
        var profiles = root["profiles"]!.AsObject();
        if (!profiles.ContainsKey(profileName)) throw new InvalidDataException($"Unknown profile '{profileName}'.");
        root["active_profile"] = profileName;
        await SaveAsync(root.ToJsonString(new() { WriteIndented = true }), token).ConfigureAwait(false);
    }

    public static JsonObject Validate(string json)
    {
        JsonObject root;
        try { root = JsonNode.Parse(json)?.AsObject() ?? throw new InvalidDataException("Profile document must be an object."); }
        catch (JsonException ex) { throw new InvalidDataException("Profile document is invalid JSON.", ex); }
        if (root["version"]?.GetValue<int>() != 1) throw new InvalidDataException("Unsupported profile document version.");
        if (root["profiles"] is not JsonObject profiles || profiles.Count == 0) throw new InvalidDataException("At least one profile is required.");
        var active = root["active_profile"]?.GetValue<string>();
        if (active is null || !profiles.ContainsKey(active)) throw new InvalidDataException("Active profile must name an existing profile.");
        foreach (var pair in profiles)
        {
            if (pair.Value is not JsonObject profile) throw new InvalidDataException($"Profile '{pair.Key}' must be an object.");
            if (profile["routes"] is JsonNode routes && routes is not JsonObject) throw new InvalidDataException($"Routes in '{pair.Key}' must be an object.");
        }
        return root;
    }

    private NativeProfileRuntime InstallLoaded()
    {
        var loaded = NativeProfileRuntime.Load(_path); // full native-schema validation before swap
        Volatile.Write(ref _current, loaded);
        _lastWrite = File.GetLastWriteTimeUtc(_path);
        return loaded;
    }

    private static async Task WriteAtomicAsync(string path, string payload, CancellationToken token)
    {
        var temp = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(path)!, $".{System.IO.Path.GetFileName(path)}.{Guid.NewGuid():N}.tmp");
        await using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
        await using (var writer = new StreamWriter(stream)) { await writer.WriteAsync(payload.AsMemory(), token).ConfigureAwait(false); await writer.FlushAsync(token).ConfigureAwait(false); stream.Flush(true); }
        File.Move(temp, path, true);
    }

    private static string DefaultDocument() => """
        {
          "version": 1,
          "active_profile": "Default",
          "profiles": { "Default": { "routes": {}, "enabled_output_slots": [1] } }
        }
        """ + "\n";
}
