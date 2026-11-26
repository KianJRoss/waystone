using UnifiedController.Core;
namespace UnifiedController.Linux.Platform.Output;
public static class LinuxOutputWatchdog
{
    public static async ValueTask NeutralizeAsync(IVirtualControllerOutput output)
    { try { if(output.Connected) await output.SubmitNeutralAsync(CancellationToken.None); } catch(IOException) { } }
}
