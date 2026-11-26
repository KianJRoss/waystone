import json
from types import SimpleNamespace

from native_services.game_monitor import (ANYWHERE, GameMonitorSettings, GameWindowPlacer, HyprlandMonitors,
                                          label, resolve)

AOC = {"name": "DP-2", "description": "AOC 32G2WG3 0x00000370", "make": "AOC", "model": "32G2WG3",
       "refresh": 144, "id": 2}
SAMSUNG = {"name": "DP-1", "description": "Samsung Electric Company LF27T35 HCNTA06296",
           "make": "Samsung Electric Company", "model": "LF27T35", "refresh": 60, "id": 1}


def test_settings_default_overrides_and_other_categories_kept(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"general": {"theme": "light"}}))
    settings = GameMonitorSettings(path)
    assert settings.effective("game-1") == ""
    settings.set_default(AOC["description"])
    settings.set_override("game-2", SAMSUNG["description"])
    settings.set_override("game-3", ANYWHERE)
    assert settings.effective("game-1") == AOC["description"]
    assert settings.effective("game-2") == SAMSUNG["description"]
    assert settings.effective("game-3") == ""
    settings.set_override("game-2", "")          # back to the default
    assert settings.effective("game-2") == AOC["description"]
    assert json.loads(path.read_text())["general"] == {"theme": "light"}


def test_resolve_by_description_then_make_model_after_a_cable_swap():
    assert resolve(AOC["description"], [SAMSUNG, AOC]) == "DP-2"
    swapped = dict(AOC, name="DP-1", description="AOC 32G2WG3 0x00000999")
    assert resolve(AOC["description"], [swapped]) == "DP-1"
    assert resolve(AOC["description"], [SAMSUNG]) is None
    assert resolve("", [AOC]) is None
    assert label(AOC) == "AOC 32G2WG3 (DP-2, 144 Hz)"


class FakeRun:
    def __init__(self, clients=(), monitors=(AOC, SAMSUNG), new_syntax=True):
        self.calls = []
        self.clients = list(clients)
        self.monitors = list(monitors)
        self.new_syntax = new_syntax

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[1:] == ["-j", "monitors"]:
            out = json.dumps([{**m, "refreshRate": m["refresh"]} for m in self.monitors])
        elif argv[1:] == ["-j", "clients"]:
            out = json.dumps(self.clients)
        elif argv[1] == "dispatch":
            out = "ok" if (argv[2].startswith("hl.dsp") == self.new_syntax) else "Invalid dispatcher"
        else:
            out = ""
        return SimpleNamespace(stdout=out)


def _backend(run):
    return HyprlandMonitors(run=run, environ={"HYPRLAND_INSTANCE_SIGNATURE": "x"}, which=lambda _: "/usr/bin/hyprctl")


def test_prepare_focuses_monitor_with_new_or_classic_syntax():
    run = FakeRun()
    assert GameWindowPlacer(_backend(run)).prepare(AOC["description"]) == "DP-2"
    assert ["hyprctl", "dispatch", 'hl.dsp.focus({ monitor = "DP-2" })'] in run.calls
    classic = FakeRun(new_syntax=False)
    GameWindowPlacer(_backend(classic)).prepare(AOC["description"])
    assert ["hyprctl", "dispatch", "focusmonitor", "DP-2"] in classic.calls


def test_no_compositor_means_no_calls():
    run = FakeRun()
    backend = HyprlandMonitors(run=run, environ={}, which=lambda _: None)
    assert GameWindowPlacer(backend).prepare(AOC["description"]) is None
    assert run.calls == []


def test_placer_moves_only_the_game_window_once_and_skips_hidden_launchers():
    run = FakeRun(clients=[
        {"address": "0xgame", "pid": 50, "monitor": 1, "workspace": {"name": "11"}},
        {"address": "0xsteam", "pid": 60, "monitor": 1, "workspace": {"name": "special:waystone-hidden"}},
        {"address": "0xother", "pid": 70, "monitor": 1, "workspace": {"name": "3"}},
    ])
    placer = GameWindowPlacer(_backend(run))
    placer.track("s1", "DP-2", now=0)
    pids = {"s1": [50, 60]}
    placer.poll(lambda sid: pids.get(sid), now=1)
    moves = [c for c in run.calls if c[1] == "dispatch"]
    assert moves == [["hyprctl", "dispatch",
                      'hl.dsp.window.move({ monitor = "DP-2", follow = true, window = "address:0xgame" })']]
    placer.poll(lambda sid: pids.get(sid), now=2)
    assert [c for c in run.calls if c[1] == "dispatch"] == moves     # once per window
    pids.clear()
    placer.poll(lambda sid: pids.get(sid), now=3)
    assert not placer.pending                                         # session over


def test_window_already_on_the_monitor_is_left_alone_and_placer_gives_up_eventually():
    run = FakeRun(clients=[{"address": "0xgame", "pid": 50, "monitor": 2, "workspace": {"name": "1"}}])
    placer = GameWindowPlacer(_backend(run), give_up_after=60)
    placer.track("s1", "DP-2", now=0)
    placer.poll(lambda sid: [50], now=1)
    assert not [c for c in run.calls if c[1] == "dispatch"]
    placer.track("s2", "DP-2", now=0)
    placer.poll(lambda sid: [], now=61)
    assert not placer.pending
