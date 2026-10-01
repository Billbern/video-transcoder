"""FFmpeg wrapper: builds the argv for a given preset and runs the subprocess.

`build_ffmpeg_args` is a *pure* function over the input paths + preset and is
the primary surface for unit tests. `transcode` performs the actual subprocess
call; tests can mock the runner to avoid invoking FFmpeg.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.services.formats import TranscodePreset, get_preset

logger = logging.getLogger(__name__)


class FFmpegRunner(Protocol):
    """Anything that can run a command asynchronously and return (returncode, stderr).

    Lets tests inject a fake runner without monkey-patching subprocess. Always
    async so we never block the FastAPI event loop on the worker.
    """

    async def __call__(
        self, args: list[str], *, input_path: Path, output_path: Path
    ) -> tuple[int, str]: ...


@dataclass(slots=True)
class TranscodeResult:
    """Outcome of a successful FFmpeg run."""

    output_path: Path
    returncode: int
    stderr: str


class FFmpegProcessor:
    """High-level video-transcoding service.

    Args:
        preset: Target preset (e.g., `"720p"`).
        ffmpeg_binary: Path to the FFmpeg binary; defaults to env `FFMPEG_BINARY`
            or `"ffmpeg"`.
        runner: Callable used to actually run the subprocess. Defaults to
            `asyncio.subprocess.Process`-backed implementation. Override for
            tests.
    """

    def __init__(
        self,
        preset: str,
        *,
        ffmpeg_binary: str | None = None,
        runner: FFmpegRunner | None = None,
    ) -> None:
        self.preset: TranscodePreset = get_preset(preset)
        self.ffmpeg_binary: str = (
            ffmpeg_binary or os.environ.get("FFMPEG_BINARY") or "ffmpeg"
        )
        self._runner = runner or _default_runner

    # -- Public API --------------------------------------------------------

    @staticmethod
    def build_ffmpeg_args(
        *,
        input_path: Path,
        output_path: Path,
        preset: TranscodePreset,
    ) -> list[str]:
        """Construct the argv list for an FFmpeg transcode to `preset`.

        This function is *pure* (no side-effects, no I/O) so unit tests can
        assert exact argv shapes.
        """
        return [
            "ffmpeg",  # placeholder; resolved by caller
            "-y",  # overwrite output without asking
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vf",
            f"scale=w={preset.width}:h={preset.height}:force_original_aspect_ratio=decrease,"
            f"pad={preset.width}:{preset.height}:(ow-iw)/2:(oh-ih)/2",
            "-c:v",
            preset.video_codec,
            "-b:v",
            preset.video_bitrate,
            "-c:a",
            preset.audio_codec,
            "-b:a",
            preset.audio_bitrate,
            *preset.extra_args,
            str(output_path),
        ]

    async def transcode(self, input_path: Path, output_path: Path) -> TranscodeResult:
        """Run FFmpeg to transcode `input_path` -> `output_path`.

        Raises:
            FileNotFoundError: If the input file is missing.
            FFmpegError: If FFmpeg exits with a non-zero return code.
        """
        if not input_path.exists():
            raise FileNotFoundError(f"Input file does not exist: {input_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        args = self.build_ffmpeg_args(
            input_path=input_path, output_path=output_path, preset=self.preset
        )
        # Replace the placeholder binary with the resolved one.
        args[0] = self.ffmpeg_binary

        logger.info(
            "Running FFmpeg",
            extra={"preset": self.preset.name, "input": str(input_path)},
        )
        returncode, stderr = await self._runner(
            args, input_path=input_path, output_path=output_path
        )

        if returncode != 0:
            logger.warning(
                "FFmpeg failed",
                extra={
                    "preset": self.preset.name,
                    "returncode": returncode,
                    "stderr": stderr[:500],
                },
            )
            raise FFmpegError(returncode=returncode, stderr=stderr)

        return TranscodeResult(
            output_path=output_path, returncode=returncode, stderr=stderr
        )


# --- Errors ----------------------------------------------------------------------


class FFmpegError(RuntimeError):
    """Raised when FFmpeg exits with a non-zero status."""

    def __init__(self, *, returncode: int, stderr: str) -> None:
        super().__init__(f"FFmpeg exited {returncode}: {stderr[:200]!r}")
        self.returncode = returncode
        self.stderr = stderr


# --- Default subprocess runner ---------------------------------------------------


async def _default_runner(
    args: list[str], *, input_path: Path, output_path: Path  # noqa: ARG001
) -> tuple[int, str]:
    """Real subprocess invocation.

    We pull stderr out via a pipe so we can persist it on the Job row when the
    job fails.
    """
    # Defensive: ensure binary exists to give a clearer error than FileNotFoundError.
    if not shutil.which(args[0]):
        raise FFmpegError(
            returncode=-1,
            stderr=f"FFmpeg binary not found on PATH: {args[0]!r}",
        )

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    stderr_text = stderr.decode("utf-8", errors="replace")
    # Best-effort log so operators can grep stdout in container logs.
    if stdout:
        logger.debug("FFmpeg stdout: %s", stdout.decode("utf-8", errors="replace"))
    return process.returncode or 0, stderr_text
