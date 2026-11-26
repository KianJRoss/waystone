using System.Text.Json.Nodes;
using UnifiedController.Core;
using UnifiedController.Linux.App;
var runtime=new FakeRuntime();
var svc=new ControllerCommandService(runtime,ControllerCommandService.AllOperations);
JsonObject Caps()=>svc.Handle(new JsonObject{["operation"]="command_capabilities"});
void Check(bool v,string note){if(!v)throw new Exception(note);}
runtime.Current=runtime.Current with { SelectedSourceId="all_connected" };
Check(Caps()["physical_source_available"]!.GetValue<bool>(),"merged physical target missing");
Check(Caps()["operations"]!.AsArray().Any(x=>x!.GetValue<string>()=="rumble_test"),"rumble unavailable in merge mode");
runtime.Current=runtime.Current with { Devices=runtime.Current.Devices.Where(d=>d.IsVirtual).ToArray() };
Check(!Caps()["physical_source_available"]!.GetValue<bool>(),"virtual device advertised physical target");
runtime.Current=runtime.Current with { SelectedSourceId="missing",Devices=new[]{new InputDeviceInfo("other","fixture",1,1,"/fixture",false,true)} };
Check(!Caps()["physical_source_available"]!.GetValue<bool>(),"single-source selection leaked across devices");
runtime.Current=runtime.Current with { SelectedSourceId="other" };
Check(Caps()["physical_source_available"]!.GetValue<bool>(),"exact physical selection unavailable");
runtime.Current=runtime.Current with { Running=false };
Check(!Caps()["operations"]!.AsArray().Any(x=>x!.GetValue<string>()=="rumble_test"),"stopped engine permits rumble test");
Console.WriteLine("PASS: 6 capability checks; all_connected, exact, absent, virtual-only, stopped; no device commands executed");
namespace UnifiedController.Linux.App { public class ControllerOperationException(string code):Exception(code){} }
