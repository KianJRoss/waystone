"""Per-game modding knowledge shipped with Waystone (game_mod_recipes.json): sites, frameworks, paths, Proton
launch options and which manager to use. Matched to library titles exactly (sequels never match)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from native_services.game_installs import normalize_title

DATA = Path(__file__).with_name("game_mod_recipes.json")


@lru_cache(maxsize=1)
def recipes() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))["games"]


def recipe_for(title: str) -> dict | None:
    key = normalize_title(title or "")
    if not key:
        return None
    for rid, recipe in recipes().items():
        if any(normalize_title(t) == key for t in recipe.get("titles", [])):
            return {"id": rid, **recipe}
    return None
