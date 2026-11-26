#!/usr/bin/env python3
"""Add (or update) a local library entry in Waystone plus its launch profile.

Example (Nolvus as a Skyrim instance launched straight through GE-Proton):
  add_local_game.py --id local:nolvus-ascension --title "Skyrim SE: Nolvus Ascension" --based-on steam:489830 \
      --tag Modlist --exe ~/.local/share/Steam/compatibilitytools.d/GE-Proton10-14/proton \
      --arg run --arg "/mnt/games/.../ModOrganizer.exe" --arg "moshortcut://:SKSE" \
      --cwd /mnt/games/.../MO2 --env STEAM_COMPAT_DATA_PATH=... --env SteamAppId=489830

Writes the entry to the local games file and an app-owned `native_game` launch profile (provider `local`)
associated with the entry id. Restart Waystone (or its service) to see the entry.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_services import local_games, waystone_paths as _wpaths  # noqa: E402
from native_services.launch_profiles import LaunchProfile, LaunchProfileStore  # noqa: E402

ROOT = _wpaths.data_dir()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--based-on", default="")
    ap.add_argument("--platform", default="PC (Windows)")
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--notes", default="")
    ap.add_argument("--exe", required=True, help="absolute path of the program to run")
    ap.add_argument("--arg", action="append", default=[], help="one argument (repeat; no shell)")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE (repeat)")
    ap.add_argument("--status-file", default="", help="status file a launcher wrapper writes (shown next to Play)")
    ap.add_argument("--local-games", type=Path, default=ROOT / "local-games-v1.json")
    ap.add_argument("--profiles-dir", type=Path, default=ROOT / "launch-profiles-v1")
    a = ap.parse_args(argv)

    exe = Path(a.exe).expanduser()
    if not exe.is_absolute() or not exe.is_file():
        ap.error(f"--exe must be an existing absolute file: {exe}")
    env = {}
    for pair in a.env:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            ap.error(f"--env needs KEY=VALUE, got {pair!r}")
        env[key] = value

    entry = {"id": a.id, "title": a.title, "platform": a.platform, "tags": a.tag}
    if a.based_on:
        entry["based_on"] = a.based_on
    if a.notes:
        entry["notes"] = a.notes
    local_games.upsert(a.local_games, entry)

    store = LaunchProfileStore(a.profiles_dir)   # loads; every change below is saved atomically
    profile = LaunchProfile(
        profile_id="local-" + a.id.split(":", 1)[1], display_name=a.title, target_type="native_game",
        platform_or_provider=local_games.PROVIDER, executable=str(exe), argv_template=tuple(a.arg),
        cwd=a.cwd, env=env, extra_metadata={"local_game": a.id, **({"status_file": a.status_file} if a.status_file else {})})
    if store.get_profile(profile.profile_id):
        store.update_profile(profile)
    else:
        store.create_profile(profile)
    store.set_game_override(a.id, profile.profile_id)
    print(f"added {a.id} ({a.title}) -> profile {profile.profile_id}; restart Waystone to see it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
