import json

from native_services.ui_sounds import APP_PACKS, EVENTS, UiSounds, discover_packs


class FakeEffect:
    def __init__(self, path, volume, log):
        self.path, self.volume, self.log, self.playing = path, volume, log, False

    def play(self):
        self.log.append(self.path.parent.name + "/" + self.path.stem)

    def isPlaying(self):
        return self.playing

    def setVolume(self, volume):
        self.volume = volume


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def make(tmp_path, *, quiet=False, roots=None, settings=None):
    log, effects = [], []
    clock = Clock()
    path = tmp_path / "settings.json"
    if settings is not None:
        path.write_text(json.dumps(settings))
    state = {"quiet": quiet}

    def factory(p, volume):
        effect = FakeEffect(p, volume, log)
        effects.append(effect)
        return effect
    sounds = UiSounds(pack_roots=roots if roots is not None else [APP_PACKS], settings_path=path,
                      effect_factory=factory, quiet=lambda: state["quiet"], clock=clock)
    return sounds, log, effects, clock, path, state


def test_built_in_packs_have_every_event():
    packs = discover_packs([APP_PACKS])
    assert {"console", "glass", "warm"} <= set(packs)
    for pack in packs.values():
        assert set(pack["files"]) == set(EVENTS)


def test_plays_from_the_chosen_pack_and_saves_settings(tmp_path):
    sounds, log, _effects, _clock, path, _state = make(tmp_path, settings={"general": {"theme": "light"}})
    assert sounds.pack == "console"
    sounds.play("select")
    sounds.setPack("warm")
    sounds.play("select")
    assert log == ["console/select", "warm/select"]
    saved = json.loads(path.read_text())
    assert saved["ui"]["sound_pack"] == "warm" and saved["general"] == {"theme": "light"}


def test_off_or_zero_volume_or_unknown_event_is_silent(tmp_path):
    sounds, log, *_ = make(tmp_path)
    sounds.play("explode")
    sounds.setVolume(0)
    sounds.play("select")
    sounds.setVolume(60)
    sounds.setEnabled(False)
    sounds.play("select")
    assert log == []
    sounds.preview("glass", "launch")          # Settings preview works even when switched off
    assert log == ["glass/launch"]


def test_quiet_while_a_game_runs_except_in_the_overlay(tmp_path):
    sounds, log, *_ = make(tmp_path, quiet=True)
    sounds.play("move")
    sounds.preview("console", "move")
    sounds.playIn("open", "overlay")
    assert log == ["console/open"]


def test_fast_moves_are_throttled_and_repeats_reuse_a_small_pool(tmp_path):
    sounds, log, effects, clock, *_ = make(tmp_path)
    for _ in range(4):                       # 0-30 ms: inside the 35 ms throttle
        sounds.play("move")
        clock.now += 0.01
    assert log == ["console/move"]
    clock.now += 1
    for _ in range(6):
        sounds.play("select")
        effects[-1].playing = True
    assert len([e for e in effects if e.path.stem == "select"]) == 3


def test_user_pack_overrides_and_files_cannot_escape_the_pack(tmp_path):
    user = tmp_path / "user"
    (user / "console").mkdir(parents=True)
    (user / "console" / "move.wav").write_bytes(b"RIFF")
    (user / "console" / "pack.json").write_text(json.dumps({"name": "My Console", "sounds": {
        "move": "move.wav", "select": "../../../etc/passwd"}}))
    packs = discover_packs([APP_PACKS, user])
    assert packs["console"]["name"] == "My Console"
    assert set(packs["console"]["files"]) == {"move"}


def test_saved_pack_that_no_longer_exists_falls_back(tmp_path):
    sounds, *_ = make(tmp_path, settings={"ui": {"sound_pack": "gone", "sound_volume": 40}})
    assert sounds.pack == "console" and sounds.volume == 40


def test_offscreen_and_opt_out_runs_are_silent():
    from native_services.ui_sounds import audio_allowed
    assert not audio_allowed({"QT_QPA_PLATFORM": "offscreen"})
    assert not audio_allowed({"QT_QPA_PLATFORM": "xcb", "WAYSTONE_UI_SOUNDS": "0"})
    assert audio_allowed({"QT_QPA_PLATFORM": "xcb"})
    assert audio_allowed({})
