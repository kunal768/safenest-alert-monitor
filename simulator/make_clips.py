"""Generate short synthetic WAV clips for each scenario (stand-ins for recorded audio)."""

from pathlib import Path
import math
import random
import struct
import wave

OUT = Path(__file__).parent / "clips"
RATE = 8000


def _noise(rng: random.Random) -> float:
    return rng.uniform(-1.0, 1.0)


def glass(i: int, rng: random.Random) -> float:
    t = i / RATE
    # Sharp broadband crash, then tinkling shards, then silence.
    crash = _noise(rng) * math.exp(-t * 9) if t < 0.6 else 0.0
    shards = sum(math.sin(2 * math.pi * f * t) for f in (2300, 3100, 3700)) / 3 * math.exp(-((t - 0.5) % 0.17) * 40) * 0.4 if 0.2 < t < 1.2 else 0.0
    return crash + shards


def fall(i: int, rng: random.Random) -> float:
    t = i / RATE
    thud = math.sin(2 * math.pi * 70 * t) * math.exp(-t * 10) + _noise(rng) * 0.3 * math.exp(-t * 25)
    groan = math.sin(2 * math.pi * (140 + 20 * math.sin(t * 6)) * t) * 0.35 if 0.7 < t < 1.6 else 0.0
    return thud + groan


def cough(i: int, rng: random.Random) -> float:
    t = i / RATE
    out = 0.0
    for start in (0.0, 0.45, 0.9):
        dt = t - start
        if 0 <= dt < 0.3:
            out += _noise(rng) * math.exp(-dt * 14) * (0.6 + 0.4 * math.sin(2 * math.pi * 300 * dt))
    return out


def scream(i: int, rng: random.Random) -> float:
    t = i / RATE
    f = 900 + 250 * math.sin(2 * math.pi * 5 * t)
    return (math.sin(2 * math.pi * f * t) + 0.3 * math.sin(4 * math.pi * f * t)) * min(1, t * 8) * 0.6


def door(i: int, rng: random.Random) -> float:
    t = i / RATE
    click = _noise(rng) * math.exp(-t * 60)
    creak = math.sin(2 * math.pi * (220 + 180 * t) * t) * 0.3 if 0.2 < t < 1.0 else 0.0
    return click + creak


def help_call(i: int, rng: random.Random) -> float:
    t = i / RATE
    syllable = (t % 0.6) < 0.35
    return math.sin(2 * math.pi * (480 + 60 * math.sin(t * 9)) * t) * 0.5 if syllable else 0.0


def default(i: int, rng: random.Random) -> float:
    t = i / RATE
    return _noise(rng) * 0.25 * math.exp(-t * 2)


CLIPS = {
    "glass.wav": glass,
    "fall.wav": fall,
    "cough.wav": cough,
    "scream.wav": scream,
    "door.wav": door,
    "help.wav": help_call,
    "default.wav": default,
}


def write_clip(path: Path, fn, seconds: float = 1.6):
    rng = random.Random(path.name)
    n = int(RATE * seconds)
    frames = bytearray()
    for i in range(n):
        env = min(1.0, (n - i) / 400)
        sample = max(-1.0, min(1.0, fn(i, rng) * env))
        frames += struct.pack("<h", int(sample * 26000))
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)


def write_all() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in CLIPS.items():
        write_clip(OUT / name, fn)
    return len(CLIPS)


if __name__ == "__main__":
    print("wrote", write_all(), "clips to", OUT)
