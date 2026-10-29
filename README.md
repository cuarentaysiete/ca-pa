# ca-pa

**ca-pa** (*«capas»*) is a small macOS utility that opens big layered image
files -- **layered TIFF** and **PSD/PSB** -- and shows their **layer
structure** (the tree of folders, pixel layers, shapes, text and adjustment
layers, with each layer's metadata) **without decoding a single pixel**.

A 1 GB layered file with 30+ layers opens in a couple of seconds and uses a
few tens of megabytes of RAM, because the parser only reads the header and
the per-layer metadata records and steps over the pixel data.

## What it reads

For every layer ca-pa shows:

* **Name**
* **Type** -- Pixel, Shape, Text, Smart Object, Folder, Adjustment (with the
  adjustment kind, e.g. *Curves*)
* **Color** -- the layer's colour swatch, when one is set
* **Opacity** and **blend mode**
* **Visibility** (including "hidden but has content" layers)
* **Masks** -- vector mask / alpha mask presence
* **Layer effects** (drop shadow, bevel, glow, ...)

And for the document: format, canvas size, bit depth and colour mode.

Files with no layer information (flattened exports) are detected and shown
as a single merged layer, with a note.

## Layout

```
src/ca_pa/
    parser.py       dispatch by extension + magic bytes
    psd_parser.py   PSD/PSB reader (metadata only, skips pixel data)
    tiff_parser.py  layered-TIFF reader (37724/37725/37726 + the
                    byte-swapped "MIB8" block some tools emit)
    model.py        DocumentInfo / LayerNode dataclasses
    labels.py       blend modes, sheet colours, effect names, ...
    errors.py       ParseError
tests/
    make_samples.py generates the files the test suite asserts on
    test_parser.py  the test suite
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# run the test suite
.venv/bin/python -m pytest

# dump a file's layer tree as text
.venv/bin/python -m ca_pa /ruta/imagen.psd
```

The real-world reference files that motivated the byte-swapped TIFF path are
**not** committed (they are 10-140 MB). To include them in the test run, put
them in a directory and point the suite at it:

```bash
CA_PA_SAMPLES=/ruta/samples .venv/bin/python -m pytest
```

## Status

The parser layer is complete and tested (PSD, PSB, classic layered TIFF and
the non-standard byte-swapped variant). The interactive GUI and the `.app`
packaging are next.
