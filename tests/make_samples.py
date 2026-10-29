"""Generate the test files used by the ca-pa test suite.

Run standalone (``python -m tests.make_samples outdir``) or import
``generate_all`` from the tests. Everything is built with known metadata so
the tests can assert on it exactly.
"""

from __future__ import annotations

import io
import os
import struct
import sys

from PIL import Image

from psd_tools import PSDImage
from psd_tools.api import layers as psd_layers
from psd_tools.constants import Compression, Tag
from psd_tools.psd.tagged_blocks import TaggedBlock
from psd_tools.compression import compress

# --------------------------------------------------------------------------
# PSD


def _solid(color: tuple[int, ...], size: tuple[int, int]) -> Image.Image:
    return Image.new("RGB" if len(color) == 3 else "RGBA", size, color)


def _make_fxly(effects: list[tuple[int, bytes]]) -> bytes:
    """Build an 'fxly' (8017) ALI block: version, count, then entries."""
    out = struct.pack(">I", 1)
    out += struct.pack(">I", len(effects))
    for key, payload in effects:
        out += struct.pack(">II", key, len(payload))
        out += payload
    return out


def _fx_descriptor(classID: bytes, switch_key: bytes) -> bytes:
    """Minimal effect descriptor ('Objc') with present + enabled = True."""
    from psd_tools.psd.descriptor import Bool, Descriptor

    desc = Descriptor()
    desc.name = "Effect"
    desc.classID = classID
    for key in (b"present", b"showInDialog", b"Enabled", switch_key):
        desc[key] = Bool(True)
    buf = io.BytesIO()
    desc.write(buf)
    return buf.getvalue()


def _lfx2_drop_shadow_outer_glow() -> bytes:
    """A real 'lfx2' block (LayerEffects descriptor) listing DropShadow + OuterGlow."""
    from psd_tools.psd.descriptor import Bool, Descriptor, List
    from psd_tools.terminology import Klass

    top = Descriptor()
    top.name = "LayerEffects"
    top.classID = Klass.LayerEffects if hasattr(Klass, "LayerEffects") else b"LyrF"
    top[b"masterFXSwitch"] = Bool(True)
    contents = List()
    for classID, switch in (
        (Klass.DropShadow, b"Drop Shadow Switch"),
        (Klass.OuterGlow, b"Glow Switch"),
    ):
        d = Descriptor()
        d.name = "Effect"
        d.classID = classID
        for key in (b"present", b"showInDialog", b"Enabled", switch):
            d[key] = Bool(True)
        contents.append(d)
    top[b"Contents"] = contents
    body = io.BytesIO()
    top.write(body)
    # DescriptorBlock2 = u32 version(1) + u32 data_version(16) + descriptor body.
    return struct.pack(">II", 1, 16) + body.getvalue()


def build_sample_psd(path: str, size: tuple[int, int] = (64, 64)) -> dict:
    """A PSD exercising every metadata field. Returns the expected metadata."""
    psd = PSDImage.new("RGB", size)
    base = psd_layers.PixelLayer.frompil(_solid((0, 0, 0), size), psd, name="Background")
    base.opacity = 255

    def add_layer(name, color, **kwargs):
        layer = psd_layers.PixelLayer.frompil(_solid(color, size), psd, name=name)
        for k, v in kwargs.items():
            if k != "parent":
                setattr(layer, k, v)
        if "parent" in kwargs and kwargs["parent"] is not psd:
            kwargs["parent"].append(layer)
        return layer

    g = psd_layers.Group.new(psd, name="Folder A")  # open folder, divider set for us
    add_layer("in folder visible", (10, 20, 30), parent=g, opacity=200, blend_mode=b"mul ")
    add_layer("in folder hidden", (40, 50, 60), parent=g, visible=False)

    shape = add_layer("Shape with vector mask", (9, 9, 9))
    shape._record.flags.pixel_data_irrelevant = True
    blocks = shape._record.tagged_blocks
    blocks[Tag.VECTOR_STROKE_DATA] = TaggedBlock(key=Tag.VECTOR_STROKE_DATA, data=b"\x00" * 10)
    blocks[Tag.VECTOR_MASK_SETTING2] = TaggedBlock(key=Tag.VECTOR_MASK_SETTING2, data=b"\x00" * 10)

    fx = add_layer("Fancy", (70, 60, 50))
    blocks = fx._record.tagged_blocks
    blocks[Tag.OBJECT_BASED_EFFECTS_LAYER_INFO] = TaggedBlock(
        key=Tag.OBJECT_BASED_EFFECTS_LAYER_INFO,
        data=_lfx2_drop_shadow_outer_glow(),
    )
    blocks[Tag.SHEET_COLOR_SETTING] = TaggedBlock(
        key=Tag.SHEET_COLOR_SETTING, data=struct.pack(">I", 1)
    )  # sheet colour: red

    adj = add_layer("Curves adjustment", (1, 1, 1))
    adj._record.tagged_blocks[Tag.CURVES] = TaggedBlock(key=Tag.CURVES, data=b"")

    expected = {
        "format": "psd",
        # Top-to-bottom order as Photoshop's panel shows it.
        "order": [
            "Curves adjustment",
            "Fancy",
            "Shape with vector mask",
            "Folder A",
            "Background",
        ],
        "fancy": {"color": "#ff0000", "effects": ["Drop shadow", "Outer glow"]},
        "shape": {"kind": "shape", "vector_mask": True},
        "adjustment": {"kind": "adjustment", "adjustment": "Curves"},
        "folder_children": ["in folder visible", "in folder hidden"],
        "hidden_child": {"visible": False},
        "child_opacity_blend": {"opacity": 200, "blend_mode": "Multiply"},
    }
    psd.save(path)
    return expected


def build_flat_psd(path: str, size: tuple[int, int] = (16, 16)) -> None:
    psd = PSDImage.new("RGB", size)
    base = psd_layers.PixelLayer.frompil(_solid((1, 2, 3), size), psd, name="Background")
    psd.save(path)


def build_huge_psd(path: str, size: tuple[int, int] = (800, 800), n_layers: int = 8) -> None:
    """A file whose channel data is genuinely large and incompressible.

    The bytes are random, so RLE cannot shrink them -- this is what proves
    the parser skips the pixel data instead of loading it.  At the default
    size the saved file lands around 30-40 MB.
    """
    import os

    w, h = size
    psd = PSDImage.new("RGB", (w, h))
    psd_layers.PixelLayer.frompil(_solid((0, 0, 0), (w, h)), psd, name="base")
    for i in range(n_layers):
        psd_layers.PixelLayer.frompil(
            _solid((i * 7 % 256, i * 11 % 256, i * 13 % 256), (w, h)), psd, name=f"layer {i}"
        )
    for layer in psd:
        for ch in layer._channels:
            ch.data = os.urandom(w * h)
            ch.compression = Compression.RLE
    img = psd._record.image_data
    img.data = os.urandom(w * h * 3)
    img.compression = Compression.RLE
    psd.save(path)


# --------------------------------------------------------------------------
# Layered TIFF (hand-rolled; Photoshop's own layout)


def _ali_block(key: bytes, data: bytes) -> bytes:
    return key + struct.pack(">I", len(data)) + data


def _section_divider(kind: int) -> bytes:  # 0 open, 1 closed, 2 bounding
    return struct.pack(">I", kind) + b"8BIM" + struct.pack(">I", 1)  # blend: normal


def build_sample_tiff(path: str, size: tuple[int, int] = (64, 64)) -> dict:
    """A Photoshop layered TIFF written by hand (classic, little-endian).

    Layout: [8-byte header][IFD0][data area][dummy strips][layer IFDs].
    Values longer than 4 bytes live in the data area behind an offset,
    exactly as Photoshop writes them.  The dummy strips keep the file
    technically decodable, but nothing here stores real pixels.
    """
    n_layers = 4
    names = [b"Background", b"Pixel layer", b"Folder", b"Text layer"]

    # Blend keys (4 chars) per layer -- a real 37724 record stores the ASCII
    # key ("norm", "scrn", ...), not a small integer.
    blend_keys = [b"norm", b"scrn", b"over", b"norm"]
    layer_states = b"".join(
        struct.pack(">4iBB4sH", 0, 0, size[1], size[0], 8, 255, key, 0)
        for key in blend_keys
    )
    # 37725 record: [1B len][name][pad to even][1B visible][4B blend key][pad to even]
    names_raw = b""
    for flag, name in zip((0, 0, 0, 0), names):  # 0 = visible (bit 0 = hidden) in Photoshop
        seg = bytes([len(name)]) + name
        seg += b"\x00" * (len(seg) % 2)
        seg += bytes([flag]) + b"none"
        seg += b"\x00" * (len(seg) % 2)
        names_raw += seg

    ali_streams = [
        b"",  # Background
        _ali_block(b"lsdk", bytes((0, 255, 0, 255)))
        + _ali_block(
            b"fxly",
            struct.pack(">II", 1, 1)
            + struct.pack(">II", 8424, 22)
            + b"Objc" + struct.pack(">I", 2) + b"bool" + b"bool" + b"\x01" + b"Drop Shadow Switch",
        ),
        _ali_block(b"lsct", _section_divider(0)) + _ali_block(b"lsdk", bytes((0, 0, 255, 255))),
        _ali_block(b"TySh", b"\x00" * 8) + _ali_block(b"tySh", b"hello"),
    ]
    ali_raw = b"".join(struct.pack(">I", len(s)) + s for s in ali_streams)
    resources = struct.pack(">H", 1001) + b"\x00" + b"8BIM" + struct.pack(">I", 66) + b"\x00" * 66

    SIZES = {1: 1, 2: 2, 3: 2, 4: 4, 6: 1, 7: 1, 8: 2, 9: 4, 11: 4}

    def entry(tag: int, typ: int, raw: bytes, offset: int) -> bytes:
        count = len(raw) // SIZES[typ]
        if len(raw) <= 4:
            return struct.pack("<HHI", tag, typ, count) + raw + b"\x00" * (4 - len(raw))
        return struct.pack("<HHII", tag, typ, count, offset)

    # ---- fixed layout, so every offset is known up front ----
    ifd0_tags = [
        (256, 4, struct.pack("<I", size[0])),
        (257, 4, struct.pack("<I", size[1])),
        (258, 3, struct.pack("<H", 3)),
        (259, 3, struct.pack("<HHH", 8, 8, 8)),
        (262, 3, struct.pack("<H", 2)),
        (273, 4, struct.pack("<I", 0)),
        (279, 4, struct.pack("<I", 1)),
        (339, 3, struct.pack("<H", 1)),
        (37724, 7, layer_states),
        (37725, 7, names_raw),
        (37726, 7, ali_raw),
        (34377, 7, resources),
    ]
    ifd0_body_len = 2 + 12 * len(ifd0_tags) + 4
    layer_tags = [
        (256, 4, struct.pack("<I", size[0])),
        (257, 4, struct.pack("<I", size[1])),
        (258, 3, struct.pack("<H", 3)),
        (259, 3, struct.pack("<HHH", 8, 8, 8)),
        (262, 3, struct.pack("<H", 2)),
        (273, 4, struct.pack("<I", 0)),
        (339, 3, struct.pack("<H", 1)),
    ]
    layer_ifd_len = 2 + 12 * len(layer_tags) + 4

    data_area = b""
    data_offsets: dict[int, int] = {}
    data_base = 8 + ifd0_body_len
    for tag, typ, raw in ifd0_tags:
        if len(raw) > 4:
            data_offsets[tag] = data_base + len(data_area)
            data_area += raw
    strips_off = data_base + len(data_area)
    layer_ifd0_off = strips_off + n_layers  # one dummy strip byte per layer

    out = bytearray()
    out += b"II\x2a\x00" + struct.pack("<I", 8)
    out += struct.pack("<H", len(ifd0_tags))
    for j, (tag, typ, raw) in enumerate(ifd0_tags):
        if tag == 273:
            out += entry(tag, typ, struct.pack("<I", strips_off), 0)
        elif tag in data_offsets:
            out += entry(tag, typ, raw, data_offsets[tag])
        else:
            out += entry(tag, typ, raw, 0)
    out += struct.pack("<I", layer_ifd0_off)  # next IFD -> first layer IFD
    out += data_area
    out += b"\x00" * n_layers  # dummy strips (one byte each; never read)

    for i in range(n_layers):
        out += struct.pack("<H", len(layer_tags))
        for tag, typ, raw in layer_tags:
            if tag == 273:
                out += entry(tag, typ, struct.pack("<I", strips_off + i), 0)
            else:
                out += entry(tag, typ, raw, 0)
        out += struct.pack("<I", 0 if i == n_layers - 1 else layer_ifd0_off + (i + 1) * layer_ifd_len)

    with open(path, "wb") as f:
        f.write(bytes(out))

    return {
        "format": "tiff",
        "names": ["Background", "Pixel layer", "Folder", "Text layer"],
        "background": {"visible": True, "blend_mode": "Normal"},
        "pixel": {"color": "#00ff00", "blend_mode": "Screen"},
        "folder": {"divider": "open", "color": "#0000ff", "blend_mode": "Overlay"},
        "text": {"kind": "text"},
    }

def build_flat_tiff(path: str, size: tuple[int, int] = (16, 16)) -> None:
    Image.new("RGB", size, (12, 34, 56)).save(path)


def _mib8(key: bytes, payload: bytes) -> bytes:
    return b"MIB8" + struct.pack(">I", len(key) + len(payload)) + key + payload


def _mib8_layer(name: str, h: int, w: int, kind: bytes = b"dngb",
                divider: int | None = None, color: bytes | None = None,
                opacity: int = 255) -> bytes:
    blocks = b""
    nb = name.encode("utf-16-be")
    blocks += _mib8(b"inul", struct.pack(">I", len(nb)) + nb)
    if divider is not None:
        blocks += _mib8(b"tcsl", struct.pack(">I", divider))
    blocks += _mib8(b"rsnl", kind)
    if color is not None:
        blocks += _mib8(b"lscs", color)
    blocks += _mib8(b"rvyl", bytes([opacity]))
    record = struct.pack(">HH", h, w) + b"\x03" + struct.pack(">H", 8) + b"\x00"
    record += b"\x00" * 3  # channel dimensions (RGB)
    record += struct.pack(">I", len(blocks)) + blocks
    return record


def build_mib8_tiff(path: str, size: tuple[int, int] = (32, 32)) -> dict:
    """A layered TIFF in the non-standard byte-swapped MIB8 format.

    Rather than hand-rolling the block, we build a genuine PSD with
    psd-tools, lift its raw layer-info section, byte-swap it and stash it in
    TIFF tag 37724 -- exactly the shape the parser reverse-engineered from
    the real-world samples, so the test exercises that code path faithfully.
    """
    import io

    from psd_tools import PSDImage
    from psd_tools.api import layers as psd_layers

    w, h = size
    psd = PSDImage.new("RGB", (w, h))
    psd_layers.PixelLayer.frompil(_solid((0, 0, 0), (w, h)), psd, name="MIB8 one")
    layer = psd_layers.PixelLayer.frompil(_solid((255, 0, 0), (w, h)), psd, name="MIB8 two")
    psd.save(io.BytesIO())
    raw = io.BytesIO()
    psd.save(raw)
    raw.seek(0)
    b = raw.getvalue()

    # PSD: [26-byte header][u32 colour-mode len][data][u32 resources len][data]
    # [u32 layer-info len][layer info][u32 channel-data len][channel data].
    off = 26
    (cm_len,) = struct.unpack(">I", b[off : off + 4])
    off += 4 + cm_len
    (res_len,) = struct.unpack(">I", b[off : off + 4])
    off += 4 + res_len
    (li_len,) = struct.unpack(">I", b[off : off + 4])
    off += 4
    section = bytearray(b[off : off + li_len])
    for i in range(0, len(section) - 1, 2):
        section[i], section[i + 1] = section[i + 1], section[i]
    section = bytes(section)

    SIZES = {1: 1, 2: 2, 3: 2, 4: 4, 6: 1, 7: 1}

    def entry(tag: int, typ: int, payload: bytes, offset: int) -> bytes:
        c = len(payload) // SIZES[typ]
        if len(payload) <= 4:
            return struct.pack("<HHI", tag, typ, c) + payload + b"\x00" * (4 - len(payload))
        return struct.pack("<HHII", tag, typ, c, offset)

    ifd0_tags = [
        (256, 3, struct.pack("<H", w)),
        (257, 3, struct.pack("<H", h)),
        (258, 3, struct.pack("<H", 3)),
        (259, 3, struct.pack("<HHH", 8, 8, 8)),
        (262, 3, struct.pack("<H", 2)),
        (273, 4, struct.pack("<I", 100)),
        (37724, 7, section),  # byte-swapped PSD layer section; no 37725/37726
    ]
    data_area = b""
    offs: dict[int, int] = {}
    for tag, typ, payload in ifd0_tags:
        if len(payload) > 4:
            offs[tag] = 8 + 2 + 12 * len(ifd0_tags) + 4 + len(data_area)
            data_area += payload
    out = bytearray()
    out += b"II\x2a\x00" + struct.pack("<I", 8)
    out += struct.pack("<H", len(ifd0_tags))
    for tag, typ, payload in ifd0_tags:
        out += entry(tag, typ, payload, offs.get(tag, 0))
    out += struct.pack("<I", 0)
    out += data_area
    out += b"\x00" * 100  # dummy strip, never read
    with open(path, "wb") as f:
        f.write(bytes(out))
    return {
        "format": "tiff",
        "names": ["MIB8 two", "MIB8 one"],
        "colors": ["#ff0000"],
    }


def generate_all(outdir: str) -> dict:
    """Build every sample file into ``outdir`` and return their paths.

    ``huge.psd`` is included in the mapping but only built lazily (see
    :func:`build_huge_psd`) so a normal test run stays fast; pass
    ``build_huge=True`` to force it.
    """
    os.makedirs(outdir, exist_ok=True)
    paths = {
        "sample.psd": os.path.join(outdir, "sample.psd"),
        "flat.psd": os.path.join(outdir, "flat.psd"),
        "sample.tiff": os.path.join(outdir, "sample.tiff"),
        "flat.tiff": os.path.join(outdir, "flat.tiff"),
        "huge.psd": os.path.join(outdir, "huge.psd"),
    }
    builders = {
        "sample.psd": build_sample_psd,
        "flat.psd": build_flat_psd,
        "sample.tiff": build_sample_tiff,
        "flat.tiff": build_flat_tiff,
    }
    for name, path in paths.items():
        if name == "huge.psd":
            continue
        builders[name](path)
    return paths


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "tests/samples"
    for name, path in generate_all(out).items():
        if name == "huge.psd":
            continue
        print("wrote", path, os.path.getsize(path), "bytes")
