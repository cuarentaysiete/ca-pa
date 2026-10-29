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

BigTIFF is detected and rejected with a clear error for now.
"""

from __future__ import annotations

import os
import struct
from typing import IO, Any

from . import labels
from .errors import ParseError, UnsupportedFormatError
from .model import DocumentInfo, LayerNode

TAG_IMAGE_WIDTH = 256
TAG_IMAGE_LENGTH = 257
TAG_SAMPLES_PER_PIXEL = 258
TAG_BITS_PER_SAMPLE = 259
TAG_COMPRESSION = 259
TAG_PHOTOMETRIC = 262
TAG_STRIP_OFFSETS = 273
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


def _looks_like_embedded(blob: bytes) -> bool:
    """True when ``blob`` is the byte-swapped PSD "Document Data Block"."""
    head = blob.lstrip(b"\r\n ")[: len(_EMBED_MARKER)]
    return head == _EMBED_MARKER


def _mib8_findall(blob: bytes, key: bytes, start: int = 0) -> list[int]:
    """Offsets of every ``8BIM``+``key`` block in ``blob`` (little-endian)."""
    out: list[int] = []
    sig = _MIB8 + key
    i = start
    while True:
        j = blob.find(sig, i)
        if j < 0:
            return out
        out.append(j)
        i = j + 8


def _mib8_value(blob: bytes, pos: int, cap: int = 8192) -> bytes:
    """Payload of the block at ``pos``; its length field is little-endian."""
    if pos + 12 > len(blob):
        return b""
    ln = struct.unpack_from("<I", blob, pos + 8)[0]
    ln = max(0, min(ln, cap, len(blob) - pos - 12))
    return blob[pos + 12 : pos + 12 + ln]


def _mib8_first(blob: bytes, key: bytes, lo: int, hi: int) -> bytes | None:
    j = blob.find(_MIB8 + key, lo, hi)
    if j < 0:
        return None
    return _mib8_value(blob, j)


def _parse_embedded_layers(blob: bytes, width: int, height: int) -> list[LayerNode]:
    """Parse the non-standard "Adobe Photoshop Document Data Block".

    Certain writers stash a whole byte-swapped (little-endian) copy of the
    PSD layer section inside TIFF tag 37724 instead of the compact
    37724/37725/37726 triple.  The block is a sequence of ``MIB8`` blocks;
    we read only the small metadata blocks (names, section dividers, colour,
    opacity, type) and skip the large image-data blocks, so even a 2 GB file
    costs a few megabytes.
    """
    inuls = _mib8_findall(blob, b"inul")
    if not inuls:
        return []
    tcsls = _mib8_findall(blob, b"tcsl")
    full = (0, 0, width, height)

    def meta(lo: int, hi: int) -> dict[str, Any]:
        color = None
        cv = _mib8_first(blob, b"rlcl", lo, hi)
        if cv:
            color = labels.SHEET_COLORS.get(cv[0])
        ov = _mib8_first(blob, b"rvyl", lo, hi)
        opacity = ov[0] if ov else 255
        opacity = max(0, min(255, opacity))
        return {"color": color, "opacity": opacity, "type": _mib8_first(blob, b"rsnl", lo, hi)}

    entries: list[tuple[str, int, dict[str, Any]]] = []
    for k, p in enumerate(inuls):
        hi = inuls[k + 1] if k + 1 < len(inuls) else len(blob)
        ln = struct.unpack_from("<I", blob, p + 8)[0]
        nch = struct.unpack_from("<I", blob, p + 12)[0] if ln >= 4 else 0
        name = blob[p + 16 : p + 16 + nch * 2].decode("utf-16-le", "replace").rstrip("\x00")
        divider = 0
        for t in tcsls:
            if p <= t < hi:
                divider = blob[t + 12]  # 0 bbox, 1 open, 2 closed, 3 placeholder
                break
        entries.append((name, divider, meta(p, hi)))

    def kind_for(ty: bytes | None) -> tuple[str, str]:
        if ty == b"tnoc":
            return "adjustment", "Adjustment"
        return "pixel", "Pixel"

    root = LayerNode(name="<document>", kind="group", kind_label="")
    stack: list[LayerNode] = [root]
    for name, divider, m in entries:
        if divider == 3:  # an "open folder" placeholder (</Layer group>)
            stack.append(LayerNode(name=name, kind="group", kind_label="Folder", bounds=full))
        elif divider in (1, 2):  # the folder's real record: name + colour/opacity
            node = stack.pop() if len(stack) > 1 else LayerNode(name=name)
            target = stack[-1]
            node.name = name
            node.kind = "group"
            node.kind_label = "Folder"
            node.collapsed = divider == 2
            node.color = m["color"]
            node.opacity = m["opacity"]
            node.bounds = full
            target.children.append(node)
        else:
            kind, klabel = kind_for(m["type"])
            stack[-1].children.append(
                LayerNode(
                    name=name, kind=kind, kind_label=klabel,
                    color=m["color"], opacity=m["opacity"],
                    visible=True, bounds=full,
                )
            )

    def prune(n: LayerNode) -> None:
        n.children = [
            c for c in n.children
            if not (c.kind == "group" and not c.children and c.name == "</Layer group>")
        ]
        for c in n.children:
            prune(c)

    def rev(n: LayerNode) -> None:
        n.children = list(reversed(n.children))
        for c in n.children:
            rev(c)

    prune(root)
    rev(root)
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
                "non-standard embedded layer block (best-effort metadata)"
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
