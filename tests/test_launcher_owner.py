import json
from pathlib import Path

from native_services.launcher_owner import (
    HIDDEN_WORKSPACE,
    Hyprland,
    WindowOwnership,
    atomic_status_write,
    matching_game_processes,
    process_records,
    process_snapshot,
    process_tree,
    pids_with_env,
)
from native_services.launch_profiles import LaunchIntent
from native_services.launcher_owner import build_owned_launch_intent


class FakeHypr:
    def __init__(self):
        self.moves = []
        self.places = []

    def move(self, address, workspace):
        self.moves.append((address, workspace))

    def place(self, address, monitor, fullscreen, *, follow=True):
        self.places.append((address, monitor, fullscreen, follow))

    def clients(self):
        return []


def _proc(root: Path, pid: int, *, ppid: int = 1, comm: str = "game", cmdline="/games/game",
          env: bytes = b""):
    directory = root / str(pid)
    directory.mkdir(parents=True)
    # Linux stat fields after the closing comm paren: state, ppid, ..., starttime (field 22).
    fields = ["S", str(ppid)] + ["0"] * 17 + ["12345"]
    (directory / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields))
    (directory / "cmdline").write_bytes(cmdline.encode() + b"\0")
    (directory / "environ").write_bytes(env)


def test_process_tree_and_snapshot_are_fixture_only(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    _proc(proc, 10, ppid=1, comm="owner")
    _proc(proc, 11, ppid=10, comm="game")
    _proc(proc, 12, ppid=11, comm="child")
    snapshot = process_snapshot(proc)
    assert process_tree(10, snapshot) == {10, 11, 12}
    assert process_tree(999, snapshot) == set()


def test_env_reader_only_matches_named_value(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    _proc(proc, 20, env=b"SteamAppId=123\0SECRET=do-not-read\0")
    _proc(proc, 21, env=b"SteamAppId=456\0")
    assert pids_with_env("SteamAppId", "123", [20, 21], proc) == {20}


def test_process_records_allowlists_env_and_steam_match_excludes_client(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    _proc(proc, 30, comm="game.exe", env=b"SteamAppId=42\0TOKEN=not-retained\0")
    _proc(proc, 31, comm="steamwebhelper", env=b"SteamAppId=42\0")
    records = process_records(proc)
    assert records[30].env == {"SteamAppId": "42"}
    assert matching_game_processes(records, provider="steam", game_id="42") == {30}


def test_window_ownership_hides_only_owned_non_game_clients_and_restores_original():
    backend = FakeHypr()
    owner = WindowOwnership(backend)
    clients = [
        {"address": "heroic", "pid": 7, "workspace": {"name": "3"}, "monitor": "DP-1", "fullscreen": True},
        {"address": "game", "pid": 8, "workspace": {"name": "2"}},
        {"address": "other", "pid": 9, "workspace": {"name": "1"}},
    ]
    hidden = owner.hide(clients, {7, 8}, exclude_pids={8})
    assert hidden == {"heroic"}
    assert backend.moves == [("heroic", HIDDEN_WORKSPACE)]
    assert owner.restore(clients) == {"heroic"}
    assert backend.moves[-1] == ("heroic", "3")
    assert backend.places == [("heroic", "DP-1", False, False)]


def test_window_owner_can_reveal_to_current_workspace_without_monitor_follow():
    backend = FakeHypr()
    owner = WindowOwnership(backend)
    clients = [{"address": "mo2", "pid": 5, "workspace": {"name": "2"}}]
    owner.hide(clients, {5})
    owner.restore(clients, destination="4")
    assert backend.moves == [("mo2", HIDDEN_WORKSPACE), ("mo2", "4")]
    assert backend.places == []


def test_atomic_status_is_complete_json(tmp_path):
    path = tmp_path / "status" / "session.json"
    atomic_status_write(path, {"stage": "playing", "text": "Playing", "extra": {"pid": 9}})
    assert json.loads(path.read_text()) == {"stage": "playing", "text": "Playing", "extra": {"pid": 9}}
    assert list(path.parent.iterdir()) == [path]


def test_hyprland_falls_back_to_noop_without_compositor():
    backend = Hyprland(enabled=False, run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert backend.clients() == []
    backend.move("0x123", HIDDEN_WORKSPACE)
    backend.place("0x123", "DP-1", True)
    assert backend.active_workspace() == "1"


def test_reviewed_uri_launch_intents_wrap_only_matching_game_identity(tmp_path):
    steam = LaunchIntent("steam-profile", "42", None, "/usr/bin/steam",
                         ("/usr/bin/steam", "-silent", "steam://rungameid/42"), None, {},
                         "workstation", True)
    wrapped = build_owned_launch_intent(steam, provider="steam", game_id="42",
                                        status_file=tmp_path / "steam.json", game_folder="/games/steam")
    assert wrapped is not None
    assert wrapped.executable == __import__("sys").executable
    assert wrapped.argv[0] == wrapped.executable
    assert wrapped.argv[1].endswith("native_services/launcher_owner_runtime.py")
    assert "-silent" in wrapped.argv[wrapped.argv.index("--launch-args-json") + 1]
    assert build_owned_launch_intent(steam, provider="steam", game_id="43",
                                     status_file=tmp_path / "wrong.json") is None

    heroic = LaunchIntent("heroic-profile", "epic-1", None, "/opt/heroic",
                          ("/opt/heroic", "heroic://launch?appName=epic-1&runner=legendary"), None, {},
                          "workstation", True)
    assert build_owned_launch_intent(heroic, provider="heroic-epic", game_id="epic-1",
                                     status_file=tmp_path / "heroic.json") is not None
    sideload = LaunchIntent("heroic-sideload", "sid-1", None, "/usr/bin/heroic",
                            ("/usr/bin/heroic", "heroic://launch?appName=sid-1&runner=sideload"),
                            None, {}, "workstation", True)
    sideload_wrapped = build_owned_launch_intent(
        sideload, provider='heroic-sideload', game_id='sid-1',
        status_file=tmp_path / 'heroic-sideload.json', game_folder='/games/manual')
    assert sideload_wrapped is not None
    args = sideload_wrapped.argv[sideload_wrapped.argv.index('--launch-args-json') + 1]
    assert 'runner=sideload' in args
    assert sideload_wrapped.argv[sideload_wrapped.argv.index('--game-folder') + 1] == '/games/manual'
    direct_backend = LaunchIntent("waystone-epic", "epic-1", None, "/waystone/legendary",
                                  ("/waystone/legendary", "launch", "epic-1"), None, {},
                                  "workstation", True)
    assert build_owned_launch_intent(direct_backend, provider="heroic-epic", game_id="epic-1",
                                     status_file=tmp_path / "direct.json") is None


def test_old_owner_status_files_are_pruned_but_other_status_files_are_kept(tmp_path):
    import os, sys, time
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "craft-projects/playnite-desktop/app"))
    from launch_view import _prune_owner_status_files
    old = tmp_path / "steam-42-0123456789ab.json"
    new = tmp_path / "steam-43-0123456789ac.json"
    mo2 = tmp_path / "nolvus.json"
    for f in (old, new, mo2):
        f.write_text("{}")
    long_ago = time.time() - 10 * 86400
    os.utime(old, (long_ago, long_ago))
    os.utime(mo2, (long_ago, long_ago))
    _prune_owner_status_files(tmp_path)
    assert not old.exists() and new.exists() and mo2.exists()


def test_monitor_and_its_parents_are_never_the_game_even_with_the_folder_in_argv():
    from native_services.launcher_owner import ProcessRecord, ancestors, matching_game_processes
    folder = "/games/rocketleague"
    records = {
        1: ProcessRecord(1, 0, "systemd", 1, ("systemd",), None, "/", {}),
        10: ProcessRecord(10, 1, "python", 2, ("python", "app.py"), None, "/", {}),
        20: ProcessRecord(20, 10, "python", 3, ("python", "launcher_owner_runtime.py", "--game-folder", folder),
                          None, "/", {}),
        30: ProcessRecord(30, 20, "heroic", 4, ("/opt/Heroic/heroic",), "/opt/Heroic/heroic", "/", {}),
        40: ProcessRecord(40, 1, "RocketLeague.exe", 5, (f"{folder}/Binaries/Win64/RocketLeague.exe",),
                          None, folder, {}),
    }
    mine = ancestors(20, records)
    assert mine == {20, 10, 1}
    assert matching_game_processes(records, provider="heroic", game_id="Sugar", game_folder=folder,
                                   exclude_pids=mine) == {40}


def test_protonfixes_progress_window_is_not_the_game():
    from native_services.launcher_owner import ProcessRecord, matching_game_processes
    folder = "/games/rl"
    records = {
        50: ProcessRecord(50, 1, "zenity", 1, ("zenity", "--progress", "--title=ProtonFixes"), "/usr/bin/zenity", folder, {}),
        60: ProcessRecord(60, 1, "RocketLeague.exe", 2, (f"{folder}/RocketLeague.exe",), None, folder, {}),
    }
    assert matching_game_processes(records, provider="heroic", game_id="Sugar", game_folder=folder) == {60}


def test_hyprland_event_socket_reports_new_windows(tmp_path):
    import socket
    import threading
    from native_services.launcher_owner import Hyprland
    sig = "abc"
    sock_dir = tmp_path / "hypr" / sig
    sock_dir.mkdir(parents=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_dir / ".socket2.sock"))
    server.listen(1)
    seen = []
    got = threading.Event()
    hypr = Hyprland(enabled=True, environ={"XDG_RUNTIME_DIR": str(tmp_path), "HYPRLAND_INSTANCE_SIGNATURE": sig})
    stop = hypr.watch_new_windows(lambda a, c, w: (seen.append((a, c, w)), got.set()))
    assert stop is not None
    conn, _ = server.accept()
    conn.sendall(b"activewindow>>x,y\nopenwindow>>55aa,1,heroic,Heroic Games Launcher\n")
    assert got.wait(2)
    stop()
    conn.close()
    server.close()
    assert seen == [("0x55aa", "heroic", "1")]


def test_no_event_socket_means_polling_only(tmp_path):
    from native_services.launcher_owner import Hyprland
    hypr = Hyprland(enabled=True, environ={"XDG_RUNTIME_DIR": str(tmp_path), "HYPRLAND_INSTANCE_SIGNATURE": "none"})
    assert hypr.watch_new_windows(lambda *a: None) is None


def test_heroic_install_info_reads_heroic_install_lists(tmp_path):
    import json as _json
    from native_services.launcher_owner import heroic_install_info
    legendary = tmp_path / ".config/heroic/legendaryConfig/legendary"
    legendary.mkdir(parents=True)
    (legendary / "installed.json").write_text(_json.dumps({"Sugar": {
        "install_path": "/games/rocketleague", "executable": "Binaries/Win64/Launcher.exe"}}))
    gog = tmp_path / ".var/app/com.heroicgameslauncher.hgl/config/heroic/gog_store"
    gog.mkdir(parents=True)
    (gog / "installed.json").write_text(_json.dumps({"installed": [{"appName": "1207658924", "install_path": "/games/gog"}]}))
    assert heroic_install_info("Sugar", home=tmp_path) == {
        "install_path": "/games/rocketleague", "executable": "/games/rocketleague/Binaries/Win64/Launcher.exe"}
    assert heroic_install_info("1207658924", home=tmp_path)["install_path"] == "/games/gog"
    assert heroic_install_info("nope", home=tmp_path) == {}


def test_heroic_install_info_reads_exact_sideload_identity_and_executable(tmp_path):
    from native_services.launcher_owner import heroic_install_info
    game = tmp_path / 'Games' / 'sid'
    game.mkdir(parents=True)
    exe = game / 'game.exe'
    exe.write_bytes(b'fixture')
    path = tmp_path / '.config/heroic/sideload_apps/library.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'games': [
        {'runner': 'sideload', 'app_name': 'sid-abc', 'title': 'Manual',
         'folder_name': str(game), 'is_installed': True,
         'install': {'executable': 'game.exe'}}]}))
    assert heroic_install_info('sid-abc', home=tmp_path) == {
        'install_path': str(game), 'executable': str(exe)}
    assert heroic_install_info('../sid-abc', home=tmp_path) == {}
