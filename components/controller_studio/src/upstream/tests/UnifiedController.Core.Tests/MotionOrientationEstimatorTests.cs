using UnifiedController.Core;
using Xunit;

public sealed class MotionOrientationEstimatorTests
{
    [Fact] public void ConvergesToGravityAndDetectsStillness()
    {
        var estimator=new MotionOrientationEstimator(); MotionOrientation result=default!;
        for(ulong i=1;i<=100;i++) result=estimator.Update([0,0,0],[.5f,0,.8660254f],i*10_000);
        Assert.InRange(result.PitchDeg,-31,-29); Assert.InRange(result.RollDeg,-.1,.1); Assert.True(result.Still);
        Assert.InRange(result.TotalAccelG,.99,1.01);
    }
    [Fact] public void IntegratesYawAndRecenterResetsIt()
    {
        var estimator=new MotionOrientationEstimator(); MotionOrientation turned=estimator.Update([0,90,0],[0,0,1],1_000);
        for(ulong i=1;i<=100;i++) turned=estimator.Update([0,90,0],[0,0,1],1_000+i*10_000); Assert.InRange(turned.YawDeg,89,91);
        estimator.Recenter(); var reset=estimator.Update([0,0,0],[0,0,1],2_000_000); Assert.Equal(0,reset.YawDeg);
    }
}
