# Current C# mapper port checklist

- Mouse uses the current `170 x 300` C# canvas and exact shell/button bounds.
- Controller state colors are clipped through the individual PNG masks; no rectangular state overlays remain.
- Controller art retains the corrected `(20, 25)` placement and physical hit regions.
- Keyboard names and 38-unit geometry match `BuildKeyboard`, including spaced numpad canonical codes.
- Stick dialog reads live runtime input/output, previews with `NativeProfileRuntime.ProcessStick`, and saves only on Save through `MutateActiveProfile` + `SetStick`.
- Motion dialog maps yaw/pitch/roll to left/right X/Y using parser vocabulary and preserves unrelated JSON through `SetMotion` merging.
- No lifecycle, single-instance, runtime, main-window, project, or shared-contract files were changed.
