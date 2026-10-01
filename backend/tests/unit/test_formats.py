"""Unit tests for `app.services.formats`."""

from __future__ import annotations

import pytest
from app.services.formats import (
    PRESETS,
    looks_like_video,
    match_magic_bytes,
)


class TestLooksLikeVideo:
    @pytest.mark.parametrize(
        "content_type,filename",
        [
            ("video/mp4", "movie.mp4"),
            ("video/quicktime", "clip.mov"),
            ("video/x-matroska", "archive.mkv"),
            ("application/mp4", "movie.mp4"),
        ],
    )
    def test_accepts_supported_types(self, content_type: str, filename: str) -> None:
        assert looks_like_video(content_type, filename) is True

    @pytest.mark.parametrize(
        "content_type,filename",
        [
            ("application/octet-stream", "movie.mp4"),  # bad MIME
            ("video/mp4", "movie.exe"),  # bad extension
            ("", ""),  # empty
            ("video/mp4", "no_extension"),
        ],
    )
    def test_rejects_unsupported_or_mismatched(
        self, content_type: str, filename: str
    ) -> None:
        assert looks_like_video(content_type, filename) is False


class TestMagicBytes:
    def test_matches_mp4_with_offset(self) -> None:
        # Real MP4: 32-byte size, then "ftyp" at offset 4.
        head = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16
        assert match_magic_bytes(head) == "MP4/MOV"

    def test_matches_mp4_at_offset_0(self) -> None:
        head = b"ftypisom" + b"\x00" * 16
        assert match_magic_bytes(head) == "MP4/MOV"

    def test_matches_mov(self) -> None:
        # MOV uses the same ftyp box with brand "qt  ".
        head = b"\x00\x00\x00\x20ftypqt  " + b"\x00" * 8
        assert match_magic_bytes(head) == "MP4/MOV"

    def test_matches_matroska(self) -> None:
        head = b"\x1a\x45\xdf\xa3" + b"\x00" * 16
        assert match_magic_bytes(head) == "Matroska/WebM"

    def test_rejects_unknown(self) -> None:
        head = b"NOT A VIDEO FILE\x00\x00\x00\x00"
        assert match_magic_bytes(head) is None

    def test_handles_empty(self) -> None:
        assert match_magic_bytes(b"") is None


class TestPresets:
    def test_720p_preset_exists(self) -> None:
        assert "720p" in PRESETS
        p = PRESETS["720p"]
        assert p.width == 1280
        assert p.height == 720
        assert p.video_codec == "libx264"
        assert p.audio_codec == "aac"
