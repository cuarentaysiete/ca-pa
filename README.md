![ca-pa icon](docs/icon.png)

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

## Using the app

```bash
ca-pa                      # open the window; drag & drop files onto it
ca-pa a.tiff b.psd         # open the window with those files already loaded
ca-pa --text a.tiff        # print the layer tree as text (for scripting)
ca-pa --json a.tiff        # print the layer tree as JSON
```

The window has a file list on the left (one entry per dropped file, with its
layer count and size) and the layer tree on the right, with columns for name,
type, colour swatch, opacity, blend mode and a notes column (hidden, masks,
effects).  Selecting a file shows its tree; the status bar shows the canvas
size, bit depth, colour mode, layer count and any warnings.

## Status

The parser and the PySide6 GUI are complete and tested.  The remaining piece
is the PyInstaller `.app` bundle for distribution (see
`docs/packaging.md`).
