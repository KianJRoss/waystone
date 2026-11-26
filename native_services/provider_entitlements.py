"""Native entitlement extraction from local Heroic, Legendary, GOGdl, and Nile store caches.

Provides read-only parsing of local store cache JSON files for Epic Games, GOG, and Amazon Games
to populate download pickers and installation targets without requiring manual product ID entry.

Design Principles & Security Hardening:
- Read-Only & Pure: Never writes to cache files, config files, or store state.
- Constructor Safety: __init__ performs zero filesystem scanning, file reads, or side effects.
- Strict File Whitelisting: Only explicitly whitelisted relative cache targets under the verified
  config root can be read.
- Sensitive File Blacklisting: Explicitly rejects sensitive files (e.g. auth.json, user.json, tokens).
- Path Traversal Defense: Verifies all resolved paths remain strictly within the designated root.
- Strict Resource Limits: Enforces maximum file size caps (10 MiB), record count caps (10,000),
  and string field length limits (512 chars) to prevent resource exhaustion.
- Genuine Store IDs: Extracts native store identifiers (e.g. Legendary app_name, GOG numeric IDs,
  Nile product IDs). Never synthesizes or hashes product IDs from game titles. Never treats
  Playnite/canonical GUIDs as store product IDs.
- Honest Ownership Attribution: Explicitly models all records as 'cached_entitlement', making it clear
  that cached local metadata presence does NOT guarantee active live authentication or current entitlement.
- Provider Isolation: Keeps entitlements with identical IDs across different stores distinct.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple
from native_services import waystone_paths as _wpaths

logger = logging.getLogger("waystone.provider_entitlements")

SUPPORTED_PROVIDERS = frozenset({"epic", "gog", "amazon"})
GOG_NUMERIC_ID_REGEX = re.compile(r"^[1-9][0-9]{0,19}$")
SAFE_ID_REGEX = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. -]{0,127}$")

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB cap per cache file
MAX_ENTITLEMENT_RECORDS = 10_000   # Cap on total parsed records
MAX_FIELD_LENGTH = 512              # Cap on string fields

# Whitelisted relative subpaths per provider under the config root
ALLOWED_CACHE_TARGETS: Mapping[str, Tuple[Path, ...]] = {
    "epic": (
        Path("epic/store_cache/legendary_library.json"),
        Path("epic/legendary/installed.json"),
        # Explicitly selected legacy cache roots remain importable, but are not defaults.
        Path("store_cache/legendary_library.json"),
        Path("legendaryConfig/legendary/installed.json"),
    ),
    "gog": (
        Path("gog/store_cache/gog_library.json"),
        Path("gog/installed.json"),
        Path("store_cache/gog_library.json"),
        Path("gog_store/installed.json"),
    ),
    "amazon": (
        Path("amazon/store_cache/nile_library.json"),
        Path("amazon/nile/installed.json"),
        Path("amazon/nile/library.json"),
        Path("store_cache/nile_library.json"),
        Path("nile_store/installed.json"),
        Path("nile_config/installed.json"),
    ),
}

# Blacklist of sensitive file name components to prevent accidental credential leakage
SENSITIVE_FILENAME_PATTERNS = frozenset({
    "auth.json", "user.json", "tokens.json", "token.json", "cookies",
    "credentials", "config.ini", "config.json", "savedtimestamppassword"
})


class ProviderEntitlementsError(ValueError):
    """Base error for provider entitlement operations."""


class ProviderCacheSecurityError(ProviderEntitlementsError):
    """Raised when an invalid, sensitive, or traversing path is requested."""


@dataclass(frozen=True)
class ProviderEntitlement:
    """Detached, immutable entitlement record parsed from local store metadata.

    IMPORTANT: A cached entitlement claim indicates historical presence in local
    store metadata or installed state; it does NOT attest to live authentication
    or current active ownership in external store accounts.
    """
    provider: str                      # 'epic', 'gog', or 'amazon'
    product_id: str                    # Provider-native product identifier
    title: str                         # Sanitized display title
    cache_provenance: str              # Relative path of source cache file
    observed_at: str                   # ISO-8601 UTC timestamp of parsing
    is_installed: Optional[bool] = None # Cached installation status if known
    ownership_confidence: str = "cached_entitlement"  # Explicit confidence indicator

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Invalid provider '{self.provider}'.")
        if not self.product_id or len(self.product_id) > MAX_FIELD_LENGTH:
            raise ValueError("Product ID must be a non-empty string within length bounds.")
        if not self.title or len(self.title) > MAX_FIELD_LENGTH:
            raise ValueError("Title must be a non-empty string within length bounds.")


class ProviderEntitlementsReader:
    """Read-only extractor for cached provider entitlements from Heroic store metadata."""

    def __init__(self, default_config_root: Optional[Path] = None) -> None:
        """Initializes reader. Performs ZERO filesystem scanning, file reads, or side effects."""
        self._default_config_root: Path = (
            Path(default_config_root).resolve()
            if default_config_root
            else _wpaths.store("providers-v1").resolve()
        )

    @property
    def default_config_root(self) -> Path:
        return self._default_config_root

    def load_entitlements(
        self,
        provider: str,
        config_root: Optional[Path] = None,
    ) -> List[ProviderEntitlement]:
        """Loads and parses cached entitlements for a single provider.

        Strictly inspects only whitelisted cache files. Never reads auth or token files.
        """
        if provider not in SUPPORTED_PROVIDERS:
            raise ProviderEntitlementsError(
                f"Unsupported provider '{provider}'. Must be one of {sorted(SUPPORTED_PROVIDERS)}."
            )

        root = Path(config_root).resolve() if config_root else self._default_config_root
        if not root.is_dir():
            logger.debug("Provider config root '%s' does not exist or is not a directory.", root)
            return []

        observed_at = datetime.now(timezone.utc).isoformat()
        targets = ALLOWED_CACHE_TARGETS.get(provider, ())
        
        # Deduplicated map by product_id preserving richness
        records: Dict[str, ProviderEntitlement] = {}

        for rel_path in targets:
            full_path = (root / rel_path).resolve()
            # Enforce path containment
            try:
                full_path.relative_to(root)
            except ValueError:
                raise ProviderCacheSecurityError(f"Path '{rel_path}' attempts directory traversal.")

            # Enforce sensitive filename blacklist
            if full_path.name.lower() in SENSITIVE_FILENAME_PATTERNS:
                raise ProviderCacheSecurityError(f"Access to sensitive file '{full_path.name}' is forbidden.")

            if not full_path.is_file():
                continue

            # Enforce file size limit
            try:
                st = full_path.stat()
                if st.st_size > MAX_FILE_BYTES:
                    logger.warning("Cache file '%s' exceeds %d bytes; skipping.", rel_path, MAX_FILE_BYTES)
                    continue
                if st.st_size == 0:
                    continue
            except OSError:
                continue

            # Read and parse JSON safely
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                data = json.loads(content)
            except Exception as exc:
                logger.debug("Failed to parse cache JSON '%s': %s", rel_path, exc)
                continue

            parsed_items = self._parse_target_data(provider, str(rel_path), data, observed_at)
            for item in parsed_items:
                if len(records) >= MAX_ENTITLEMENT_RECORDS:
                    break
                pid = item.product_id
                if pid not in records:
                    records[pid] = item
                else:
                    # Merge status: if installed in any cache, mark installed
                    existing = records[pid]
                    merged_installed = existing.is_installed or item.is_installed
                    if merged_installed != existing.is_installed:
                        records[pid] = ProviderEntitlement(
                            provider=existing.provider,
                            product_id=existing.product_id,
                            title=existing.title or item.title,
                            cache_provenance=f"{existing.cache_provenance}, {item.cache_provenance}",
                            observed_at=observed_at,
                            is_installed=merged_installed,
                            ownership_confidence=existing.ownership_confidence,
                        )

        return list(records.values())

    def load_all_providers(
        self,
        config_root: Optional[Path] = None,
    ) -> Dict[str, List[ProviderEntitlement]]:
        """Loads cached entitlements across all supported providers (epic, gog, amazon)."""
        root = Path(config_root).resolve() if config_root else self._default_config_root
        result: Dict[str, List[ProviderEntitlement]] = {}
        for p in sorted(SUPPORTED_PROVIDERS):
            result[p] = self.load_entitlements(p, config_root=root)
        return result

    def search_entitlements(
        self,
        query: str,
        provider: Optional[str] = None,
        config_root: Optional[Path] = None,
        limit: int = 50,
    ) -> List[ProviderEntitlement]:
        """Performs a substring search against title and product_id without network access."""
        query_norm = (query or "").strip().lower()
        if not query_norm:
            return []

        providers_to_check = [provider] if provider else sorted(SUPPORTED_PROVIDERS)
        matches: List[ProviderEntitlement] = []

        for p in providers_to_check:
            items = self.load_entitlements(p, config_root=config_root)
            for it in items:
                if query_norm in it.title.lower() or query_norm in it.product_id.lower():
                    matches.append(it)
                    if len(matches) >= limit:
                        return matches

        return matches

    def _parse_target_data(
        self,
        provider: str,
        rel_path: str,
        data: Any,
        observed_at: str,
    ) -> List[ProviderEntitlement]:
        """Dispatches data parsing according to provider schema."""
        if provider == "epic":
            return self._parse_epic_cache(rel_path, data, observed_at)
        elif provider == "gog":
            return self._parse_gog_cache(rel_path, data, observed_at)
        elif provider == "amazon":
            return self._parse_amazon_cache(rel_path, data, observed_at)
        return []

    def _parse_epic_cache(
        self,
        rel_path: str,
        data: Any,
        observed_at: str,
    ) -> List[ProviderEntitlement]:
        """Parses Legendary library and installed cache files."""
        results: List[ProviderEntitlement] = []
        raw_list: Sequence[Any] = []

        if isinstance(data, dict):
            if "library" in data and isinstance(data["library"], list):
                raw_list = data["library"]
            else:
                # Could be installed.json keyed by app_name
                raw_list = list(data.values())
        elif isinstance(data, list):
            raw_list = data

        for item in raw_list:
            if not isinstance(item, dict):
                continue
            app_name = item.get("app_name") or item.get("appName")
            title = item.get("title")
            if not app_name or not title:
                continue

            pid = str(app_name).strip()
            display_title = str(title).strip()

            # Validate ID safety: reject option prefixes or traversal
            if pid.startswith(("-", "/")) or not SAFE_ID_REGEX.fullmatch(pid):
                continue
            if not display_title:
                continue

            is_inst = item.get("is_installed")
            if is_inst is None and "installed" in rel_path.lower():
                is_inst = True

            results.append(
                ProviderEntitlement(
                    provider="epic",
                    product_id=pid[:MAX_FIELD_LENGTH],
                    title=display_title[:MAX_FIELD_LENGTH],
                    cache_provenance=rel_path,
                    observed_at=observed_at,
                    is_installed=bool(is_inst) if is_inst is not None else None,
                )
            )
        return results

    def _parse_gog_cache(
        self,
        rel_path: str,
        data: Any,
        observed_at: str,
    ) -> List[ProviderEntitlement]:
        """Parses GOGdl library and installed cache files."""
        results: List[ProviderEntitlement] = []
        raw_list: Sequence[Any] = []

        if isinstance(data, dict):
            if "games" in data and isinstance(data["games"], list):
                raw_list = data["games"]
            elif "installed" in data and isinstance(data["installed"], list):
                raw_list = data["installed"]
            elif "library" in data and isinstance(data["library"], list):
                raw_list = data["library"]
            else:
                raw_list = list(data.values())
        elif isinstance(data, list):
            raw_list = data

        for item in raw_list:
            if not isinstance(item, dict):
                continue

            # GOG product ID can be app_name, appName, or id
            raw_id = item.get("app_name") or item.get("appName") or item.get("id")
            title = item.get("title")
            if not raw_id or not title:
                continue

            pid = str(raw_id).strip()
            display_title = str(title).strip()

            # Enforce strict positive integer GOG ID
            if not GOG_NUMERIC_ID_REGEX.fullmatch(pid):
                continue
            if not display_title:
                continue

            is_inst = item.get("is_installed")
            if is_inst is None and "installed" in rel_path.lower():
                is_inst = True

            results.append(
                ProviderEntitlement(
                    provider="gog",
                    product_id=pid[:MAX_FIELD_LENGTH],
                    title=display_title[:MAX_FIELD_LENGTH],
                    cache_provenance=rel_path,
                    observed_at=observed_at,
                    is_installed=bool(is_inst) if is_inst is not None else None,
                )
            )
        return results

    def _parse_amazon_cache(
        self,
        rel_path: str,
        data: Any,
        observed_at: str,
    ) -> List[ProviderEntitlement]:
        """Parses Nile library and installed cache files."""
        results: List[ProviderEntitlement] = []
        raw_list: Sequence[Any] = []

        if isinstance(data, dict):
            if "library" in data and isinstance(data["library"], list):
                raw_list = data["library"]
            elif "games" in data and isinstance(data["games"], list):
                raw_list = data["games"]
            elif "installed" in data and isinstance(data["installed"], list):
                raw_list = data["installed"]
            else:
                raw_list = list(data.values())
        elif isinstance(data, list):
            raw_list = data

        for item in raw_list:
            if not isinstance(item, dict):
                continue

            # Nile product ID can be id, product_id, app_name, or appName
            raw_id = item.get("id") or item.get("product_id") or item.get("app_name") or item.get("appName")
            title = item.get("title")
            if not raw_id or not title:
                continue

            pid = str(raw_id).strip()
            display_title = str(title).strip()

            # Validate ID: reject option prefixes, traversal, or invalid chars
            if pid.startswith(("-", "/")) or not SAFE_ID_REGEX.fullmatch(pid):
                continue
            if not display_title:
                continue

            is_inst = item.get("is_installed")
            if is_inst is None and "installed" in rel_path.lower():
                is_inst = True

            results.append(
                ProviderEntitlement(
                    provider="amazon",
                    product_id=pid[:MAX_FIELD_LENGTH],
                    title=display_title[:MAX_FIELD_LENGTH],
                    cache_provenance=rel_path,
                    observed_at=observed_at,
                    is_installed=bool(is_inst) if is_inst is not None else None,
                )
            )
        return results
