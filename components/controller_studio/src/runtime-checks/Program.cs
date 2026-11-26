using System.Text.Json.Nodes;
using UnifiedController.Core;
using UnifiedController.Linux.App;
using UnifiedController.Linux.App.Services;
using UnifiedController.Linux.Platform.Output;

internal static class Program
{
    private static int _checks;
    private static void Check(bool condition,string name) { if(!condition) throw new Exception("FAIL: "+name); _checks++; }
    private static async Task Code(Func<Task> action,string code)
    {
        try { await action(); throw new Exception("Expected "+code); }
        catch(OutputSwitchException ex) { Check(ex.Code==code,code); }
        catch(ControllerOperationException ex) { Check(ex.Code==code,code); }
    }
    public static async Task Main()
    {
        await ManagerChecks(); await FailureChecks(); await XboxChecks(); await SocketServiceChecks(); await RuntimeMotionChecks();
        Console.WriteLine($"PASS: {_checks} fake-only runtime/output checks");
    }
    private static async Task ManagerChecks()
    {
        Check(VirtualOutputTypes.Normalize("Xbox One")=="xboxone","One alias");
        Check(VirtualOutputTypes.Normalize("DS4")=="dualshock4","Sony alias");
        try { VirtualOutputTypes.Normalize("unknown"); throw new Exception("Unknown accepted"); } catch(ArgumentException) { Check(true,"Unknown rejected"); }
        var fleet=new FakeFleet(); double now=10; var rumbles=0;
        await using var manager=new VirtualOutputManager(fleet.Create,rumble:(_,_)=>rumbles++,now:()=>now);
        await manager.RebuildAsync(new[]{1,2},default);
        Check(fleet.Live.Count==2,"two slots");
        Check(!await manager.SwitchAsync("xbox360",false,default),"same type idempotent while unknown");
        await Code(()=>manager.SwitchAsync("xboxone",false,default).AsTask(),"ERR_GAME_STATE_UNKNOWN");
        manager.ObserveGames([]);
        var old=fleet.Created.ToArray(); var checkpoint=fleet.Log.Count;
        Check(await manager.SwitchAsync("xboxone",false,default),"idle switch");
        var changes=fleet.Log.Skip(checkpoint).ToArray();
        Check(Array.FindLastIndex(changes,x=>x.StartsWith("dispose:"))<Array.FindIndex(changes,x=>x.StartsWith("start:")),"all old gone before any new");
        Check(fleet.MaxLivePerSlot==1,"no duplicate pad per slot");
        Check(manager.Status.CurrentType=="xboxone" && !manager.Status.PartialFailure,"live type status");
        old[0].EmitRumble(); Check(rumbles==0,"old rumble detached");
        fleet.Created[^1].EmitRumble(); Check(rumbles==1,"new rumble attached");
        var frame=new ControllerFrame(0x1000,255,1,-2,3,-4,5);
        var motion=new MotionFrame([1,2,3],[0,0,1],25);
        var extended=new HashSet<string>{"Share"};
        var frames=await manager.SubmitAsync(new Dictionary<int,ControllerFrame>{{1,frame}},
            new Dictionary<int,InputSample>{{1,new("pad",frame,motion,extended,0)}},default);
        var first=fleet.Created.Last(x=>x.Slot==1);
        Check(first.Last==frame && first.Motion==motion && first.Extended==extended,"motion/extended/frame forwarded");
        Check(frames[2]==default && fleet.Created.Last(x=>x.Slot==2).Motion is null,"missing slot neutral no motion leak");
        manager.ObserveGames(["game:one"]);
        await Code(()=>manager.SwitchAsync("dualshock4",false,default).AsTask(),"ERR_GAME_ACTIVE");
        Check(!await manager.SwitchAsync("xboxone",false,default),"same type active idempotent");
        Check(await manager.SwitchAsync("dualshock4",true,default),"force while active");
        manager.ObserveGames([],false); Check(manager.Status.GameState=="unknown","unknown invalidates immediately");
        await Code(()=>manager.SwitchAsync("dualsense",false,default).AsTask(),"ERR_GAME_STATE_UNKNOWN");
        manager.ObserveGames([]); now+=15;
        await Code(()=>manager.SwitchAsync("dualsense",false,default).AsTask(),"ERR_GAME_STATE_UNKNOWN");
        Check(manager.Status.GameObservationAgeSeconds==15,"lease uses seconds");
        manager.ObserveGames([]); await manager.RebuildAsync(new[]{2},default);
        Check(fleet.Live.Keys.SequenceEqual(new[]{2}),"disabled slot disposed");
        await manager.NeutralAsync(); Check(fleet.Created[^1].Last==default,"stop neutral");
        await manager.RebuildAsync([],default);
        Check(manager.Status.CurrentType=="dualshock4"&&manager.Status.Slots.Count==0,"no slots retains selected target");
    }
    private static async Task FailureChecks()
    {
        var fleet=new FakeFleet(); await using(var manager=new VirtualOutputManager(fleet.Create))
        {
            fleet.FailStartSlot=1;
            await manager.RebuildAsync(new[]{1,2},default);
            Check(manager.Status.Slots.Single(s=>s.Slot==1).State=="disconnected"&&manager.Status.Slots.Single(s=>s.Slot==2).State=="connected"&&fleet.Live.ContainsKey(2),"one failed slot does not prevent rebuilding healthy slots");
            fleet.FailStartSlot=null;
            await manager.RebuildAsync(new[]{1,2},default);
            Check(manager.Status.Slots.All(s=>s.State=="connected"),"explicit rebuild recovers a startup-failed slot");
        }
        fleet=new FakeFleet(); await using(var manager=new VirtualOutputManager(fleet.Create))
        {
            await manager.RebuildAsync(new[]{1,2},default); manager.ObserveGames([]);
            using var cts=new CancellationTokenSource();
            fleet.Live[1].SubmitFailure=new OperationCanceledException(cts.Token);
            fleet.Live[1].BeforeSubmit=cts.Cancel;
            try
            {
                await manager.SubmitAsync(new Dictionary<int,ControllerFrame>(),new Dictionary<int,InputSample>(),cts.Token);
                throw new Exception("Cancellation was swallowed");
            }
            catch(OperationCanceledException)
            {
                Check(manager.Status.Slots.All(s=>s.State=="connected"),"runtime cancellation is not misreported as per-slot disconnect");
            }
        }
        fleet=new FakeFleet(); await using(var manager=new VirtualOutputManager(fleet.Create))
        {
            await manager.RebuildAsync(new[]{1,2},default); manager.ObserveGames([]);
            var failed=fleet.Live[1]; failed.FailSubmit=true;
            var frames=await manager.SubmitAsync(new Dictionary<int,ControllerFrame>{{1,new(1,1,1,1,1,1,1)},{2,new(2,2,2,2,2,2,2)}},
                new Dictionary<int,InputSample>(),default);
            Check(!frames.ContainsKey(1)&&frames.ContainsKey(2)&&fleet.Live[2].Last.Buttons==2,"one failed slot does not starve remaining outputs");
            Check(manager.Status.Slots.Single(s=>s.Slot==1).State=="disconnected"&&manager.Status.Slots.Single(s=>s.Slot==2).State=="connected","partial output status is explicit");
            var submits=failed.SubmitCount;
            frames=await manager.SubmitAsync(new Dictionary<int,ControllerFrame>{{1,new(3,0,0,0,0,0,0)},{2,new(4,0,0,0,0,0,0)}},
                new Dictionary<int,InputSample>(),default);
            Check(!frames.ContainsKey(1)&&frames.ContainsKey(2)&&failed.SubmitCount==submits,"failed slot is skipped until rebuild");
            await manager.RebuildAsync(new[]{1,2},default);
            Check(manager.Status.Slots.All(s=>s.State=="connected"),"explicit rebuild recovers failed slot");
        }
        fleet=new FakeFleet(); await using(var manager=new VirtualOutputManager(fleet.Create))
        {
            await manager.RebuildAsync(new[]{1},default); manager.ObserveGames([]);
            fleet.FailStartType="xboxone";
            await Code(()=>manager.SwitchAsync("xboxone",false,default).AsTask(),"ERR_OUTPUT_SWITCH_FAILED");
            Check(manager.Status.CurrentType=="xbox360"&&!manager.Status.PartialFailure,"rollback restores old type");
            Check(manager.Status.LastSwitchError?.Contains("New output failed")==true,"rollback preserves failure");
            Check(fleet.MaxLivePerSlot==1,"rollback no overlap");
            fleet.FailStartType=null; fleet.Created[^1].FailDispose=true;
            var before=fleet.Created.Count;
            await Code(()=>manager.SwitchAsync("xboxone",true,default).AsTask(),"ERR_OUTPUT_SWITCH_FAILED");
            Check(fleet.Created.Count==before && manager.Status.Slots[0].State=="uncertain","disposal failure blocks replacement");
            Check(manager.Status.CurrentType is null && manager.Status.PartialFailure,"uncertain status honest");
            fleet.Created[^1].FailDispose=false;
        }
        fleet=new FakeFleet(); await using(var manager=new VirtualOutputManager(fleet.Create))
        {
            await manager.RebuildAsync(new[]{1},default); manager.ObserveGames([]);
            fleet.FailAllStarts=true;
            await Code(()=>manager.SwitchAsync("xboxone",false,default).AsTask(),"ERR_OUTPUT_SWITCH_FAILED");
            Check(manager.Status.CurrentType is null && manager.Status.PartialFailure && fleet.Live.Count==0,"rollback failure disconnected");
            Check(manager.Status.LastSwitchError?.Contains("Rollback failed")==true,"rollback error retained");
        }
        fleet=new FakeFleet(); await using(var manager=new VirtualOutputManager(fleet.Create))
        {
            await manager.RebuildAsync(new[]{1},default); manager.ObserveGames([]);
            fleet.FailNeutralType="dualsense";
            await Code(()=>manager.SwitchAsync("dualsense",false,default).AsTask(),"ERR_OUTPUT_SWITCH_FAILED");
            Check(manager.Status.CurrentType=="xbox360"&&fleet.MaxLivePerSlot==1,"new neutral failure rolls back");
        }
        fleet=new FakeFleet(); await using(var manager=new VirtualOutputManager(fleet.Create))
        {
            await manager.RebuildAsync(new[]{1},default); manager.ObserveGames([]);
            fleet.FailStartType="dualsense"; fleet.FailDisposeType="dualsense";
            await Code(()=>manager.SwitchAsync("dualsense",false,default).AsTask(),"ERR_OUTPUT_SWITCH_FAILED");
            Check(fleet.Created.Count==2&&manager.Status.PartialFailure,"uncertain candidate prevents rollback create");
            foreach(var pad in fleet.Created) pad.FailDispose=false;
        }
    }
    private static async Task XboxChecks()
    {
        var writer=new FakeXboxWriter(); await using(var output=new UInputXboxOneOutput(2,writer:writer))
        {
            await output.StartAsync(default);
            Check(writer.Device?.Vendor==0x045e && writer.Device.Product==0x02ea && writer.Device.Bus==3,"One USB identity");
            Check(writer.Device!.Name.Contains("One S") && writer.Device.Name.EndsWith("Slot 2)"),"One name slot");
            Check(writer.Device.Axes.Single(a=>a.Code==2).Maximum==1023 && !writer.Device.Buttons.Contains(167),"One layout");
            await output.SubmitAsync(new(0x1000,255,128,short.MinValue,short.MinValue,short.MaxValue,short.MaxValue),default);
            Check(writer.Events.Single(e=>e.Type==3&&e.Code==2).Value==1023,"10bit trigger max");
            Check(writer.Events.Single(e=>e.Type==3&&e.Code==5).Value==514,"10bit trigger midpoint");
            Check(writer.Events.Single(e=>e.Type==3&&e.Code==1).Value==32767,"Y min inversion");
            Check(writer.Events.Single(e=>e.Type==3&&e.Code==4).Value==-32767,"Y max inversion");
            Check(writer.Events.Single(e=>e.Type==1&&e.Code==304).Value==1 && writer.Events[^1]==new XboxUInputEvent(0,0,0),"face and syn events");
        }
        Check(writer.Disposed,"writer disposal");
        writer=new FakeXboxWriter(); await using(var output=new UInputXboxOneOutput(1,true,writer))
        {
            await output.StartAsync(default);
            Check(writer.Device?.Product==0x0b12 && writer.Device.Name.Contains("Series") && writer.Device.Buttons.Contains(167),"Series identity/share capability");
            await output.SubmitSampleAsync(default,null,new HashSet<string>{"Share"},default);
            Check(writer.Events.Single(e=>e.Type==1&&e.Code==167).Value==1,"Series Share");
            await output.SubmitNeutralAsync(default);
            Check(writer.Events.Where(e=>e.Type==1).All(e=>e.Value==0) && writer.Events.Where(e=>e.Type==3).All(e=>e.Value==0),"neutral complete including Share");
            writer.FailWrite=true;
        }
    }
    private static async Task SocketServiceChecks()
    {
        var root=Path.Combine(Path.GetTempPath(),"w16-fakes-"+Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root);
        try
        {
            var path=Path.Combine(root,"profiles.json");
            File.WriteAllText(path,"""{"version":1,"active_profile":"Default","profiles":{"Default":{"routes":{}}},"extension":{"keep":[1]},"game_virtual_gamepad_drivers":{"game:one":"dualsense"}}""");
            var docs=new ProfileDocumentService(root);
            var fleet=new FakeFleet(); await using var manager=new VirtualOutputManager(fleet.Create);
            await manager.RebuildAsync(new[]{1},default);
            var controller=new FakeController(manager) { OwnedDevices=[
                new("physical-a","Pad A",0x3537,0x0575,"/dev/input/event-test",false,true),
                new("physical-b","Disconnected",0x1234,0x5678,"/dev/input/event-gone",false,false),
                new("virtual-a","Virtual",0x045e,0x028e,"",true,true)
            ]}; var service=new OutputControlService(docs,controller);
            var initial=service.Read();
            Check(initial["default_type"]!.GetValue<string>()=="xbox360"&&initial["game_state"]!.GetValue<string>()=="unknown","read missing defaults");
            var owned=initial["owned_devices"]!.AsArray();
            Check(owned.Count==1&&owned[0]!["stable_id"]!.GetValue<string>()=="physical-a"&&owned[0]!["vendor_id"]!.GetValue<ushort>()==0x3537&&owned[0]!["product_id"]!.GetValue<ushort>()==0x0575,"status exposes connected engine-owned physical VID/PID only");
            await service.HandleAsync(new(){["operation"]="output_game_state",["active_game_ids"]=new JsonArray()});
            var result=await service.HandleAsync(new(){["operation"]="output_set",["type"]="PS5",["persist_default"]=false});
            Check(result["current_type"]!.GetValue<string>()=="dualsense"&&result["default_type"]!.GetValue<string>()=="xbox360","temporary override not persisted");
            result=await service.HandleAsync(new(){["operation"]="output_set",["type"]="xboxseries",["expected_revision"]=result["revision"]!.GetValue<string>()});
            Check(result["default_type"]!.GetValue<string>()=="xboxseries","default persisted after switch");
            var saved=JsonNode.Parse(File.ReadAllText(path))!;
            Check(saved["extension"]!["keep"]![0]!.GetValue<int>()==1&&saved["game_virtual_gamepad_drivers"]!["game:one"]!.GetValue<string>()=="dualsense","unknown fields preserved");
            await Code(()=>service.HandleAsync(new(){["operation"]="output_set",["type"]="xboxone",["force"]="true"}),"ERR_INVALID_REQUEST");
            await Code(()=>service.HandleAsync(new(){["operation"]="output_set",["type"]="xboxone",["persist_default"]=0}),"ERR_INVALID_REQUEST");
            await Code(()=>service.HandleAsync(new(){["operation"]="output_set",["type"]="nonsense"}),"ERR_INVALID_OUTPUT_TYPE");
            var before=fleet.Created.Count;
            await Code(()=>service.HandleAsync(new(){["operation"]="output_set",["type"]="xboxone",["expected_revision"]=new string('0',64)}),"ERR_REVISION_CONFLICT");
            Check(fleet.Created.Count==before,"CAS before side effects");
            await service.HandleAsync(new(){["operation"]="output_game_state",["active_game_ids"]=new JsonArray(),["observation_known"]=false});
            await Code(()=>service.HandleAsync(new(){["operation"]="output_set",["type"]="xboxone"}),"ERR_GAME_STATE_UNKNOWN");
            await Code(()=>service.HandleAsync(new(){["operation"]="output_game_state",["active_game_ids"]=new JsonArray("game:one"),["observation_known"]=false}),"ERR_INVALID_REQUEST");
            await service.HandleAsync(new(){["operation"]="output_game_state",["active_game_ids"]=new JsonArray()});
            controller.AfterSwitch=()=>File.AppendAllText(path," ");
            await Code(()=>service.HandleAsync(new(){["operation"]="output_set",["type"]="xboxone"}),"ERR_OUTPUT_PERSISTENCE_FAILED");
            var readback=service.Read();
            Check(readback["current_type"]!.GetValue<string>()=="xboxone"&&readback["default_type"]!.GetValue<string>()=="xboxseries"&&readback["partial_failure"]!.GetValue<bool>(),"persistence failure honest readback");
            Check(readback["last_switch_error"]!.GetValue<string>().Contains("revision changed"),"persistence error explicit");
        }
        finally { Directory.Delete(root,true); }
    }
    private static async Task RuntimeMotionChecks()
    {
        var root=Path.Combine(Path.GetTempPath(),"w16-motion-"+Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root);
        try
        {
            File.WriteAllText(Path.Combine(root,"profiles.json"),"""{"version":1,"active_profile":"Default","profiles":{"Default":{"routes":{},"enabled_output_slots":[1,2],"device_output_slots":{"second":2}}}}""");
            var firstMotion=new MotionFrame([1,2,3],[0,0,1],100);
            var secondMotion=new MotionFrame([4,5,6],[0,1,0],200);
            var input=new FixtureInput([
                new("first",new(0x1000,0,0,0,0,0,0),firstMotion,new HashSet<string>{"Share"},0),
                new("second",new(0x2000,0,0,0,0,0,0),secondMotion,new HashSet<string>{"Touchpad"},0)]);
            var fleet=new FakeFleet();
            await using(var runtime=new ControllerRuntime(root,input,fleet.Create,"dualshock4",new FixtureDesktopInput(),new FixtureDesktopOutput(),new FixtureClock()))
            {
                await runtime.StartAsync();
                var deadline=DateTime.UtcNow.AddSeconds(3);
                while((fleet.Created.Count<2||fleet.Created.Any(p=>p.Motion is null))&&DateTime.UtcNow<deadline) await Task.Delay(5);
                Check(fleet.Created.Single(p=>p.Slot==1).Motion==firstMotion,"runtime slot1 motion");
                Check(fleet.Created.Single(p=>p.Slot==2).Motion==secondMotion,"runtime slot2 motion no cross-slot leakage");
                Check(fleet.Created.Single(p=>p.Slot==1).Extended!.Contains("Share")&&fleet.Created.Single(p=>p.Slot==2).Extended!.Contains("Touchpad"),"runtime per-slot extended");
                runtime.ObserveGames([]);
                await runtime.SetOutputTypeAsync("dualsense");
                Check(runtime.OutputStatus.CurrentType=="dualsense"&&fleet.MaxLivePerSlot==1,"live runtime switch concurrent forwarding");
                await runtime.StopAsync();
                Check(fleet.Live.Values.All(p=>p.Last==default),"runtime stop neutral");
            }
            Check(fleet.Live.Count==0,"runtime disposal removes outputs");
        }
        finally { Directory.Delete(root,true); }
    }
    private sealed class FixtureInput(IReadOnlyList<InputSample> samples) : IControllerInputProvider
    {
        public event Action<InputDeviceInfo>? DeviceChanged { add{} remove{} }
        public IReadOnlyList<InputDeviceInfo> Devices=>[];
        public ValueTask<IReadOnlyList<InputSample>> PollAsync(CancellationToken token)=>ValueTask.FromResult(samples);
        public void SetRumble(string id,byte low,byte high){}
        public ValueTask DisposeAsync()=>ValueTask.CompletedTask;
    }
    private sealed class FixtureDesktopInput : IDesktopInputSource
    {
        public bool KeyboardEnabled{get;set;} public bool MouseEnabled{get;set;}
        public InputSnapshot Drain()=>new(new HashSet<int>(),new HashSet<string>(),new HashSet<string>(),0,0,new Dictionary<string,HashSet<int>>(),new Dictionary<string,DesktopMouseState>());
        public void Dispose(){}
    }
    private sealed class FixtureDesktopOutput : IDesktopOutputSink { public void Apply(DesktopFrame frame){}public void ReleaseAll(){}public void Dispose(){} }
    private sealed class FixtureClock : IMonotonicClock
    { public long Timestamp=>System.Diagnostics.Stopwatch.GetTimestamp();public double Seconds=>Timestamp/(double)System.Diagnostics.Stopwatch.Frequency; }
    private sealed class FakeController(VirtualOutputManager manager) : IOutputTypeController
    {
        public Action? AfterSwitch;
        public VirtualOutputStatus OutputStatus=>manager.Status;
        public IReadOnlyList<InputDeviceInfo> OwnedDevices { get; init; }=[];
        public async ValueTask<bool> SetOutputTypeAsync(string type,bool force=false,CancellationToken token=default)
        { var changed=await manager.SwitchAsync(type,force,token); AfterSwitch?.Invoke(); return changed; }
        public void ObserveGames(IReadOnlyList<string> ids,bool known=true)=>manager.ObserveGames(ids,known);
    }
    private sealed class FakeFleet
    {
        public Dictionary<int,FakePad> Live=[]; public List<FakePad> Created=[]; public List<string> Log=[];
        public string? FailStartType,FailNeutralType,FailDisposeType; public int? FailStartSlot; public bool FailAllStarts; public int MaxLivePerSlot;
        public IVirtualControllerOutput Create(string type,int slot)
        {
            var pad=new FakePad(this,type,slot) { FailStart=FailAllStarts||FailStartType==type||FailStartSlot==slot,FailNeutral=FailNeutralType==type,FailDispose=FailDisposeType==type };
            Created.Add(pad); Log.Add($"factory:{type}:{slot}"); return pad;
        }
        public sealed class FakePad(FakeFleet fleet,string type,int slot) : IMotionVirtualControllerOutput
        {
            public int Slot=>slot; public bool Connected{get;private set;} public bool FailStart,FailNeutral,FailDispose,FailSubmit; public Exception? SubmitFailure; public Action? BeforeSubmit; public int SubmitCount;
            public ControllerFrame Last; public MotionFrame? Motion; public IReadOnlySet<string>? Extended;
            public event Action<byte,byte>? Rumble; public void EmitRumble()=>Rumble?.Invoke(1,2);
            public ValueTask StartAsync(CancellationToken token)
            {
                fleet.Log.Add($"start:{type}:{slot}"); if(FailStart) throw new IOException("start failure");
                if(fleet.Live.ContainsKey(slot)) { fleet.MaxLivePerSlot=2; throw new Exception("Duplicate pad"); }
                fleet.Live[slot]=this;fleet.MaxLivePerSlot=Math.Max(1,fleet.MaxLivePerSlot);Connected=true;return ValueTask.CompletedTask;
            }
            public ValueTask SubmitAsync(ControllerFrame f,CancellationToken token) { SubmitCount++; BeforeSubmit?.Invoke(); if(SubmitFailure is{} failure)throw failure; if(FailSubmit)throw new IOException("write failure");Last=f;return ValueTask.CompletedTask; }
            public ValueTask SubmitSampleAsync(ControllerFrame f,MotionFrame? motion,IReadOnlySet<string>? extended,CancellationToken token)
            { SubmitCount++; BeforeSubmit?.Invoke(); if(SubmitFailure is{} failure)throw failure; if(FailSubmit)throw new IOException("write failure");Last=f;Motion=motion;Extended=extended;return ValueTask.CompletedTask; }
            public ValueTask SubmitNeutralAsync(CancellationToken token) { fleet.Log.Add($"neutral:{type}:{slot}"); if(FailNeutral) throw new IOException("neutral failure");Last=default;return ValueTask.CompletedTask; }
            public ValueTask DisposeAsync() { fleet.Log.Add($"dispose:{type}:{slot}");if(FailDispose)throw new IOException("dispose failure");if(fleet.Live.GetValueOrDefault(slot)==this)fleet.Live.Remove(slot);Connected=false;return ValueTask.CompletedTask; }
        }
    }
    private sealed class FakeXboxWriter : IXboxUInputWriter
    {
        public bool Connected{get;private set;} public bool Disposed,FailWrite;public XboxUInputDevice? Device;public IReadOnlyList<XboxUInputEvent> Events=[];
        public event Action<byte,byte>? Rumble { add{} remove{} }
        public ValueTask StartAsync(XboxUInputDevice device,CancellationToken token){Device=device;Connected=true;return ValueTask.CompletedTask;}
        public ValueTask WriteAsync(IReadOnlyList<XboxUInputEvent> events,CancellationToken token){if(FailWrite){FailWrite=false;throw new IOException("write failure");}Events=events;return ValueTask.CompletedTask;}
        public ValueTask DisposeAsync(){Disposed=true;Connected=false;return ValueTask.CompletedTask;}
    }
}
