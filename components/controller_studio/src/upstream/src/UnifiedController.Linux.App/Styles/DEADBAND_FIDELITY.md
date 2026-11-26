# Deadband fidelity verification

The shared and page-specific implementation in this pass was checked against
`research/deadband-reference/screenshots/controller-sticks.png` and the QML
components `Card.qml`, `RangeSlider.qml`, `CurveEditor.qml`, `AxisSide.qml`, and
`MotionPage.qml`.

- Shared cards use the short accent stripe, title, and optional right-side hint.
- Numeric values use compact text entry; accent sliders use a thin track and round thumb.
- Stick halves mirror controls and plots, expose curve presets, show live XY state, and keep the pending bar outside the scroller.
- Vibration uses separate motor cards, live meters, per-motor tests, and an honest no-source state.
- Motion uses a live orientation object with six readouts, DSU status pill, and mirrored mapping cards.
- Verification command: `dotnet build src/UnifiedController.Linux.App/UnifiedController.Linux.App.csproj --no-restore`.
