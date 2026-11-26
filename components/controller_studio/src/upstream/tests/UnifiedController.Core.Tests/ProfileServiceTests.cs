using System.Text.Json.Nodes;
using UnifiedController.Core;
using Xunit;

public sealed class ProfileServiceTests : IDisposable
{
    private readonly string _root=System.IO.Path.Combine(System.IO.Path.GetTempPath(),"uc-core-"+Guid.NewGuid().ToString("N"));
    [Fact] public async Task SelectPreservesUnknownFieldsAndOutputSlots()
    {
        Directory.CreateDirectory(_root); var service=new ProfileService(_root);
        await service.SaveAsync("""{"version":1,"active_profile":"A","future":{"keep":true},"profiles":{"A":{"routes":{}},"B":{"routes":{},"enabled_output_slots":[1,3],"unknown":42}}}""");
        await service.SelectAsync("B"); var root=JsonNode.Parse(await File.ReadAllTextAsync(service.Path))!.AsObject();
        Assert.Equal("B",root["active_profile"]!.GetValue<string>()); Assert.True(root["future"]!["keep"]!.GetValue<bool>()); Assert.Equal(42,root["profiles"]!["B"]!["unknown"]!.GetValue<int>());
    }
    [Fact] public async Task InvalidReplacementLeavesLastGoodPrimary()
    {
        var service=new ProfileService(_root); await service.LoadAsync(); var before=await File.ReadAllTextAsync(service.Path);
        await Assert.ThrowsAsync<InvalidDataException>(()=>service.SaveAsync("{\"version\":1,\"active_profile\":\"missing\",\"profiles\":{}}"));
        Assert.Equal(before,await File.ReadAllTextAsync(service.Path));
    }
    [Fact] public async Task BadPrimaryRecoversFromBackup()
    {
        var service=new ProfileService(_root); await service.LoadAsync(); await service.SaveAsync("""{"version":1,"active_profile":"Good","profiles":{"Good":{"routes":{}}}}""");
        await File.WriteAllTextAsync(service.Path,"bad json"); var loaded=await service.LoadAsync(); Assert.Equal("Default",loaded.ActiveProfileName);
    }
    public void Dispose(){if(Directory.Exists(_root))Directory.Delete(_root,true);}
}
