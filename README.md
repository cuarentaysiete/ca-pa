<p align="center"><img src="docs/icon.png" width="128" alt="ca-pa icon"></p>

<h1 align="center"><i>ca-pa</i></h1>

_**ca-pa**_ is a layer inspector, a small macOS utility that opens big layered image files,
**layered TIFF**, **PSD** and **PSB**, and shows the **layer structure**:
the tree of folders, pixel layers, shapes, text, smart objects and adjustment
layers, with each layer's metadata. It does this **without decoding a single
pixel**, so a multi-hundred-MB file with 30+ layers opens in a couple of
seconds and uses a few tens of megabytes of RAM.

<p align="center"><img src="docs/screenshot.png" alt="ca-pa inspecting a 26-layer PSD" width="760"></p>
The name is a nod to *capas* (Spanish for “layers”).

---

## Why

Full-featured editors load an image's pixels to show its layers. For a 1–2 GB
layered TIFF or PSD that means gigabytes of RAM and a long wait, even when you
only want to *see* what's in the file. ca-pa reads just the metadata — the
header and the per-layer records — and steps straight over the pixel data, so
it stays fast and light no matter how big or deep the file is.

Think of it as a “layer inspector” / file peeker: you open a file, glance at
its structure and metadata, and move on, without a heavyweight editor.

## What it shows

For every **layer** (and folder):

| Field | Meaning |
| ----- | ------- |
| **Name** | The layer's name. |
| **Type** | Pixel, Shape, Text, Smart Object, Folder, or Adjustment (with the adjustment kind, e.g. *Curves*). |
| **Colour** | The layer's colour swatch, when the file sets one. |
| **Opacity** | 0–100 % (less than 100 % is the common case worth noting). |
| **Blend** | The blend mode (Normal, Multiply, Screen, Overlay, …). |
| **Notes** | Flags: hidden, vector mask, alpha mask, and the layer effects (drop shadow, bevel, glow, …). |

For every **document**: format, canvas size, bit depth, colour mode and the
total layer count, plus any warnings (e.g. a non-standard layer block that was
read best-effort, or a flattened file).

Files that carry **no layer information** (flattened exports) are detected and
shown as a single merged layer with a note, rather than failing.

## How it works

```mermaid
graph TD
    A[Input File<br/>PSD, PSB, TIFF] --> B{Dispatcher<br/>ca_pa.parser}
    
    B -->|Magic Bytes: 8BPS| C[PSD / PSB Parser]
    B -->|Magic Bytes: II* / MM*| D[TIFF Parser]
    
    C --> E[Read Metadata & Blocks<br/>Header, Resources, Layers]
    C -.->|Seek Past| F[Heavy Pixel Data]
    
    D --> G{Layered TIFF Type}
    G -->|Classic Photoshop| H[Read Tags:<br/>37724, 37725, 37726]
    G -->|Byte-swapped| I[Read Tag 37724<br/>MIB8 Metadata Blocks]
    
    E --> J((Data Model))
    H --> J
    I --> J
    
    J --> K[GUI Window]
    J --> L[CLI Output<br/>Text / JSON]
    
    classDef skip fill:#19a0c4,stroke:#fc0,stroke-width:3px,stroke-dasharray: 5 5;
    class F skip;
```

* **PSD / PSB** — the reader walks the file header, image resources and layer
  records, and **seeks past** the channel image data...

* **PSD / PSB** — the reader walks the file header, image resources and layer
  records, and **seeks past** the channel image data and the merged image. The
  per-layer tagged blocks (names, colours, masks, effects, section dividers)
  are small, so only those are read.
* **Layered TIFF** — reads the classic Photoshop triple: `37724` (layer state:
  bounds/opacity/blend), `37725` (names) and `37726` (additional layer info:
  colours, effects, folder dividers).
* **Byte-swapped TIFF** — a number of exporters stash a *whole byte-swapped
  copy of the PSD layer section* inside TIFF tag `37724` (a sequence of
  `MIB8` blocks) and omit the `37725`/`37726` tags entirely. ca-pa detects that
  shape and reads the small metadata blocks (names, dividers, colours, opacity,
  type) while skipping the large image-data blocks, so even a 140 MB file costs
  a few megabytes.
* **Dispatch** — `ca_pa.parser.parse_file()` picks the reader by extension and,
  more reliably, by magic bytes (`8BPS` for PSD/PSB, `II*\0`/`MM\0*` for
  TIFF), so a mis-named file still opens correctly. A file that can't be read
  raises `ca_pa.errors.ParseError` with a useful message.

The data model is two plain dataclasses — `DocumentInfo` (canvas + layer tree)
and `LayerNode` (one layer) — with `to_dict()`/`to_json()` for scripting.

```mermaid
classDiagram
    class DocumentInfo {
        +String format
        +Tuple canvas_size
        +Int bit_depth
        +String colour_mode
        +Int total_layer_count
        +List warnings
        +List~LayerNode~ layers
        +to_dict() dict
        +to_json() string
    }
    
    class LayerNode {
        +String name
        +String type
        +String colour
        +Float opacity
        +String blend_mode
        +List notes
        +List~LayerNode~ children
    }
    
    DocumentInfo "1" *-- "many" LayerNode : contains
    LayerNode "1" *-- "many" LayerNode : nested folders
```

---

## Getting the app

The easiest way is a prebuilt bundle from the [releases](https://github.com/cuarentaysiete/ca-pa/releases):
download `ca-pa-macos.zip`, unzip it, and you have `ca-pa.app`.

<details>
<summary><b>Building from source</b> (macOS only)</summary>

```bash
git clone https://github.com/cuarentaysiete/ca-pa
cd ca-pa
python3 -m venv .venv
.venv/bin/pip install -e ".[app]"          # app + PySide6 + psd-tools + pyinstaller
.venv/bin/pyinstaller --clean --noconfirm ca-pa.spec
open dist/ca-pa.app
```

Because PyInstaller does not build across platforms, the `.app` must be built
on macOS. See [`docs/packaging.md`](docs/packaging.md) for the full guide,
including the optional high-resolution icon and code signing.
</details>

## Using it

**The window** — drop `.tif` / `.tiff` / `.psd` / `.psb` files anywhere on the
window (several at once). Each becomes an entry in the left list; selecting one
shows its layer tree on the right. The status bar summarises the document and
any warnings. Folders open and close with the tree's disclosure triangles.

**The command line** — for scripting, without the window:

```bash
ca-pa                          # open the window
ca-pa a.tiff b.psd             # open the window with those files loaded
ca-pa --text a.tiff            # print the layer tree as indented text
ca-pa --json a.tiff            # print the structure as JSON
```

`--json` emits the full `DocumentInfo` (canvas, warnings, and the nested layer
tree with every field above), which is handy for feeding other tools.

## Supported files

| Format | Notes |
| ------ | ----- |
| PSD | 8-bit and 16-bit, any colour mode. |
| PSB | Big PSD (files > 300 MB / > 30 000 px). |
| Layered TIFF | The classic Photoshop `37724`/`37725`/`37726` triple. |
| TIFF (byte-swapped) | The non-standard embedded-PSD variant some tools write. |
| Flat TIFF / PSD | Detected; shown as one merged layer with a note. |

## Project layout

```mermaid
graph LR
    subgraph Interfaces
        CLI[CLI<br/>__main__.py]
        GUI[PySide6 GUI<br/>gui.py]
    end

    subgraph Core Parsing Logic
        DISP[Dispatcher<br/>parser.py]
        PSD[PSD Reader<br/>psd_parser.py]
        TIFF[TIFF Reader<br/>tiff_parser.py]
    end

    subgraph Data & Utilities
        MOD[Data Model<br/>model.py]
        LBL[Labels & Enums<br/>labels.py]
        ERR[Exceptions<br/>errors.py]
    end

    CLI --> DISP
    GUI --> DISP
    
    DISP --> PSD
    DISP --> TIFF
    DISP -.-> ERR
    
    PSD --> MOD
    TIFF --> MOD
    
    PSD -.-> LBL
    TIFF -.-> LBL
```

```
src/ca_pa/
    __main__.py     CLI + GUI entry point (python -m ca_pa)
    run_app.py      single frozen entry point for the PyInstaller bundle
...
```

```
src/ca_pa/
    __main__.py     CLI + GUI entry point (python -m ca_pa)
    run_app.py      single frozen entry point for the PyInstaller bundle
    parser.py       dispatch by extension + magic bytes
    psd_parser.py   PSD/PSB reader (metadata only, seeks past pixels)
    tiff_parser.py  layered-TIFF reader (classic triple + byte-swapped block)
    model.py        DocumentInfo / LayerNode dataclasses
    labels.py       blend modes, sheet colours, adjustments, effect names
    errors.py       ParseError
    gui.py          the PySide6 window (sidebar + tree + status)
    resources/      the app icon (PNG + .icns)
tests/
    make_samples.py synthesises PSD/TIFF files with known metadata
    test_parser.py  parser + memory-bound tests
    test_gui.py     GUI smoke tests (offscreen)
ca-pa.spec          PyInstaller specification (-> ca-pa.app)
.github/workflows/  CI: build + release on macOS
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/python -m pytest            # the whole suite
.venv/bin/python -m ca_pa --text file.psd
```

The suite generates its own sample files (PSD, layered TIFF, flat, and a large
incompressible one for the memory-bound test) into a temp directory, so it runs
anywhere with no fixtures. Real-world reference files are **not** committed
(they are 10–140 MB); to include them in a run, point the suite at a folder:

```bash
CA_PA_SAMPLES=/ruta/samples .venv/bin/python -m pytest
```

## Packaging & CI

A GitHub Actions workflow (`.github/workflows/build.yml`) runs on `macos-latest`:
it runs the test suite, builds `ca-pa.app` with PyInstaller (full-resolution
icon via `sips`/`iconutil`), and zips it as a release artifact. Pushing a
`v*` tag also publishes a GitHub Release. Manual runs are available from the
*Actions* tab.

## License

See [LICENSE](LICENSE).
