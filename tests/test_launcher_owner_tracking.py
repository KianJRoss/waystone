from native_services.game_process_tracker import GameProcessTracker


class FakeProc:
    def __init__(self):
        self.rows = {
            101: {"stat": (1, "python3", 10), "exe": "/usr/bin/python3",
                  "cmd": ("python3", "-m", "native_services.launcher_owner_runtime"),
                  "env": {}, "cwd": "/opt/waystone"},
            202: {"stat": (101, "StarQuest.exe", 20), "exe": "/games/Heroic/StarQuest/StarQuest.exe",
                  "cmd": ("/games/Heroic/StarQuest/StarQuest.exe",), "env": {},
                  "cwd": "/games/Heroic/StarQuest"},
        }

    def list_pids(self):
        return sorted(self.rows)

    def read_stat(self, pid):
        return self.rows[pid]["stat"] if pid in self.rows else None

    def read_exe(self, pid):
        return self.rows[pid]["exe"] if pid in self.rows else None

    def read_cmdline(self, pid):
        return self.rows[pid]["cmd"] if pid in self.rows else ()

    def read_targeted_env(self, pid):
        return self.rows[pid]["env"] if pid in self.rows else {}

    def read_cwd(self, pid):
        return self.rows[pid]["cwd"] if pid in self.rows else None


def test_heroic_session_tracks_game_not_waystone_owner_wrapper():
    proc = FakeProc()
    tracker = GameProcessTracker(proc_reader=proc, clock=lambda: 100)
    session = tracker.start_tracking(
        "star-quest", "heroic-epic", target_type="native_game", launcher_pid=101,
        launcher_pid_is_game=False, game_folder="/games/Heroic/StarQuest", prelaunch_pids={101},
    )
    assert session.state == "running"
    assert set(session.tracked_processes) == {202}



def test_owner_wrapper_pid_is_excluded_even_though_its_argv_names_the_game_folder():
    import os
    from native_services.game_process_tracker import GameProcessTracker

    class Proc:
        def list_pids(self): return [500, 600]
        def read_stat(self, pid): return (1, "python" if pid == 500 else "Game.exe", pid)
        def read_exe(self, pid): return None
        def read_cmdline(self, pid):
            return ("python", "launcher_owner_runtime.py", "--game-folder", "/games/x") if pid == 500 else ("/games/x/Game.exe",)
        def read_cwd(self, pid): return "/"
        def read_targeted_env(self, pid): return {}
        def read_steam_app_ids(self, pid): return set()

    tracker = GameProcessTracker()
    tracker.proc = Proc()
    found, _ = tracker._find_native_or_emulator_processes(
        [500, 600], launcher_pid=None, target_executable=None, target_payload=None, game_folder="/games/x",
        custom_exclusions=(), custom_inclusions=(), excluded_pids=frozenset({500, os.getpid()}))
    assert set(found) == {600}
