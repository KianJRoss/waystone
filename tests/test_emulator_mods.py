"""Tests for emulator_mods.py"""

import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from native_services.emulator_mods import (
    EMULATORS,
    apply_install,
    detect_layout,
    mod_root,
    plan_install,
    undo_install,
)


class TestModRoot:
    """Tests for mod_root function."""

    def test_eden_mod_root(self, tmp_path):
        home = tmp_path
        result = mod_root("eden", title_id="0100000000001000", mod_name="MyMod", home=home)
        expected = home / ".local/share/eden/load/0100000000001000/MyMod"
        assert result == expected

    def test_ryujinx_mod_root(self, tmp_path):
        home = tmp_path
        result = mod_root("ryujinx", title_id="0100000000001000", mod_name="MyMod", home=home)
        expected = home / ".config/Ryujinx/mods/contents/0100000000001000/MyMod"
        assert result == expected

    def test_azahar_mod_root(self, tmp_path):
        home = tmp_path
        result = mod_root("azahar", title_id="0004000000123400", mod_name="MyMod", home=home)
        expected = home / ".local/share/azahar-emu/load/mods/0004000000123400"
        assert result == expected

    def test_cemu_mod_root(self, tmp_path):
        home = tmp_path
        result = mod_root("cemu", title_id="", mod_name="GraphicPack", home=home)
        expected = home / ".local/share/Cemu/graphicPacks/GraphicPack"
        assert result == expected

    def test_dolphin_mod_root(self, tmp_path):
        home = tmp_path
        result = mod_root("dolphin", title_id="GAME01", mod_name="TexturePack", home=home)
        expected = home / ".local/share/dolphin-emu/Load/Textures/GAME01"
        assert result == expected

    def test_eden_title_id_normalized_to_uppercase(self, tmp_path):
        result = mod_root("eden", title_id="0100000000001000", mod_name="Mod", home=tmp_path)
        assert "0100000000001000" in str(result)
        result = mod_root("eden", title_id="0100000000001000".lower(), mod_name="Mod", home=tmp_path)
        assert "0100000000001000" in str(result)

    def test_ryujinx_title_id_lowercase_in_path(self, tmp_path):
        result = mod_root("ryujinx", title_id="0100000000001000", mod_name="Mod", home=tmp_path)
        assert "0100000000001000" in str(result).lower()

    def test_invalid_title_id_eden_raises(self, tmp_path):
        with pytest.raises(ValueError, match="16 hex chars"):
            mod_root("eden", title_id="INVALID", mod_name="Mod", home=tmp_path)
        with pytest.raises(ValueError, match="16 hex chars"):
            mod_root("eden", title_id="010000000000100", mod_name="Mod", home=tmp_path)  # 15 chars
        with pytest.raises(ValueError, match="title id needed"):
            mod_root("eden", title_id="", mod_name="Mod", home=tmp_path)

    def test_invalid_title_id_dolphin_raises(self, tmp_path):
        with pytest.raises(ValueError, match="6 chars"):
            mod_root("dolphin", title_id="GAME1", mod_name="Mod", home=tmp_path)  # 5 chars
        with pytest.raises(ValueError, match="6 chars"):
            mod_root("dolphin", title_id="GAME012", mod_name="Mod", home=tmp_path)  # 7 chars
        with pytest.raises(ValueError, match="title id needed"):
            mod_root("dolphin", title_id="", mod_name="Mod", home=tmp_path)

    def test_cemu_ignores_title_id(self, tmp_path):
        result = mod_root("cemu", title_id="ANYTHING", mod_name="Mod", home=tmp_path)
        assert "ANYTHING" not in str(result)

    def test_unknown_emulator_raises_keyerror(self, tmp_path):
        with pytest.raises(KeyError):
            mod_root("unknown", title_id="0100000000001000", mod_name="Mod", home=tmp_path)

    def test_mod_name_sanitization(self, tmp_path):
        # Disallowed chars become underscore
        result = mod_root("eden", title_id="0100000000001000", mod_name="My@Mod#", home=tmp_path)
        assert "My_Mod_" in str(result)
        # Leading dots removed
        result = mod_root("eden", title_id="0100000000001000", mod_name=".hidden", home=tmp_path)
        assert "hidden" in str(result)
        # Max 80 chars
        long_name = "a" * 100
        result = mod_root("eden", title_id="0100000000001000", mod_name=long_name, home=tmp_path)
        assert len(os.path.basename(str(result))) == 80
        # Empty after sanitization raises (all leading dots)
        with pytest.raises(ValueError, match="empty after sanitization"):
            mod_root("eden", title_id="0100000000001000", mod_name="...", home=tmp_path)


class TestDetectLayout:
    """Tests for detect_layout function."""

    def test_switch_content_nested_titleid_mod_romfs(self):
        names = [
            "0100000000001000/",
            "0100000000001000/MyMod/",
            "0100000000001000/MyMod/romfs/",
            "0100000000001000/MyMod/romfs/file.txt",
        ]
        result = detect_layout(names)
        assert result["kind"] == "switch-content"
        assert result["root"] == "0100000000001000/MyMod/"
        assert result["title_id"] == "0100000000001000"

    def test_switch_content_flat_romfs(self):
        names = [
            "romfs/",
            "romfs/file.txt",
            "exefs/",
            "exefs/main.npdm",
        ]
        result = detect_layout(names)
        assert result["kind"] == "switch-content"
        assert result["root"] == ""
        assert result["title_id"] == ""

    def test_switch_content_with_title_id_in_path(self):
        names = [
            "0100000000001000/",
            "0100000000001000/romfs/",
            "0100000000001000/romfs/file.txt",
        ]
        result = detect_layout(names)
        assert result["kind"] == "switch-content"
        assert result["root"] == "0100000000001000/"
        assert result["title_id"] == "0100000000001000"

    def test_3ds_content_same_as_switch(self):
        names = [
            "0004000000123400/",
            "0004000000123400/romfs/",
            "0004000000123400/romfs/file.bin",
        ]
        result = detect_layout(names)
        assert result["kind"] == "switch-content"
        assert result["root"] == "0004000000123400/"
        assert result["title_id"] == "0004000000123400"

    def test_cemu_graphicpack_with_rules_txt(self):
        names = [
            "MyGraphicPack/",
            "MyGraphicPack/rules.txt",
            "MyGraphicPack/textures/",
            "MyGraphicPack/textures/tex.png",
        ]
        result = detect_layout(names)
        assert result["kind"] == "cemu-graphicpack"
        assert result["root"] == "MyGraphicPack/"
        assert result["title_id"] == ""

    def test_dolphin_textures_shortest_prefix(self):
        # Dolphin should find shortest prefix P where textures exist under P
        # and no romfs/exefs/rules.txt anywhere
        names = [
            "Textures/",
            "Textures/GAME01/",
            "Textures/GAME01/tex1.png",
            "Textures/GAME01/tex2.dds",
        ]
        result = detect_layout(names)
        assert result["kind"] == "dolphin-textures"
        # Dolphin packs ship as "<GAMEID>/...": that folder's contents go straight into Load/Textures/<GAMEID>/
        assert result["root"] == "Textures/GAME01/"
        assert result["title_id"] == "GAME01"

    def test_dolphin_textures_with_title_id_in_path(self):
        names = [
            "GAME01/",
            "GAME01/tex1.png",
            "GAME01/tex2.dds",
        ]
        result = detect_layout(names)
        assert result["kind"] == "dolphin-textures"
        assert result["root"] == "GAME01/"
        assert result["title_id"] == "GAME01"

    def test_dolphin_excludes_when_romfs_exists(self):
        # romfs triggers switch-content detection first
        names = [
            "Textures/",
            "Textures/tex.png",
            "romfs/",  # This triggers switch-content
            "romfs/file.txt",
        ]
        result = detect_layout(names)
        assert result["kind"] == "switch-content"
        assert result["root"] == ""

    def test_dolphin_excludes_when_exefs_exists(self):
        # exefs triggers switch-content detection first
        names = [
            "Textures/",
            "Textures/tex.png",
            "exefs/",
            "exefs/main.npdm",
        ]
        result = detect_layout(names)
        assert result["kind"] == "switch-content"
        assert result["root"] == ""

    def test_dolphin_excludes_when_rules_txt_exists(self):
        # rules.txt triggers cemu-graphicpack detection first
        names = [
            "Textures/",
            "Textures/tex.png",
            "rules.txt",  # This triggers cemu-graphicpack
        ]
        result = detect_layout(names)
        assert result["kind"] == "cemu-graphicpack"
        assert result["root"] == ""

    def test_dolphin_allows_cheats_folder(self):
        # cheats/ triggers switch-content detection (cheats is a switch-content marker)
        names = [
            "Textures/",
            "Textures/tex.png",
            "cheats/",
            "cheats/cheat.txt",
        ]
        result = detect_layout(names)
        assert result["kind"] == "switch-content"
        assert result["root"] == ""

    def test_unknown_layout(self):
        names = [
            "random/",
            "random/file.txt",
        ]
        result = detect_layout(names)
        assert result["kind"] == "unknown"
        assert result["root"] == ""
        assert result["title_id"] == ""


class TestPlanInstall:
    """Tests for plan_install function."""

    def _make_zip(self, tmp_path, names_and_content):
        """Create a zip file with given member names and content."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, content in names_and_content:
                if name.endswith("/"):
                    zf.writestr(name, "")
                else:
                    zf.writestr(name, content)
        return zip_path

    def test_plan_install_eden_success(self, tmp_path):
        home = tmp_path / "home"
        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"content"),
        ])
        plan = plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="MyMod", home=home)
        assert plan["emulator"] == "eden"
        assert plan["kind"] == "switch-content"
        assert len(plan["files"]) == 1
        assert plan["files"][0]["member"] == "romfs/file.txt"
        assert "0100000000001000/MyMod/romfs/file.txt" in plan["files"][0]["target"]
        assert plan["conflicts"] == []

    def test_plan_install_ryujinx_success(self, tmp_path):
        home = tmp_path / "home"
        zip_path = self._make_zip(tmp_path, [
            ("0100000000001000/", ""),
            ("0100000000001000/romfs/", ""),
            ("0100000000001000/romfs/file.txt", b"content"),
        ])
        plan = plan_install(zip_path, "ryujinx", title_id="", mod_name="MyMod", home=home)
        assert plan["emulator"] == "ryujinx"
        assert plan["kind"] == "switch-content"
        assert plan["title_id"] == "0100000000001000"  # from archive
        assert len(plan["files"]) == 1

    def test_plan_install_cemu_success(self, tmp_path):
        home = tmp_path / "home"
        zip_path = self._make_zip(tmp_path, [
            ("GraphicPack/", ""),
            ("GraphicPack/rules.txt", b"rules"),
            ("GraphicPack/textures/tex.png", b"pngdata"),
        ])
        plan = plan_install(zip_path, "cemu", title_id="", mod_name="GraphicPack", home=home)
        assert plan["emulator"] == "cemu"
        assert plan["kind"] == "cemu-graphicpack"
        assert len(plan["files"]) == 2  # rules.txt and tex.png

    def test_plan_install_dolphin_success(self, tmp_path):
        home = tmp_path / "home"
        zip_path = self._make_zip(tmp_path, [
            ("Textures/", ""),
            ("Textures/GAME01/", ""),
            ("Textures/GAME01/tex1.png", b"pngdata"),
        ])
        plan = plan_install(zip_path, "dolphin", title_id="GAME01", mod_name="TexPack", home=home)
        assert plan["emulator"] == "dolphin"
        assert plan["kind"] == "dolphin-textures"
        assert len(plan["files"]) == 1

    def test_plan_rejects_absolute_path(self, tmp_path):
        home = tmp_path / "home"
        zip_path = self._make_zip(tmp_path, [
            ("/absolute/path.txt", b"bad"),
        ])
        with pytest.raises(ValueError, match="absolute path"):
            plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="Mod", home=home)

    def test_plan_rejects_path_traversal(self, tmp_path):
        home = tmp_path / "home"
        zip_path = self._make_zip(tmp_path, [
            ("romfs/../escape.txt", b"bad"),
        ])
        with pytest.raises(ValueError, match="path traversal"):
            plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="Mod", home=home)

    def test_plan_rejects_backslash(self, tmp_path):
        home = tmp_path / "home"
        zip_path = self._make_zip(tmp_path, [
            ("romfs\\file.txt", b"bad"),
        ])
        with pytest.raises(ValueError, match="backslash"):
            plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="Mod", home=home)

    def test_plan_rejects_symlink(self, tmp_path):
        home = tmp_path / "home"
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            info = zipfile.ZipInfo("romfs/link.txt")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "target")
        with pytest.raises(ValueError, match="symlink"):
            plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="Mod", home=home)

    def test_kind_mismatch_error(self, tmp_path):
        home = tmp_path / "home"
        # Switch content zip for cemu
        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"content"),
        ])
        with pytest.raises(ValueError, match="looks like switch-content"):
            plan_install(zip_path, "cemu", title_id="", mod_name="Mod", home=home)

    def test_title_id_taken_from_archive(self, tmp_path):
        home = tmp_path / "home"
        zip_path = self._make_zip(tmp_path, [
            ("0100000000001000/", ""),
            ("0100000000001000/romfs/", ""),
            ("0100000000001000/romfs/file.txt", b"content"),
        ])
        plan = plan_install(zip_path, "eden", title_id="", mod_name="Mod", home=home)
        assert plan["title_id"] == "0100000000001000"

    def test_title_id_needed_error_when_missing(self, tmp_path):
        home = tmp_path / "home"
        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"content"),
        ])
        with pytest.raises(ValueError, match="title id needed"):
            plan_install(zip_path, "eden", title_id="", mod_name="Mod", home=home)

    def test_conflict_detection(self, tmp_path):
        home = tmp_path / "home"
        target_dir = home / ".local/share/eden/load/0100000000001000/MyMod/romfs"
        target_dir.mkdir(parents=True)
        (target_dir / "file.txt").write_bytes(b"different content")

        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"new content"),
        ])
        plan = plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="MyMod", home=home)
        assert len(plan["conflicts"]) == 1
        assert "file.txt" in plan["conflicts"][0]

    def test_identical_file_no_conflict(self, tmp_path):
        home = tmp_path / "home"
        target_dir = home / ".local/share/eden/load/0100000000001000/MyMod/romfs"
        target_dir.mkdir(parents=True)
        (target_dir / "file.txt").write_bytes(b"same content")

        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"same content"),
        ])
        plan = plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="MyMod", home=home)
        assert plan["conflicts"] == []


class TestApplyInstall:
    """Tests for apply_install function."""

    def _make_zip(self, tmp_path, names_and_content):
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, content in names_and_content:
                if name.endswith("/"):
                    zf.writestr(name, "")
                else:
                    zf.writestr(name, content)
        return zip_path

    def test_apply_writes_files_and_journal(self, tmp_path):
        home = tmp_path / "home"
        journal_dir = tmp_path / "journals"
        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"content"),
            ("romfs/sub/", ""),
            ("romfs/sub/file2.txt", b"content2"),
        ])
        plan = plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="MyMod", home=home)
        result = apply_install(plan, journal_dir=journal_dir)

        assert result["written"] == 2
        assert (journal_dir / f"{plan['plan_id']}.json").exists()

        # Verify files exist with correct content
        target_root = Path(plan["target_root"])
        assert (target_root / "romfs/file.txt").read_bytes() == b"content"
        assert (target_root / "romfs/sub/file2.txt").read_bytes() == b"content2"

        # Verify journal content
        with open(journal_dir / f"{plan['plan_id']}.json") as f:
            journal = json.load(f)
        assert journal["plan"]["plan_id"] == plan["plan_id"]
        assert len(journal["written"]) == 2
        assert len(journal["created_dirs"]) >= 1

    def test_apply_refuses_when_archive_changed(self, tmp_path):
        home = tmp_path / "home"
        journal_dir = tmp_path / "journals"
        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"content"),
        ])
        plan = plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="MyMod", home=home)

        # Modify the archive
        with zipfile.ZipFile(zip_path, "a") as zf:
            zf.writestr("romfs/extra.txt", b"extra")

        with pytest.raises(ValueError, match="archive changed since preview"):
            apply_install(plan, journal_dir=journal_dir)

    def test_apply_refuses_when_conflicts_exist(self, tmp_path):
        home = tmp_path / "home"
        journal_dir = tmp_path / "journals"
        target_dir = home / ".local/share/eden/load/0100000000001000/MyMod/romfs"
        target_dir.mkdir(parents=True)
        (target_dir / "file.txt").write_bytes(b"different")

        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"content"),
        ])
        plan = plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="MyMod", home=home)

        assert plan["conflicts"]
        with pytest.raises(ValueError, match="conflicts exist"):
            apply_install(plan, journal_dir=journal_dir)

    def test_apply_cleanup_on_failure(self, tmp_path):
        home = tmp_path / "home"
        journal_dir = tmp_path / "journals"
        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"content"),
        ])
        plan = plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="MyMod", home=home)

        # Corrupt the zip file.txt content to cause hash mismatch during apply
        # Recreate zip with corrupted content
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("romfs/", "")
            zf.writestr("romfs/file.txt", b"corrupted content")
        # Update plan's archive_sha256 to match corrupted zip so it passes initial check
        import hashlib
        plan["archive_sha256"] = hashlib.sha256(zip_path.read_bytes()).hexdigest()

        # But the member file.txt hash won't match (plan has original hash)
        with pytest.raises(ValueError, match="hash mismatch"):
            apply_install(plan, journal_dir=journal_dir)

        # Verify no files were left behind
        target_root = Path(plan["target_root"])
        assert not target_root.exists()


class TestUndoInstall:
    """Tests for undo_install function."""

    def _make_zip(self, tmp_path, names_and_content):
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, content in names_and_content:
                if name.endswith("/"):
                    zf.writestr(name, "")
                else:
                    zf.writestr(name, content)
        return zip_path

    def test_undo_removes_files_and_dirs(self, tmp_path):
        home = tmp_path / "home"
        journal_dir = tmp_path / "journals"
        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"content"),
            ("romfs/sub/", ""),
            ("romfs/sub/file2.txt", b"content2"),
        ])
        plan = plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="MyMod", home=home)
        apply_result = apply_install(plan, journal_dir=journal_dir)

        journal_path = Path(apply_result["journal"])
        result = undo_install(journal_path)

        assert result["removed"] == 2
        assert result["kept_changed"] == []

        # Files should be gone
        target_root = Path(plan["target_root"])
        assert not (target_root / "romfs/file.txt").exists()
        assert not (target_root / "romfs/sub/file2.txt").exists()

        # Journal should be renamed to .undone
        assert not journal_path.exists()
        assert journal_path.with_name(journal_path.stem + ".undone").exists()

    def test_undo_keeps_changed_file(self, tmp_path):
        home = tmp_path / "home"
        journal_dir = tmp_path / "journals"
        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/file.txt", b"original"),
        ])
        plan = plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="MyMod", home=home)
        apply_install(plan, journal_dir=journal_dir)

        # User modifies the file
        target_file = Path(plan["target_root"]) / "romfs/file.txt"
        target_file.write_bytes(b"modified")

        journal_path = journal_dir / f"{plan['plan_id']}.json"
        result = undo_install(journal_path)

        assert result["removed"] == 0
        assert len(result["kept_changed"]) == 1
        assert "file.txt" in result["kept_changed"][0]
        assert target_file.read_bytes() == b"modified"

    def test_undo_removes_created_dirs_deepest_first(self, tmp_path):
        home = tmp_path / "home"
        journal_dir = tmp_path / "journals"
        zip_path = self._make_zip(tmp_path, [
            ("romfs/", ""),
            ("romfs/deep/", ""),
            ("romfs/deep/nested/", ""),
            ("romfs/deep/nested/file.txt", b"content"),
        ])
        plan = plan_install(zip_path, "eden", title_id="0100000000001000", mod_name="MyMod", home=home)
        apply_install(plan, journal_dir=journal_dir)

        journal_path = journal_dir / f"{plan['plan_id']}.json"
        undo_install(journal_path)

        target_root = Path(plan["target_root"])
        # All created dirs should be removed if empty
        assert not target_root.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_dolphin_textures_at_zip_root(tmp_path):
    from native_services.emulator_mods import detect_layout
    result = detect_layout(["tex1.png", "sub/tex2.dds"])
    assert result["kind"] == "dolphin-textures" and result["root"] == ""
