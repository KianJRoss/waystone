Citron Unified Controller

This bridge exposes one stable virtual Xbox 360 controller to Citron.
It forwards whichever real XInput controller is active, including a local
Xbox controller or the virtual Xbox 360 controller supplied by Sunshine.
Press Ctrl+Alt+F12 to switch keyboard controller mode on or off. A dark flyout
on the active monitor confirms the change. In controller mode,
all keyboard events are consumed before they reach Windows or another app.
Press Ctrl+Alt+F11 to toggle mouse-to-right-stick translation for the active
profile. A dark flyout on the active monitor confirms the change. Mouse capture
begins when keyboard controller mode is also on. Press
Ctrl+Alt+F10 to open the control center or restore its existing window.
Currently C maps to the controller button used for Switch A, and Escape maps
to the controller button used for Switch Plus. The default full layout is:

  WASD       Left stick       Arrow keys  D-pad
  Z/X/C/V    Y/B/A/X          Q/E         L/R
  1/3        ZL/ZR            Shift/Ctrl  Left/Right stick click
  Esc/Tab    Plus/Minus

Unified Controller.lnk is a fallback shortcut for the profile editor and visual cross-reference.
Profiles can change mappings, enable mouse-to-right-stick translation, and
adjust mouse sensitivity. When mouse translation is enabled, left/right click
map to ZR/ZL, middle click maps to right-stick click, and side buttons map to
R/L. Fishing Helper catches emit the Switch A action. In normal mode the
keyboard and mouse pass through normally.

Fishing Helper is integrated into the control center and loads the existing six
bite samples automatically. Press Ctrl+Alt+F1 to enable or disable auto catch;
the flyout appears on the monitor containing the active game. In Animal
Crossing, tap right bumper to prime detection for ten seconds or hold it to keep
the window refreshed. A flyout on the game monitor shows the live prime
countdown and closest match score. The Fishing Helper page is for training, sample playback,
and deleting bad samples; normal use does not require its window or an on/off
button.

The interactive mapper shows a complete Xbox controller, full desktop keyboard,
and mouse. Click any source control and then any destination control to create a
mapping; right-click a mapped source to remove it. Changes save immediately.
Controller, keyboard, and mouse buttons can be used as either sources or outputs
while controller mode is active. The Xbox controller artwork is from Xelu's CC0
input prompt pack; its license is stored under assets\xelu-prompts\LICENSE.txt.

Home/Xbox and Share/Capture are shown as separate controls in the mapper. Home
uses XInputGetStateEx so the physical Guide bit reaches the stable virtual pad.
The GameSir Cyclone 2 Share and Camera/Capture bits are read from its vendor HID
interface and can be assigned even though ordinary XInput omits them. The mapper
also provides Bridge Actions as destinations: Fishing Helper Toggle, Keyboard
Controller Mode, Mouse Aim Mode, and Open Control Center. Controller action
routes remain active while keyboard-controller mode is off; keyboard and mouse
routes still require that mode so desktop input is not intercepted accidentally.

The current ViGEm Xbox 360 output can carry Home but has no Share/Misc1 field.
Share/Capture therefore works as a remappable input and action trigger now. A
true console Screenshot output remains assigned to the planned extended virtual
HID adapter rather than being faked with a desktop keyboard event.

Virtual SDL GUID: 030000000912000010c7000014017801

The bridge starts automatically at Windows sign-in. Its status is written to
status.json and diagnostics are written to unified_controller.log.

Low input latency is a primary project requirement. The active controller is
polled on a high-resolution absolute 1 ms schedule; inactive XInput slots are
scanned in rotation so device switching remains fast without delaying the
active device. Telemetry serialization, file writes, GameSir vendor-HID reads,
audio analysis, and GUI rendering run outside the forwarding loop. status.json
publishes average/maximum loop and work time plus loops exceeding 2 ms under
the `performance` field. New features must not perform blocking I/O, image
processing, audio processing, or filesystem work on the forwarding thread.

The GUI is event-driven. It redraws only panels whose hover, mapping, size, or
live input state changed, limits redraw scheduling to 60 Hz, and caches the
resized controller artwork. It must not return to timer-driven full-window
redrawing because that previously consumed nearly one complete CPU core.

unified_controller_supervisor.py keeps both the bridge and GUI running. Closing
the GUI window minimizes it; Ctrl+Alt+F10 restores it. Supervisor health is
written to supervisor_status.json and unified_controller_supervisor.log.

Continued research, architecture decisions, user preferences, current paths,
and feature priorities are documented in PROJECT_HANDOFF.md.
