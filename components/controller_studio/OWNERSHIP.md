# SUPERSEDED: see NATIVE-QML-PLAN.md (Kian 2026-11-16)

# Existing UnifiedController UI absorbed into Waystone — current user correction

User explicitly requires the ACTUAL existing UnifiedController UI as it was, inside
Waystone. Not the simplified new QML mapping panel, JSON editor or controls dashboard.
Preserve existing original layout, screens, visuals, event handlers and behavior.

Source is now app-owned under upstream/ with SHA256 import manifest. Do not mutate
the original ~/Work/UnifiedController dirty checkout or the existing running engine.
Parent owns source import/packaging/runtime lifecycle and final integration.

Worker A (puter-agy2-gemini-medium): ONLY ui-fixture/ + research-ui-host.md.
Worker B (deepai-glm-5-3-flash): ONLY foreign-host-probe/ + research-qt-host.md.
Do not edit upstream/ during probes. Never launch real controller devices, grabs,
input engines, games or storefront clients in tests. Private Xvfb only. No system
configuration/service/profile changes. Parent handles a verified later cutover.

Acceptance: real existing Avalonia UI, every current screen retained, embedded in
Waystone (not an external window/button). Waystone packages and owns its module and
runtime; no dependency on separately installed UnifiedController or user-managed
unified-controller.service. Internal low-latency module processes are implementation
details, not a separate installed app. Preserve existing profiles, devices/settings,
macros, layout/theme, runtime safety and one authoritative input loop.
