"""Red Dead Redemption 2 RAGE Engine & Lenny's Mod Loader (LML) Package Stager.

Parses install.xml manifests, stages stream textures/models into lml/stream/,
routes ScriptHookRDR2 .asi files to game root, and verifies Proton
WINEDLLOVERRIDES="dinput8=n,b;vfs=n,b" execution environments.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class LmlFileMapping:
    source_rel: str
    target_rel: str
    is_stream: bool = False
    is_root: bool = False


@dataclass
class LmlPackageInfo:
    name: str
    author: str
    version: str
    description: str
    mappings: List[LmlFileMapping] = field(default_factory=list)
    raw_xml: str = ""


class Rdr2LmlStager:
    """Parser and stager for Red Dead Redemption 2 Lenny's Mod Loader (LML) packages."""

    @staticmethod
    def parse_install_xml(xml_content: str) -> Optional[LmlPackageInfo]:
        """Parse install.xml package manifest."""
        if not xml_content or not xml_content.strip():
            return None
        try:
            root = ET.fromstring(xml_content.strip())
        except ET.ParseError:
            return None

        name = root.findtext("Name") or root.findtext("name") or "Unnamed LML Package"
        author = root.findtext("Author") or root.findtext("author") or "Unknown"
        version = root.findtext("Version") or root.findtext("version") or "1.0.0"
        desc = root.findtext("Description") or root.findtext("description") or ""

        mappings: List[LmlFileMapping] = []
        files_elem = root.find("Files")
        if files_elem is None:
            files_elem = root.find("files")
        if files_elem is not None:
            for item in files_elem.findall("File") + files_elem.findall("file"):
                from_path = item.get("from") or item.get("From") or item.text or ""
                to_path = item.get("to") or item.get("To") or from_path
                if from_path:
                    clean_from = from_path.replace("\\", "/").strip("/")
                    clean_to = to_path.replace("\\", "/").strip("/")
                    is_stream = clean_to.startswith("stream/") or "stream" in clean_to
                    is_root = clean_to.startswith("replace/") or Path(clean_to).suffix.lower() == ".asi"
                    mappings.append(
                        LmlFileMapping(
                            source_rel=clean_from,
                            target_rel=clean_to,
                            is_stream=is_stream,
                            is_root=is_root,
                        )
                    )

        return LmlPackageInfo(
            name=name,
            author=author,
            version=version,
            description=desc,
            mappings=mappings,
            raw_xml=xml_content,
        )

    @classmethod
    def classify_rdr2_files(cls, file_list: List[str]) -> Dict[str, str]:
        """Classify a list of files in an archive and route to destination subpaths."""
        routes: Dict[str, str] = {}
        has_install_xml = any(Path(f).name.lower() == "install.xml" for f in file_list)

        for f in file_list:
            norm = f.replace("\\", "/").strip("/")
            p = Path(norm)
            ext = p.suffix.lower()

            if p.name.lower() == "install.xml":
                routes[norm] = f"lml/{p.parent.name or 'Package'}/install.xml"
            elif ext in (".asi", ".ini") and "lml" not in norm.lower():
                # Root ScriptHook plugin
                routes[norm] = p.name
            elif "stream" in norm.lower() or ext in (".ydr", ".ytd", ".yft", ".ybn"):
                # Texture / model stream asset
                routes[norm] = f"lml/stream/{p.name}"
            elif norm.lower().startswith("lml/"):
                routes[norm] = norm
            else:
                # Default LML package asset
                routes[norm] = f"lml/{norm}"

        return routes

    @staticmethod
    def get_proton_environment() -> Dict[str, str]:
        """Proton Wine DLL overrides required for ScriptHookRDR2 and LML."""
        return {
            "WINEDLLOVERRIDES": "dinput8=n,b;vfs=n,b;ScriptHookRDR2=n,b"
        }
