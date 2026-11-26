using System.Text.Json.Nodes;
using UnifiedController.Linux.App.Services;

namespace UnifiedController.Ui.Tests;

public sealed class ProfileDocumentServiceTests
{
    [Fact]
    public void Export_preserves_document_and_profile_extensions()
    {
        var root = Path.Combine(Path.GetTempPath(), $"uc-doc-{Guid.NewGuid():N}"); Directory.CreateDirectory(root);
        try
        {
            File.WriteAllText(Path.Combine(root, "profiles.json"), """{"version":1,"active_profile":"A","document_extension":{"x":1},"profiles":{"A":{"routes":{},"profile_extension":2},"B":{"routes":{}}}}""");
            var service = new ProfileDocumentService(root); var export = Path.Combine(root, "export.json"); service.Export("A", export);
            var node = JsonNode.Parse(File.ReadAllText(export))!;
            Assert.Equal(1, node["document_extension"]!["x"]!.GetValue<int>());
            Assert.Equal(2, node["profiles"]!["A"]!["profile_extension"]!.GetValue<int>());
            Assert.Null(node["profiles"]!["B"]);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void Invalid_native_value_is_rejected_before_primary_or_backup_changes()
    {
        var root = Path.Combine(Path.GetTempPath(), $"uc-doc-{Guid.NewGuid():N}"); Directory.CreateDirectory(root);
        try
        {
            var service = new ProfileDocumentService(root); var before = File.ReadAllText(service.Path);
            service.ActiveProfile["routes"] = "invalid";
            Assert.ThrowsAny<Exception>(() => service.Save());
            Assert.Equal(before, File.ReadAllText(service.Path));
            Assert.False(File.Exists(service.Path + ".bak"));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void Failed_transaction_restores_primary_backup_and_in_memory_document()
    {
        var root = Path.Combine(Path.GetTempPath(), $"uc-doc-{Guid.NewGuid():N}"); Directory.CreateDirectory(root);
        try
        {
            var service = new ProfileDocumentService(root);
            service.MutateActiveProfile(profile => profile["first_valid_edit"] = true);
            var primary = File.ReadAllBytes(service.Path);
            var backup = File.ReadAllBytes(service.Path + ".bak");

            Assert.ThrowsAny<Exception>(() => service.MutateActiveProfile(profile => profile["routes"] = "invalid"));

            Assert.Equal(primary, File.ReadAllBytes(service.Path));
            Assert.Equal(backup, File.ReadAllBytes(service.Path + ".bak"));
            Assert.IsType<JsonObject>(service.ActiveProfile["routes"]);
            Assert.True(service.ActiveProfile["first_valid_edit"]!.GetValue<bool>());
        }
        finally { Directory.Delete(root, true); }
    }
}
