import json
from pathlib import Path

from native_services.launcher_owner_runtime import monitor_launch, window_needs_attention
from native_services.launcher_owner import WindowOwnership


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, amount):
        self.value += amount


class FakeWindows:
    def __init__(self):
        self.commands = []
        self.current = []

    def clients(self):
        return list(self.current)

    def move(self, address, workspace):
        self.commands.append(("move", address, workspace))

    def place(self, address, monitor, fullscreen, *, follow=True):
        self.commands.append(("place", address, monitor, fullscreen, follow))

    def active_workspace(self):
        return "2"


def _write_process(proc_root: Path, pid: int, *, ppid=1, comm="steam", cmdline="steam", env=b""):
    directory = proc_root / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(pid * 10)]
    (directory / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields))
    (directory / "cmdline").write_bytes(cmdline.encode() + b"\0")
    (directory / "environ").write_bytes(env)
    (directory / "exe").symlink_to("/usr/bin/" + comm)
    (directory / "cwd").symlink_to("/tmp")


def test_attention_title_only_matches_launcher_prompts():
    assert window_needs_attention({"title": "Steam - Sign in"}, "steam")
    assert window_needs_attention({"initialTitle": "Cloud Sync Conflict"}, "steam")
    assert not window_needs_attention({"title": "Steam Library"}, "steam")


def test_fake_steam_session_tracks_tagged_game_hides_windows_and_never_terminates(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    clock = FakeClock()
    backend = FakeWindows()
    owner = WindowOwnership(backend)
    calls = []
    game_started = False
    game_removed = False
    class FakePopen:
        pid = 111
        def poll(self):
            return None

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        _write_process(proc_root, 111, comm="steam")
        backend.current[:] = [{"address": "steam-ui", "pid": 111, "workspace": {"name": "1"}, "monitor": "DP-1"}]
        return FakePopen()

    def sleep(seconds):
        nonlocal game_started, game_removed
        clock.advance(seconds)
        if clock.value >= 0.5 and not game_started:
            _write_process(proc_root, 222, ppid=111, comm="StarQuest.exe",
                           cmdline="/games/StarQuest.exe", env=b"SteamAppId=42\0")
            game_started = True
        if clock.value >= 4 and not game_removed:
            (proc_root / "222").rename(tmp_path / "gone-222")
            backend.current[:] = []
            game_removed = True

    rc = monitor_launch(provider="steam", launcher="/fake/steam",
                        launch_args=["-silent", "steam://rungameid/42"], game_id="42",
                        status_file=tmp_path / "status.json", proc_root=proc_root,
                        windows=owner, clock=clock, sleep=sleep, popen=popen,
                        terminate=lambda *args: (_ for _ in ()).throw(AssertionError("Steam must not be closed")),
                        attention_after=10, startup_timeout=20)
    assert rc == 0
    assert calls[0][0] == ["/fake/steam", "-silent", "steam://rungameid/42"]
    assert json.loads((tmp_path / "status.json").read_text())["stage"] == "done"
    assert ("move", "steam-ui", "special:waystone-hidden") in backend.commands



def _run_steam(tmp_path, *, preexisting=(), steam_running=False, game_starts=True, scope_calls=None,
               startup_timeout=20):
    proc_root = tmp_path / "proc"
    proc_root.mkdir(parents=True)
    if steam_running:
        _write_process(proc_root, 50, comm="steam")
    clock = FakeClock()
    backend = FakeWindows()
    backend.current[:] = [dict(w) for w in preexisting]
    owner = WindowOwnership(backend)
    calls = []
    state = {"started": False, "removed": False}

    class FakePopen:
        pid = 111
        def poll(self):
            return None

    def popen(argv, **kwargs):
        calls.append(argv)
        _write_process(proc_root, 111, comm="steam")
        backend.current.append({"address": "steam-popup", "pid": 111, "workspace": {"name": "1"}, "monitor": "DP-1"})
        return FakePopen()

    def sleep(seconds):
        clock.advance(seconds)
        if game_starts and clock.value >= 0.5 and not state["started"]:
            _write_process(proc_root, 222, ppid=111, comm="Game.exe", cmdline="/g/Game.exe", env=b"SteamAppId=42\0")
            state["started"] = True
        if game_starts and clock.value >= 4 and not state["removed"]:
            (proc_root / "222").rename(tmp_path / "gone-222")
            state["removed"] = True

    def scope(argv, provider):
        if scope_calls is not None:
            scope_calls.append(provider)
        return ["systemd-run", "--scope", "--", *argv]

    rc = monitor_launch(provider="steam", launcher="/fake/steam", launch_args=["-silent", "steam://rungameid/42"],
                        game_id="42", status_file=tmp_path / "status.json", proc_root=proc_root, windows=owner,
                        clock=clock, sleep=sleep, popen=popen, terminate=lambda *a: None,
                        attention_after=10, startup_timeout=startup_timeout, scope_argv=scope)
    return rc, calls, backend, json.loads((tmp_path / "status.json").read_text())


def test_windows_open_before_the_launch_are_never_hidden(tmp_path):
    chat = {"address": "friends-chat", "pid": 50, "workspace": {"name": "3"}, "monitor": "DP-1"}
    rc, _calls, backend, _status = _run_steam(tmp_path, preexisting=[chat], steam_running=True)
    assert rc == 0
    moved = {cmd[1] for cmd in backend.commands if cmd[0] == "move"}
    assert "steam-popup" in moved and "friends-chat" not in moved


def test_steam_started_by_waystone_gets_its_own_scope_but_running_steam_does_not(tmp_path):
    scoped = []
    _rc, calls, _b, _s = _run_steam(tmp_path / "a", scope_calls=scoped)
    assert scoped == ["steam"] and calls[0][0] == "systemd-run"
    scoped = []
    _rc, calls, _b, _s = _run_steam(tmp_path / "b", steam_running=True, scope_calls=scoped)
    assert scoped == [] and calls[0][0] == "/fake/steam"


def test_game_that_never_starts_ends_as_failed_instead_of_starting_forever(tmp_path):
    rc, _calls, _backend, status = _run_steam(tmp_path, game_starts=False, startup_timeout=20)
    assert rc == 1
    assert status["stage"] == "failed" and status["reason"] == "startup-timeout"


def test_separate_scope_only_in_scoped_releases():
    from native_services.launcher_owner_runtime import separate_scope_argv
    which = lambda name: "/usr/bin/systemd-run"
    assert separate_scope_argv(["steam"], "steam", environ={}, which=which) == ["steam"]
    argv = separate_scope_argv(["steam", "-silent"], "steam", environ={"WAYSTONE_GAME_SCOPES": "1"}, which=which)
    assert argv[0] == "/usr/bin/systemd-run" and argv[-2:] == ["steam", "-silent"]
    assert any(a.startswith("--unit=app-waystone-steam-") for a in argv)


def test_playing_waits_for_the_game_window_not_proton_helpers(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    clock = FakeClock()
    backend = FakeWindows()
    owner = WindowOwnership(backend)
    status_file = tmp_path / "status.json"
    stages = []

    class FakePopen:
        pid = 111
        def poll(self):
            return None

    def popen(argv, **kwargs):
        _write_process(proc_root, 111, comm="steam")
        return FakePopen()

    def sleep(seconds):
        clock.advance(seconds)
        if status_file.exists():
            stage = json.loads(status_file.read_text())["stage"]
            if not stages or stages[-1] != stage:
                stages.append(stage)
        if clock.value == 1.0:   # Proton helper tagged with the app id, no window yet
            _write_process(proc_root, 222, ppid=111, comm="python3", env=b"SteamAppId=42\0")
        if clock.value == 3.0:   # the game's window appears
            _write_process(proc_root, 333, ppid=222, comm="Game.exe", env=b"SteamAppId=42\0")
            backend.current[:] = [{"address": "game", "pid": 333, "workspace": {"name": "1"}}]
        if clock.value == 6.0:
            for pid in ("222", "333"):
                (proc_root / pid).rename(tmp_path / f"gone-{pid}")
            backend.current[:] = []

    rc = monitor_launch(provider="steam", launcher="/fake/steam", launch_args=["steam://rungameid/42"],
                        game_id="42", status_file=status_file, proc_root=proc_root, windows=owner,
                        clock=clock, sleep=sleep, popen=popen, terminate=lambda *a: None,
                        attention_after=60, startup_timeout=120, scope_argv=lambda a, p: a)
    assert rc == 0
    assert stages.index("game-starting") < stages.index("playing")


def test_processes_running_before_play_are_never_the_game(tmp_path):
    """A terminal or file manager already open in the game folder must not count as the game."""
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_process(proc_root, 90, comm="bash", cmdline="bash")
    (proc_root / "90" / "cwd").unlink()
    (proc_root / "90" / "cwd").symlink_to("/games/x")
    clock = FakeClock()
    backend = FakeWindows()

    class FakePopen:
        pid = 111
        def poll(self):
            return 0

    def popen(argv, **kwargs):
        return FakePopen()

    rc = monitor_launch(provider="heroic", launcher="/fake/heroic", launch_args=["heroic://launch?appName=x&runner=legendary"],
                        game_id="x", status_file=tmp_path / "status.json", game_folder="/games/x",
                        proc_root=proc_root, windows=WindowOwnership(backend), clock=clock,
                        sleep=lambda s: clock.advance(s), popen=popen, terminate=lambda *a: None,
                        attention_after=60, startup_timeout=120, scope_argv=lambda a, p: a)
    assert rc == 1
    assert json.loads((tmp_path / "status.json").read_text())["stage"] == "failed"


def test_heroic_window_is_hidden_the_moment_it_opens(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    clock = FakeClock()
    backend = FakeWindows()
    callbacks = []
    backend.watch_new_windows = lambda cb: (callbacks.append(cb), lambda: None)[1]
    owner = WindowOwnership(backend)
    moved_at_open = []

    class FakePopen:
        pid = 111
        def poll(self):
            return 0

    def popen(argv, **kwargs):
        callbacks[0]("0xheroic", "heroic", "1")          # Hyprland: window opened
        callbacks[0]("0xchat", "discord", "1")           # someone else's window: untouched
        moved_at_open.extend(c for c in backend.commands if c[0] == "move")
        return FakePopen()

    monitor_launch(provider="heroic", launcher="/fake/heroic", launch_args=["heroic://launch?appName=x&runner=legendary"],
                   game_id="x", status_file=tmp_path / "s.json", proc_root=proc_root, windows=owner, clock=clock,
                   sleep=lambda s: clock.advance(s), popen=popen, terminate=lambda *a: None,
                   attention_after=60, startup_timeout=120, scope_argv=lambda a, p: a)
    assert moved_at_open == [("move", "0xheroic", "special:waystone-hidden")]
