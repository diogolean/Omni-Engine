# -*- coding: utf-8 -*-
"""Zero-disk high-speed FFmpeg pipe.

Converts frames to compact YUV420 in RAM and streams them into ``ffmpeg`` via
stdin — no per-frame PNGs ever touch the disk. Uses a CRF-quality
``libx264 -preset veryfast`` contract with one-second locked GOPs. Muxes the dialogue
audio track as normalized-rate AAC
(``-c:a aac -b:a 192k -ar 44100 -map 0:v -map 1:a -shortest``).
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

_LOG = logging.getLogger("animator.renderer")

_NVENC_PROBE_CACHE: dict[str, bool] = {}


def _escape_filter_path(path: Path) -> str:
    """Make a Windows path safe inside an FFmpeg filtergraph argument.

    The filtergraph parser treats ``\\`` as an escape and ``:`` as an option
    separator, so ``G:\\My Drive\\subs.ass`` has to become
    ``G\\:/My Drive/subs.ass`` before it is wrapped in single quotes — the
    single most common reason a ``subtitles=`` filter silently fails on
    Windows.
    """
    text = str(path).replace("\\", "/")
    return text.replace(":", r"\:")


def _resolve_ffmpeg() -> str:
    """Resolve ffmpeg from shared, then PATH / imageio so the package still
    runs when the factory tree is not the caller. The pipe stays ffmpeg;
    MoviePy is not a renderer."""
    try:
        from shared.ffmpeg import resolve_ffmpeg  # noqa: PLC0415

        return resolve_ffmpeg()
    except Exception:  # noqa: BLE001 — standalone extraction / no shared resolver
        pass

    import shutil

    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg  # noqa: PLC0415

    return imageio_ffmpeg.get_ffmpeg_exe()


def _nvenc_available(ffmpeg_path: str) -> bool:
    """True only when ``h264_nvenc`` is both *listed* and actually usable.

    A CUDA-capable ffmpeg build can list ``h264_nvenc`` in ``-encoders`` on a
    machine with no NVIDIA driver at all (or a driver missing ``nvcuda.dll``),
    so the listing alone is not trustworthy — it must be able to open the
    encoder for a single throwaway frame, or every real render would pay for
    a failed GPU attempt before falling back to libx264.
    """
    if ffmpeg_path in _NVENC_PROBE_CACHE:
        return _NVENC_PROBE_CACHE[ffmpeg_path]
    available = False
    try:
        from shared.ffmpeg import run_ffmpeg

        listed = run_ffmpeg(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            text=True,
            timeout=15,
        )
        if "h264_nvenc" in (listed.stdout or ""):
            probe = run_ffmpeg(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=64x64:d=0.1",
                    "-frames:v",
                    "1",
                    "-c:v",
                    "h264_nvenc",
                    "-f",
                    "null",
                    "-",
                ],
                timeout=15,
            )
            available = probe.returncode == 0
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("nvenc probe failed: %s", exc)
    _NVENC_PROBE_CACHE[ffmpeg_path] = available
    return available


@dataclass(slots=True)
class RenderStats:
    """Telemetry returned alongside the rendered file."""

    output_path: Path
    frames_written: int
    fps: int
    width: int
    height: int
    render_seconds: float
    video_seconds: float
    encoder: str
    file_size_bytes: int

    @property
    def effective_fps(self) -> float:
        return self.frames_written / self.render_seconds if self.render_seconds > 0 else 0.0

    @property
    def speedup_factor(self) -> float:
        """How many seconds of finished video came out per second of wall time."""
        return self.video_seconds / self.render_seconds if self.render_seconds > 0 else 0.0


class RenderTimeout(TimeoutError):
    """The ffmpeg encode exceeded its budget and the process was stopped."""

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = float(timeout_s)
        super().__init__(
            f"ffmpeg encode exceeded {self.timeout_s:.1f}s and the process was stopped"
        )


def _kill_if_alive(proc: subprocess.Popen) -> None:
    """Stop ffmpeg when it is still running. A finished process is left alone."""
    poll = getattr(proc, "poll", None)
    if not callable(poll):
        return
    try:
        if poll() is not None:
            return
    except Exception:  # noqa: BLE001 — test doubles may not be a real Popen
        return
    try:
        from shared.ffmpeg import kill_process_tree  # noqa: PLC0415

        kill_process_tree(proc)
    except Exception:  # noqa: BLE001 — the timeout path must not raise a second error
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _signal_kill(proc: subprocess.Popen) -> None:
    """Ask the process group to stop. The caller reaps it.

    The watchdog must not ``wait``: the pipe thread is the one that reaps,
    and two threads waiting on one ``Popen`` is not reliable.
    """
    pid = getattr(proc, "pid", None)
    if not pid:
        return
    if os.name != "nt":
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _arm_encode_watchdog(proc: subprocess.Popen, deadline: float) -> threading.Event:
    """Signal the ffmpeg process group if the encode is still running at ``deadline``."""
    cancel = threading.Event()

    def _watch() -> None:
        remaining = deadline - time.monotonic()
        if cancel.wait(timeout=max(0.0, remaining)):
            return
        _signal_kill(proc)

    threading.Thread(target=_watch, name="ffmpeg-deadline", daemon=True).start()
    return cancel


def _write_stdin(proc: subprocess.Popen, payload: bytes, timeout: float, timeout_s: float) -> None:
    """Write one frame. If ffmpeg stops reading, kill it instead of blocking."""
    error: list[BaseException] = []

    def _write() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(payload)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller thread
            error.append(exc)

    worker = threading.Thread(target=_write, name="ffmpeg-stdin", daemon=True)
    worker.start()
    worker.join(timeout if timeout > 0 else 0.05)
    if worker.is_alive():
        _kill_if_alive(proc)
        worker.join(timeout=2)
        raise RenderTimeout(timeout_s)
    if error:
        raise error[0]


def _past_deadline(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


class AnimationRenderer:
    """Pipes compact YUV420 frames into ffmpeg and muxes dialogue audio."""

    def __init__(self, *, width: int = 1080, height: int = 1920, fps: int = 30) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.ffmpeg_path = _resolve_ffmpeg()

    def _video_codec_args(
        self,
        *,
        use_gpu: bool,
        preset: str = "veryfast",
        crf: int = 17,
        gop: int = 30,
        tune: str | None = "grain",
    ) -> list[str]:
        del use_gpu  # The delivery contract is intentionally encoder-stable.
        args = [
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(int(crf)),
            "-g",
            str(int(gop)),
            "-keyint_min",
            str(int(gop)),
            "-sc_threshold",
            "0",
        ]
        if tune:
            args += ["-tune", tune]
        return args

    def render(
        self,
        *,
        frame_iter: Iterable[np.ndarray],
        audio_path: str | Path | None,
        output_path: Path,
        duration_s: float,
        subtitles_path: str | Path | None = None,
        preset: str = "veryfast",
        crf: int = 17,
        gop: int = 30,
        tune: str | None = "grain",
        threads: int | None = None,
        deterministic: bool = False,
        encode_timeout_s: float | None = None,
        retain_frames: bool = True,
    ) -> RenderStats:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        use_gpu = False
        with tempfile.TemporaryDirectory(prefix="animation_render_") as temp_dir:
            encoded_path = Path(temp_dir) / output_path.name
            frames_written, encoder_used, elapsed = self._run_ffmpeg(
                frame_iter,
                audio_path=audio_path,
                output_path=encoded_path,
                use_gpu=use_gpu,
                subtitles_path=Path(subtitles_path) if subtitles_path else None,
                preset=preset,
                crf=crf,
                gop=gop,
                tune=tune,
                threads=threads,
                deterministic=deterministic,
                encode_timeout_s=encode_timeout_s,
                retain_frames=retain_frames,
            )
            shutil.copyfile(encoded_path, output_path)

        size = output_path.stat().st_size if output_path.is_file() else 0
        stats = RenderStats(
            output_path=output_path,
            frames_written=frames_written,
            fps=self.fps,
            width=self.width,
            height=self.height,
            render_seconds=elapsed,
            video_seconds=duration_s,
            encoder=encoder_used,
            file_size_bytes=size,
        )
        _LOG.info(
            "rendered %s: %d frames in %.2fs (%.1fx realtime, %s) -> %d bytes",
            output_path,
            frames_written,
            elapsed,
            stats.speedup_factor,
            encoder_used,
            size,
        )
        return stats

    def _build_cmd(
        self,
        *,
        audio_path: Path | None,
        output_path: Path,
        use_gpu: bool,
        audio_codec: str,
        subtitles_path: Path | None = None,
        preset: str = "veryfast",
        crf: int = 17,
        gop: int = 30,
        tune: str | None = "grain",
        threads: int | None = None,
        deterministic: bool = False,
    ) -> list[str]:
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-loglevel",
            "error",
        ]
        if deterministic:
            # Drop the encode clock and the lavc version banner so two runs
            # of the same frames produce the same file.
            cmd += ["-fflags", "+bitexact"]
        cmd += [
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-s",
            f"{self.width}x{self.height}",
            "-r",
            str(self.fps),
            "-i",
            "-",
        ]
        if audio_path is not None:
            # ``-i merged_audio.wav`` — the per-turn TTS/dialogue track,
            # already normalized + resampled to a consistent rate by
            # whichever bridge module built it (see build_session_audio()).
            cmd += ["-i", str(audio_path)]
        filters = ["setsar=1:1"]
        if subtitles_path is not None:
            # Burn karaoke after declaring square pixels, preventing players
            # from applying an anamorphic display transform to the final MP4.
            filters.append(f"subtitles='{_escape_filter_path(subtitles_path)}'")
        cmd += ["-vf", ",".join(filters)]
        cmd += self._video_codec_args(
            use_gpu=use_gpu,
            preset=preset,
            crf=crf,
            gop=gop,
            tune=tune,
        )
        if deterministic:
            cmd += [
                "-flags",
                "+bitexact",
                "-x264-params",
                "threads=1:lookahead-threads=1:sliced-threads=0",
            ]
        if threads is not None:
            cmd += ["-threads", str(int(threads))]
        cmd += ["-pix_fmt", "yuv420p"]
        if audio_path is not None:
            cmd += ["-c:a", audio_codec]
            if audio_codec == "aac":
                cmd += ["-b:a", "192k", "-ar", "44100"]
            # Explicit stream mapping: video from input 0, audio from input 1.
            # ``-shortest`` trims to whichever stream is shorter so a
            # duration_override that truncates the video frame count never
            # produces a video-audio length mismatch in the final container.
            cmd += ["-map", "0:v", "-map", "1:a", "-shortest"]
        if deterministic:
            cmd += [
                "-map_metadata",
                "-1",
                "-metadata",
                "creation_time=1970-01-01T00:00:00Z",
            ]
        cmd.append(str(output_path))
        return cmd

    def _run_ffmpeg(
        self,
        frame_iter: Iterable[np.ndarray],
        *,
        audio_path: str | Path | None,
        output_path: Path,
        use_gpu: bool,
        subtitles_path: Path | None = None,
        preset: str = "veryfast",
        crf: int = 17,
        gop: int = 30,
        tune: str | None = "grain",
        threads: int | None = None,
        deterministic: bool = False,
        encode_timeout_s: float | None = None,
        retain_frames: bool = True,
    ) -> tuple[int, str, float]:
        audio_p = Path(audio_path) if audio_path else None
        # Always encode the final stream to a known-good AAC/44.1 kHz
        # contract. Stream-copying arbitrary source audio was a source of
        # inaudible or incompatible MP4 tracks.
        codec_attempts = ["aac"]

        last_error: Exception | None = None
        for encoder_is_gpu in ([use_gpu, False] if use_gpu else [False]):
            for audio_codec in codec_attempts:
                cmd = self._build_cmd(
                    audio_path=audio_p,
                    output_path=output_path,
                    use_gpu=encoder_is_gpu,
                    audio_codec=audio_codec,
                    subtitles_path=subtitles_path,
                    preset=preset,
                    crf=crf,
                    gop=gop,
                    tune=tune,
                    threads=threads,
                    deterministic=deterministic,
                )
                encoder_name = "h264_nvenc" if encoder_is_gpu else "libx264"
                try:
                    frames_written, elapsed = self._pipe_frames(
                        cmd,
                        frame_iter,
                        output_path,
                        encode_timeout_s=encode_timeout_s,
                    )
                    return frames_written, f"{encoder_name}+{audio_codec}", elapsed
                except RenderTimeout:
                    raise
                except _FfmpegFailure as exc:
                    last_error = exc
                    _LOG.warning(
                        "ffmpeg attempt failed (encoder=%s audio_codec=%s): %s — retrying",
                        encoder_name,
                        audio_codec,
                        exc,
                    )
                    if not retain_frames:
                        raise RuntimeError(
                            "ffmpeg failed for every encoder/audio-codec combination: "
                            f"{last_error}"
                        ) from exc
                    # Legacy callers retry once. That retry is the only place
                    # a frame iterator is copied, and only after a failure.
                    frame_iter = exc.consumed_frames + list(frame_iter)
        raise RuntimeError(f"ffmpeg failed for every encoder/audio-codec combination: {last_error}")

    def _pipe_frames(
        self,
        cmd: list[str],
        frame_iter: Iterable[np.ndarray],
        output_path: Path,
        *,
        encode_timeout_s: float | None = None,
    ) -> tuple[int, float]:
        if encode_timeout_s is not None and encode_timeout_s <= 0:
            raise ValueError("encode_timeout_s must be positive")
        start = time.perf_counter()
        deadline = None if encode_timeout_s is None else time.monotonic() + float(encode_timeout_s)
        from shared.ffmpeg import popen_ffmpeg

        proc = popen_ffmpeg(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdin is not None
        frames_written = 0
        cancel = _arm_encode_watchdog(proc, deadline) if deadline is not None else None
        try:
            try:
                for frame in frame_iter:
                    if _past_deadline(deadline):
                        _kill_if_alive(proc)
                        raise RenderTimeout(encode_timeout_s or 0.0)
                    rgb = np.ascontiguousarray(frame, dtype=np.uint8)
                    yuv420 = cv2.cvtColor(rgb, cv2.COLOR_RGB2YUV_I420)
                    payload = yuv420.tobytes()
                    if deadline is None:
                        proc.stdin.write(payload)
                    else:
                        _write_stdin(
                            proc,
                            payload,
                            max(0.05, deadline - time.monotonic()),
                            float(encode_timeout_s or 0.0),
                        )
                    frames_written += 1
            except RenderTimeout:
                _kill_if_alive(proc)
                raise
            except (BrokenPipeError, OSError) as exc:
                if _past_deadline(deadline):
                    _kill_if_alive(proc)
                    raise RenderTimeout(encode_timeout_s or 0.0) from exc
                stderr = _finish_ffmpeg_pipe(proc, timeout=30)
                elapsed = time.perf_counter() - start
                # -shortest closes stdin once the audio ends. A clean exit with
                # a finished file is the stop, not a failed encode.
                if proc.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0:
                    _LOG.info(
                        "ffmpeg closed the frame pipe after %d frame(s) (exit 0)",
                        frames_written,
                    )
                    return frames_written, elapsed
                raise _FfmpegFailure(_ffmpeg_failure_text(stderr, exc, proc.returncode), [])
            if _past_deadline(deadline):
                _kill_if_alive(proc)
                raise RenderTimeout(encode_timeout_s or 0.0)
            finish_timeout = 120.0 if deadline is None else max(1.0, deadline - time.monotonic())
            stderr = _finish_ffmpeg_pipe(proc, timeout=finish_timeout)
            elapsed = time.perf_counter() - start
            if _past_deadline(deadline) and proc.returncode != 0:
                raise RenderTimeout(encode_timeout_s or finish_timeout)
            if proc.returncode != 0 or not output_path.is_file():
                raise _FfmpegFailure(
                    _ffmpeg_failure_text(stderr, None, proc.returncode),
                    [],
                )
            return frames_written, elapsed
        finally:
            if cancel is not None:
                cancel.set()
            _kill_if_alive(proc)


class _FfmpegFailure(RuntimeError):
    def __init__(self, message: str, consumed_frames: list[np.ndarray]) -> None:
        super().__init__(message)
        self.consumed_frames = consumed_frames


def _ffmpeg_failure_text(
    stderr: bytes,
    exc: BaseException | None,
    returncode: int | None,
) -> str:
    """Surface ffmpeg's own stderr. A closed pipe often has an empty one."""
    detail = stderr.decode("utf-8", "ignore").strip()
    if not detail:
        detail = f"ffmpeg exited {returncode} with empty stderr"
    if exc is None:
        return detail[-2000:]
    return f"{exc} :: {detail[-2000:]}"


def _finish_ffmpeg_pipe(proc: subprocess.Popen, timeout: float) -> bytes:
    """Close ffmpeg's stdin, drain both pipes, and wait for the process.

    Calling ``stdin.close()`` and then ``communicate()`` makes POSIX flush
    the already-closed pipe (``ValueError: flush of closed file``). Stdout
    and stderr are read on separate threads so neither pipe can fill and
    stall the other. The return code and stderr bytes are what callers
    already inspect.
    """
    stdin = proc.stdin
    if stdin is not None and not stdin.closed:
        try:
            stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    stderr_parts: list[bytes] = []

    def _drain(stream, sink: list[bytes] | None) -> None:
        if stream is None:
            return
        try:
            data = stream.read()
        except (OSError, ValueError):
            data = b""
        if sink is not None:
            sink.append(data)

    stdout_reader = threading.Thread(
        target=_drain, args=(proc.stdout, None), name="ffmpeg-stdout", daemon=True
    )
    stderr_reader = threading.Thread(
        target=_drain, args=(proc.stderr, stderr_parts), name="ffmpeg-stderr", daemon=True
    )
    stdout_reader.start()
    stderr_reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        from shared.ffmpeg import kill_process_tree

        kill_process_tree(proc)
    stdout_reader.join(timeout)
    stderr_reader.join(timeout)
    return b"".join(stderr_parts)


def resolve_ffmpeg() -> str:
    """Public accessor for the ffmpeg executable this package will pipe into."""
    return _resolve_ffmpeg()


__all__ = ["AnimationRenderer", "RenderStats", "RenderTimeout", "resolve_ffmpeg"]
