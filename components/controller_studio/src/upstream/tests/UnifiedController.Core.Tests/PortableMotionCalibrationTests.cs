using UnifiedController.App.Engine;
using Xunit;

namespace UnifiedController.Core.Tests;

public sealed class PortableMotionCalibrationTests
{
    [Fact]
    public void SdlCalibrationStartsUnbiasedAndDoesNotTouchFilesystem()
    {
        var missing = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"));
        var calibration = new NativeMotionCalibration(missing, fileStorage: false);
        var sample = calibration.Update(new float[3], [0, 0, 1], 0);
        Assert.Equal(new float[3], sample.Gyro);
        Assert.False(Directory.Exists(missing));
    }

    [Fact]
    public void RequestedLevelTareIsAppliedAndCanBeRestoredForThatDevice()
    {
        var calibration = new NativeMotionCalibration("unused", fileStorage: false);
        calibration.RequestLevelCalibration();
        (float[] Gyro, float[] Accel) sample = default;
        for (var i = 0; i < 200; i++) sample = calibration.Update([1, 2, 3], [0, .2f, .98f], i);
        Assert.InRange(Math.Abs(sample.Accel[1]), 0, .001);
        Assert.InRange(sample.Accel[2], .999, 1.001);
        Assert.InRange(Math.Abs(sample.Gyro[0]), 0, .001);
        var other = new NativeMotionCalibration("unused", fileStorage: false);
        Assert.Equal(new float[3], other.Update(new float[3], [0, 0, 1], 0).Gyro);
        other.RestoreState(calibration.CaptureState());
        Assert.InRange(other.Update([1, 2, 3], [0, .2f, .98f], 0).Accel[2], .999, 1.001);
    }
}
