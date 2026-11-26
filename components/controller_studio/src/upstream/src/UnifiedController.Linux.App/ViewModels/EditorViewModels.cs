// Editors intentionally operate on JsonNode objects through ProfileDocumentService. This preserves
// fields introduced by Windows or future runtimes instead of round-tripping through a lossy DTO.
namespace UnifiedController.Linux.App.ViewModels;
public sealed record ProfilesViewModel;
public sealed record RoutesViewModel;
public sealed record MacroEditorViewModel;
public sealed record MotionViewModel;
public sealed record StickTuningViewModel;
public sealed record DevicesViewModel;
public sealed record DiagnosticsViewModel;
