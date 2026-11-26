namespace UnifiedController.App.Engine;

public static class NativeReportMerger
{
    public static NativeControllerReport Merge(IEnumerable<NativeControllerReport> reports)
    {
        var result = new NativeControllerReport();
        long leftPower = -1;
        long rightPower = -1;

        foreach (var report in reports)
        {
            result.Buttons |= report.Buttons;
            result.LeftTrigger = Math.Max(result.LeftTrigger, report.LeftTrigger);
            result.RightTrigger = Math.Max(result.RightTrigger, report.RightTrigger);

            var currentLeftPower = (long)report.LeftX * report.LeftX + (long)report.LeftY * report.LeftY;
            if (currentLeftPower > leftPower)
            {
                result.LeftX = report.LeftX;
                result.LeftY = report.LeftY;
                leftPower = currentLeftPower;
            }

            var currentRightPower = (long)report.RightX * report.RightX + (long)report.RightY * report.RightY;
            if (currentRightPower > rightPower)
            {
                result.RightX = report.RightX;
                result.RightY = report.RightY;
                rightPower = currentRightPower;
            }
        }

        return result;
    }
}
