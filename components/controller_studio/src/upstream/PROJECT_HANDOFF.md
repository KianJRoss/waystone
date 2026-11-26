# Unified Controller project handoff

## Purpose of this handoff

Continue researching and planning Unified Controller as a system-wide mixed-input layer for emulators and PC games. Focus on finding useful features and interaction patterns from existing projects, especially optional game-assistance features similar to Fishing Helper and the advanced macro mode called Trick Assist.

Do not begin a large implementation unless the user explicitly changes the current research/planning direction. Small, obvious fixes and documentation improvements are acceptable.

## Summary of Completed Work (Since Previous Handoff)

A comprehensive session handoff detailing all technical architectures, packet formulas, state machine flowcharts, and newly built tools has been compiled in:
📂 **[`research/SESSION_HANDOFF_2026-09-19.md`](file:///C:/Users/batma/Documents/Emulators/Citron-2026-04-27-clean/Unified%20Controller/research/SESSION_HANDOFF_2026-09-19.md)**

### Key Milestones Completed:
1. **Windows Controller Interception & Device Control Toggle Tool**:
   - Created [`toggle_windows_controller_intercept.bat`](file:///C:/Users/batma/Documents/Emulators/Citron-2026-04-27-clean/Unified%20Controller/toggle_windows_controller_intercept.bat) and [`tools/Toggle-WindowsControllerInterception.ps1`](file:///C:/Users/batma/Documents/Emulators/Citron-2026-04-27-clean/Unified%20Controller/tools/Toggle-WindowsControllerInterception.ps1).
   - Unlinks controller Guide (Home) and Share (Screenshot) buttons from Windows capture, stops `GameInputSvc` from navigating the Windows UI, and preserves the Xbox Game Bar app as a whole (`Win+G` remains functional). Includes an interactive Admin elevation prompt (Y/N) and automated settings backup in [`backups/windows-controller-intercept-backup.json`](file:///C:/Users/batma/Documents/Emulators/Citron-2026-04-27-clean/Unified%20Controller/backups/windows-controller-intercept-backup.json).
2. **Casual, Retro & Speedrunning Assistance Guide**:
   - Created [`research/CASUAL_EMULATOR_ASSISTANCE_GUIDE.md`](file:///C:/Users/batma/Documents/Emulators/Citron-2026-04-27-clean/Unified%20Controller/research/CASUAL_EMULATOR_ASSISTANCE_GUIDE.md) (28 KB): comprehensive blueprints for typewriter text auto-advance, choice-pause, stamina-gated smart turbo, auto-run latches, bulk crafting, WASAPI fishing detection, analog fast-forward/rewind throttles, chorded savestate carousels with undo, DS/3DS stick pointers and synthetic pink noise mic blow, strict 4-way D-pad gating ($12^\circ$ hysteresis), retro stick anti-deadzones, savestate practice loops with instant death rewind, LiveSplit auditory telemetry, conical stick angle snapping, YouTube PiP scrubbers, ban-safe Direct2D overlays, and Core Audio WASAPI Discord crossfaders with haptic center detents.
3. **Master Broad Research Repository (13 Frontiers)**:
   - Expanded [`research/BROAD_FEATURE_INGESTION.md`](file:///C:/Users/batma/Documents/Emulators/Citron-2026-04-27-clean/Unified%20Controller/research/BROAD_FEATURE_INGESTION.md) to 766 lines across 13 core sections, covering hardware protocols, accessibility, multi-instance streaming, speedrun automation, edge AI (YOLOv10n DirectML), MIDI/polyphonic grid gear, BCI/sEMG pre-movement negative latency (30-50ms), and sim telemetry/FFB.
4. **Controller 'Overclocking' Assessment**:
   - Formally evaluated `hidusbf` kernel filter drivers. Official Xbox controllers are locked at 125Hz/250Hz in firmware (returning duplicate stale packets), modern pads already feature 1,000 Hz native firmware, and ViGEmBus virtual outputs already operate sub-millisecond ($<100\,\mu	ext{s}$). Formally deferred in [`FEATURE_CATALOG.md`](file:///C:/Users/batma/Documents/Emulators/Citron-2026-04-27-clean/Unified%20Controller/research/FEATURE_CATALOG.md).
5. **Catalog, Roadmap & Reference Synchronization**:
   - [`FEATURE_CATALOG.md`](file:///C:/Users/batma/Documents/Emulators/Citron-2026-04-27-clean/Unified%20Controller/research/FEATURE_CATALOG.md) updated with 18 new feature rows and the Casual/Emulation Subsystems section.
   - [`REFERENCES.json`](file:///C:/Users/batma/Documents/Emulators/Citron-2026-04-27-clean/Unified%20Controller/research/REFERENCES.json) expanded from 31 to 40 fully indexed reference projects.
   - [`ROADMAP.md`](file:///C:/Users/batma/Documents/Emulators/Citron-2026-04-27-clean/Unified%20Controller/research/ROADMAP.md) updated with Phase 8 (Gamer Companion, Retro Superpowers & Cozy Automation Suite).
6. **Code Integrity**:
   - All runtime python files (`unified_controller.py`, `unified_controller_gui.py`, `profiles.json`) remain 100% untouched.

## User's product goal

The user wants one always-running program that makes controllers, keyboard, mouse, remote/cloud inputs, and optional automation look like stable virtual controllers. Games and emulators should remain bound to Unified Controller instead of requiring controller-profile or input-device changes whenever the physical input source changes.

The system should eventually support:

- keyboard and mouse to controller
- controller to keyboard and mouse desktop mode
- several physical inputs merged into one virtual controller
- one or more physical devices split across multiple virtual controllers
- up to eight logical output slots, subject to backend/application limits
- ordinary remapping, layers, chords, toggles, turbo, and macros
- optional Fishing Helper integration
- optional audio, visual, OCR, timer, and emulator events
- speedrun assistance through advanced real-time macros
- per-device and per-game profiles managed by this application rather than by each emulator

The project is for personal emulation simplification, accessibility, practice, speedrunning, and tool-assisted live play. The current goal is not multiplayer automation or a competitive cheat system.

## Critical terminology

### Macro engine

There should be one shared macro scheduler and action format. A basic macro is a sequence of typed button, axis, keyboard, mouse, delay, layer, and internal-command steps.

### Trick Assist

Trick Assist is **a mode of the macro engine**, not a separate engine or independent bot. It is a beefed-up macro workflow for difficult live speedrun tricks. It adds:

- high-resolution input recording
- editable button, trigger, and stick timelines
- exact analog values and ramps
- millisecond or frame-derived timing
- waits for sound, image, OCR, controller, timer, or emulator cues
- branching on cue success/failure
- FPS/emulation-speed metadata and warnings
- manual-input merge/override/cancel policies
- dry-run and immediate global cancellation
- strict output ownership and cleanup

The intended flow is: manually perform a trick successfully, record it, trim/edit it, optionally insert cue waits, then bind it to one controller or keyboard trigger.

### Fishing Helper

Fishing Helper is the first specialized audio-cue feature. During integration it should become an optional module that emits events such as `audio:fishing:bite`; the shared router decides which controller action and output slot receives the event. Preserve the working recognition code and samples before refactoring it.

### Game assistance modules

Audio, visual, OCR, timer, LiveSplit, and emulator adapters observe something and publish typed events. They must not create their own virtual controllers or directly inject competing output. Macros and routes consume their events.


### Windows Controller Interception Toggle (Home / Screenshot)
- Added 	oggle_windows_controller_intercept.bat and 	ools/Toggle-WindowsControllerInterception.ps1.
- Allows users to toggle Windows system-level interception of the controller Guide (Home/Nexus) and Share (Screenshot) buttons.
- Features interactive elevation prompt: asks the user whether to elevate to Administrator (Y/N). If elevated, it sets system-wide Group Policy HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR\AllowGameDVR = 0. In user mode (or elevated), it sets HKCU:\Software\Microsoft\GameBar\UseNexusForGameBarEnabled = 0, AppCaptureEnabled = 0, and GameDVR_Enabled = 0.
- Automatically backs up previous registry settings to ackups/windows-controller-intercept-backup.json for full restoration.

## Current live installation

Current Citron executable:

`C:\Users\batma\Documents\Emulators\Citron-2026-04-27-clean\citron.exe`

Unified Controller root:

`C:\Users\batma\Documents\Emulators\Citron-2026-04-27-clean\Unified Controller`

Main files:

- `unified_controller_supervisor.py` — self-healing parent that keeps the bridge and GUI alive
- `unified_controller.py` — always-running bridge
- `unified_controller_gui.py` — interactive visual mapper
- `profiles.json` — current version-1 profile
- `status.json` — live status/telemetry
- `unified_controller.log` — bridge log
- `unified_controller_supervisor.log` — process restart log
- `supervisor_status.json` — supervisor and child process heartbeat
- `restart_controller.bat` — restart helper
- `README.txt` — current operating notes

Startup shortcut:

`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Citron Unified Controller.lnk`

The shortcut now starts `unified_controller_supervisor.py`. Windows Task Scheduler also has a task named `Unified Controller Supervisor` with an at-logon trigger, ignored duplicate instances, unlimited runtime, and restart-on-failure settings. The supervisor owns and continuously monitors both child processes. Closing the GUI minimizes it; killing either child causes an automatic restart.

Fishing Helper root:

`C:\Users\batma\Documents\Emulators\Citron-2026-04-27-clean\Fishing Helper`

Important Fishing Helper files:

- `fishing_helper.py`
- `recognition.py`
- `test_recognition.py`
- `main_capture.cpp` and `main_capture.dll`
- `settings.json`
- `sound-profile.npz`
- `samples\`
- `helper.log`

Do not move, clear, retrain, or rewrite Fishing Helper samples during research.

## Current controls and mappings

Global hotkeys:

- Ctrl+Alt+F10 — open or restore Unified Controller GUI
- Ctrl+Alt+F11 — toggle mouse-to-right-stick mode
- Ctrl+Alt+F12 — toggle exclusive keyboard-controller mode

Current requested face mappings:

- C -> virtual Xbox A
- V -> virtual Xbox B
- X -> virtual Xbox X
- Z -> virtual Xbox Y

Other current defaults include WASD to left stick, arrows to D-pad, Esc to Menu/Plus, Tab to View/Minus, mouse buttons to triggers/shoulders, and Shift/Ctrl to stick clicks. Verify `profiles.json` before assuming any mapping is unchanged.

The virtual controller currently appears through ViGEm as XInput slot 0. Citron's SDL GUID used for it is:

`030000000912000010c7000014017801`

## Implemented baseline

- Always-running Python bridge using a ViGEm Xbox 360 virtual target.
- Self-healing supervisor for the bridge and GUI, plus Windows Task Scheduler recovery for the supervisor.
- Low-level keyboard and mouse hooks on the interactive Windows desktop.
- Exclusive keyboard-controller mode that suppresses mapped keys.
- Physical XInput, keyboard, mouse button, and mouse movement inputs.
- Generalized `routes` in the current profile format.
- Keyboard/mouse/controller sources can target controller, keyboard, or mouse outputs.
- Injected keyboard/mouse events are marked so hooks do not feed them back into the router.
- Existing Fishing Helper IPC can trigger the virtual controller catch button.
- Interactive GUI with a full keyboard, recognizable mouse, Xbox controller graphic, click-to-map behavior, right-click removal, and live highlighting.
- GUI close requests minimize the window instead of stopping it; Ctrl+Alt+F10 restores it.
- Controller art from Xelu's CC0 prompt pack is stored under `assets\xelu-prompts`.

Current known architectural weaknesses:

- GUI and bridge do not yet share a formal profile service.
- Version-1 profiles use a mixed legacy/generalized structure.
- Device identities and controller groups are not yet persistent enough for robust split/merge behavior.
- Only one virtual output is active.
- Macros, turbo, layers, visual detection, and module APIs are planned but not implemented.
- Fishing Helper remains a separate application/process.

## Important user preferences

- Do not take over the mouse or click through Citron unless explicitly requested for that task.
- Do not automatically start games, press Start, or walk the user through obvious testing steps.
- Do not roll Citron back. The current updated build is the desired primary build.
- Do not make normal operation depend on Citron's broken `Any` input behavior.
- The user wants Citron bound to one stable Unified Controller.
- Do not focus on true frame-by-frame TAS when discussing Trick Assist; it is primarily a live macro mode with stronger timing and cue support.
- Keep gyro low priority. GameSir Cyclone 2 gyro behavior is inconsistent and may vary by connection mode.
- Keep researching and planning unless a change is easy or explicitly requested.

## Research package

Research documentation is under:

`C:\Users\batma\Documents\Emulators\Citron-2026-04-27-clean\Unified Controller\research`

Start with:

1. `ROADMAP.md` — architecture and staged build order
2. `FEATURE_CATALOG.md` — prioritized feature matrix
3. `GAME_ASSISTANCE_MODULES.md` — Fishing Helper, Trick Assist macro mode, visual/audio helpers, speedrun tools, and TAS boundary
4. `PROFILE_SCHEMA_V2.md` — proposed device/output/route/effect/macro/module format
5. `ACCEPTANCE_TEST_PLAN.md` — reliability requirements
6. `REFERENCES.json` — URLs, pinned commits, licenses, and adoption decisions

The references directory contains shallow or sparse source clones used only for inspection. No clone has been installed or executed. Executable, installer, library, and driver artifacts in clones were renamed with `.reference-disabled`; keep research clones free of runnable `.exe`, `.dll`, `.sys`, `.msi`, `.com`, and `.scr` files so Flow Launcher does not index them.

## Key research findings

### Input/remapping architecture

- DeviceMapper: Raw Input identity, JSON profiles, actions, combos, monitoring.
- UCR: provider/plugin boundaries, nested profiles, filters, disconnected-device cache.
- HE Keyboard Analog Mapper: stable per-keyboard identity and one virtual output per keyboard.
- vcon: profile snapshots, save/revert, tray and global hotkeys.
- Joystick Gremlin: reusable action graphs, conditions, macro scheduling, mode inheritance, axis transforms.
- AntiMicroX: turbo modes, cycles, sets, application profiles, mouse spring/region ideas.
- JoyShockMapper: tap/hold/double/chord activators, stick rings, trigger zones, rumble, optional gyro.
- Steam Input: bidirectional keyboard/mouse/controller mappings, action layers, mode shifts, activators, regions, radial menus.
- HidHide: optional session-scoped physical-device hiding to prevent double input.
- DS4Windows/Handheld Companion/PadForge: output slots, curves, auto profiles, remote input, overlays, many-controller concepts.

### Game assistance and speedruns

- AutoSplit: screen-region image matching, OCR, ordered states, thresholds, LiveSplit integration.
- LiveSplit Auto Splitters: timer start/split/reset, game time, load removal, dry-run/debug patterns.
- BizHawk TAStudio and libTAS: useful timeline and editing ideas, but deterministic frame systems are lower priority than live Trick Assist.
- Citron already contains a TAS recorder/playback path. It records per-emulated-frame button and two-stick state to a plain-text `record.txt` and uses `script0-1.txt`, `script0-2.txt`, etc. for playback. This is useful as an optional recording import/export format.
- Citron's own TAS playback requires selecting its separate `TAS` input device. Normal Trick Assist should instead import the recording and replay through Unified Controller so Citron stays on the stable virtual pad.

## Recommended feature model

The same action scheduler should power:

| Mode | Added behavior |
| --- | --- |
| Normal mapping | Hold/release one routed output |
| Toggle | Persist output until toggled off |
| Turbo | Scheduled pulse rate and duty cycle |
| Basic macro | Ordered typed steps and delays |
| Trick Assist macro | Recording, analog timeline, cues, FPS metadata, branching, stronger cancellation |
| Repeat-until macro | Loop a macro until a cue, timeout, iteration limit, or user stop |

Do not build separate schedulers for turbo, Trick Assist, Fishing Helper, and ordinary macros.

The shared event system should accept:

- keyboard, mouse, and controller state
- audio cue events
- image/color/motion/OCR events
- timers and countdowns
- LiveSplit state/events
- emulator-adapter events
- internal layer/profile/module state

## High-value feature ideas to continue researching

Prioritize ideas that fit the shared macro/event/router architecture:

1. Trick Assist recording and timeline-editing UX.
2. Audio cue training that supports positive and negative samples, sample playback/deletion, confidence history, cooldown, and arming.
3. Generic screen-region detectors: image, color, motion, OCR, and multi-step state recognition.
4. Cue-synchronized macros that pause at a step until audio/visual confirmation arrives.
5. Repeat-until workflows with visible iteration limits, timeouts, stop conditions, and hard cancellation.
6. LiveSplit bridge with dry-run logging.
7. Input recorder and live ghost/comparison tools.
8. Practice points combining a macro/profile, local timer, notes, and emulator reset/savestate hotkey.
9. Dialogue, menu, crafting, encounter, QTE, rhythm, and rare-sound helpers built as reusable detectors plus macros.
10. Controller-to-keyboard/mouse desktop layouts, fine pointer mode, scrolling, media keys, and an emergency exit chord.
11. Real device grouping that makes `Any controller` actually aggregate local or Sunshine/Moonlight sources.
12. Split/merge routing across output slots with clear device and player colors.

Further research should identify the feature, user flow, technical dependency, timing requirements, failure behavior, license, and whether it belongs in the core, macro engine, detector module, emulator adapter, or GUI only.

## Research findings and resolutions

- **Windows capture API**: Use **Windows Graphics Capture (WGC)** via Direct3D11 frame pool. It captures individual HWNDs cleanly even when the game/emulator window is occluded or in the background (no focus stealing). DXGI Desktop Duplication is tied to monitor output and captures overlapping windows.
- **OCR backend**: Use **`rapidocr-onnxruntime`** (already installed in Python 3.14). It is pure Python/ONNX, runs on CPU with zero external system binaries, and is already tested on this machine in `C:\AI\college-assistant\app\integrations\ocr\service.py` and `C:\AI\perception-mcp\server.py`.
- **Audio detection with positive/negative samples**: Dual-bank similarity check `(Sim_pos >= Thresh_pos) AND (Sim_neg < Thresh_neg)`. Voicemeeter Potato 8-channel stream allows isolating front/center SFX channels to eliminate surround music bias.
- **Timeline editor dual representation**: Data model stores milliseconds, source frame index, and original FPS. The editor offers a toggle between millisecond scale and frame scale (30/60 FPS) with frame-snap grid.
- **Playback FPS drop policy**: Configurable per Trick Assist recipe: `continue` (real-time clock), `scale` (time-dilated), `cue_synchronized` (hold until audio/visual event), or `abort_on_desync` (neutralize outputs if FPS deviates > tolerance).
- **Citron `record.txt` compatibility**: 100% losslessly round-trippable. Format is plain-text `<frame> <semicolon_buttons> <lx;ly> <rx;ry>`. Contiguous frames compress into duration steps for editing and expand back to exact Citron frame text.
- **Controller identity across SDL, XInput, and Moonlight**: Use SDL2 GUID / Raw Input path hashes for primary hardware identity. Distinguish Sunshine/Moonlight virtual controllers via parent bus enumerator (`ROOT` / `VIGEM` / `VIRTUAL_HID` vs. `USB` / `HID`).
- **Non-XInput slots 5-8**: The 4-controller limit is strictly Microsoft XInput, not ViGEmBus. Slots 5-8 can be spawned as virtual **DualShock 4** gamepads (`vgamepad.VDS4Gamepad`). Citron's SDL2 backend explicitly supports DualShock 4 and up to 8 players.
- **HidHide session behavior**: HidHide configuration is persistent in the kernel driver. It does *not* auto-unhide on app exit or crash. It must be strictly opt-in with a process-scoped whitelist rather than global masking.
- **Edge AI & Computer Vision Perception**: Use **YOLOv10n ONNX DirectML / TensorRT** running at 320x320 resolution (sub-2ms inference, 600+ FPS) coupled with a continuous-discrete Extended Kalman Filter (EKF) and anti-windup PID aim smoother. Real-time vision processes frames asynchronously through lock-free atomic triple buffers, applying SSIM perceptual change gating to prevent redundant inference loops.
- **MIDI & Polyphonic Matrix Gear**: Ingest USB-MIDI 1.0/2.0 streams through native OS audio-thread callbacks (`python-rtmidi` / `mido`). Pitch bend wheels provide high-resolution 14-bit axes (0-16383) for flight sticks, velocity curves provide dynamic walk/run thresholds, and 64-pad RGB matrices (Launchpad) enable live in-game inventory mirroring.
- **Biosignals & Pre-Movement sEMG ("Negative Latency")**: Forearm surface electromyography (sEMG) detects motor unit action potentials (MUAP) 30-50ms *before* physical tendon movement occurs. By processing 10ms sliding windows with Hudgins time-domain features (MAV, WL, ZC, SSC) and an LDA classifier, the virtual controller fires actions up to 25ms ahead of physical switch contact. Deliberate double-blinks are captured via EOG cornea-retinal dipole tracking with high-frequency jaw-clench EMG noise rejection.
- **Sim Telemetry & Dynamic Haptics**: Win32 shared memory (`Local\acpmf_physics`, `Local\IRSDKMemMapFileName`) and high-rate UDP broadcast sockets (Forza Dash 311-byte, Codemasters F1) feed phase-continuous audio haptic synthesis (engine RPM harmonics $f = \text{RPM} \times N/120$, curb FM, and tire lockup chatter) and DualSense motorized trigger resistance (Mode 0x06 ABS pedal vibration on L2, Mode 0x02 traction-loss snap detents on R2).
- **Casual & Cozy Game Automations**: Typewriter text ROI frame-differencing with choice-pause heuristics, stamina-gated auto-mashing, auto-run latches with vector dot-product pull-back trip, bulk crafting state machines, and parametric orbital stick loops for egg hatching.
- **Retro & Emulator Superpowers**: Progressive analog trigger fast-forward throttles ($1.0	imes 	o 5.0	imes$) and proportional rewind scrubbing via emulator IPC, chorded savestate carousel with overwrite undo caches and distinct haptic signatures, Right Stick touchscreen pointers with spring-center/cursor memory, synthetic turbulent pink noise microphone blow buttons on L3, strict 4-way D-pad sector gating with $12^\circ$ hysteresis, and retro stick anti-deadzone linearizers.
- **Light Speedrunning & Practice Assists**: In-engine / external practice loops with instant death rewind, LiveSplit auditory telemetry chimes (ahead/behind PB audio cues), conical stick angle snapping (Celeste $37.5^\circ / 45^\circ$, SM64 swim angles), DirectX screen-edge parry/buffer flashes, and multimedia timer haptic metronomes.
- **Gamer Companion & Overlay Suite**: WinRT SMTC + embedded WebView2 player controlling YouTube walkthroughs via `Guide + D-Pad` chords without focus stealing, ban-safe Direct2D out-of-process semi-transparent reference overlays (Peek Mode vs Interactive Checklist Mode), Core Audio WASAPI crossfader balancing Discord vs game volume with haptic center detents, ballistic virtual mouse with exponential power curve ($\gamma = 2.2$), and OBS WebSocket clip/bookmark loggers.
- **Controller 'Overclocking' Assessment**: Evaluated and deliberately excluded. Official Xbox controllers are locked at 125Hz/250Hz in firmware (yielding duplicate stale packets on forced 1000Hz USB polls), kernel filter drivers (`hidusbf`) introduce BSOD risks and anti-cheat bans, modern controllers feature native 1000Hz firmware, and our virtual ViGEmBus output already operates sub-millisecond.


## Remaining open design questions

- How should profile/layer conflicts and route loops be shown without overwhelming the mapper GUI?
- Which game-assistance features are broadly reusable enough to justify core UI instead of a per-game preset?

## Proposed next implementation order

This is planning guidance, not authorization to start all phases immediately.

1. Stabilize profile writes, schema migration, hot reload, and output ownership.
2. Separate input adapters, router, scheduler, output slots, profile service, and GUI communication.
3. Add stable Raw Input/SDL device identity and device groups.
4. Add the shared action scheduler, then toggle/turbo/basic macros.
5. Add Trick Assist as an advanced macro editor/recorder mode.
6. Integrate Fishing Helper as the first cue-producing module.
7. Add visual/OCR events and cue-synchronized macro steps.
8. Add multiple output slots and split/merge editing.
9. Add LiveSplit, practice, logging, and optional emulator adapters.
10. Consider deterministic TAS tools only after a trustworthy emulator frame API exists.

## Validation expectations

Before changing the running bridge:

- preserve the current C/V/X/Z bindings
- preserve hotkeys and keyboard suppression behavior
- preserve the virtual controller GUID expected by Citron
- back up profiles before migration
- compile both Python entry points
- verify the running bridge reloads the profile
- verify the GUI does not create another virtual controller
- test release/neutralization on profile change, device disconnect, macro cancel, and shutdown
- keep research binaries quarantined
- leave the supervisor and scheduled task in control of the live bridge/GUI; restart through `restart_controller.bat` or the scheduled task rather than launching an unmanaged bridge

The acceptance plan contains detailed tests for persistence, device identity, desktop mode, routing ownership, turbo, macros, Trick Assist, Fishing Helper, game-assistance detectors, LiveSplit, multiple outputs, performance, and recovery.

## Current research-only status

The latest work changed planning documents and source references. It did not enable new bots, macros, turbo, visual capture, OCR, LiveSplit control, multiple virtual outputs, or Citron TAS playback. The only current configuration change from this research session is the requested C/V/X/Z face-button mapping.
