# Third-party software

Waystone is licensed under the GNU Affero General Public License v3.0 (see `LICENSE`). It works with, downloads or
includes the following third-party software, each under its own license.

## Downloaded on first use (not included in Waystone)
Waystone downloads these official release builds from GitHub the first time they are needed, checks each against a
pinned SHA-256 checksum, and runs them as separate programs (`native_services/provider_engines.py`).

| Program | Used for | License | Source |
|---|---|---|---|
| legendary 0.20.43 (Heroic fork) | Epic Games Store sign-in, installs, updates | GPL-3.0 | https://github.com/Heroic-Games-Launcher/legendary |
| gogdl 1.3.0 | GOG sign-in, installs, updates | GPL-3.0 | https://github.com/Heroic-Games-Launcher/heroic-gogdl |
| nile 1.2.0 | Amazon Games sign-in, installs, updates | GPL-3.0 | https://github.com/imLinguin/nile |
| GE-Proton (user's choice of version) | running Windows games | BSD-3-Clause and component licenses | https://github.com/GloriousEggroll/proton-ge-custom |

## Included
| Component | Where | License |
|---|---|---|
| Herald SDK | `components/herald_sdk` | AGPL-3.0 |
| python-dotenv | `components/herald_sdk/python_dotenv-*` | BSD-3-Clause |
| qrcode | `components/waystone_dependencies/qrcode*` | BSD (see its dist-info) |
| Xelu controller prompts | `components/controller_studio/src/upstream/assets/xelu-prompts` | CC0 1.0 |
| Xodus voice adapter | `adapters/xodus_voice` | MIT |
| Avalonia 11 (controller studio UI) | NuGet, at build time | MIT |
| Tmds.DBus.Protocol | NuGet, at build time | MIT |

## Required on the system (installed by the package manager)
Python 3 with PySide6 (Qt for Python, LGPL-3.0), the .NET 10 runtime (MIT), libevdev (MIT).

## Used, not included
Heroic Games Launcher, Steam, Lutris, Deadband, AreWeAntiCheatYet data (downloaded at runtime, MIT), SteamGridDB
(user's own API key).
