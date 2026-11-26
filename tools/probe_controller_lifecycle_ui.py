"""End-to-end integration probe driving real ProfilesTab lifecycle dialogs in full headless session."""
import json, os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'craft-projects/playnite-desktop/app'
sys.path[:0] = [str(APP), str(ROOT)]

from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from app import create_session
from controller_engine_host import ControllerEngineHost
from native_services.unified_controller_control import ControllerControlClient

def wait(fn, timeout_ms=6000):
    start = time.time()
    while time.time() - start < (timeout_ms / 1000.0):
        QTest.qWait(20)
        if fn(): return
    raise AssertionError('Timeout waiting for condition')

def main():
    evidence_dir = ROOT / 'evidence/controller-lifecycle-ui-20261116'
    evidence_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        data = p / 'data'
        data.mkdir()
        runtime = p / 'rt'
        runtime.mkdir(mode=0o700)
        os.environ['XDG_RUNTIME_DIR'] = str(runtime)
        os.environ['XDG_DATA_HOME'] = str(p / 'app-data')

        # Initial disposable profile with unknown custom fields
        initial_profile = {
            'routes': {'Keyboard:W': 'Controller:A', 'Controller:A': 'Controller:A'},
            'custom_kept': 17,
            'future_lifecycle_tag': 'alpha_test'
        }
        (data / 'profiles.json').write_text(json.dumps({
            'version': 1,
            'active_profile': 'Default',
            'profiles': {'Default': initial_profile}
        }))

        app = QGuiApplication([])
        host = ControllerEngineHost(ROOT / 'components/controller_studio/headless/publish/UnifiedController.Headless', data, fixture=True)
        client = ControllerControlClient(socket_path=runtime / 'waystone-unified-controller/control.sock')

        engine, model, root, warnings = create_session(
            app, headless=True, controller_client=client, studio_host=host, native_controller_ui=True
        )

        try:
            root.setVisible(True)
            root.openControllers()

            view = engine.unified_controller
            live = engine.controller_live
            commands = engine.controller_commands
            profile_commands = engine.controller_profile_commands
            device_commands = engine.controller_device_commands

            wait(lambda: host.available and view.available and not view.busy)
            wait(lambda: not profile_commands.busy and 'profile_create' in profile_commands.capabilities)

            # Find native controller studio and switch to Profiles tab
            native_studio = root.findChild(QObject, 'nativeControllerStudio')
            assert native_studio is not None, "nativeControllerStudio component not found"

            native_studio.setProperty('activeTab', 'Profiles')
            QTest.qWait(100)
            profiles_tab = native_studio.findChild(QObject, 'profilesTab')
            assert profiles_tab is not None, "profilesTab component not found"

            # Locate dialog and lifecycle buttons
            create_btn = profiles_tab.findChild(QObject, "createProfileButton")
            copy_btn = profiles_tab.findChild(QObject, "copyProfileButton")
            rename_btn = profiles_tab.findChild(QObject, "renameProfileButton")
            delete_btn = profiles_tab.findChild(QObject, "deleteProfileButton")
            import_btn = profiles_tab.findChild(QObject, "importProfileButton")
            export_btn = profiles_tab.findChild(QObject, "exportProfileButton")
            dialog_confirm_btn = profiles_tab.findChild(QObject, "dialogConfirmButton")
            dialog_name_field = profiles_tab.findChild(QObject, "dialogNameField")
            dialog_doc_area = profiles_tab.findChild(QObject, "dialogDocArea")

            assert all(x is not None for x in [create_btn, copy_btn, rename_btn, delete_btn, import_btn, export_btn, dialog_confirm_btn, dialog_name_field, dialog_doc_area])

            # 1. Test Dialog Capability Read Isolation (Draft Preserved)
            create_btn.clicked.emit()
            assert profiles_tab.property("activeDialog") == "create"
            dialog_name_field.setProperty("text", "DraftToPreserve")
            profiles_tab.setProperty("dialogInputName", "DraftToPreserve")

            # Trigger capability refresh read operation while dialog is open
            profile_commands.refreshProfiles()
            wait(lambda: not profile_commands.busy)

            assert profiles_tab.property("activeDialog") == "create", "Capability read dismissed active dialog!"
            assert profiles_tab.property("dialogInputName") == "DraftToPreserve", "Capability read wiped draft input!"
            results["capability_read_retains_dialog_and_draft"] = True

            # Cancel dialog
            profiles_tab.setProperty("activeDialog", "")

            # 2. Test Real Create Profile via Dialog Controls
            create_btn.clicked.emit()
            assert profiles_tab.property("activeDialog") == "create"
            dialog_name_field.setProperty("text", "Speedrun")
            profiles_tab.setProperty("dialogInputName", "Speedrun")
            dialog_confirm_btn.clicked.emit()

            wait(lambda: not profile_commands.busy and 'Speedrun' in profile_commands.profiles)
            assert profiles_tab.property("activeDialog") == ""
            results["ui_create_profile_success"] = True

            # 3. Test Real Copy Profile via Dialog Controls
            profiles_tab.setProperty("selectedProfileName", "Default")
            copy_btn.clicked.emit()
            assert profiles_tab.property("activeDialog") == "copy"
            dialog_name_field.setProperty("text", "DefaultCopy")
            profiles_tab.setProperty("dialogInputName", "DefaultCopy")
            dialog_confirm_btn.clicked.emit()

            wait(lambda: not profile_commands.busy and 'DefaultCopy' in profile_commands.profiles)
            assert profiles_tab.property("activeDialog") == ""

            # Check unknown fields preserved in copied profile
            saved_disk = json.loads((data / 'profiles.json').read_text())['profiles']
            assert 'DefaultCopy' in saved_disk
            assert saved_disk['DefaultCopy'].get('custom_kept') == 17
            assert saved_disk['DefaultCopy'].get('future_lifecycle_tag') == 'alpha_test'
            results["ui_copy_profile_preserved_unknown_fields"] = True

            # 4. Test Real Rename Profile via Dialog Controls
            profiles_tab.setProperty("selectedProfileName", "DefaultCopy")
            rename_btn.clicked.emit()
            assert profiles_tab.property("activeDialog") == "rename"
            dialog_name_field.setProperty("text", "DefaultRenamed")
            profiles_tab.setProperty("dialogInputName", "DefaultRenamed")
            dialog_confirm_btn.clicked.emit()

            wait(lambda: not profile_commands.busy and 'DefaultRenamed' in profile_commands.profiles and 'DefaultCopy' not in profile_commands.profiles)
            assert profiles_tab.property("activeDialog") == ""

            # Check unknown fields preserved after rename
            saved_disk = json.loads((data / 'profiles.json').read_text())['profiles']
            assert 'DefaultRenamed' in saved_disk
            assert 'DefaultCopy' not in saved_disk
            assert saved_disk['DefaultRenamed'].get('custom_kept') == 17
            results["ui_rename_profile_preserved_unknown_fields"] = True

            # 5. Test Real Export Profile via Dialog Controls
            profiles_tab.setProperty("selectedProfileName", "DefaultRenamed")
            export_btn.clicked.emit()
            assert profiles_tab.property("activeDialog") == "export"
            wait(lambda: not profile_commands.busy and bool(profile_commands.exportDocument))

            exported_text = profiles_tab.property("dialogDocumentText")
            assert len(exported_text) > 0, "Export document text was empty in dialog"
            exported_obj = json.loads(exported_text)
            assert exported_obj['profiles']['DefaultRenamed']['custom_kept'] == 17
            results["ui_export_profile_displayed_and_preserved_fields"] = True
            profiles_tab.setProperty("activeDialog", "")

            # 6. Test Real Import Profile via Dialog Controls
            import_btn.clicked.emit()
            assert profiles_tab.property("activeDialog") == "import"
            dialog_name_field.setProperty("text", "ImportedFromUI")
            profiles_tab.setProperty("dialogInputName", "ImportedFromUI")
            dialog_doc_area.setProperty("text", exported_text)
            profiles_tab.setProperty("dialogDocumentText", exported_text)
            dialog_confirm_btn.clicked.emit()

            wait(lambda: not profile_commands.busy and 'ImportedFromUI' in profile_commands.profiles)
            assert profiles_tab.property("activeDialog") == ""

            # Verify imported profile committed to disk with unknown fields intact
            saved_disk = json.loads((data / 'profiles.json').read_text())['profiles']
            assert 'ImportedFromUI' in saved_disk
            assert saved_disk['ImportedFromUI'].get('custom_kept') == 17
            results["ui_import_profile_committed_with_unknown_fields"] = True

            # 7. Test Real Delete Profile via Dialog Confirmation
            profiles_tab.setProperty("selectedProfileName", "Speedrun")
            delete_btn.clicked.emit()
            assert profiles_tab.property("activeDialog") == "delete"
            dialog_confirm_btn.clicked.emit()

            wait(lambda: not profile_commands.busy and 'Speedrun' not in profile_commands.profiles)
            assert profiles_tab.property("activeDialog") == ""
            results["ui_delete_profile_confirmed"] = True

            # Delete remaining non-default profiles to test sole remaining profile delete guard
            for prof_to_clean in ["DefaultRenamed", "ImportedFromUI"]:
                if prof_to_clean in profile_commands.profiles:
                    profile_commands.deleteProfile(prof_to_clean)
                    wait(lambda: not profile_commands.busy)

            assert len(profile_commands.profiles) == 1
            assert profile_commands.profiles == ["Default"]

            # 8. Test Last Profile Delete Guard
            profiles_tab.setProperty("selectedProfileName", "Default")
            QTest.qWait(50)
            assert delete_btn.property("enabled") == False, "Delete button must be disabled when only 1 profile remains"
            results["ui_delete_last_profile_guard_enforced"] = True

            # Clean shutdown
            live.stop()
            host.shutdown()
            wait(lambda: not host.running)

            results["all_lifecycle_dialogs_passed"] = True
            (evidence_dir / 'probe-results.json').write_text(json.dumps(results, indent=2))
            print(json.dumps(results, indent=2))

        finally:
            engine.controller_profile_commands.shutdown()
            engine.controller_device_commands.shutdown()
            engine.controller_commands.shutdown()
            live.shutdown()
            host.shutdown()
            view.shutdown()
            root.setProperty('quitRequested', True)
            root.close()
            engine.voice_media.shutdown()
            engine.social_view.shutdown()

if __name__ == '__main__':
    main()
