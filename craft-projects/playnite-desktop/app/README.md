# Waystone — Craft Anything desktop candidate

Built through Craft Anything 0.6.1 with the explicitly selected installed **Codex** backend and **Python** stack. This is a PySide6/Qt Quick desktop app, not a web mockup. Primary reference: a read-only copy of the Playnite 10.56 Default desktop theme.

Compact50-DIP toolbar, narrow icon rail, resizable/collapsible Explorer and Filter panels, portrait grid with adjustable density and optional overview, separate Details and sortable column-list views. The previous dashboard hero and decorative initial cards are removed. Missing artwork remains missing; no images have been generated, fetched or inferred from a game's title.

## Local validation

From this directory:

```bash
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 -B -m unittest discover -p 'test_*.py'
python3 -B app.py --smoke-test
python3 -B app.py --smoke-test --snapshot fixtures/demo-snapshot.json
python3 -B app.py --self-test shots/interaction.json --screenshot shots/grid.png --width 1440 --height 900
python3 -B app.py --view details --screenshot shots/details.png
```

All validation/screenshot switches force offscreen software rendering before creating Qt. For a visible preview, omit them:

```bash
python3 -B app.py --snapshot /explicit/path/to/app-owned-steam-export.json
```

Repeat `--snapshot` for additional explicitly selected exports. Source/host/install identities and individual observation timestamps/completeness are retained; never merged by title. A manifest's `declared-installed` does not become validated `installed`. Snapshot import performs no scanner/config discovery, launches or upstream writes. The Organizer is deterministic preview only, no AI/repair/merge/delete actions. REA is static compatibility/investigation evidence, not a launcher backend.

## Current integration boundary

This is a separately owned candidate, not the installed release. Independent review found security/import issues despite passing the first build's tests; targeted fixes and reviewer reruns are tracked in `../../../handoff/active-delegation-20261115.md` and `../../../research/independent-review-20261115.md`. Do not treat generated screenshots, Craft's partial internal checks or test counts alone as deployment clearance. Source `ui-prototype/launcher-refresh` remains preserved and is not the hardened candidate.

The new root-level `modules/library_review.py` facade is separately tested; this candidate currently uses its bundled original Organizer health engine, not that new facade. Existing cached Playnite media is being mapped in an independent task; this candidate does not yet consume it or claim full plugin parity.
