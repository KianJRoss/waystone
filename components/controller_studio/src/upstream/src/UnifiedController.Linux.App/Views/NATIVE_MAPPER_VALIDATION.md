# Native mapper integration checklist

Reference checked against `research/visual-reference/NATIVE_UI_PREVIEW.png`,
`NATIVE_BUTTON_PRESS_PREVIEW.png`, the WPF `MainWindow`, and the Python
controller, mouse, and six-row keyboard drawing code.

- Mapper is the first/default workspace; profile and advanced editors remain available.
- Input, output, and active-profile cards remain readable at 1440x860.
- Controller and mouse share the upper workspace; the full keyboard is below.
- Horizontal and vertical splitters resize the keyboard, mouse, and binding inspector.
- Visual clicks select sources unless **Pick destination visually** is explicitly active.
- Cancel exits destination mode; route selection returns to source mode.
- Destination category and target selectors use friendly `BindingTarget` labels.
- Assign and remove surface a success color only when `BindingMutationResult.Succeeded`.
- Capture can be cancelled without writing a route.
- Stick and motion affordances navigate to the existing real editors.
- Mapper surfaces refresh from real `RuntimeSnapshot` values on the existing UI timer.
- Profile selection is shared with profile management and refreshes mappings/routes.

## Verification evidence

The Avalonia project compiled successfully on 2026-11-02 with the repository SDK
after substituting compile-only implementations of the frozen worker APIs. The
temporary implementations were removed immediately after validation. This proves
the view XAML, event handlers, styling, and contract call sites compile together;
the final integration build additionally requires the parallel `Controls` and
`BindingWorkspaceViewModel` contributions described in `visual-missions.json`.

Interactive launch was not attempted in this isolated worker because no X display
or virtual display server is installed. Dedicated merged GUI validation must check
1440x860 and 1920x1080, splitter bounds, every controller hit region, and all six
keyboard rows before deployment.
