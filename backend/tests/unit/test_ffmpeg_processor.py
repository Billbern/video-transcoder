"""Unit tests for `app.services.ffmpeg_processor`.

Per `docs/testing_strategies.md` we verify FFmpeg arg generation without
actually invoking the binary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.services.ffmpeg_processor import FFmpegError, FFmpegProcessor
from app.services.formats import PRESETS


class TestBuildFFmpegArgs:
    def test_720p_argv_shape(self, tmp_path: Path) -> None:
        input_path = tmp_path / "in.mp4"
        output_path = tmp_path / "out.mp4"
        preset = PRESETS["720p"]
        argv = FFmpegProcessor.build_ffmpeg_args(
            input_path=input_path, output_path=output_path, preset=preset
        )

        # Must include input and output paths.
        assert str(input_path) in argv
        assert str(output_path) in argv

        # Must use libx264 / aac with the configured bitrates.
        assert "libx264" in argv
        assert "aac" in argv
        assert "2500k" in argv
        assert "128k" in argv

        # Should include the scale filter targeting 1280x720.
        idx = argv.index("-vf")
        scale_filter = argv[idx + 1]
        assert "1280" in scale_filter
        assert "720" in scale_filter

        # Should enable faststart (streaming-friendly MP4).
        assert "+faststart" in argv
        assert "veryfast" in argv

        # First element is a placeholder replaced by the runner.
        assert argv[0] == "ffmpeg"

    def test_argv_is_a_list(self, tmp_path: Path) -> None:
        argv = FFmpegProcessor.build_ffmpeg_args(
            input_path=tmp_path / "i.mp4",
            output_path=tmp_path / "o.mp4",
            preset=PRESETS["720p"],
        )
        assert isinstance(argv, list)
        assert all(isinstance(a, str) for a in argv)


class TestTranscode:
    async def test_success(self, tmp_path: Path) -> None:
        input_path = tmp_path / "in.mp4"
        input_path.write_bytes(b"fake-mp4-bytes")
        output_path = tmp_path / "out.mp4"

        async def fake_runner(args, *, input_path, output_path):
            return 0, ""

        proc = FFmpegProcessor(preset="720p", runner=fake_runner)
        result = await proc.transcode(input_path, output_path)
        assert result.returncode == 0

    async def test_missing_input_raises(self, tmp_path: Path) -> None:
        async def fake_runner(args, *, input_path, output_path):
            return 0, ""

        proc = FFmpegProcessor(preset="720p", runner=fake_runner)
        with pytest.raises(FileNotFoundError):
            await proc.transcode(tmp_path / "missing.mp4", tmp_path / "out.mp4")

    async def test_nonzero_exit_raises_ffmpeg_error(self, tmp_path: Path) -> None:
        input_path = tmp_path / "in.mp4"
        input_path.write_bytes(b"x")
        output_path = tmp_path / "out.mp4"

        async def fake_runner(args, *, input_path, output_path):
            return 1, "Conversion failed!"

        proc = FFmpegProcessor(preset="720p", runner=fake_runner)
        with pytest.raises(FFmpegError) as exc_info:
            await proc.transcode(input_path, output_path)
        assert exc_info.value.returncode == 1
        assert "Conversion failed" in exc_info.value.stderr

    async def test_uses_resolved_binary(self, tmp_path: Path) -> None:
        input_path = tmp_path / "in.mp4"
        input_path.write_bytes(b"x")

        seen: dict[str, list[str]] = {}

        async def fake_runner(args, *, input_path, output_path):
            seen["args"] = list(args)
            return 0, ""

        proc = FFmpegProcessor(
            preset="720p",
            ffmpeg_binary="/usr/local/bin/ffmpeg-static",
            runner=fake_runner,
        )
        await proc.transcode(input_path, tmp_path / "out.mp4")
        assert seen["args"][0] == "/usr/local/bin/ffmpeg-static"
