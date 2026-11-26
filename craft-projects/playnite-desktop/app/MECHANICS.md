# Desktop interaction specification (not a game)

The specific request is a read-only PySide6 / Qt Quick desktop library. Movement,
weapons, health, scoring and game modes are **not applicable**. No gameplay or
launch/install behavior has been added to satisfy generic game-checker wording.
`mechanics.json` records actual UI values instead.

## Observed theme values

Primary evidence: the read-only copied Playnite 10.56 Default theme at
`research/craft-playnite-reference/installed-theme/Default`.

| Value | Implemented | Evidence |
|---|---|---|
| Toolbar height | 50 px | Views/TopPanel.xaml, Border Height=50 |
| Search width | 300 px at normal width; shrinks at narrow widths | Views/TopPanel.xaml, PART_TextMainSearch Width=300; responsive shrink is an adaptation |
| Icon rail | 44 px | Views/Sidebar.xaml, main-menu 44×44 |
| Text | 12 px secondary, 13 px compact rows, 14 px standard; 20 px details title | Constants.xaml 12/14/20; intermediate 13 is a readability adaptation |
| Corners / control border | 1 px | Constants.xaml ControlCornerRadius / ControlBorderThickness |
| Background | #0D1225 | Constants.xaml MainColorDark |
| Main / selection | #2C3A67 | Constants.xaml MainColor |
| Glyph / focus | #55CDFF | Constants.xaml GlyphColor |
| Hover | #247BA0 | Constants.xaml HoverColor |
| Text / secondary | #F2F2F2 / #A3A3A3 | Constants.xaml TextColor / TextColorDarker |
| Panel | #151E3D | Constants.xaml GridItemBackgroundColor |
| Border | #3E6184 | Constants.xaml PopupBorderColor |
| Popup | #1F2847 | Constants.xaml PopupBackgroundBrush (brush literal, not unused PopupBackgroundColor) |
| Preferred font | Trebuchet MS | Constants.xaml FontFamily; unavailable here, Qt uses a local font fallback (fc-match: Liberation Sans) |

## Explicit estimates and adaptations

The theme binds many dimensions to runtime Settings, which were not copied. The
reference JPEGs confirm the five-region arrangement, not a live host's settings.

- Initial window 1280×800, minimum 900×560: estimated review viewport.
- Explorer 195 px, filter 220 px, grid inspector 300 px, details list 255 px: estimated;
  all are user-resizable. Explorer/filter/inspector can be closed and reopened.
- Cover width 126 px, zoom range 96–184 px, step 2 px: estimated density choices.
  Portrait ratio 456:620 follows the dimensions of the theme's missing-cover image;
  no pixels of that image are copied. Neutral #25282E placeholders are an adaptation.
- Cover cell padding 12 px, caption/status area 48 px: estimated. Grid selection has
  a thin cyan outline. No cover art, initial tiles, gradients or background media.
- Compact rows 30 px, column headers 32 px, splitter grip 5 px, status 25 px: estimated.
- Column widths 220/145/110/240/85 px: estimated; individually resizable with horizontal
  scrolling when necessary. Every header sorts its column.
- Grid inspector omits a redundant cover; details mode includes a small placeholder.
- No logos/private game art/font files are shipped. Icons use local text glyphs.
- No Play, Install, Edit, preset-save, random-game or update control is imitated: actions
  outside this read-only scope are absent, rather than fake inert buttons.

## Functional rules

- Ctrl+F focuses/selects search; Ctrl+E toggles explorer; Ctrl+G selects grid. Escape
  clears an active search. Arrow keys select grid/list entries. Double-click opens details.
- Search is trimmed, case-insensitive title substring matching, not fuzzy identity matching.
- Library, platform and install-state filters must match **the same variant**.
- `installed`, `declared-installed`, `registered`, `not-installed` and `unknown` stay distinct.
  Export snapshots cannot claim validated installation; their schema accepts declarations,
  registration and unknown states only. Fixture installed labels are synthetic observations.
- Filters never discard variants/evidence from selected-game details. Mixed observations
  stay mixed. Equal titles never merge. Selection follows stable ID across sorting; an
  unavailable selection falls back to first result (or none for zero results).
- Sort applies to name/library/platform/install state/record count, ascending or descending,
  with a stable work-ID tie-breaker. Filter changes retain sort direction. Explorer counts
  explicitly count the full catalog, not current filtered results.
- Snapshot details show availability/completeness/observation times; incomplete or offline
  exports are not uninstall deltas. Every source and installation identity remains intact.
- Organizer always reviews the full loaded catalog, not the filtered subset. Duplicate
  candidates are proposals, never merged/deleted. Missing metadata is not corruption.
- Screenshot mode forces offscreen software rendering, disables desktop focus, requests
  120 paint/event cycles, saves a real window PNG and exits. Self-tests drive actual Qt
  controls and read model/UI state; there is no synthetic game-state success report.

## Scope of inspection

Craft `core_target_open` and `core_target_triage` confirmed a theme directory, not an
executable. All eight specifically requested XAML files, Sidebar and item/overview
styles were inspected. No native functions or managed executable were present in this
selected target to decompile. No Playnite/Wine execution, source scan, network access,
paid backend or extra package was used.
