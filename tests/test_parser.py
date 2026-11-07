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
    assert d.layer_count() == 7  # 5 top + 2 inside the folder


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
    assert d.layer_count() >= 20


@pytest.mark.skipif(not os.path.isdir(REAL_SAMPLES), reason="reference samples not present")
def test_real_tiff_mib8():
    path = os.path.join(REAL_SAMPLES, "layered_TIFF-compression.tif")
    d = parse_file(path)
    assert (d.width, d.height) == (5366, 8044)
    names = [l.name for l in d.layers]
    assert "RETOUCH" in names
    assert any(l.kind == "group" for l in d.layers)
    assert d.layer_count() >= 20


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
    psd = _flat_full(parse_file(os.path.join(REAL_SAMPLES, "sample2.psd")).layers)
    assert len(psd) > 100  # the real reference is a large document
    for other in ("sample2.tif", "sample2.psb"):
        got = _flat_full(parse_file(os.path.join(REAL_SAMPLES, other)).layers)
        assert len(got) == len(psd), other
        for ref, g in zip(psd, got):
            assert ref == g, (other, ref[1], ref, g)


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
