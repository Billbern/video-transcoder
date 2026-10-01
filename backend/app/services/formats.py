"""Video format metadata: allowed source MIME types, magic-byte signatures, and presets.

Per `docs/prd.md`, validation must be by MIME *and* magic bytes. Magic-byte
lookups are performed server-side via a ranged GET from Bucket A so the backend
never holds the raw bytes in memory.

The lists here are intentionally conservative; the UI advertises "MP4/MOV/MKV".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# --- Source file validation -----------------------------------------------

#: MIME types we accept on the ingest path. Lowercase.
ALLOWED_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "video/mp4",
        "video/quicktime",  # .mov
        "video/x-matroska",  # .mkv
        # Some browsers/OSes report slightly different MIME strings:
        "application/mp4",
    }
)

#: File extensions we accept (lowercase, with leading dot).
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({".mp4", ".mov", ".mkv"})


@dataclass(frozen=True, slots=True)
class MagicByteSignature:
    """A single file-format fingerprint.

    `offset` is the byte offset where the signature starts; `signature` is the
    raw bytes that must appear at that offset. Comparison is performed on the
    first `len(signature)` bytes read from the object.
    """

    container: str  # human-readable name (used in error messages)
    signature: bytes


#: Magic-byte signatures for the formats we accept. Sourced from the public
#: ISOBMFF/Matroska specifications; covers the dominant browsers.
MAGIC_SIGNATURES: Final[tuple[MagicByteSignature, ...]] = (
    # MP4 / MOV: ISO Base Media File Format.
    # Box "ftyp" appears at offset 4; the first 4 bytes are the box size.
    MagicByteSignature(container="MP4/MOV", signature=b"\x00\x00\x00\x18ftyp"),
    MagicByteSignature(container="MP4/MOV", signature=b"\x00\x00\x00\x20ftyp"),
    MagicByteSignature(container="MP4/MOV", signature=b"\x00\x00\x00\x1cftyp"),
    MagicByteSignature(container="MP4/MOV", signature=b"ftyp"),
    # Matroska / WebM: EBML header.
    MagicByteSignature(container="Matroska/WebM", signature=b"\x1a\x45\xdf\xa3"),
)


def looks_like_video(content_type: str, filename: str) -> bool:
    """Cheap pre-flight check used by the API layer (does not open the file).

    Returns True iff the declared MIME *and* the extension are both on the
    allow-list. The byte-level check happens later in the worker.
    """
    if content_type.lower() not in ALLOWED_CONTENT_TYPES:
        return False
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return f".{ext}" in ALLOWED_EXTENSIONS


def match_magic_bytes(head: bytes) -> str | None:
    """Return the human-readable container name if `head` matches a known signature.

    `head` should be at least the first ~12 bytes of the file (the longest
    signature is 4 bytes, but offset 4 means we need >= 8 bytes).
    """
    if not head:
        return None
    for sig in MAGIC_SIGNATURES:
        sig_len = len(sig.signature)
        # Search every possible offset within the head buffer.
        for offset in range(0, max(1, len(head) - sig_len + 1)):
            if head[offset : offset + sig_len] == sig.signature:
                return sig.container
    return None


# --- Transcoding presets --------------------------------------------------


@dataclass(frozen=True, slots=True)
class TranscodePreset:
    """Definition of a single output preset (FFmpeg arguments)."""

    name: str
    video_codec: str
    audio_codec: str
    width: int
    height: int
    video_bitrate: str
    audio_bitrate: str
    extra_args: tuple[str, ...] = ()


PRESETS: Final[dict[str, TranscodePreset]] = {
    "720p": TranscodePreset(
        name="720p",
        video_codec="libx264",
        audio_codec="aac",
        width=1280,
        height=720,
        # Cap bitrate; real value should be tuned per source.
        video_bitrate="2500k",
        audio_bitrate="128k",
        # `-movflags +faststart` lets the file play before it's fully downloaded.
        extra_args=("-movflags", "+faststart", "-preset", "veryfast"),
    ),
}


def get_preset(name: str) -> TranscodePreset:
    """Look up a preset by name; raises KeyError if missing."""
    try:
        return PRESETS[name]
    except KeyError as e:
        raise KeyError(f"Unknown preset {name!r}; available: {sorted(PRESETS)}") from e
