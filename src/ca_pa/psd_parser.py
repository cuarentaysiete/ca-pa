"""PSD / PSB parser: full layer metadata, no pixels.

``psd-tools`` normally loads *every* layer's channel data and the merged
image into memory on open (fine for a small file, a multi-gigabyte
allocation for a 2 GB file). ca-pa only needs the layer structure, so this
module re-uses the individual *section* readers of ``psd_tools.psd`` and
skips (seeks past) the two pixel-bearing sections:

* the per-layer channel image data, and
* the merged image data.

What is read instead (all small):

* file header (size, channels, depth, colour mode)
* colour mode data (usually 0 or 1 byte)
* image resource block list (thumbnails, IPTC/XMP metadata, global mask
  names -- a few KB at most)
* the per-layer 600/1000-byte records, which include each layer's
  *Additional Layer Info* tagged blocks (names, colours, effects, masks,
  text-tool settings, ...) -- a few KB per layer

The records are wrapped in psd-tools' own API objects, so classification
(groups, text, shapes, smart objects, adjustments) re-uses the library's
battle-tested logic.
"""

from __future__ import annotations

import io
import os
import struct
from typing import Any

from psd_tools.constants import Tag
from psd_tools.psd import PSD
from psd_tools.psd.base import read_fmt
from psd_tools.psd.bin_utils import (
    is_readable,
    read_exact,
    read_padding,
    read_pascal_string,
)
from psd_tools.psd.color_mode_data import ColorModeData
from psd_tools.psd.header import FileHeader
from psd_tools.psd.image_data import ImageData
from psd_tools.psd.image_resources import ImageResources
from psd_tools.psd.layer_and_mask import (
    ChannelInfo,
    ChannelData,
    ChannelDataList,
    ChannelImageData,
    LayerAndMaskInformation,
    LayerBlendingRanges,
    LayerFlags,
    LayerInfo,
    LayerRecord,
    LayerRecords,
    MaskData,
)
from psd_tools.psd.tagged_blocks import TYPES, TaggedBlock, TaggedBlocks

from .tiff_parser import _FileWindow, _TAG_MAX

from . import labels
from .errors import ParseError, UnsupportedFormatError
from .model import DocumentInfo, LayerNode

import logging

# psd-tools logs a warning for every tagged-block key it does not recognise
# (common in files saved by non-Adobe tools).  That is noise for a structure
# browser, so raise its threshold to hard errors only.
# psd-tools logs a warning (and, for blocks it cannot parse, an error) for
# keys it does not recognise.  That is noise for a structure browser, so push
# the whole package above WARNING -- the app only surfaces its own warnings.
for _name in (
    "psd_tools",
    "psd_tools.psd.tagged_blocks",
    "psd_tools.psd.bin_utils",
    "psd_tools.psd.adjustments",
):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

ENCODING = "macroman"

_COLOR_MODE_NAMES = {
    0: "Bitmap",
    1: "Grayscale",
    2: "Indexed",
    3: "RGB",
    4: "CMYK",
    7: "Multichannel",
    8: "Duotone",
    9: "Lab",
}


def _header(fp: io.BufferedIOBase) -> FileHeader:
    magic = fp.read(4)
    if magic not in (b"8BPS",):
        raise UnsupportedFormatError(
            "not a PSD/PSB file (missing 8BPS signature)"
        )
    fp.seek(-4, 1)  # FileHeader.read expects the cursor at the signature
    return FileHeader.read(fp)


def _skip_merged_image(fp: io.BufferedIOBase) -> None:
    """Seek past the merged image section (compression + data)."""
    fp.read(2)  # compression method
    remaining = fp.tell()
    fp.seek(0, 2)
    size = fp.tell() - remaining
    if size < 0:
        raise ParseError("truncated PSD: merged image section is incomplete")
    fp.seek(remaining)  # do not read the pixels


# -- light record reading (pixels never enter RAM) ---------------------------
#
# psd-tools' ``LayerRecord.read`` materialises each record's *extra* region
# (mask pixel data + Pascal name + tagged blocks) into one in-memory buffer.
# For 16/32-bit layers the mask data alone is hundreds of megabytes, so a
# large document allocates -- and the allocator often never returns --
# several gigabytes.  Nothing downstream ever touches the mask pixels, so
# the light reader below reads the identical fields in the identical order
# but seeks over the pixel bytes, and caps per-block tagged payloads (a
# smart object's embedded source file is the other classic multi-gigabyte
# block).  On well-formed files the resulting records are field-identical
# to psd-tools'.

_TAG_MAX = 1 << 20      # most blocks are small
_STRUCTURAL_KEYS = {b"Lr16", b"Lr32"}
_EFFECT_KEYS = {b"lfx2"}


def _tag_cap(key: bytes) -> int:
    if key in _STRUCTURAL_KEYS:
        return _TAG_MAX
    if key in _EFFECT_KEYS:
        return _TAG_MAX
    return _TAG_MAX


def _read_mask_light(fp) -> MaskData | None:
    """Mask data block: consume the small header, seek over the pixel bytes.

    Returns a bare ``MaskData`` marker when a mask with pixel data is
    present, else ``None`` -- the same presence/absence psd-tools reports.
    The mask rectangle, flags and parameters are not used by ca-pa.
    """
    (mlen,) = read_fmt("I", fp)
    if mlen <= 0:
        return None
    head = min(mlen, 64)
    fp.read(head)
    if mlen > head:
        fp.seek(mlen - head, 1)  # the mask's pixel data: never read
    read_padding(fp, mlen, 1)
    return MaskData()


def _read_tagged_blocks_light(fp, version: int = 1, end_pos: int | None = None) -> TaggedBlocks:
    """Tagged blocks with a per-block payload cap.

    Same frame layout and typed parsing as psd-tools, but a payload above
    its cap is seeked past and stubbed (key kept, payload emptied) so a
    smart object's embedded source never lands in RAM.  ``end_pos`` bounds
    the run at the end of the record's extra region.
    """
    items = []
    while is_readable(fp, 8) and (end_pos is None or fp.tell() < end_pos):
        signature = read_fmt("4s", fp)[0]
        if signature not in (b"8BIM", b"8B64"):
            fp.seek(-4, 1)
            break
        key_raw = read_fmt("4s", fp)[0]
        try:
            key = Tag(key_raw)
        except ValueError:
            key = key_raw
        fmt = TaggedBlock._length_format(key, version)
        (length,) = read_fmt(fmt, fp)
        if length < 0 or length > (1 << 31):
            raise OSError("implausible tagged block length %d" % length)
        if length <= _tag_cap(key_raw):
            raw = read_exact(fp, length)
        else:
            raw = b""  # keep the key (classification), drop the payload
            fp.seek(length, 1)
        read_padding(fp, length, 1)
        data = raw
        if raw:
            kls = TYPES.get(key) if isinstance(key, Tag) else None
            if kls is not None:
                try:
                    data = kls.frombytes(raw, version=version)
                except Exception:
                    data = raw  # psd-tools falls back to raw on parse failure
        items.append((key, TaggedBlock(signature, key, data)))
    return TaggedBlocks(items)


def _read_record_light(fp, encoding: str, version: int) -> LayerRecord:
    """One layer record, pixel-free.

    Mirrors ``LayerRecord.read`` field for field (same formats, same
    order), with two differences that only save memory:

    * the mask block's pixel data is seeked past (see
      :func:`_read_mask_light`);
    * tagged-block payloads above the cap are seeked past and stubbed (see
      :func:`_read_tagged_blocks_light`).

    Raises ``OSError``/``struct.error`` on truncated or implausible data;
    the caller stops the record loop.
    """
    top, left, bottom, right, nch = read_fmt("4iH", fp)
    if not 0 <= nch <= 4096:
        raise OSError("implausible channel count %d" % nch)
    channel_info = [ChannelInfo.read(fp, version) for _ in range(nch)]
    signature, blend_mode, opacity, clipping = read_fmt("4s4sBB", fp)
    # Standard files carry ``Layr`` here.  One exporter (the same one behind
    # the MIB8 TIFFs) frames the extra region instead, putting ``8BIM`` in
    # the signature slot; psd-tools accepts whatever it finds, and so do we.
    if signature not in (b"Layr", b"8BIM"):
        raise OSError("bad layer record signature %r" % (signature,))
    flags = LayerFlags.read(fp)
    (extra_len,) = read_fmt("xI", fp)
    if extra_len < 0 or extra_len > (1 << 31):
        raise OSError("implausible layer extra length %d" % extra_len)
    extra_start = fp.tell()
    mask_data = _read_mask_light(fp)
    blending_ranges = LayerBlendingRanges.read(fp)
    name = read_pascal_string(fp, encoding, padding=4)
    tagged_blocks = _read_tagged_blocks_light(
        fp, version=version, end_pos=extra_start + extra_len
    )
    # Land exactly where psd-tools' read_length_block("xI") would: block plus
    # its even padding, regardless of how the inner readers advanced.
    fp.seek(extra_start + extra_len + (extra_len & 1))
    return LayerRecord(
        top=top,
        left=left,
        bottom=bottom,
        right=right,
        channel_info=channel_info,
        signature=signature,
        blend_mode=blend_mode,
        opacity=opacity,
        clipping=clipping,
        flags=flags,
        mask_data=mask_data,
        blending_ranges=blending_ranges,
        name=name,
        tagged_blocks=tagged_blocks,
    )


def _read_records_light(fp, count: int, encoding: str, version: int) -> list[LayerRecord]:
    """All ``count`` layer records, pixel-free (see :func:`_read_record_light`)."""
    records: list[LayerRecord] = []
    for _ in range(max(0, min(int(count), 1_000_000))):
        try:
            records.append(_read_record_light(fp, encoding, version))
        except (OSError, ValueError, struct.error):
            break  # truncated section: keep what parsed
    return records


def _framed_layer_info(fp, length_fmt: str, version: int):
    """(count, records) of an 8BIM-framed layer info, or ``None``.

    Some 16/32-bit exporters write the section's layer info as a tagged
    frame instead of the standard LayerInfo block:

        [8BIM][Lr16|Lr32][u32/u64 frame length]
          [i16 count][records...][channel image data...]

    leaving the standard LayerInfo length and count fields zero.  The
    channel data inside the frame (gigabytes of pixels for 16/32-bit
    documents) is seeked past, never read.
    """
    pos = fp.tell()
    # The frame usually starts right here; some builds pad a couple of zero
    # bytes first.  Scan a small window for the exact key, then commit.
    window = fp.read(64)
    offset = -1
    for key in (b"Lr16", b"Lr32"):
        for skip in (0, 2, 4, 6, 8, 10, 12, 16):
            if window[skip:skip + 8] == b"8BIM" + key:
                offset = skip
                break
        if offset >= 0:
            break
    if offset < 0:
        fp.seek(pos)
        return None
    fp.seek(pos + offset + 8)
    (flen,) = read_fmt(length_fmt, fp)
    if flen < 2 or flen > (1 << 40):
        fp.seek(pos)
        return None
    frame_end = fp.tell() + flen
    count = read_fmt("h", fp)[0]
    recs = _read_records_light(fp, abs(count), ENCODING, version)
    fp.seek(frame_end)  # the frame's channel image data: never read
    return count, recs


def _load_structure(path: str):
    """Read header/resources/layer records of a PSD, skipping pixel data.

    Returns ``(header, resources, records, warnings)``.
    """
    warnings: list[str] = []
    with open(path, "rb") as fp:
        header = _header(fp)
        color_mode_data = ColorModeData.read(fp)
        resources = ImageResources.read(fp, ENCODING)
        version = header.version  # 1 = PSD, 2 = PSB (Big)
        length_fmt = "I" if version == 1 else "Q"

        # The Layer & Mask section nests two length-prefixed blocks:
        #   [u32 section length]
        #     [u32 layer-info length]   <- LayerInfo's own prefix
        #       [i16 layer count][records...][channel image data...]
        #     [global layer mask info][section tagged blocks]
        # We read only the records (which carry every per-layer block) and
        # seek the whole section away, never touching the pixel data.
        section_start = fp.tell()
        length = read_fmt(length_fmt, fp)[0]
        if length == 0:
            records, count = [], 0
        else:
            read_fmt(length_fmt, fp)  # LayerInfo length (not needed to skip)
            count = read_fmt("h", fp)[0]
            records = []
            if count == 0:
                # The 8BIM-framed 16/32-bit exporter variant: zero in the
                # standard fields, everything inside the Lr16/Lr32 frame.
                framed = _framed_layer_info(fp, length_fmt, version)
                if framed is not None:
                    count = framed[0]
                    records = LayerRecords(framed[1])
            elif count != 0:
                records = LayerRecords(
                    _read_records_light(fp, abs(count), ENCODING, version)
                )
            if len(records) != abs(count):
                warnings.append(
                    "layer section is truncated: declared %d layers, read %d"
                    % (abs(count), len(records))
                )
        # Seek past the channel image data, global layer masks and the
        # section-level tagged blocks: none of it is needed for metadata.
        fp.seek(section_start + length)
        _skip_merged_image(fp)
    return header, resources, records, warnings, count


def _build_psdimage(header, resources, records) -> Any:
    """Wrap the records in a psd-tools PSDImage with empty channel data.

    ``PSDImage`` builds its layer tree by zipping records with channel
    data, so each record needs one (zero-length) channel per declared
    channel; no pixel bytes are allocated.
    """
    from psd_tools.api.psd_image import PSDImage

    channel_items = [
        ChannelDataList([ChannelData() for _ in rec.channel_info]) for rec in records
    ]
    psd = PSD(
        header=header,
        color_mode_data=ColorModeData(),
        image_resources=resources,
        layer_and_mask_information=LayerAndMaskInformation(
            layer_info=LayerInfo(
                layer_count=len(records),
                layer_records=LayerRecords(records),
                channel_image_data=ChannelImageData(channel_items),
            ),
            global_layer_mask_info=None,
            tagged_blocks=None,
        ),
        image_data=ImageData(),
    )
    return PSDImage(psd)


# -- metadata mapping ---------------------------------------------------------

def _raw_block(data, key: bytes) -> bytes | None:
    """Raw payload of the block with 4-byte key ``key``, or None.

    The key may be stored either as raw ``bytes`` or as a ``Tag`` enum
    member, so both spellings are tried.  ``get_data`` is deliberately
    avoided: psd-tools returns its default (None) for a recognised key whose
    payload it could not parse into a typed object -- exactly what happens
    with a plain u32 sheet-colour (``lclr``) block.
    """
    block = data.get(key)
    if block is None:
        try:
            block = data.get(Tag(key))
        except (Exception, TypeError):
            pass
    if block is None:
        return None
    payload = getattr(block, "data", block)
    return bytes(payload) if isinstance(payload, (bytes, bytearray)) else None


def _color(record, blocks) -> str | None:
    # Legacy 4-byte RGBA colour (lsdk), then the modern sheet colour (lclr,
    # a single u32 code).
    raw = _raw_block(blocks, b"lsdk")
    if raw is not None and len(raw) >= 4:
        hexcolor = labels.rgba_to_hex(raw[:4])
        if hexcolor:
            return hexcolor
    code = _sheet_color_code(blocks)
    if code:
        return labels.SHEET_COLORS.get(code)
    return None


def _sheet_color_code(blocks) -> int:
    """The ``lclr`` sheet-colour code (0-11), whether psd-tools parsed it into
    a ``SheetColorType`` enum or left it as raw bytes."""
    block = blocks.get(b"lclr")
    if block is None:
        try:
            block = blocks.get(Tag.SHEET_COLOR_SETTING)
        except Exception:
            block = None
    if block is None:
        return 0
    data = getattr(block, "data", block)
    value = getattr(data, "value", data)  # SheetColorType enum -> int
    try:
        return int(value)
    except (TypeError, ValueError):
        if isinstance(data, (bytes, bytearray)) and len(data) >= 4:
            return struct.unpack(">I", data[:4])[0]
    return 0


def _effects(layer) -> list[str]:
    """Human-readable names of the layer's live effects (``lfx2``).

    Uses psd-tools' high-level ``layer.effects`` API, which parses the block
    for us; we only translate each effect's class name into a friendly label
    and skip effects that are present but switched off.
    """
    names: list[str] = []
    try:
        effects = list(layer.effects)
    except Exception:  # pragma: no cover - effects unavailable
        return names
    for effect in effects:
        try:
            if getattr(effect, "present", True) is False:
                continue  # in the block but disabled
        except Exception:  # pragma: no cover
            pass
        raw = getattr(effect, "name", None)
        if not raw:
            klass = getattr(effect, "classID", None)
            raw = getattr(klass, "name", None)
        if not raw:
            continue
        label = labels.EFFECT_LABELS.get(raw, raw)
        if label not in names:
            names.append(label)
    return names




def _map_api_layer(layer) -> LayerNode:
    """Map a psd-tools API layer (or group) onto a LayerNode, recursively."""
    import psd_tools.api.layers as psd_layers
    import psd_tools.psd.adjustments as psd_adjustments

    record = layer._record
    blocks = record.tagged_blocks

    # Kind
    if isinstance(layer, psd_layers.Group):
        kind = "group"
        kind_label = "Folder"
        if any(k in blocks for k in (Tag.ARTBOARD_DATA1, Tag.ARTBOARD_DATA2, Tag.ARTBOARD_DATA3)):
            kind_label = "Artboard"
    elif isinstance(layer, psd_layers.TypeLayer):
        kind, kind_label = "text", "Text"
    elif isinstance(layer, psd_layers.SmartObjectLayer):
        kind, kind_label = "smartobject", "Smart Object"
    elif isinstance(layer, psd_layers.ShapeLayer):
        kind, kind_label = "shape", "Shape"
    elif isinstance(layer, psd_layers.FillLayer):
        kind, kind_label = "fill", "Fill"
    elif isinstance(layer, psd_layers.AdjustmentLayer):
        kind, kind_label = "adjustment", "Adjustment"
        for key, name in labels.ADJUSTMENT_NAMES.items():
            if key in blocks:
                kind_label = "Adjustment: " + name
                break
    else:
        kind, kind_label = "pixel", "Pixel"
        for key, name in labels.ADJUSTMENT_NAMES.items():
            if key in blocks and key not in labels.NON_ADJUSTMENT_KEYS:
                kind, kind_label = "adjustment", "Adjustment: " + name
                break

    # Group divider state (lsct): 0=open, 1=closed, 2=bounding.
    collapsed = False
    divider = blocks.get_data(Tag.SECTION_DIVIDER_SETTING)
    if kind == "group" and divider is not None:
        collapsed = int(divider.kind) == 1

    name = layer.name or "Layer"
    has_alpha_mask = any(
        c.id == -2 or c.id == -3 for c in record.channel_info
    ) or record.mask_data is not None
    has_vector_mask = any(
        k in blocks for k in (b"vmsk", b"vogk", b"vsms", b"lsco", b"lsvm")
    )

    node = LayerNode(
        name=name,
        kind=kind,
        kind_label=kind_label,
        color=_color(record, blocks),
        opacity=int(record.opacity),
        blend_mode=labels.blend_mode_name(record.blend_mode),
        visible=bool(record.flags.visible),
        collapsed=collapsed,
        bounds=(record.left, record.top, record.right, record.bottom),
        has_alpha_mask=has_alpha_mask,
        has_vector_mask=has_vector_mask,
        effects=_effects(layer),
    )
    if kind == "adjustment":
        node.adjustment = kind_label.split(": ", 1)[1]
    return node


def _map_children(psd_layer) -> list[LayerNode]:
    """Map a psd-tools layer's children to LayerNodes, recursively.

    The argument is a psd-tools API layer (not a LayerNode), since only the
    former is iterable over its sub-layers.
    """
    import psd_tools.api.layers as psd_layers

    if not isinstance(psd_layer, psd_layers.Group):
        return []  # only folders/groups hold sub-layers
    out: list[LayerNode] = []
    for child in psd_layer:
        node = _map_api_layer(child)
        node.children = _map_children(child)
        out.append(node)
    # File order is bottom-to-top; the Layers panel reads top-to-bottom.
    return list(reversed(out))


# -- public API ---------------------------------------------------------------

def build_doc_from_records(
    header: Any,
    resources: Any,
    records: list[Any],
    *,
    path: str,
    size_bytes: int,
    format_name: str,
    warnings: list[str],
    count: int,
) -> DocumentInfo:
    """Map already-read PSD layer records onto a :class:`DocumentInfo`.

    Shared by true PSD/PSB files and by the *standard* embedded layer section
    that some layered TIFFs carry (the ``Layr`` block inside an "Adobe
    Photoshop Document Data Block").  Both store byte-identical layer
    records, so one code path maps either source to the same tree -- names,
    nesting, kinds, colours, opacity, blends, masks and effects included.
    No pixel data is touched: only the records and their ALI blocks are read.
    """
    image = _build_psdimage(header, resources, records)
    layers = []
    for layer in reversed(list(image._layers)):  # file order: bottom-to-top
        node = _map_api_layer(layer)
        node.children = _map_children(layer)
        layers.append(node)

    doc = DocumentInfo(
        path=path,
        format=format_name,
        size_bytes=size_bytes,
        width=header.width,
        height=header.height,
        bit_depth=header.depth,
        color_mode=_COLOR_MODE_NAMES.get(int(header.color_mode), str(header.color_mode)),
        channels=header.channels,
        has_alpha=header.channels > 3 or any(
            any(c.id == -1 for c in r.channel_info) for r in records
        ),
        layers=layers,
        warnings=list(warnings),
    )
    if count != 0 and not layers:
        doc.flat = True
        doc.warnings.append(
            "file has no layers (flattened image); showing the merged image as one layer"
        )
        doc.layers = [
            LayerNode(
                name=os.path.basename(path),
                bounds=(0, 0, header.width, header.height),
            )
        ]
    elif not layers and count == 0:
        doc.flat = True
        doc.warnings.append("flattened file: no layer information")
        doc.layers = [
            LayerNode(
                name=os.path.basename(path),
                bounds=(0, 0, header.width, header.height),
            )
        ]
    return doc


def parse_psd(path: str) -> DocumentInfo:
    """Parse a PSD/PSB file into a :class:`DocumentInfo` (metadata only)."""
    path = os.fspath(path)
    size_bytes = os.path.getsize(path)
    header, resources, records, warnings, count = _load_structure(path)
    return build_doc_from_records(
        header,
        resources,
        records,
        path=path,
        size_bytes=size_bytes,
        format_name="psb" if header.version == 2 else "psd",
        warnings=warnings,
        count=count,
    )
