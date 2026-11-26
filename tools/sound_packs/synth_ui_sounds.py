"""Waystone UI sound packs, synthesized from scratch (no samples, nothing copied).

Usage: uv run --with numpy python tools/sound_packs/synth_ui_sounds.py [assets/ui-sounds]
Writes <out>/<pack>/<event>.wav (44.1 kHz, 16-bit mono) and <out>/<pack>/pack.json for every pack below.
To add a pack: write a function returning {event: samples} for the EVENTS, add it to PACKS, run this script.
"""
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100


def t(seconds):
    return np.arange(int(SR * seconds)) / SR


def env(length, attack=0.003, decay=0.15, curve=4.0):
    n = len(length) if hasattr(length, "__len__") else int(length)
    x = np.arange(n) / SR
    a = np.clip(x / max(attack, 1e-4), 0, 1)
    d = np.exp(-curve * np.maximum(x - attack, 0) / max(decay, 1e-4))
    return a * d


def sine(freq, dur, phase=0.0):
    tt = t(dur)
    if callable(freq):
        f = freq(tt)
        ph = 2 * np.pi * np.cumsum(f) / SR
        return np.sin(ph + phase)
    return np.sin(2 * np.pi * freq * tt + phase)


def bell(freq, dur, decay, partials=((1, 1.0), (2.76, 0.35), (5.4, 0.12)), attack=0.002):
    out = np.zeros(int(SR * dur))
    for ratio, amp in partials:
        s = sine(freq * ratio, dur) * amp * env(out, attack, decay / ratio ** 0.5)
        out += s
    return out


def noise(dur, seed=1):
    return np.random.default_rng(seed).standard_normal(int(SR * dur))


def lowpass(x, cutoff):
    # one-pole low-pass, cutoff may be an array (sweeps)
    a = np.exp(-2 * np.pi * np.broadcast_to(cutoff, x.shape) / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, (xi, ai) in enumerate(zip(x, a)):
        acc = (1 - ai) * xi + ai * acc
        y[i] = acc
    return y


def highpass(x, cutoff):
    return x - lowpass(x, cutoff)


def reverb(x, mix=0.25, room=0.35):
    """Tiny feedback-delay reverb for air (comb + allpass)."""
    out = np.concatenate([x, np.zeros(int(SR * 0.6))])
    wet = np.zeros_like(out)
    for delay_ms, gain in ((29.7, room), (37.1, room * 0.95), (41.1, room * 0.9), (43.7, room * 0.85)):
        d = int(SR * delay_ms / 1000)
        buf = out.copy()
        for i in range(d, len(buf)):
            buf[i] += gain * buf[i - d]
        wet += buf
    wet /= 4
    return (1 - mix) * out + mix * wet


def mix(*parts):
    n = max(len(p) for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[: len(p)] += p
    return out


def delay(x, seconds):
    return np.concatenate([np.zeros(int(SR * seconds)), x])


def finish(x, peak=0.7, tail=0.02):
    x = np.asarray(x, dtype=float)
    # trim trailing silence, short fade-out
    idx = np.where(np.abs(x) > 1e-4 * np.max(np.abs(x)))[0]
    if len(idx):
        x = x[: idx[-1] + 1]
    fade = min(len(x), int(SR * tail))
    x[-fade:] *= np.linspace(1, 0, fade)
    return x / (np.max(np.abs(x)) + 1e-9) * peak


# ---------------- Style 1: Glass (airy, bright, gentle) ----------------
def glass():
    return {
        "move": finish(reverb(bell(2093, 0.25, 0.05), 0.2, 0.25), 0.35),
        "select": finish(reverb(mix(bell(1568, 0.5, 0.18), delay(bell(2349, 0.5, 0.2), 0.045)), 0.3), 0.55),
        "back": finish(reverb(mix(bell(1760, 0.4, 0.12), delay(bell(1319, 0.4, 0.14), 0.05)), 0.25), 0.5),
        "open": finish(reverb(lowpass(noise(0.35) * env(t(0.35), 0.12, 0.12, 3), np.linspace(800, 6000, int(SR * 0.35))), 0.35)
                       + 0.0, 0.4),
        "launch": finish(reverb(mix(*(delay(bell(f, 1.6, 0.9), i * 0.07) for i, f in enumerate((1047, 1319, 1568, 2093)))), 0.4, 0.4), 0.7),
        "error": finish(reverb(mix(bell(622, 0.5, 0.15), delay(bell(587, 0.5, 0.2), 0.12)), 0.2), 0.5),
        "notify": finish(reverb(mix(bell(1319, 0.9, 0.35), delay(bell(1976, 0.9, 0.4), 0.09)), 0.35), 0.55),
    }


# ---------------- Style 2: Console (tight, punchy, modern) ----------------
def tick(freq, dur=0.04, decay=0.012):
    body = sine(freq, dur) * env(t(dur), 0.0008, decay, 3)
    click = highpass(noise(dur, 3) * env(t(dur), 0.0003, 0.003, 3), 3000) * 0.3
    return body + click


def thump(freq_start, freq_end, dur, decay):
    tt = t(dur)
    return sine(lambda x: freq_start + (freq_end - freq_start) * np.minimum(x / dur, 1), dur) * env(tt, 0.001, decay, 3)


def console():
    sweep_t = t(0.9)
    sweep = sine(lambda x: 180 * (2 ** (x * 3.2)), 0.9) * env(sweep_t, 0.02, 0.5, 2.5)
    sweep += 0.5 * sine(lambda x: 270 * (2 ** (x * 3.2)), 0.9) * env(sweep_t, 0.02, 0.5, 2.5)
    whoosh = lowpass(noise(0.3, 5) * env(t(0.3), 0.06, 0.12, 3), np.linspace(400, 5000, int(SR * 0.3)))
    return {
        "move": finish(tick(1400), 0.3),
        "select": finish(mix(tick(1800, 0.06, 0.02), delay(thump(220, 120, 0.12, 0.05), 0.005)), 0.6),
        "back": finish(mix(tick(1100, 0.05, 0.015), delay(thump(160, 90, 0.1, 0.04), 0.004)), 0.5),
        "open": finish(whoosh, 0.45),
        "launch": finish(reverb(mix(sweep, delay(thump(110, 55, 0.6, 0.3), 0.75), delay(tick(2400, 0.08, 0.04), 0.75)), 0.2), 0.75),
        "error": finish(mix(thump(180, 170, 0.12, 0.06), delay(thump(150, 140, 0.16, 0.08), 0.11)), 0.6),
        "notify": finish(reverb(mix(tick(1600, 0.1, 0.04), delay(tick(2400, 0.12, 0.05), 0.08)), 0.2), 0.5),
    }


# ---------------- Style 3: Warm (wooden, soft, low) ----------------
def marimba(freq, dur=0.5, decay=0.12):
    tt = t(dur)
    tone = sine(freq, dur) * env(tt, 0.002, decay, 3) + 0.25 * sine(freq * 4, dur) * env(tt, 0.001, decay * 0.25, 3)
    knock = lowpass(noise(0.02, 7) * env(t(0.02), 0.0005, 0.004, 3), 1800) * 0.3
    return mix(tone, knock)


def warm():
    return {
        "move": finish(marimba(784, 0.2, 0.05), 0.35),
        "select": finish(reverb(mix(marimba(523, 0.5, 0.14), delay(marimba(784, 0.5, 0.16), 0.06)), 0.2), 0.6),
        "back": finish(reverb(mix(marimba(659, 0.4, 0.12), delay(marimba(440, 0.4, 0.14), 0.06)), 0.2), 0.55),
        "open": finish(lowpass(noise(0.4, 9) * env(t(0.4), 0.15, 0.12, 3), np.linspace(300, 2500, int(SR * 0.4))), 0.35),
        "launch": finish(reverb(mix(*(delay(marimba(f, 1.2, 0.5), i * 0.09) for i, f in enumerate((392, 523, 659, 784, 1047)))), 0.3, 0.4), 0.7),
        "error": finish(mix(marimba(311, 0.4, 0.12), delay(marimba(294, 0.45, 0.16), 0.13)), 0.55),
        "notify": finish(reverb(mix(marimba(659, 0.8, 0.3), delay(marimba(988, 0.8, 0.35), 0.1)), 0.3), 0.55),
    }


def write(path: Path, x):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data)


EVENTS = ("move", "select", "back", "open", "launch", "error", "notify")
PACKS = {
    "console": ("Console", "Tight clicks and low thumps. Punchy and quick.", console),
    "glass": ("Glass", "Airy chimes. Bright, gentle, a soft reverb tail.", glass),
    "warm": ("Warm", "Wooden marimba tones. Soft and low.", warm),
}


if __name__ == "__main__":
    import json
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "assets/ui-sounds")
    for pack_id, (name, description, make) in PACKS.items():
        sounds = make()
        assert set(sounds) == set(EVENTS), f"{pack_id} must define {EVENTS}"
        for event, x in sounds.items():
            write(out / pack_id / f"{event}.wav", x)
        (out / pack_id / "pack.json").write_text(json.dumps({
            "name": name, "description": description, "version": 1,
            "sounds": {event: f"{event}.wav" for event in EVENTS},
        }, indent=2) + "\n")
        print(pack_id, "ok")
