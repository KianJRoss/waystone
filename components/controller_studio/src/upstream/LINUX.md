# Unified Controller on Linux

This is a native .NET 10/Avalonia desktop application backed by the Linux SDL/evdev/uinput runtime. It is not a web dashboard. The control window may be hidden while the runtime remains active; **Safe exit** is the explicit shutdown path that stops macros, releases injected keyboard/mouse state, and neutralizes virtual controller output.

## Build and run

Use a .NET 10 SDK and restore the official Avalonia 11.3.9 packages:

```sh
dotnet restore src/UnifiedController.Linux.App/UnifiedController.Linux.App.csproj
dotnet run --project src/UnifiedController.Linux.App/UnifiedController.Linux.App.csproj
```

Profile data defaults to `${XDG_DATA_HOME:-$HOME/.local/share}/unified-controller`; the application honors `XDG_DATA_HOME` directly. Override it with `--data-dir /absolute/path`. Application-only options are removed before Avalonia parses its arguments. The directory is created when absent; an existing `profiles.json` is never replaced with defaults. `--headless` awaits safe cleanup on SIGINT or SIGTERM.

For a self-contained user installation:

```sh
dotnet publish src/UnifiedController.Linux.App/UnifiedController.Linux.App.csproj -c Release -r linux-x64 --self-contained
packaging/linux/install.sh src/UnifiedController.Linux.App/bin/Release/net10.0/linux-x64/publish
```

The generic installer uses `$HOME` and XDG locations and writes a launcher with a fixed installed application path. It does not enable autostart, close applications, or contain a machine/user name.

## Profiles and editor behavior

The editor reads the version-1 `profiles.json` document as a JSON tree. Profile selection, duplicate, rename, delete, import/export, routes, macros, stick tuning, motion mapping, and controller-mouse edits change only their owned fields. Unknown keys and imported Windows settings/actions are retained. Before every save a separate candidate is loaded through the production `NativeProfileRuntime` parser. Only success backs up the known-good primary and atomically replaces it. Export retains unknown document-level and profile-level fields.

Routes support conditions/layers/chords, native `while-held`/press/release/tap/hold/double-tap activators, normal/toggle/turbo effects with numeric timing, and output slots 1–4. Enabled slots, device assignments, and route assignments edit the exact native fields. Listening uses explicit XInput masks and timestamped per-device keyboard/mouse sources. Macros use `repeat_count`, and macro edits retain unknown fields.

Stick controls include native `enabled` switches and numeric curves. Motion uses the native `motion` object and all six presets; custom left/right objects expose the full native side schema losslessly. Source merge policy and Mod key behavior are available in Profile settings.

Keyboard, mouse, and exclusive physical-controller capture are off unless explicitly enabled in Devices. Exclusive capture can prevent duplicate input when a game also opens a physical controller. Losing access to `/dev/uinput` or evdev devices is reported by the runtime rather than hidden by the UI.

## Controller acceptance guidance

No hardware is claimed as tested merely by this build. For each available controller, verify these points on the target machine:

- **DualShock 4 / PS4:** one physical device appears with a stable ID; buttons, sticks, triggers and motion change live telemetry; reconnect retains identity. Virtual-output rumble feedback is not implemented yet.
- **Individual Joy-Con:** each side appears independently with its stable ID. Pair a left and right in Devices, restart, and confirm the explicit pair persists and produces one combined source. Unpair and confirm the individual sources return.
- **Switch Pro:** one physical source appears, inputs and (where SDL exposes it) motion update, reconnect is clean, and no duplicate joystick/controller view appears.
- **All devices:** opt into exclusive capture only when needed; unplug while holding a control and confirm virtual output returns neutral; confirm the app's virtual uinput devices never appear as physical sources.

Kernel permissions vary by distribution. The runtime needs access to `/dev/uinput` and the selected `/dev/input/event*` devices. Prefer distribution udev/group policy; do not run the GUI as root and do not weaken device or SSH security.

## Honest platform limits

- Fishing audio/perception and automation actions are preserved but not ported.
- DSU streaming and virtual-output force-feedback forwarding are not implemented. Gyro-to-stick processing and per-device calibration are available.
- Windows GameInput and Windows Bluetooth management are preserved but not ported; pair devices using the Linux desktop/BlueZ tools.
- Motion availability and exclusive capture depend on the controller, SDL/kernel support, and device permissions. Some SDL backends return no sensor timestamp; those samples are explicitly marked with timestamp 0.
- The UI reports runtime snapshots and limitations; it does not invent connected devices or simulated live controls.

## Implementation verification checklist

- [x] Native Avalonia application references Core and Linux Platform and constructs the frozen `IControllerRuntime` through `PlatformServices.CreateRuntime(dataRoot)`.
- [x] XDG default, `--data-dir`, awaited headless signal cleanup, single-instance restore, background-on-hide, and safe exit are implemented.
- [x] Lossless JSON mutation, validation, backup, atomic replace, and profile import/export are implemented.
- [x] Route, macro, stick, motion, controller-mouse, pairing/capture, device, and telemetry surfaces are present.
- [x] Unsupported Windows behavior and output-slot constraints are labeled.
- [ ] Hardware acceptance remains intentionally deferred until the named devices are physically available.

Editor integration tests load edited files through the linked native parser, cover non-contiguous ABXY masks and timestamped per-device listening, and verify unknown-field retention. The application and tests build with `$HOME/.local/share/unified-controller-sdk/dotnet`. `Tmds.DBus.Protocol` is pinned to patched version 0.21.3; no advisory warning is suppressed.

## Local installation and observed verification

Installed per user on 2026-11-02. Launcher: **Unified Controller**. Application:
`~/.local/share/unified-controller/app`; profiles/data:
`~/.local/share/unified-controller`. The preserved Windows profile document was
checksum-matched before the first launch. Keyboard/mouse capture and exclusive
controller capture start disabled. A targeted udev rule grants the active user
access to the Keychron V1 (`3434:0311`), without granting access to every input
device or running the application as root.

The Cyclone 2 connected in DS4 mode (`054c:09cc`) supplied actual gyro and
accelerometer samples. Integration probes created exactly one virtual Xbox
output, excluded it from physical input, and removed it on shutdown. The real
runtime started, selected that controller, produced output, reported no runtime
error, and cleaned up after stop/disposal. Individual PS4, Joy-Con and Switch Pro
hardware acceptance and actual Minecraft input remain unverified until those
physical checks are performed.

The GUI starts the runtime on its first opening. Closing hides the window;
launching again restores the existing window. Use **Safe exit** to release
outputs and shut down. This installation uses the existing system .NET 10
runtime, with no automatic startup enabled.

Calibration reuses the existing C#/Python stillness/tare behavior, starts SDL
devices without GameSir raw-count offsets, and keeps state per source in
`linux-motion-calibration.json`. State is saved outside the forwarding loop on
clean stop. Linux polling uses an absolute monotonic sleep rather than a busy
yield loop.
