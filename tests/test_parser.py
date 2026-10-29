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


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("samples")
    return make_samples.generate_all(str(out))


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
    assert fancy.color == "#ff0000"
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
