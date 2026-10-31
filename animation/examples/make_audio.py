"""Write a short deterministic tone for the Rigly hello scene.

The wav is generated, not stored in git.
"""
from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path


def write_sample(path: str | Path, *, seconds: float = 2.4, rate: int = 16000) -> Path:
    """A 220 Hz tone with a loud, quiet, loud envelope so the mouth changes."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = max(1, int(rate * seconds))
    frames = bytearray()
    for index in range(count):
        t = index / float(rate)
        if t < 0.25 or t >= seconds - 0.15:
            amplitude = 0.0
        elif t < 1.05:
            amplitude = 0.85
        elif t < 1.35:
            amplitude = 0.18
        else:
            amplitude = 0.7
        sample = int(amplitude * 32767.0 * math.sin(2.0 * math.pi * 220.0 * t))
        frames += struct.pack("<h", sample)
    with wave.open(str(destination), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a short tone wav for the hello scene")
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=float, default=2.4)
    parser.add_argument("--rate", type=int, default=16000)
    args = parser.parse_args(argv)
    written = write_sample(args.output, seconds=args.seconds, rate=args.rate)
    print(written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
