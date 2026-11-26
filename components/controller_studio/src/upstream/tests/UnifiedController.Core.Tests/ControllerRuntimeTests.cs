using System.Diagnostics;
using UnifiedController.Core;
using Xunit;

public sealed class ControllerRuntimeTests : IDisposable
{
    private readonly string _root=Path.Combine(Path.GetTempPath(),"uc-runtime-"+Guid.NewGuid().ToString("N"));

    [Fact] public async Task ForwardsFramesAndNeutralizesOnStop()
    {
        var input=new FakeInput(); var output=new FakeOutput(); await using var runtime=Create(input,output);
        await runtime.StartAsync(); input.Frame=new(0x1000,7,8,100,-200,300,-400);
        await WaitUntil(()=>output.Last.Buttons==0x1000 && runtime.Snapshot.Running && runtime.Snapshot.SelectedSourceId=="pad");
        await runtime.StopAsync(); Assert.Equal(default,output.Last); Assert.True(output.NeutralCount>0);
    }

    [Fact] public async Task DirectoryLockRejectsSecondHost()
    {
        await using var first=Create(new FakeInput(),new FakeOutput()); await using var second=Create(new FakeInput(),new FakeOutput());
        await first.StartAsync(); await Assert.ThrowsAsync<InvalidOperationException>(()=>second.StartAsync().AsTask());
    }

    [Fact] public async Task DeviceAssignmentsProduceAndPreserveMultipleSlots()
    {
        Directory.CreateDirectory(_root);
        await File.WriteAllTextAsync(Path.Combine(_root,"profiles.json"), """{"version":1,"active_profile":"A","profiles":{"A":{"routes":{},"enabled_output_slots":[1,2],"device_output_slots":{"second":2}}}}""");
        var input=new FakeInput { Samples =
        [
            new("pad",new(0x1000,0,0,1,0,0,0),null,new HashSet<string>(),0),
            new("second",new(0x2000,0,0,2,0,0,0),null,new HashSet<string>(),0)
        ]};
        var outputs=new Dictionary<int,FakeOutput>();
        await using var runtime=new ControllerRuntime(_root,input,slot=>outputs[slot]=new FakeOutput(),new FakeDesktopInput(),new FakeDesktopOutput(),new Clock());
        await runtime.StartAsync();
        await WaitUntil(()=>runtime.Snapshot.OutputSlots.Count==2 && outputs[2].Last.Buttons==0x2000);
        input.Samples=[];
        await WaitUntil(()=>outputs[1].Last==default && outputs[2].Last==default);
        Assert.Equal(new[]{1,2},outputs.Keys.Order().ToArray());
    }

    [Fact] public async Task LoopFaultIsPublishedImmediatelyAndStopStillCleansUp()
    {
        var input=new FakeInput { Failure=new IOException("poll exploded") }; var output=new FakeOutput();
        await using var runtime=Create(input,output); await runtime.StartAsync();
        await WaitUntil(()=>runtime.Snapshot.Health=="Faulted");
        Assert.Contains("poll exploded",runtime.Snapshot.Error); await runtime.StopAsync();
        Assert.True(output.NeutralCount>0); Assert.Equal(default,output.Last);
    }

    [Fact] public async Task OutputStartupFailureIsPublishedWithoutStoppingRuntime()
    {
        var output=new FakeOutput { StartFailure=new IOException("uinput unavailable") };
        await using var runtime=new ControllerRuntime(_root,new FakeInput(),_=>output,new FakeDesktopInput(),new FakeDesktopOutput(),new Clock());
        await runtime.StartAsync();
        await WaitUntil(()=>runtime.Snapshot.Running && runtime.OutputStatus.Slots.Single().State=="disconnected");
        Assert.Contains("uinput unavailable",runtime.OutputStatus.Slots.Single().Error);
        Assert.True(output.Disposed); Assert.Equal("Running",runtime.Snapshot.Health);
    }

    [Fact] public async Task StartupCleanupFailurePreservesStartupErrorAndReleasesRuntimeLock()
    {
        Directory.CreateDirectory(_root);
        await File.WriteAllTextAsync(Path.Combine(_root,"profiles.json"),"""{"version":1,"active_profile":"A","profiles":{"A":{"routes":{},"enabled_output_slots":[1,2]}}}""");
        using var source=new CancellationTokenSource();
        var output=new FakeOutput { DisposeFailure=new IOException("close failed"),BeforeStart=_=>source.Cancel() };
        var runtime=new ControllerRuntime(_root,new FakeInput(),slot=>slot==1?output:new FakeOutput{StartFailure=new OperationCanceledException(source.Token)},new FakeDesktopInput(),new FakeDesktopOutput(),new Clock());
        var error=await Assert.ThrowsAsync<AggregateException>(()=>runtime.StartAsync(source.Token).AsTask());
        Assert.IsType<OperationCanceledException>(error.InnerExceptions[0]);
        Assert.Contains(error.InnerExceptions,ex=>ex.Message.Contains("close failed"));
        Assert.Equal("Faulted",runtime.Snapshot.Health);
        // The failed output close is retained for retry, but does not strand the process lock.
        using var reacquired=new FileStream(Path.Combine(_root,".runtime.lock"),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);
        output.DisposeFailure=null;
        await runtime.DisposeAsync();
    }

    [Fact] public async Task RuntimeRebuildsTransientlyFailedOutputWithoutChangingProfileSlots()
    {
        Directory.CreateDirectory(_root);
        await File.WriteAllTextAsync(Path.Combine(_root,"profiles.json"),"""{"version":1,"active_profile":"A","profiles":{"A":{"routes":{},"enabled_output_slots":[1,2]}}}""");
        var attempts=new Dictionary<int,int>(); var outputs=new Dictionary<int,List<FakeOutput>>();
        var runtime=new ControllerRuntime(_root,new FakeInput(),slot=>
        {
            attempts[slot]=attempts.GetValueOrDefault(slot)+1;
            var output=new FakeOutput();
            if(slot==1&&attempts[slot]==1)output.FailSubmit=true;
            if(!outputs.TryGetValue(slot,out var list))outputs[slot]=list=[];
            list.Add(output); return output;
        },new FakeDesktopInput(),new FakeDesktopOutput(),new Clock());
        await using(runtime)
        {
            await runtime.StartAsync();
            await WaitUntil(()=>attempts.GetValueOrDefault(1)>=2 && runtime.OutputStatus.Slots.Count==2 && runtime.OutputStatus.Slots.All(s=>s.State=="connected"),timeoutMs:5000);
            Assert.True(outputs[1][0].Disposed);
            Assert.True(outputs[1][1].Connected);
            Assert.True(outputs[2][0].Connected);
        }
    }

    [Fact] public async Task ActiveControllerWinsOverEarlierIdleControllerInTheSameSlot()
    {
        var input=new FakeInput { Samples =
        [
            new("idle",default,null,new HashSet<string>(),0),
            new("active",new(0x2000,0,0,0,0,0,0),null,new HashSet<string>(),0)
        ]};
        var output=new FakeOutput(); await using var runtime=Create(input,output);
        await runtime.StartAsync();
        await WaitUntil(()=>runtime.Snapshot.SelectedSourceId=="active" && output.Last.Buttons==0x2000);
    }

    [Fact] public async Task RumbleHonorsScalingAndTestStopBypassesIt()
    {
        Directory.CreateDirectory(_root); await File.WriteAllTextAsync(Path.Combine(_root,"profiles.json"),"""{"version":1,"active_profile":"A","profiles":{"A":{"routes":{},"rumble":{"enabled":true,"left_intensity":50,"right_intensity":25}}}}""");
        var input=new FakeInput();var output=new FakeOutput();await using var runtime=Create(input,output);await runtime.StartAsync();await WaitUntil(()=>runtime.Snapshot.SelectedSourceId=="pad");
        output.EmitRumble(200,100);await WaitUntil(()=>input.Rumbles.Count>0);Assert.Equal(("pad",(byte)100,(byte)25),input.Rumbles[^1]);Assert.Equal(new RumbleState(100,25,false),runtime.Snapshot.Rumble);
        runtime.TestRumble(1,.5,30);await WaitUntil(()=>input.Rumbles.Any(r=>r.Low==255&&r.High==128));Assert.Equal(new RumbleState(255,128,true),runtime.Snapshot.Rumble);
        await WaitUntil(()=>input.Rumbles[^1].Low==0&&input.Rumbles[^1].High==0);
    }

    [Fact] public async Task ActionsToggleModesRaiseUiAndRoutesUseSamePath()
    {
        Directory.CreateDirectory(_root);await File.WriteAllTextAsync(Path.Combine(_root,"profiles.json"),"""{"version":1,"active_profile":"A","profiles":{"A":{"routes":{"Controller:A":"Action:Desktop Mode"}}}}""");
        var input=new FakeInput();var output=new FakeOutput();await using var runtime=Create(input,output);string? requested=null;runtime.ActionRequested+=a=>requested=a;await runtime.StartAsync();
        Assert.True(runtime.InvokeAction(RuntimeActions.KeyboardControllerMode));Assert.True(runtime.InvokeAction(RuntimeActions.MouseAimMode));Assert.True(runtime.InvokeAction(RuntimeActions.OpenControlCenter));Assert.False(runtime.InvokeAction(RuntimeActions.FishingHelperToggle));
        await WaitUntil(()=>runtime.Snapshot.ActiveModes.Contains(RuntimeActions.KeyboardControllerMode)&&runtime.Snapshot.ActiveModes.Contains(RuntimeActions.MouseAimMode)&&requested==RuntimeActions.OpenControlCenter);
        input.Frame=new(0x1000,0,0,0,0,0,0);await WaitUntil(()=>runtime.Snapshot.ActiveModes.Contains(RuntimeActions.DesktopMode));
    }

    [Fact] public async Task DisabledRumbleFansNeutralToAllConnectedSources()
    {
        Directory.CreateDirectory(_root);await File.WriteAllTextAsync(Path.Combine(_root,"profiles.json"),"""{"version":1,"active_profile":"A","profiles":{"A":{"routes":{},"source_merge_policy":"all_connected","rumble":{"enabled":false}}}}""");
        var input=new FakeInput{Samples=[new("one",default,null,new HashSet<string>(),0),new("two",default,null,new HashSet<string>(),0)]};var output=new FakeOutput();await using var runtime=Create(input,output);await runtime.StartAsync();await WaitUntil(()=>runtime.Snapshot.SelectedSourceId=="all_connected");
        output.EmitRumble(255,255);await WaitUntil(()=>input.Rumbles.Count>=2);Assert.Contains(("one",(byte)0,(byte)0),input.Rumbles);Assert.Contains(("two",(byte)0,(byte)0),input.Rumbles);Assert.Equal(default,runtime.Snapshot.Rumble);
    }

    [Fact] public async Task MacroActionsUseRuntimeActionPath()
    {
        Directory.CreateDirectory(_root);await File.WriteAllTextAsync(Path.Combine(_root,"profiles.json"),"""{"version":1,"active_profile":"A","profiles":{"A":{"routes":{"Controller:A":"Macro:Mode"},"macros":{"Mode":{"steps":[{"type":"action","action":"Desktop Mode"}]}}}}}""");
        var input=new FakeInput();var output=new FakeOutput();await using var runtime=Create(input,output);await runtime.StartAsync();
        input.Frame=new(0x1000,0,0,0,0,0,0);await WaitUntil(()=>runtime.Snapshot.ActiveModes.Contains(RuntimeActions.DesktopMode));
    }

    [Fact] public async Task AllConnectedShareArmsOneShotModAndTogglesMouseMode()
    {
        Directory.CreateDirectory(_root);
        await File.WriteAllTextAsync(Path.Combine(_root,"profiles.json"),"""
        {
          "version": 1,
          "active_profile": "A",
          "profiles": {
            "A": {
              "routes": {},
              "source_merge_policy": "all_connected",
              "mod_key": { "source": "Controller:Share", "behavior": "one-shot" },
              "controller_mouse": {
                "enabled": true,
                "toggle_source": "Controller:Right Stick",
                "requires_mod": true,
                "stick": "right"
              }
            }
          }
        }
        """);

        var input = new FakeInput {
            Samples = [
                new("pad1", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "Share" }, 0),
                new("pad2", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0)
            ]
        };
        var output = new FakeOutput();
        await using var runtime = Create(input, output);
        await runtime.StartAsync();

        // 1. Share on pad1 arms one-shot mod under all_connected
        await WaitUntil(() => runtime.Snapshot.ModActive && runtime.Snapshot.ExtendedButtons.Contains("Share"));
        Assert.True(runtime.Snapshot.ModActive);
        Assert.False(runtime.Snapshot.ControllerMouseModeEnabled);

        // 2. Release Share: one-shot mod remains armed
        input.Samples = [
            new("pad1", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0),
            new("pad2", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0)
        ];
        await WaitUntil(() => !runtime.Snapshot.ExtendedButtons.Contains("Share"));
        Assert.True(runtime.Snapshot.ModActive);

        // 3. Right Stick click on pad2 triggers toggle because Mod is armed
        input.Samples = [
            new("pad1", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0),
            new("pad2", new(0x0080, 0, 0, 0, 0, 0, 0), null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0)
        ];
        await WaitUntil(() => runtime.Snapshot.ControllerMouseModeEnabled);
        Assert.True(runtime.Snapshot.ControllerMouseModeEnabled);
        Assert.False(runtime.Snapshot.ModActive);
        Assert.Equal(0, output.Last.Buttons);

        // 4. Release Right Stick
        input.Samples = [
            new("pad1", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0),
            new("pad2", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0)
        ];
        await WaitUntil(() => runtime.Snapshot.Input.Buttons == 0);

        // 5. Right Stick click without mod does NOT toggle and passes standard button through
        input.Samples = [
            new("pad1", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0),
            new("pad2", new(0x0080, 0, 0, 0, 0, 0, 0), null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0)
        ];
        await WaitUntil(() => output.Last.Buttons == 0x0080);
        Assert.True(runtime.Snapshot.ControllerMouseModeEnabled);

        // 6. Release Right Stick again
        input.Samples = [
            new("pad1", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0),
            new("pad2", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0)
        ];
        await WaitUntil(() => runtime.Snapshot.Input.Buttons == 0 && output.Last.Buttons == 0);

        // 7. Repress Share to arm again
        input.Samples = [
            new("pad1", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "Share" }, 0),
            new("pad2", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0)
        ];
        await WaitUntil(() => runtime.Snapshot.ModActive);

        // 8. Release Share
        input.Samples = [
            new("pad1", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0),
            new("pad2", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0)
        ];
        await WaitUntil(() => !runtime.Snapshot.ExtendedButtons.Contains("Share"));
        Assert.True(runtime.Snapshot.ModActive);

        // 9. Press Right Stick to toggle mouse mode off (and verify suppression with no standard button leakage)
        input.Samples = [
            new("pad1", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0),
            new("pad2", new(0x0080, 0, 0, 0, 0, 0, 0), null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0)
        ];
        await WaitUntil(() => !runtime.Snapshot.ControllerMouseModeEnabled);
        Assert.False(runtime.Snapshot.ControllerMouseModeEnabled);
        Assert.False(runtime.Snapshot.ModActive);
        Assert.Equal(0, output.Last.Buttons);
    }

    [Fact] public async Task SingleSourceIgnoresOtherDeviceExtendedButtons()
    {
        Directory.CreateDirectory(_root);
        await File.WriteAllTextAsync(Path.Combine(_root,"profiles.json"),"""
        {
          "version": 1,
          "active_profile": "A",
          "profiles": {
            "A": {
              "routes": {},
              "source_merge_policy": "first_active",
              "mod_key": { "source": "Controller:Share", "behavior": "one-shot" }
            }
          }
        }
        """);

        var input = new FakeInput {
            Samples = [
                new("pad1", new(0, 0, 0, 1000, 0, 0, 0), null, new HashSet<string>(StringComparer.OrdinalIgnoreCase), 0),
                new("pad2", default, null, new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "Share" }, 0)
            ]
        };
        var output = new FakeOutput();
        await using var runtime = Create(input, output);
        await runtime.StartAsync();

        await WaitUntil(() => runtime.Snapshot.SelectedSourceId == "pad1");
        await Task.Delay(50);
        Assert.False(runtime.Snapshot.ModActive);
        Assert.DoesNotContain("Share", runtime.Snapshot.ExtendedButtons);
    }

    private ControllerRuntime Create(FakeInput input,FakeOutput output)=>new(_root,input,output,new FakeDesktopInput(),new FakeDesktopOutput(),new Clock());
    private static async Task WaitUntil(Func<bool> condition,int timeoutMs=2000){var until=Stopwatch.GetTimestamp()+Stopwatch.Frequency*timeoutMs/1000; while(!condition()&&Stopwatch.GetTimestamp()<until)await Task.Delay(5); Assert.True(condition());}
    public void Dispose(){if(Directory.Exists(_root))Directory.Delete(_root,true);}

    private sealed class FakeInput:IControllerInputProvider
    {
        public ControllerFrame Frame; public IReadOnlyList<InputSample>? Samples; public Exception? Failure; public event Action<InputDeviceInfo>? DeviceChanged { add{} remove{} }
        public IReadOnlyList<InputDeviceInfo> Devices=>(Samples??[]).Select(s=>new InputDeviceInfo(s.StableId,s.StableId,0,0,"test",false,true)).DefaultIfEmpty(new("pad","Generic",0,0,"test",false,true)).ToArray();
        public ValueTask<IReadOnlyList<InputSample>> PollAsync(CancellationToken token){if(Failure is{} ex)throw ex;return ValueTask.FromResult(Samples??(IReadOnlyList<InputSample>)[new("pad",Frame,null,new HashSet<string>(),Stopwatch.GetTimestamp())]);}
        public readonly List<(string Id,byte Low,byte High)> Rumbles=[];
        public void SetRumble(string id,byte low,byte high)=>Rumbles.Add((id,low,high)); public ValueTask DisposeAsync()=>ValueTask.CompletedTask;
    }
    private sealed class FakeOutput:IVirtualControllerOutput
    {
        public bool Connected{get;private set;} public bool Disposed,FailSubmit; public Exception? StartFailure,DisposeFailure; public ControllerFrame Last; public int NeutralCount; public event Action<byte,byte>? Rumble;
        public Action<CancellationToken>? BeforeStart; public ValueTask StartAsync(CancellationToken token){BeforeStart?.Invoke(token);if(StartFailure is{} ex)throw ex;Connected=true;return ValueTask.CompletedTask;} public ValueTask SubmitAsync(ControllerFrame frame,CancellationToken token){if(FailSubmit)throw new IOException("output write failed");Last=frame;return ValueTask.CompletedTask;}
        public ValueTask SubmitNeutralAsync(CancellationToken token){Last=default;NeutralCount++;return ValueTask.CompletedTask;} public ValueTask DisposeAsync(){if(DisposeFailure is{} ex)throw ex;Connected=false;Disposed=true;return ValueTask.CompletedTask;}
        public void EmitRumble(byte low,byte high)=>Rumble?.Invoke(low,high);
    }
    private sealed class FakeDesktopInput:IDesktopInputSource
    {
        public bool KeyboardEnabled{get;set;} public bool MouseEnabled{get;set;} public InputSnapshot Drain()=>new(new HashSet<int>(),new HashSet<string>(),new HashSet<string>(),0,0,new Dictionary<string,HashSet<int>>(),new Dictionary<string,DesktopMouseState>()); public void Dispose(){}
    }
    private sealed class FakeDesktopOutput:IDesktopOutputSink { public void Apply(DesktopFrame frame){} public void ReleaseAll(){} public void Dispose(){} }
    private sealed class Clock:IMonotonicClock { public long Timestamp=>Stopwatch.GetTimestamp(); public double Seconds=>Timestamp/(double)Stopwatch.Frequency; }
}
