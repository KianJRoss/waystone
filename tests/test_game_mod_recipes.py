from native_services.game_mod_recipes import recipe_for, recipes


def test_every_recipe_is_complete():
    for rid, r in recipes().items():
        assert r["titles"] and r.get("manager") in ("waystone", "mo2", "prism"), rid
        for f in r.get("frameworks", []):
            assert f.get("name") and (f.get("nexus_id") or f.get("url")), (rid, f)


def test_titles_match_exactly_and_sequels_do_not():
    assert recipe_for("Cyberpunk 2077")["nexus_domain"] == "cyberpunk2077"
    assert recipe_for("The Elder Scrolls V: Skyrim Special Edition")["manager"] == "mo2"
    assert recipe_for("Subnautica: Below Zero")["id"] == "subnauticabelowzero"
    assert recipe_for("Subnautica")["id"] == "subnautica"
    assert recipe_for("Slime Rancher 2")["id"] == "slimerancher2"
    assert recipe_for("Fallout 3") is None
    assert recipe_for("Minecraft for Windows") is None   # Bedrock: Java mods do not apply
