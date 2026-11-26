using UnifiedController.Core;

namespace UnifiedController.Linux.App.Models;

public enum MapperInteractionMode { Source, Destination }
public enum VisualInvocationKind { SelectControl, OpenStickTuning, OpenMotionAxis }
public sealed record VisualControlInvocation(string Code, VisualInvocationKind Kind = VisualInvocationKind.SelectControl);
public sealed record VisualSurfaceState(
    IReadOnlyDictionary<string, string> Routes,
    string? SelectedSource,
    string? SelectedDestination,
    MapperInteractionMode InteractionMode,
    ControllerFrame Input,
    ControllerFrame Output,
    IReadOnlySet<string> ActiveDesktopControls,
    IReadOnlySet<string> ExtendedButtons,
    MotionFrame? Motion);

public sealed record BindingTarget(string Code, string Label, string Group)
{
    public override string ToString() => Label;
}
public sealed record VisualBindingRoute(string Source, string Destination, string SourceLabel, string DestinationLabel, int Slot);
public sealed record BindingMutationResult(bool Succeeded, string Message, string? Source = null, string? Destination = null);
