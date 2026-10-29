"""File dispatch: guess the format and hand off to the right parser."""

from __future__ import annotations

import os

from .errors import ParseError, UnsupportedFormatError
from .model import DocumentInfo
from .psd_parser import parse_psd
from .tiff_parser import parse_tiff

_SIGNATURES = {
    b"8BPS": parse_psd,  # PSD and PSB both start with 8BPS
    b"II*\x00": parse_tiff,
    b"MM\x00*": parse_tiff,
    b"II+\x00": None,  # BigTIFF: tiff_parser raises a clear error
    b"MM\x00+": None,
}


def sniff(path: str) -> str:
    """Return the ca-pa format name for a path, or raise UnsupportedFormatError."""
    with open(path, "rb") as fp:
        head = fp.read(8)
    if head[:4] in (b"8BPS",):
        return "psd"
    if len(head) >= 4 and head[:2] in (b"II", b"MM") and head[2:4] in (b"*\x00", b"+\x00"):
        return "tiff"
    raise UnsupportedFormatError(
        "%s: not a supported file (expected a PSD, PSB or TIFF)" % os.path.basename(path)
    )


def parse_file(path: str | os.PathLike) -> DocumentInfo:
    """Parse ``path`` (PSD/PSB/TIFF) into a DocumentInfo without loading pixels.

    Raises :class:`UnsupportedFormatError` for non-layered-image files and
    :class:`ParseError` for truncated/corrupt ones. A flat (layer-less)
    image is *not* an error: it comes back as a single-layer document with
    ``flat=True`` and a warning.
    """
    path = os.fspath(path)
    if not os.path.isfile(path):
        raise ParseError("%s: file not found" % path)
    fmt = sniff(path)
    if fmt == "psd":
        return parse_psd(path)
    return parse_tiff(path)
