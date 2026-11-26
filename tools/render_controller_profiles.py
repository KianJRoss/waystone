"""Render ACTUAL profile QML with synthetic documents, never a native engine.

Run with the cached pytest/PySide6 environment used by the offline UI tests:
  uv run --offline --no-project --with pytest --with pyside6 python \
      tools/render_controller_profiles.py research/w3/screenshots
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
os.environ['QML_DISABLE_DISK_CACHE'] = '1'
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'craft-projects/playnite-desktop/app'
sys.path[:0] = [str(ROOT), str(APP)]

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlExpression
from PySide6.QtTest import QTest
from controller_profiles_view import ControllerProfilesView
from test_controller_profile_ui import InertEditor, InertStudio, ProfileQmlHarness, synthetic_document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    application = QGuiApplication.instance() or QGuiApplication(['synthetic-profile-evidence'])
    artifacts = []
    warnings = []
    with tempfile.TemporaryDirectory(prefix='waystone-profile-render-') as isolated:
        path = Path(isolated) / 'profiles.json'
        path.write_text(json.dumps(synthetic_document()), encoding='utf-8')
        bridge = ControllerProfilesView(path)
        editor, studio = InertEditor(), InertStudio()

        def render(filename, *, popup=False, **kwargs):
            ui = ProfileQmlHarness(bridge, **kwargs)
            try:
                if popup:
                    expression = QQmlExpression(ui.engine.rootContext(), ui.child('controllerProfileChoice'), 'popup.open()')
                    expression.evaluate()
                    if expression.hasError():
                        raise AssertionError(expression.error().toString())
                    QTest.qWait(70)
                width, height = ui.screenshot(args.output / filename)
                artifacts.append({'path': str(args.output / filename), 'width': width, 'height': height})
                warnings.extend(ui.warnings)
            finally:
                ui.close()

        render('synthetic-profiles-tab.png', component='controller_native/ProfilesTab.qml',
               width=1280, height=900, editor=editor, studio=studio)
        render('synthetic-game-picker-default-first.png', game_id='synthetic:same-title-one',
               width=520, height=300, popup=True)
        assert bridge.assignGame('synthetic:same-title-one', 'Racing')
        render('synthetic-game-picker-racing.png', game_id='synthetic:same-title-one', width=520, height=190)
        bridge.setRuntimeGame('synthetic:same-title-one')
        bridge.setRuntimeStatus('Applying Racing · awaiting native readback')
        bridge.setEngineMutationBlocked(True)
        render('synthetic-overlay-picker-pending.png', game_id=bridge.runtimeGameId,
               compact=True, width=360, height=190)
        bridge.setEngineMutationBlocked(False)
        render('synthetic-general-default-picker.png', width=460, height=180)
        assert editor.calls == []
    print(json.dumps({'synthetic_fixture_only': True, 'renderer': 'Qt offscreen/software',
                      'artifacts': artifacts, 'qml_warnings': warnings}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
