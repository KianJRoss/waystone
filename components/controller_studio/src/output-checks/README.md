# Sony output checks (fakes only)

Run from the worktree root:

```sh
dotnet run --project components/controller_studio/src/output-checks/SonyOutputChecks.csproj --no-launch-profile
```

Package-free .NET 10 executable linking Core contracts and the new Sony output files only.
Every instantiated output receives `FakeWriter`; tests never instantiate the production transport,
open input/UHID/uinput devices, load SDL, or interact with controllers/games/services.

55 named checks cover complete descriptor hashes and independent HID size decoding, neutral golden
reports, per-button/dpad/axes vectors, gyro+accel/calibration/timestamp bytes, bounded output handling,
feature request/reply ABI, fake async reader, lifecycle, partial writes and cleanup. Each pad's
malformed-output check rejects all invalid lengths0..5000; each kernel-event check also uses300
fixed-seed malformed events. Checks count test groups, not individual loop iterations.

Sources and remaining live checks: `research/w16/hid-report-layouts.md` at repository root.
