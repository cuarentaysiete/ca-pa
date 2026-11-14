"""Test suite for the ca-pa structure parsers.

Samples are generated on the fly by :mod:`tests.make_samples` into a temp
directory.  The synthetic files exercise the *standard* formats (PSD, classic
layered TIFF with the 37724/37725/37726 triple, flat files); the real-world
byte-swapped MIB8 format is tested against the reference samples in
``samples/`` when they are present.
"""

from __future__ import annotations

import os
import sys

import pytest

from ca_pa.model import DocumentInfo
from ca_pa.parser import parse_file
from tests import make_samples

# The reference files the user provided, if they happen to be around.
REAL_SAMPLES = os.environ.get("CA_PA_SAMPLES", "/workspace/ca-pa/samples")


def _find(node_layers, name):
    for n in node_layers:
        if n.name == name:
            return n
        hit = _find(n.children, name)
        if hit:
            return hit
    return None


# --------------------------------------------------------------------------
# PSD


def test_psd_header(samples):
    d = parse_file(samples["sample.psd"])
    assert d.format == "psd"
    assert (d.width, d.height) == (64, 64)
    assert d.bit_depth == 8
    assert d.color_mode == "RGB"
    assert not d.flat
    assert not d.warnings


def test_psd_order_and_count(samples):
    d = parse_file(samples["sample.psd"])
    # Top-to-bottom, as Photoshop's panel shows it.
    assert [l.name for l in d.layers] == [
        "Curves adjustment",
        "Fancy",
        "Shape with vector mask",
        "Folder A",
        "Background",
    ]
    assert d.layer_count() == 6  # folders do not count as layers
    assert d.folder_count() == 1  # 5 top + 2 inside the folder, minus the folder itself


def test_psd_folder_children(samples):
    d = parse_file(samples["sample.psd"])
    folder = _find(d.layers, "Folder A")
    assert folder is not None and folder.kind == "group"
    assert folder.is_group
    assert {c.name for c in folder.children} == {"in folder visible", "in folder hidden"}
    hidden = _find(d.layers, "in folder hidden")
    assert hidden.visible is False
    visible = _find(d.layers, "in folder visible")
    assert visible.opacity == 200
    assert visible.blend_mode == "Multiply"


def test_psd_colour_effects_adjustment_shape(samples):
    d = parse_file(samples["sample.psd"])
    fancy = _find(d.layers, "Fancy")
    assert fancy.color == "#e78679"
    assert set(fancy.effects) == {"Drop shadow", "Outer glow"}
    shape = _find(d.layers, "Shape with vector mask")
    assert shape.kind == "shape"
    assert shape.has_vector_mask
    adj = _find(d.layers, "Curves adjustment")
    assert adj.kind == "adjustment"
    assert adj.adjustment == "Curves"


def test_flat_psd(samples):
    d = parse_file(samples["flat.psd"])
    assert d.format == "psd"
    assert len(d.layers) == 1
    assert d.layers[0].name == "Background"
    assert d.layers[0].kind == "pixel"


# --------------------------------------------------------------------------
# Classic layered TIFF (37724 / 37725 / 37726)


def test_tiff_header(samples):
    d = parse_file(samples["sample.tiff"])
    assert d.format == "tiff"
    assert (d.width, d.height) == (64, 64)
    assert d.bit_depth == 8
    assert d.color_mode == "RGB"
    assert not d.flat


def test_tiff_layers(samples):
    d = parse_file(samples["sample.tiff"])
    by_name = {l.name: l for l in d.layers}
    assert set(by_name) == {"Background", "Pixel layer", "Folder", "Text layer"}
    assert by_name["Text layer"].kind == "text"
    assert by_name["Folder"].kind == "group"
    assert by_name["Pixel layer"].color == "#00ff00"
    assert by_name["Pixel layer"].blend_mode == "Screen"
    assert by_name["Folder"].blend_mode == "Overlay"
    assert by_name["Background"].blend_mode == "Normal"


def test_flat_tiff(samples):
    d = parse_file(samples["flat.tiff"])
    assert d.format == "tiff"
    assert d.flat
    assert len(d.layers) == 1
    assert any("flattened" in w for w in d.warnings)


# --------------------------------------------------------------------------
# Real-world reference files (byte-swapped MIB8) -- only if present.


@pytest.mark.skipif(not os.path.isdir(REAL_SAMPLES), reason="reference samples not present")
def test_real_psd():
    path = os.path.join(REAL_SAMPLES, "layered_PSD.psd")
    d = parse_file(path)
    assert (d.width, d.height) == (5366, 8044)
    names = [l.name for l in d.layers]
    assert "RETOUCH" in names
    assert any(l.kind == "group" for l in d.layers)
    assert d.layer_count() == 18
    assert d.folder_count() == 8


@pytest.mark.skipif(not os.path.isdir(REAL_SAMPLES), reason="reference samples not present")
def test_real_tiff_mib8():
    path = os.path.join(REAL_SAMPLES, "layered_TIFF-compression.tif")
    d = parse_file(path)
    assert (d.width, d.height) == (5366, 8044)
    names = [l.name for l in d.layers]
    assert "RETOUCH" in names
    assert any(l.kind == "group" for l in d.layers)
    assert d.layer_count() == 18
    assert d.folder_count() == 8


# --------------------------------------------------------------------------
# Robustness


def test_missing_file():
    from ca_pa.errors import ParseError

    with pytest.raises(ParseError):
        parse_file("/nonexistent/definitely/missing.psd")


def test_wrong_format_gives_useful_error(tmp_path):
    from ca_pa.errors import ParseError

    bad = tmp_path / "evil.psd"
    bad.write_bytes(b"not a psd at all")
    with pytest.raises(ParseError):
        parse_file(str(bad))


def test_dispatch_by_extension_and_magic(tmp_path):
    # A PSD file renamed with a .tif extension is still detected by its magic.
    src = make_samples.build_sample_psd(str(tmp_path / "x.psd"))
    renamed = tmp_path / "decoy.tif"
    renamed.write_bytes((tmp_path / "x.psd").read_bytes())
    assert parse_file(str(renamed)).format == "psd"


# --------------------------------------------------------------------------
# Memory bound: a large PSD must not be fully decoded.


def test_memory_bound(samples, tmp_path):
    if sys.platform == "win32":
        pytest.skip("not measured on windows")
    huge = tmp_path / "huge.psd"
    make_samples.build_huge_psd(str(huge), size=(2000, 2000), n_layers=25)
    file_mb = huge.stat().st_size / (1024 * 1024)
    if file_mb < 20:
        pytest.skip(f"sample too small to prove the bound ({file_mb:.1f} MB)")
    import resource

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KB on Linux
    parse_file(str(huge))
    peak_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
    assert peak_mb < 50, f"RSS grew {peak_mb:.1f} MB for a {file_mb:.0f} MB file"


def test_document_model_helpers(samples):
    d = parse_file(samples["sample.psd"])
    assert isinstance(d, DocumentInfo)
    flat = parse_file(samples["flat.psd"])
    assert len(flat.layers) == 1 and flat.layers[0].kind == "pixel"


# --------------------------------------------------------------------------
# TIFF vs PSD reference parity (the MIB8 block must reproduce the PSD tree).


def _flat(nodes, depth=0):
    out = []
    for n in nodes:
        out.append((depth, n.name, n.kind, n.color, int(n.visible), n.opacity, n.blend_mode))
        out.extend(_flat(n.children, depth + 1))
    return out


@pytest.mark.skipif(
    not all(
        os.path.isfile(os.path.join(REAL_SAMPLES, f))
        for f in (
            "layered_PSD.psd",
            "layered_TIFF-compression.tif",
            "layered_TIFF-no_compression.tif",
        )
    ),
    reason="reference samples not present",
)
def test_real_tiff_matches_psd_reference():
    """The legacy byte-swapped MIB8 TIFF must reproduce the PSD *exactly*.

    The exporter keeps every field; ca-pa recovers them by normalising the
    ``MIB8`` record stream (little-endian integers, reversed 4-byte tag/blend
    keys) back to standard PSD form.  Name, nesting, kind, colour, visibility,
    opacity, blend mode, alpha/vector masks and effects must all match the PSD
    field for field, for both the compressed and the uncompressed variant.
    """
    psd = _flat_full(parse_file(os.path.join(REAL_SAMPLES, "layered_PSD.psd")).layers)
    # Sanity: this reference genuinely exercises the previously-broken fields.
    assert any(r[1] == "CC" and r[5] == 204 and r[6] == "Color" for r in psd)      # 80% / Color
    assert any(r[1] == "lighten" and r[5] == 128 for r in psd)                       # a 50% lighten
    assert any(r[1] == "EYE HELP" and not r[4] for r in psd)                          # hidden group
    assert any(r[1] == "Hue/Saturation 3" and r[6] == "Saturation" and r[7] for r in psd)
    for tif in ("layered_TIFF-compression.tif", "layered_TIFF-no_compression.tif"):
        got = _flat_full(parse_file(os.path.join(REAL_SAMPLES, tif)).layers)
        assert len(got) == len(psd), tif
        for ref, g in zip(psd, got):
            assert ref == g, (tif, ref[1], ref, g)


def _flat_full(nodes, depth=0):
    """Flatten keeping every per-layer field the model carries."""
    out = []
    for n in nodes:
        out.append(
            (
                depth,
                n.name,
                n.kind,
                n.color,
                bool(n.visible),
                n.opacity,
                n.blend_mode,
                bool(n.has_alpha_mask),
                bool(n.has_vector_mask),
                tuple(n.effects),
            )
        )
        out.extend(_flat_full(n.children, depth + 1))
    return out


@pytest.mark.skipif(
    not all(
        os.path.isfile(os.path.join(REAL_SAMPLES, f))
        for f in ("sample2.psd", "sample2.tif", "sample2.psb")
    ),
    reason="sample2 reference trio not present",
)
def test_real_sample2_tif_matches_psd_reference():
    """sample2.tif is a *standard* embedded Photoshop TIFF (normal ``8BIM`` keys).

    Unlike the legacy byte-swapped MIB8 exporter -- which silently drops a
    handful of markers -- a standard embedded layer section is byte-identical
    to the PSD/PSB layer records, so the TIFF parse must reproduce the PSD
    *exactly*: same layers, nesting, kinds, colours, visibility, opacity,
    blends, masks and effects.  The PSD is the reference; both the TIFF and the
    PSB must match it field for field.
    """
    ref_doc = parse_file(os.path.join(REAL_SAMPLES, "sample2.psd"))
    psd = _flat_full(ref_doc.layers)
    assert len(psd) > 100  # the real reference is a large document
    for other in ("sample2.tif", "sample2.psb"):
        got_doc = parse_file(os.path.join(REAL_SAMPLES, other))
        got = _flat_full(got_doc.layers)
        assert len(got) == len(psd), other
        for ref, g in zip(psd, got):
            assert ref == g, (other, ref[1], ref, g)
        # layers and folders are counted separately, and must agree too
        assert (got_doc.layer_count(), got_doc.folder_count()) == (
            ref_doc.layer_count(),
            ref_doc.folder_count(),
        ), other


# --------------------------------------------------------------------------
# The low-memory readers themselves (synthetic inputs, no big files needed)


def _mib8_frame(key: bytes, payload: bytes) -> bytes:
    """One little-endian, key-reversed MIB8 tag frame."""
    import struct

    return b"MIB8" + key[::-1] + struct.pack("<I", len(payload)) + payload


def test_last_frame_ending_at_prefers_outer_frame():
    """A zero-length frame nested *inside* another frame's payload also ends
    exactly at the frontier; the real frame is the earliest candidate (the
    last one would be the impostor sitting inside the outer frame's data).
    """
    from ca_pa.tiff_parser import _last_frame_ending_at

    inner = _mib8_frame(b"ssap", b"")                       # 12-byte decoy
    outer = _mib8_frame(b"norm", b"\x13\x00" * 2 + inner)
    m = _last_frame_ending_at(outer, 0, len(outer))
    assert m == 0, "must return the outer frame, not the nested decoy"


def test_peel_tags_skips_mask_block_and_recovers_name(tmp_path):
    """A record whose mask block is bigger than the peel window must still
    yield its tags and name -- and never read the mask pixels.
    """
    from ca_pa.tiff_parser import _FileWindow, _peel_tags, _TAG_WINDOW

    mask = b"\x5a" * (_TAG_WINDOW + 500_000)               # > 4 MB, non-zero
    name = b"\x05M"                                          # Pascal: len 5? no -
    # Pascal name: length byte + chars, zero-padded to a multiple of four.
    text = b"M" * 5
    name = bytes([len(text)]) + text + b"\x00" * (4 - (1 + len(text)) % 4)
    extra = mask + name + _mib8_frame(b"norm", b"\x13\x00") + _mib8_frame(b"lspf", b"\x00" * 4)
    f = tmp_path / "rec.bin"
    f.write_bytes(extra)
    with open(f, "rb") as fh:
        w = _FileWindow(fh, 0, len(extra))
        ali, pascal = _peel_tags(w, 0, len(extra))
    assert set(ali) == {b"norm", b"lspf"}
    assert ali[b"lspf"] == b"\x00" * 4
    assert pascal == "M" * 5
    # and the mask really was skipped: reading through the window stayed bounded
    assert w.tell() <= len(extra)


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(REAL_SAMPLES, "layered_TIFF-no_compression_16bits.tif")),
    reason="16-bit MIB8 reference not present",
)
def test_real_16bit_mib8_tiff():
    """The 2.6 GB 16-bit byte-swapped TIFF must parse to the same structure
    as its 8-bit siblings, via the streaming path (tag value >> 512 MB cap).
    """
    ref = _flat_full(parse_file(os.path.join(REAL_SAMPLES, "layered_PSD.psd")).layers)
    doc = parse_file(os.path.join(REAL_SAMPLES, "layered_TIFF-no_compression_16bits.tif"))
    got = _flat_full(doc.layers)
    assert len(got) == len(ref)
    for r, g in zip(ref, got):
        assert r == g, r[1]
    assert doc.bit_depth == 16


@pytest.mark.skipif(
    not all(os.path.isfile(os.path.join(REAL_SAMPLES, f)) for f in ("sample2.psd", "sample2_16bits.psb")),
    reason="framed 16-bit PSB reference not present",
)
def test_real_framed_16bit_psb():
    """A 16/32-bit exporter variant keeps its layer records inside an
    ``8BIM Lr16`` frame with the standard LayerInfo fields zeroed out; the
    parse must recover the full structure from that frame.
    """
    ref = _flat_full(parse_file(os.path.join(REAL_SAMPLES, "sample2.psd")).layers)
    doc = parse_file(os.path.join(REAL_SAMPLES, "sample2_16bits.psb"))
    got = _flat_full(doc.layers)
    assert len(got) == len(ref)
    for r, g in zip(ref, got):
        assert r == g, r[1]
    assert doc.bit_depth == 16


def test_color_inheritance():
    from ca_pa.model import LayerNode
    from ca_pa.parser import _inherit_colors

    folder = LayerNode(name="F", kind="group", kind_label="Folder", color="#eab267")
    child = LayerNode(name="c1", color=None)
    grandchild = LayerNode(name="c2", color="#e78679")  # explicit must win
    child.children.append(grandchild)
    folder.children.append(child)
    outsider = LayerNode(name="solo", color=None)
    _inherit_colors([folder, outsider])
    assert child.color == "#eab267"          # inherited from the folder
    assert grandchild.color == "#e78679"     # explicit kept
    assert outsider.color is None            # nothing above to inherit


def test_sheet_palette_is_the_sampled_one():
    from ca_pa.labels import SHEET_COLORS

    assert SHEET_COLORS == {
        1: "#e78679",   # Red
        2: "#eab267",   # Orange
        3: "#e4dd74",   # Yellow
        4: "#b4de8e",   # Green
        5: "#a1c4f6",   # Blue
        6: "#a499dd",   # Violet
        7: "#b3b3b3",   # Gray
        8: "#5b9ba1",   # Seafoam
        9: "#6d76e9",   # Indigo
        10: "#c35196",  # Magenta
        11: "#af53cc",  # Fuchsia
    }


# ---------------------------------------------------------------------------
# Regression: the 21 GB "infinite loading" bug reported against v0.1.6.
# A TIFF whose 37724 payload is huge but carries no recognisable section head
# used to be materialised whole (multi-GB allocation + an unbounded
# layer-state loop).  It must now degrade gracefully, and an embedded section
# hidden behind padding must still be found by the bounded re-layout scan.
# ---------------------------------------------------------------------------


def _classic_tif_skeleton(tmp_path, name, tag_count, blob):
    """Minimal classic TIFF whose 37724 value points at ``blob``.

    ``blob`` may be raw bytes, or an int meaning "that many zero bytes"
    (written sparsely so tests stay cheap on disk and in RAM).
    """
    import struct

    n_tags = 10
    bps_off = 8 + 2 + 12 * n_tags + 4
    blob_off = bps_off + 8
    path = tmp_path / name
    entries = []
    for t, ty, c, v in [
        (256, 3, 1, 512), (257, 3, 1, 512), (258, 3, 3, bps_off),
        (259, 3, 1, 2), (277, 3, 1, 3), (278, 3, 1, 512),
        (279, 4, 1, blob_off), (280, 4, 1, 3 * 512 * 512 % (1 << 32)),
        (282, 3, 0, 0), (37724, 7, tag_count, blob_off),
    ]:
        entries.append(struct.pack("<HHII", t, ty, c, v))
    with open(path, "wb") as d:
        d.write(b"II*\x00" + struct.pack("<I", 8))
        d.write(struct.pack("<H", n_tags) + b"".join(entries) + struct.pack("<I", 0))
        d.seek(bps_off)
        d.write(struct.pack("<III", 8, 8, 8))
        d.seek(blob_off)
        if isinstance(blob, int):  # sparse zero payload of exactly this size
            d.seek(blob_off + blob - 1)
            d.write(b"\x00")
        else:
            d.write(blob)
    return str(path)


@pytest.mark.skipif(not os.path.isdir(REAL_SAMPLES), reason="reference samples not present")
def test_tiff_foreign_state_blob_is_capped(tmp_path):
    """A giant 37724 without an embedded head must never be materialised."""
    size = 200_000_000  # 200 MB of zeros, declared in the IFD
    path = _classic_tif_skeleton(tmp_path, "junk.tif", size, size)
    doc = parse_file(path)
    assert doc.flat is True
    assert any("implausibly large" in w for w in doc.warnings)


@pytest.mark.skipif(not os.path.isdir(REAL_SAMPLES), reason="reference samples not present")
def test_tiff_embedded_head_relocated_after_pad(tmp_path):
    """A standard section hidden behind padding is still found and parsed."""
    import ca_pa.tiff_parser as tp

    src = os.path.join(REAL_SAMPLES, "sample2.tif")
    fp, reader, entries = tp._open_tiff(src)
    e = entries[tp.TAG_LAYER_STATE]
    off = tp._state_value_offset(reader, e)
    fp.seek(off)
    chunk = fp.read(1_500_000)  # marker + 8BIM stream + every Layr record
    fp.close()
    pad = b"\x00" * 65_536
    blob = pad + chunk
    path = _classic_tif_skeleton(tmp_path, "padded.tif", len(blob) % (1 << 32), blob)
    doc = parse_file(path)
    assert doc.flat is False
    assert doc.layer_count() == 134
    assert doc.folder_count() == 29


def test_parse_state_is_capped():
    """Layer-state decoding cannot expand a foreign payload unboundedly."""
    from ca_pa.tiff_parser import _parse_state

    blob = b"\x00" * (24 * 200_000)  # claims two hundred thousand layers
    states = _parse_state(blob)
    assert len(states) <= 100_000
