using UnifiedController.App.Engine;
using Xunit;

public sealed class NativeBehaviorTests
{
    [Fact] public void MergeUsesStrongestWholeStickVectorAndMaxTriggers()
    {
        var a=new NativeControllerReport{Buttons=1,LeftTrigger=20,LeftX=20000,LeftY=10000}; var b=new NativeControllerReport{Buttons=2,LeftTrigger=80,LeftX=-1000,LeftY=-1000};
        var result=NativeReportMerger.Merge([a,b]); Assert.Equal(3,result.Buttons); Assert.Equal(80,result.LeftTrigger); Assert.Equal(20000,result.LeftX); Assert.Equal(10000,result.LeftY);
    }
    [Fact] public void SourceSelectorRetainsActiveSourceAndHandsOff()
    {
        var selector=new NativeSourceSelector(); var active=new NativeControllerReport{Buttons=0x1000};
        Assert.Equal(1,selector.Select(new[]{("a",default(NativeControllerReport)),("b",active)}));
        Assert.Equal(1,selector.Select(new[]{("a",active),("b",active)}));
        Assert.Equal(0,selector.Select(new[]{("a",active)}));
    }
    [Fact] public void StickProcessingMatchesRadialPythonSemantics()
    {
        var settings=new NativeStickSettings(true,.10,1,.20,1); Assert.Equal((short)0,NativeProfileRuntime.ProcessStick(2000,0,settings).X);
        var processed=NativeProfileRuntime.ProcessStick(16384,0,settings); Assert.InRange(processed.X,(short)15000,(short)19000); Assert.Equal((short)0,processed.Y);
    }
    [Fact] public void MacroCancellationClearsHeldControls()
    {
        var scheduler=new NativeMacroScheduler(); scheduler.Configure(new Dictionary<string,NativeMacroDefinition>{{"hold",new("hold",[new("press","Controller:A",0,0,""),new("delay","",0,1000,"")],10,true)}});
        Assert.True(scheduler.Start("hold","source",0)); var report=new NativeControllerReport(); scheduler.Apply(0,ref report); Assert.NotEqual(0,report.Buttons);
        scheduler.CancelAll(); report=default; scheduler.Apply(1,ref report); Assert.Equal(0,report.Buttons);
    }
}
