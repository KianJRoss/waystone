# Portable runtime boundaries

The Linux runtime links the production profile, route, activator, effect, macro,
source-merger, stick, and motion implementations from `UnifiedController.App/Engine`.
`PortableRawInput.cs` intentionally contains only the two snapshot records needed
by that code; the Win32 Raw Input manager is not linked.

The runtime follows the native processing order: select/merge sources, derive
active sources and layers, apply controller and desktop routes, update motion,
apply stick processing, then apply macros. Profile parsing occurs before an atomic
reference swap. Invalid reloads retain the last-good runtime. Profile writes use a
same-directory temporary file, a validated backup, flush-to-disk, and rename; JSON
is edited as a document so unknown fields, the active profile, and output-slot
configuration survive selection.

Linux v1 intentionally omits the Windows audio helper and module-specific actions.
DSU is also omitted until it can be fed true selected-source sensor samples; the
runtime never emits fabricated sensor data. Force feedback is not advertised by
the uinput output because proper `UI_FF_UPLOAD`/`UI_FF_ERASE` request handling is
not yet implemented. These omissions are surfaced in `RuntimeSnapshot.Limitations`.

Controller input is provider-neutral and has no vendor allowlist. Desktop physical
capture is opt-in and starts disabled. The application uinput identities and LAN
Mouse devices must be excluded by the input provider before capture. Lack of evdev
keyboard permissions leaves controller forwarding operational and produces an
explicit capture limitation instead of a startup failure.
