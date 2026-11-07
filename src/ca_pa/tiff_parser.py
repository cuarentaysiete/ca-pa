"""Layered TIFF (Photoshop) parser: structure without pixels.

Photoshop's layered TIFF keeps everything a PSD keeps, but rearranged:

* ``34377``  ImageResource -- ``8BIM`` resource blocks (same list as a PSD)
* ``37724``  Layer state -- 24 bytes per layer: rect + depth + opacity + blend
* ``37725``  Layer names -- Pascal string + visibility flag + key per layer
* ``37726``  Additional layer info -- ALI block streams, one per layer
         (effects, colours, text-tool, vector masks, ...)
* one extra IFD per layer holding its pixel strips -- *never read*

The parser follows only the IFD chain far enough to read tag *values* that
fit in the IFD entry; long values (the layer ALI stream, resource blocks)
are read from the TIFF data area. Strip offsets/counts are never followed,
so a 2 GB file costs a few megabytes.

Three shapes of layer data are understood, all via tag ``37724``:

* **Classic** -- the 37724/37725/37726 triple (little- or big-endian).
* **Byte-swapped** -- a whole *byte-swapped* PSD layer section (``MIB8``
  blocks); only the small metadata blocks are read, large image blocks are
  skipped.  Best-effort: a given exporter may drop individual markers.
* **Standard embedded** -- a normal, non-swapped "Adobe Photoshop Document
  Data Block" holding ordinary ``8BIM`` resources, one of which is ``Layr``.
  Its layer records are byte-identical to a PSD's, so the section is parsed
  with the shared PSD record reader and reproduces the file exactly.

BigTIFF is detected and rejected with a clear error for now.
"""

from __future__ import annotations

import io
import os
import struct
from typing import IO, Any

from . import labels
from .errors import ParseError, UnsupportedFormatError
from .model import DocumentInfo, LayerNode

TAG_IMAGE_WIDTH = 256
TAG_IMAGE_LENGTH = 257
TAG_BITS_PER_SAMPLE = 258
TAG_COMPRESSION = 259
TAG_PHOTOMETRIC = 262
TAG_STRIP_OFFSETS = 273
TAG_SAMPLES_PER_PIXEL = 277
TAG_STRIP_BYTE_COUNTS = 279
TAG_EXTRA_SAMPLES = 339
TAG_X_RESOLUTION = 282
TAG_Y_RESOLUTION = 283
TAG_RESOLUTION_UNIT = 296

TAG_IMAGE_RESOURCES = 34377
TAG_LAYER_STATE = 37724
TAG_LAYER_NAMES = 37725
TAG_ADDITIONAL_LAYER_INFO = 37726
TAG_MERGE_INFO = 37727
TAG_PRINT_INFO = 37728
TAG_PRINT_SCALE = 37729

PHOTOMETRIC_NAMES = {
    0: "WhiteIsZero",
    1: "BlackIsZero",
    2: "RGB",
    3: "Palette",
    4: "CMYK",
    5: "YCbCr",
    6: "CIELab",
}


class _TIFFReader:
    """Minimal, byte-order aware reader for one classic-TIFF IFD0."""

    def __init__(self, fp: IO[bytes], byte_order: bytes):
        self.fp = fp
        self.endian = "<" if byte_order == b"II" else ">"
        self.offset_size = 4  # classic TIFF; BigTIFF is rejected in _open_tiff
        self._fp = fp

    def _read_at(self, offset: int, size: int) -> bytes:
        self.fp.seek(offset)
        data = self.fp.read(size)
        if len(data) != size:
            raise ParseError("truncated TIFF: cannot read %d bytes at offset %d" % (size, offset))
        return data

    def u16(self, data: bytes, off: int = 0) -> int:
        return struct.unpack_from(self.endian + "H", data, off)[0]

    def u32(self, data: bytes, off: int = 0) -> int:
        return struct.unpack_from(self.endian + "I", data, off)[0]

    def s32(self, data: bytes, off: int = 0) -> int:
        return struct.unpack_from(self.endian + "i", data, off)[0]

    def value(self, tag: int, typ: int, count: int, entry: bytes) -> bytes:
        """Return the raw bytes of a tag value (inline or in the data area).

        ``entry`` is the full 12-byte IFD entry; values of 4 bytes or fewer
        sit inline at bytes 8-11, longer ones behind a big/little-endian
        offset pointer.
        """
        sizes = {1: 1, 2: 2, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
        n = sizes.get(typ, 1) * count
        if n <= 4:
            return entry[8 : 8 + n]
        offset = self.u32(entry, 8)
        return self._read_at(offset, n)


def _open_tiff(path: str) -> tuple[IO[bytes], _TIFFReader, dict[int, tuple[int, int, bytes, bytes]]]:
    """Open a classic TIFF; returns (file, reader, IFD0 entries).

    The file is left open for the caller to close.  Long tag values that
    live in the TIFF data area are read on demand through ``reader``.
    """
    fp = open(path, "rb")
    try:
        head = fp.read(8)
        if len(head) < 8:
            raise UnsupportedFormatError("file is too small to be a TIFF")
        byte_order = head[:2]
        if byte_order not in (b"II", b"MM"):
            raise UnsupportedFormatError("not a TIFF file (bad byte-order mark)")
        reader = _TIFFReader(fp, byte_order)
        version = reader.u16(head, 2)
        if version == 43:
            raise UnsupportedFormatError(
                "BigTIFF is not supported yet (the file is a valid layered TIFF, "
                "but ca-pa does not read the 64-bit variant)"
            )
        if version != 42:
            raise UnsupportedFormatError("not a TIFF file (version %d)" % version)
        ifd0_offset = reader.u32(head, 4)
        entries, _extra = _read_ifd(reader, ifd0_offset)
    except Exception:
        fp.close()
        raise
    return fp, reader, entries


def _read_ifd(reader: _TIFFReader, offset: int) -> tuple[dict[int, tuple[int, int, bytes, bytes]], bytes]:
    """Read one IFD: returns {tag: (type, count, inline_bytes, whole_entry)}."""
    (n,) = struct.unpack_from(reader.endian + "H", reader._read_at(offset, 2))
    entries: dict[int, tuple[int, int, bytes, bytes]] = {}
    pos = offset + 2
    for _ in range(n):
        raw = reader._read_at(pos, 12)
        tag = reader.u16(raw, 0)
        typ = reader.u16(raw, 2)
        count = reader.u32(raw, 4)
        entries[tag] = (typ, count, raw[8:12], raw)
        pos += 12
    extra = reader._read_at(offset + 2 + 12 * n, 4)  # next-IFD pointer (not followed)
    return entries, extra


def _tag_bytes(reader: _TIFFReader, entry, tag: int) -> bytes | None:
    if entry is None:
        return None
    typ, count, _inline, raw = entry
    return reader.value(tag, typ, count, raw)


def _parse_names(data: bytes) -> list[tuple[str, bool, bytes]]:
    """37725 Layer names -> [(name, visible, blend_key)].

    Each entry: u8 name length, name, padding to even, u8 flags
    (bit 0 = hidden), 4-byte blend-mode key, padding to even.
    """
    out: list[tuple[str, bool, bytes]] = []
    i = 0
    n = len(data)
    while i + 1 < n:
        nlen = data[i]
        if i + 1 + nlen > n:
            break
        name = data[i + 1 : i + 1 + nlen].rstrip(b"\x00").decode("latin-1", "replace")
        i += 1 + nlen + ((1 + nlen) % 2)  # length byte + name, padded to even
        if i + 5 > n:
            break
        flags = data[i]
        key = data[i + 1 : i + 5]
        out.append((name, not bool(flags & 1), key))
        i += 5 + (5 % 2)
    return out


def _first_int(reader: _TIFFReader, entry, default: int = 0) -> int:
    """Interpret a numeric IFD entry (SHORT, LONG or BYTE) as a single int.

    Real Photoshop TIFFs store width/height/etc. as SHORT or LONG depending
    on the file, so we honour the entry's declared type instead of assuming
    a fixed width.
    """
    if entry is None:
        return default
    typ, count, _inline, raw = entry
    if count <= 0:
        return default
    data = reader.value(reader.u16(raw, 0), typ, count, raw)
    if typ in (4, 5, 9, 10, 11, 12) and len(data) >= 4:  # LONG/rationals
        return reader.u32(data)
    if typ in (3, 8) and len(data) >= 2:  # SHORT/SSHORT
        return reader.u16(data)
    if len(data) >= 1:  # BYTE/UBYTE/UNDEFINED
        return data[0]
    return default


def _parse_state(data: bytes) -> list[dict[str, Any]]:
    """37724 Layer state -> one dict per layer (24-byte records)."""
    out = []
    for i in range(0, len(data) // 24 * 24, 24):
        rec = data[i : i + 24]
        top, left, bottom, right = struct.unpack_from(">4i", rec, 0)
        depth, opacity = rec[16], rec[17]
        (blend,) = struct.unpack_from(">i", rec, 18)
        out.append(
            {
                "bounds": (left, top, right, bottom),
                "bit_depth": depth,
                "opacity": opacity,
                "blend_key": struct.pack(">i", blend)[:4],
            }
        )
    return out


def _fxly_effects(raw: bytes) -> list[str]:
    """Effect names from an 'fxly' ALI block (keys 8017 or 8052).

    8017: u32 version, u32 count, then count entries of u32 key + u32 size.
    8052: u16 version, u32 count, same entries.
    """
    if len(raw) < 8 or struct.unpack(">I", raw[:4])[0] not in (1, 2, 3):
        if len(raw) < 6:
            return []
        p = 6  # 8052: u16 version + u32 count
    else:
        p = 8  # 8017: u32 version + u32 count
    if p + 4 > len(raw):
        return []
    count = struct.unpack(">I", raw[p : p + 4])[0]
    p += 4
    names: list[str] = []
    seen: set[str] = set()
    for _ in range(count):
        if p + 8 > len(raw):
            break
        key, size = struct.unpack(">II", raw[p : p + 8])
        p += 8
        if p + size > len(raw):
            break
        p += size
        label = labels.FXLY_KEY_NAMES.get(key)
        if label and label not in seen:
            seen.add(label)
            names.append(label)
    return names


def _parse_resources(data: bytes) -> dict[int, list[bytes]]:
    """34377 -> {resource id: [payload, ...]}."""
    out: dict[int, list[bytes]] = {}
    i = 0
    while i + 12 <= len(data):
        rid = struct.unpack_from(">H", data, i)[0]
        pad = data[i + 2]
        sig = data[i + 3 : i + 7]
        length = struct.unpack_from(">I", data, i + 7)[0]
        if sig != b"8BIM" or i + 12 + length > len(data):
            break
        out.setdefault(rid, []).append(data[i + 12 : i + 12 + length])
        i += 12 + length
    return out


def _parse_ali_stream(data: bytes, n_layers: int) -> list[dict[bytes, bytes]]:
    """37726 Additional layer info -> per-layer {4-byte key: payload}.

    Photoshop writes, for each layer in order, a 4-byte length followed by
    that layer's ALI blocks (u16 key + u32 length + data).
    """
    per_layer: list[dict[bytes, bytes]] = [{} for _ in range(n_layers)]
    i = 0
    for layer_idx in range(n_layers):
        if i + 4 > len(data):
            break
        length = struct.unpack_from(">I", data, i)[0]
        i += 4
        end = min(i + length, len(data))
        blocks = per_layer[layer_idx]
        while i + 6 <= end:
            key = data[i : i + 4]
            size = struct.unpack_from(">I", data, i + 4)[0]
            if i + 8 + size > end:
                break
            blocks.setdefault(key, data[i + 8 : i + 8 + size])
            i += 8 + size
        i = end
    return per_layer


ADJUSTMENT_ALI_KEYS = {
    b"brit": "Brightness/Contrast",
    b"blwh": "Black & White",
    b"blnc": "Color Balance",
    b"clrL": "Color Lookup",
    b"mixr": "Channel Mixer",
    b"curv": "Curves",
    b"grdm": "Gradient Map",
    b"expA": "Exposure",
    b"hue2": "Hue/Saturation",
    b"hue ": "Hue/Saturation",
    b"levl": "Levels",
    b"phfl": "Photo Filter",
    b"selc": "Selective Color",
    b"thrs": "Threshold",
    b"vibA": "Vibrance",
}


def _layer_kind(blocks: dict[bytes, bytes], flags: bytes) -> tuple[str, str]:
    if b"lsct" in blocks:
        return "group", "Folder"
    if b"TySh" in blocks or b"tySh" in blocks:
        return "text", "Text"
    if b"plLd" in blocks or b"PlLd" in blocks or b"SoLd" in blocks or b"SoLE" in blocks:
        return "smartobject", "Smart Object"
    for key, name in ADJUSTMENT_ALI_KEYS.items():
        if key in blocks:
            return "adjustment", "Adjustment: " + name
    if any(k in blocks for k in (b"vstk", b"vscg", b"vogk", b"vmsk")):
        return "shape", "Shape"
    return "pixel", "Pixel"


_EMBED_MARKER = b"Adobe Photoshop Document Data Block"
_MIB8 = b"MIB8"  # little-endian spelling of the "8BIM" block signature


class _FileWindow(io.IOBase):
    """Seekable view over an open file limited to ``[base, end)``.

    Lets psd-tools' low-level record readers run straight on the embedded
    layer section of a layered TIFF *without* loading that section -- which
    can be hundreds of MB and ends in raw channel/pixel data -- into memory.
    Reads are transparently redirected to the underlying file handle.
    """

    def __init__(self, fp: IO[bytes], base: int, end: int):
        self._fp = fp
        self._base = base
        self._end = end
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos = self._pos + offset
        else:  # SEEK_END: measured from the end of this window
            self._pos = (self._end - self._base) + offset
        return self._pos

    def read(self, size: int = -1) -> bytes:
        self._fp.seek(self._base + self._pos)
        if size < 0:
            size = max(0, (self._end - self._base) - self._pos)
        data = self._fp.read(size)
        self._pos += len(data)
        return data


def _photo_to_psd_color(photo: int) -> int:
    """Map a TIFF PhotometricInterpretation onto a PSD colour-mode number.

    The two schemes differ (TIFF ``2`` = RGB, PSD ``3`` = RGB), so the value
    read from the IFD cannot be fed straight into a :class:`FileHeader`.
    """
    return {0: 0, 1: 1, 2: 3, 3: 2, 4: 4, 5: 3, 6: 9}.get(photo, 3)


def _entry_size(typ: int, count: int) -> int:
    """Byte size of an IFD value for the given TIFF type and count."""
    return {1: 1, 2: 2, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}.get(
        typ, 1
    ) * count


def _state_value_offset(
    reader: _TIFFReader, entry: tuple[int, int, bytes, bytes] | None
) -> int | None:
    """Absolute file offset of the tag-37724 value, or ``None`` if inline."""
    if entry is None:
        return None
    typ, count, _inline, raw = entry
    if _entry_size(typ, count) <= 4:
        return None  # tiny inline value cannot hold an embedded document
    return reader.u32(raw, 8)


def _embed_kind(reader: _TIFFReader, entries) -> str | None:
    """Classify the tag-37724 payload without loading it.

    Returns ``"standard"`` for the normal embedded Photoshop layer section
    (marker followed by ``8BIM`` resources, holding a ``Layr`` block whose
    records are byte-identical to a PSD's), ``"mib8"`` for the non-standard
    byte-swapped variant, or ``None`` when 37724 holds classic layer state.
    """
    off = _state_value_offset(reader, entries.get(TAG_LAYER_STATE))
    if off is None:
        return None
    fp = reader.fp
    fp.seek(off)
    head = fp.read(len(_EMBED_MARKER) + 8)
    if not head.startswith(_EMBED_MARKER):
        return None
    after = head[len(_EMBED_MARKER) :]
    nul = 1 if after[:1] == b"\x00" else 0
    tag4 = after[nul : nul + 4]
    if tag4 == b"8BIM":
        return "standard"
    if tag4 == _MIB8:
        return "mib8"
    return None


def _parse_standard_embedded(
    reader: _TIFFReader,
    entries: dict[int, tuple[int, int, bytes, bytes]],
    path: str,
    size_bytes: int,
    warnings: list[str],
) -> DocumentInfo:
    """Decode a *standard* embedded Photoshop layer section from a TIFF.

    Some writers (this user's ``sample2.tif``) store, in tag 37724, an
    "Adobe Photoshop Document Data Block" that is simply a mini image-resource
    stream: ``marker NUL`` followed by ordinary ``8BIM`` resources, one of
    which is ``Layr``.  The ``Layr`` payload is ``u16 count`` followed by the
    exact same layer records a PSD keeps -- so they are parsed with
    psd-tools' own record reader and mapped through the shared
    :func:`build_doc_from_records`.  Only the records and their small ALI
    blocks are read; the trailing channel/pixel data is never touched.
    """
    from psd_tools.psd.header import FileHeader
    from psd_tools.psd.image_resources import ImageResources
    from psd_tools.psd.layer_and_mask import LayerRecords

    from .psd_parser import ENCODING, build_doc_from_records

    width = _first_int(reader, entries.get(TAG_IMAGE_WIDTH))
    height = _first_int(reader, entries.get(TAG_IMAGE_LENGTH))
    photo = _first_int(reader, entries.get(TAG_PHOTOMETRIC), 2)
    spp = _first_int(reader, entries.get(TAG_SAMPLES_PER_PIXEL), 3)
    bps_raw = _tag_bytes(reader, entries.get(TAG_BITS_PER_SAMPLE), TAG_BITS_PER_SAMPLE) or b""
    depth = max([reader.u16(bps_raw, j * 2) for j in range(len(bps_raw) // 2)] or [8])

    off = _state_value_offset(reader, entries[TAG_LAYER_STATE])
    if off is None:  # pragma: no cover - _embed_kind already checked this
        raise ParseError("embedded layer block vanished")

    fp = reader.fp
    fp.seek(off + len(_EMBED_MARKER))
    nul = 1 if fp.read(1) == b"\x00" else 0
    pos = off + len(_EMBED_MARKER) + nul
    body_start = None
    while pos + 12 <= size_bytes:
        fp.seek(pos)
        hdr = fp.read(12)
        if len(hdr) < 12 or hdr[:4] != b"8BIM":
            break
        key = hdr[4:8]
        length = struct.unpack_from(">I", hdr, 8)[0]
        if key == b"Layr":
            body_start = pos + 12
            break
        pos += 12 + length
    if body_start is None:
        raise ParseError("embedded layer block found but holds no 'Layr' resource")

    fp.seek(body_start)
    (count,) = struct.unpack_from(">h", fp.read(2))
    window = _FileWindow(fp, body_start + 2, size_bytes)
    records = LayerRecords.read(window, abs(count), ENCODING, 1)

    has_alpha = any(any(c.id == -1 for c in r.channel_info) for r in records)
    # Document-level channel count mirrors the PSD header: alpha lives in
    # per-layer masks, not as an extra document channel.
    header = FileHeader(
        channels=spp,
        height=height,
        width=width,
        depth=depth,
        color_mode=_photo_to_psd_color(photo),
    )
    warnings.append("Photoshop layers embedded in the TIFF (standard 'Layr' block)")
    return build_doc_from_records(
        header,
        ImageResources(),
        records,
        path=path,
        size_bytes=size_bytes,
        format_name="tiff",
        warnings=warnings,
        count=count,
    )


def _looks_like_embedded(blob: bytes) -> bool:
    """True when ``blob`` is the byte-swapped PSD "Document Data Block"."""
    head = blob.lstrip(b"\r\n ")[: len(_EMBED_MARKER)]
    return head == _EMBED_MARKER


def _mib8_index(blob: bytes) -> list[tuple[int, bytes, int]]:
    """``(offset, key, length)`` of every ``MIB8`` block, in file order."""
    out: list[tuple[int, bytes, int]] = []
    i, n = 0, len(blob)
    while i + 8 <= n:
        j = blob.find(_MIB8, i)
        if j < 0 or j + 12 > n:
            break
        key = blob[j + 4 : j + 8].rstrip(b"\x00")
        ln = struct.unpack_from("<I", blob, j + 8)[0]
        out.append((j, key, max(0, min(ln, n - j - 12))))
        i = j + 8
    return out


def _blend_key_in(data: bytes) -> bytes | None:
    """First Photoshop blend-mode key (4 ASCII chars) embedded in ``data``."""
    for key in labels.BLEND_MODE_NAMES:
        if len(key) == 4 and key in data:
            return key
    return None


def _mib8_name(ali: dict[bytes, bytes]) -> str | None:
    """UTF-16LE layer name from the ('luni'/'inul') tagged block."""
    uni = ali.get(b"luni")
    if uni and len(uni) >= 4:
        cc = struct.unpack_from("<I", uni, 0)[0]
        txt = uni[4 : 4 + cc * 2].decode("utf-16-le", "replace")
        return txt.rstrip("\x00") or None
    return None


def _mib8_effects(ali: dict[bytes, bytes]) -> list[str]:
    """Effect labels from the modern 'lfx2' descriptor block, best effort.

    The 'lfx2' payload is a structured PS Object descriptor; rather than
    reverse the whole format we look for the known effect class-name strings
    it embeds.  Files without effects carry no such block.
    """
    raw = ali.get(b"lfx2")
    if not raw:
        return []
    names: list[str] = []
    for cls, label in labels.EFFECT_LABELS.items():
        if cls.encode() in raw and label not in names:
            names.append(label)
    return names


def _rec_divider(rec: dict[str, Any]) -> int | None:
    """Section-divider state of a record from its 'lsct' block, else None."""
    div = rec["ali"].get(b"lsct")
    if div and len(div) >= 4:
        return struct.unpack_from("<I", div, 0)[0]
    return None


def _embedded_node(rec: dict[str, Any], width: int, height: int) -> LayerNode:
    """Map one decoded byte-swapped MIB8 record onto a :class:`LayerNode`.

    Every field a normal PSD keeps is present in the record: name, blend
    mode, opacity, visibility, sheet colour, channel/mask ids, adjustment
    and effect tagged blocks.  Keys are already reversed back to their
    standard PSD spelling by :func:`_mib8_records`.
    """
    ali = rec["ali"]
    keys = set(ali)

    # Colour: RGBA swatch ('lsdk') or the 0-11 sheet code ('lclr').
    color = None
    lsd = ali.get(b"lsdk")
    if lsd and len(lsd) >= 4:
        color = labels.rgba_to_hex(lsd[:4])
    if color is None:
        cl = ali.get(b"lclr")
        if cl and len(cl) >= 4:
            color = labels.SHEET_COLORS.get(struct.unpack_from("<I", cl, 0)[0])

    chids = rec["chids"]
    has_alpha_mask = -2 in chids or -3 in chids
    has_vector_mask = b"Shks" in keys or b"shks" in keys

    if b"plLd" in keys or b"PlLd" in keys or b"SoLd" in keys or b"SoLE" in keys:
        kind, klabel = "smartobject", "Smart Object"
    elif b"TySh" in keys or b"tySh" in keys:
        kind, klabel = "text", "Text"
    elif any(k in keys and k not in labels.NON_ADJUSTMENT_KEYS for k in labels.ADJUSTMENT_NAMES):
        adj = next(
            k for k in labels.ADJUSTMENT_NAMES if k in keys and k not in labels.NON_ADJUSTMENT_KEYS
        )
        kind, klabel = "adjustment", "Adjustment: " + labels.ADJUSTMENT_NAMES[adj]
    elif any(k in keys for k in (b"vstk", b"vscg", b"vogk", b"vmsk")):
        kind, klabel = "shape", "Shape"
    else:
        kind, klabel = "pixel", "Pixel"

    return LayerNode(
        name=_mib8_name(ali) or rec.get("pascal") or "Layer",
        kind=kind,
        kind_label=klabel,
        color=color,
        opacity=rec["opacity"],
        blend_mode=labels.blend_mode_name(rec["blend_key"]),
        visible=rec["visible"],
        bounds=rec["bounds"] or (0, 0, width, height),
        has_alpha_mask=has_alpha_mask,
        has_vector_mask=has_vector_mask,
        effects=_mib8_effects(ali),
    )


def _tagged_blocks(extra: bytes) -> tuple[int, dict[bytes, bytes]]:
    """Decode the run of ``MIB8`` tagged blocks at the END of a record's extra
    region (which also holds MaskData, BlendingRanges and the Pascal name).

    Returns ``(start, blocks)`` where *blocks* maps each tag key -- reversed
    back to its standard PSD spelling -- to its payload.  The start is the
    earliest offset whose greedy block run reaches the very end of *extra*;
    returns ``(-1, {})`` when no such run exists.
    """
    m = len(extra)
    best_start, best = -1, {}
    p = 0
    while True:
        p = extra.find(_MIB8, p)
        if p < 0:
            break
        q, d, ok = p, {}, True
        while q + 12 <= m and extra[q : q + 4] == _MIB8:
            klen = struct.unpack_from("<I", extra, q + 8)[0]
            if q + 12 + klen > m:
                ok = False
                break
            d.setdefault(extra[q + 4 : q + 8][::-1], extra[q + 12 : q + 12 + klen])
            q += 12 + klen
        if ok and q == m and len(d) > len(best):
            best_start, best = p, d
        p += 1
    return best_start, best


def _mib8_pascal(extra: bytes, start: int) -> str | None:
    """Recover the ASCII Pascal name that precedes the tagged-block run."""
    if start >= 2 and extra[start - 1] == 0:
        i = start - 2
        while i >= 0 and 32 <= extra[i] < 127:
            i -= 1
        if i >= 0:
            name = extra[i + 1 : start - 1]
            if len(name) == extra[i] and name:
                return name.decode("latin-1")
    return None


def _mib8_records(blob: bytes) -> list[dict[str, Any]]:
    """Forward-decode the byte-swapped layer records of a ``MIB8`` block.

    A ``MIB8`` "Adobe Photoshop Document Data Block" is a standard PSD layer
    section whose multi-byte integers are little-endian and whose 4-byte
    tag/blend keys are stored reversed (``8BIM`` -> ``MIB8``, ``norm`` ->
    ``mron``, ``lclr`` -> ``rlcl``).  Text and tagged-block payloads are left
    in their natural order, so honouring the per-field endianness recovers
    *every* field a normal PSD keeps.  The records sit right after a
    ``MIB8 ryaL`` frame (reversed ``Layr``) and a u16 count; the trailing
    channel/pixel data is never read.
    """
    j = blob.find(_MIB8)
    start = None
    count = 0
    while j >= 0 and j + 14 <= len(blob):
        if blob[j + 4 : j + 8] == b"ryaL":  # reversed "Layr"
            count = struct.unpack_from("<H", blob, j + 12)[0]
            start = j + 14
            break
        j = blob.find(_MIB8, j + 1)
    if start is None or count <= 0:
        return []
    out: list[dict[str, Any]] = []
    P = start
    n = len(blob)
    for _ in range(count):
        if P + 18 > n:
            break
        top, left, bottom, right, nch = struct.unpack_from("<4iH", blob, P)
        cp = P + 18 + nch * 6
        if cp + 16 > n or blob[cp : cp + 4] != _MIB8:
            break  # lost alignment: stop rather than emit garbage
        elen = struct.unpack_from("<I", blob, cp + 12)[0]
        extra_end = cp + 16 + elen
        if elen > n or extra_end > n:
            break
        chids = (
            [struct.unpack_from("<h", blob, P + 18 + c * 6)[0] for c in range(nch)]
            if 0 <= nch <= 128
            else []
        )
        extra = blob[cp + 16 : extra_end]
        tstart, ali = _tagged_blocks(extra)
        out.append(
            {
                "bounds": (left, top, right, bottom),
                "blend_key": blob[cp + 4 : cp + 8][::-1],
                "opacity": blob[cp + 8],
                "visible": not bool(blob[cp + 10] & 2),
                "chids": chids,
                "ali": ali,
                "pascal": _mib8_pascal(extra, tstart),
            }
        )
        P = extra_end
    return out


def _parse_embedded_legacy(blob: bytes, width: int, height: int) -> list[LayerNode]:
    """Fallback: reconstruct the tree from ``inul`` anchors only.

    Used when the record stream is in a shape :func:`_mib8_records` cannot
    decode.  Recovers names, sheet colour, group structure and (best effort)
    opacity/blend -- but not masks/effects.
    """
    idx = _mib8_index(blob)
    inuls = [j for j, key, _ in idx if key == b"inul"]
    if not inuls:
        return []
    n = len(blob)
    recs: list[dict[str, Any]] = []
    for k, p in enumerate(inuls):
        q = inuls[k + 1] if k + 1 < len(inuls) else n
        ln = struct.unpack_from("<I", blob, p + 8)[0]
        nch = struct.unpack_from("<I", blob, p + 12)[0] if ln >= 4 else 0
        nch = max(0, min(nch, (q - 16 - p) // 2))
        name = blob[p + 16 : p + 16 + nch * 2].decode("utf-16-le", "replace").rstrip("\x00")
        meta = {key: blob[pos + 12 : pos + 12 + blen] for pos, key, blen in idx if p < pos < q and blen <= 4096}
        div = None
        tc = meta.get(b"tcsl")
        if tc and len(tc) >= 4:
            div = struct.unpack_from("<I", tc)[0]
        recs.append({"name": name, "div": div, "meta": meta})

    def node(rec: dict[str, Any]) -> LayerNode:
        meta = rec["meta"]
        color = None
        cl = meta.get(b"rlcl")
        if cl and len(cl) >= 4:
            color = labels.SHEET_COLORS.get(struct.unpack_from("<I", cl)[0])
        opacity = 255
        op = meta.get(b"rloc")
        if op and len(op) >= 4:
            opacity = max(0, min(255, struct.unpack_from("<I", op)[0]))
        ty = meta.get(b"rsnl")
        vu = meta.get(b"vruc")
        visible = True
        kind, klabel = "pixel", "Pixel"
        if ty == b"ryal":
            kind, klabel = "smartobject", "Smart Object"
        elif vu is not None or ty == b"tnoc":
            kind, klabel = "adjustment", "Adjustment"
            if ty == b"tnoc":
                visible = False
        return LayerNode(
            name=rec["name"],
            kind=kind,
            kind_label=klabel,
            color=color,
            opacity=opacity,
            blend_mode=labels.blend_mode_name(_blend_key_in(vu) if vu else None),
            visible=visible,
            bounds=(0, 0, width, height),
        )

    root = LayerNode(name="<document>", kind="group", kind_label="")
    stack: list[LayerNode] = [root]
    for rec in reversed(recs):
        if rec["div"] == 3:
            if len(stack) > 1:
                stack.pop()
            continue
        node_r = node(rec)
        if rec["div"] in (1, 2):
            node_r.kind, node_r.kind_label = "group", "Folder"
            node_r.collapsed = rec["div"] == 2
            stack[-1].children.append(node_r)
            stack.append(node_r)
        else:
            stack[-1].children.append(node_r)
    while len(stack) > 1:
        stack[-2].children.append(stack.pop())
    return root.children


def _parse_embedded_layers(blob: bytes, width: int, height: int) -> list[LayerNode]:
    """Decode the non-standard byte-swapped layer section stored in tag 37724.

    Certain writers embed, instead of the classic 37724/37725/37726 triple,
    a whole little-endian copy of the Photoshop layer section (``MIB8``
    blocks).  We forward-decode that record stream precisely (:func:`_mib8_
    records`), recovering names, blend, opacity, visibility, masks, colours,
    adjustment kind and effects, then rebuild the top-down tree from the
    section-divider markers.  If the stream is in an unrecognised shape we
    fall back to the older anchor-based best-effort pass.
    """
    recs = _mib8_records(blob)
    if not recs:
        return _parse_embedded_legacy(blob, width, height)
    root = LayerNode(name="<document>", kind="group", kind_label="")
    stack: list[LayerNode] = [root]
    for rec in reversed(recs):
        div = _rec_divider(rec)
        if div == 3:  # '</Layer group>' end divider: close innermost folder
            if len(stack) > 1:
                stack.pop()
            continue
        node = _embedded_node(rec, width, height)
        if div in (1, 2):  # named folder start
            node.kind, node.kind_label = "group", "Folder"
            node.collapsed = div == 2
            stack[-1].children.append(node)
            stack.append(node)
        else:
            stack[-1].children.append(node)
    while len(stack) > 1:  # unbalanced file: flush leftovers onto the parent
        stack[-2].children.append(stack.pop())
    return root.children


def parse_tiff(path: str) -> DocumentInfo:
    path = os.fspath(path)
    size_bytes = os.path.getsize(path)
    fp, reader, entries = _open_tiff(path)
    try:
        return _parse_tiff_body(reader, entries, path, size_bytes)
    finally:
        fp.close()


def _parse_tiff_body(
    reader: _TIFFReader,
    entries: dict[int, tuple[int, int, bytes, bytes]],
    path: str,
    size_bytes: int,
) -> DocumentInfo:
    warnings: list[str] = []

    def get(tag: int) -> bytes | None:
        return _tag_bytes(reader, entries.get(tag), tag)

    def tag_int(tag: int, default: int = 0) -> int:
        return _first_int(reader, entries.get(tag), default)

    width = tag_int(TAG_IMAGE_WIDTH)
    height = tag_int(TAG_IMAGE_LENGTH)
    spp = tag_int(TAG_SAMPLES_PER_PIXEL, 3)
    bps_raw = get(TAG_BITS_PER_SAMPLE) or b""
    bps = [reader.u16(bps_raw, j * 2) for j in range(len(bps_raw) // 2)] or [8]
    depth = max(bps)
    photo = tag_int(TAG_PHOTOMETRIC, 2)
    extra_samples = tag_int(TAG_EXTRA_SAMPLES)
    has_alpha = extra_samples > 0

    if _embed_kind(reader, entries) == "standard":
        return _parse_standard_embedded(reader, entries, path, size_bytes, warnings)

    state_raw = get(TAG_LAYER_STATE)
    _resources = _parse_resources(get(TAG_IMAGE_RESOURCES) or b"")

    doc = DocumentInfo(
        path=path,
        format="tiff",
        size_bytes=size_bytes,
        width=width,
        height=height,
        bit_depth=depth,
        color_mode=PHOTOMETRIC_NAMES.get(photo, str(photo)),
        channels=spp + (1 if has_alpha else 0),
        has_alpha=has_alpha,
        warnings=warnings,
    )

    if state_raw is not None and _looks_like_embedded(state_raw):
        # Non-standard: a whole byte-swapped PSD document in tag 37724.
        doc.layers = _parse_embedded_layers(state_raw, width, height)
        if not doc.layers:
            doc.flat = True
            doc.warnings.append("embedded layer block present but no layers could be read")
        else:
            doc.warnings.append(
                "non-standard byte-swapped embedded Photoshop layer block"
            )
        return doc

    if state_raw is None:
        # No 37724 -> no Photoshop layer information: a flat TIFF.
        doc.flat = True
        doc.warnings.append(
            "flattened file: no Photoshop layer information (tags 37724/37726 missing)"
        )
        doc.layers = [LayerNode(name=os.path.basename(path), bounds=(0, 0, width, height))]
        return doc

    states = _parse_state(state_raw)
    n_layers = len(states)
    names = _parse_names(get(TAG_LAYER_NAMES) or b"")
    if len(names) != n_layers:
        warnings.append(
            "layer count mismatch: %d layer states, %d layer names" % (n_layers, len(names))
        )
    ali_blocks = _parse_ali_stream(get(TAG_ADDITIONAL_LAYER_INFO) or b"", n_layers)
    if len(ali_blocks) < n_layers:
        warnings.append("additional layer information is truncated")

    layers: list[LayerNode] = []
    for i in range(n_layers):
        state = states[i]
        name, visible, key = names[i] if i < len(names) else (f"Layer {i + 1}", True, b"none")
        blocks = ali_blocks[i] if i < len(ali_blocks) else {}
        kind, kind_label = _layer_kind(blocks, key)

        color = None
        if b"lsdk" in blocks and len(blocks[b"lsdk"]) >= 4:
            color = labels.rgba_to_hex(blocks[b"lsdk"][:4])
        if color is None and b"lclr" in blocks and len(blocks[b"lclr"]) >= 4:
            color = labels.SHEET_COLORS.get(reader.u32(blocks[b"lclr"][:4]))

        effects = _fxly_effects(blocks[b"fxly"]) if b"fxly" in blocks else []
        layers.append(
            LayerNode(
                name=name,
                kind=kind,
                kind_label=kind_label,
                color=color,
                opacity=state["opacity"],
                blend_mode=labels.blend_mode_name(state["blend_key"]),
                visible=visible,
                bounds=tuple(state["bounds"]),
                has_alpha_mask=key == b"LMsk",
                has_vector_mask=any(k in blocks for k in (b"vmsk", b"vogk", b"lsco", b"lsvm")),
                effects=effects,
            )
        )
    # 37724 lists layers bottom-to-top, like the PSD layer section.
    layers.reverse()
    doc.layers = layers
    return doc
