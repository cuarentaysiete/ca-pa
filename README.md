<p align="center"><img src="docs/icon.png" width="128" alt="ca-pa icon"></p>

<h1 align="center"><i>ca-pa</i></h1>

_**ca-pa**_ is a layer inspector, a small utility that opens big layered image files,
**layered TIFF**, **PSD** and **PSB**, and shows the **layer structure**:
the tree of folders, pixel layers, shapes, text, smart objects and adjustment
layers, with each layer's metadata. It does this **without decoding a single
pixel**, so a multi-hundred-MB file with 30+ layers opens in a couple of
seconds and uses a few tens of megabytes of RAM.  For now it reads
**8-bit-per-channel** files; 16- and 32-bit files are not supported yet.

<p align="center"><img src="docs/screenshot.png" alt="ca-pa inspecting a layered TIFF: 18 layers and 8 folders" width="760"></p>
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

| Column | Contents |
| ------ | -------- |
| **Name** | The layer's name. The cell background is painted with the layer's colour. |
| **Type** | Pixel, Shape, Text, Smart Object, Folder, or Adjustment (with the adjustment kind, e.g. *Curves*). |
| **Opacity** | 0–100 % (values under 100 % are the ones worth noticing). |
| **Blend** | The blend mode (Normal, Multiply, Screen, Overlay, …). |
| **Notes** | Flags: hidden, vector mask, alpha mask, and the layer effects (drop shadow, bevel, glow, …). |

For every **document**: format, canvas size, bit depth, colour mode, the
**layer** and **folder** counts (folders are containers, never counted as
layers), plus any warnings (e.g. that a non-standard byte-swapped layer block was decoded, or that the file is flattened).

Files that carry **no layer information** (flattened exports) are detected and
shown as a single merged layer with a note, rather than failing.

Several files can be open at once, listed in the sidebar.  Each row has an
✕ button that closes just that file; the **Close** toolbar button (or the
Delete key) closes the selected one and **Close all** empties the window.
The app opens with a splash — the icon on white with the name *ca-pa: a
layer inspector* — visible from the very first moment of the start: a small
child process draws a native window *before* Qt is loaded, and it is
retired the instant the main window appears (if that child cannot run, an
in-window Qt splash covers the wait instead).

## How it works

```mermaid
graph TD
    A["Input file<br/>PSD · PSB · layered TIFF<br/>(8-bit per channel, for now)"] --> B{"Dispatcher<br/>ca_pa.parser"}

    B -->|"magic 8BPS"| C["PSD / PSB reader<br/>psd_parser.py"]
    B -->|"magic II* / MM*  (TIFF)"| D["TIFF reader<br/>tiff_parser.py"]

    C -->|"reads header, resources<br/>and layer records"| J[("Data model<br/>DocumentInfo + LayerNode tree")]
    C -.->|"seeks past channel data<br/>and the merged image"| F["Pixel data — never read"]

    D --> G{"embedded layer format"}
    G -->|"classic Photoshop"| H["tag 37724 (state) + tag 37725 (names)<br/>+ tag 37726 (extra info)"]
    G -->|"byte-swapped MIB8"| I["tag 37724: a byte-swapped record stream<br/>decoded and normalised to PSD form"]
    G -->|"standard embedded"| M["tag 37724: an ordinary 'Layr' block<br/>— records are reused by the PSD reader"]
    H --> J
    I --> J
    M --> J
    D -.->|"seeks past strips / tiles"| F

    J --> K["GUI window<br/>gui.py"]
    J --> L["CLI output<br/>--text / --json"]

    classDef skip fill:#19a0c4,stroke:#fc0,stroke-width:2px,stroke-dasharray:5 5;
    class F skip;
```


* **PSD / PSB** — the reader walks the file header, image resources and layer
  records, and **seeks past** the channel image data and the merged image. The
  per-layer tagged blocks (names, colours, masks, effects, section dividers)
  are small, so only those are read.
* **Layered TIFF** — reads the classic Photoshop triple: `37724` (layer state:
  bounds/opacity/blend), `37725` (names) and `37726` (additional layer info:
  colours, effects, folder dividers).
* **Byte-swapped TIFF** — some exporters stash a *whole byte-swapped copy of
  the PSD layer section* inside TIFF tag `37724`: a run of `MIB8` blocks whose
  multi-byte integers are little-endian and whose four-character tag/blend keys
  are stored reversed (`norm`→`mron`, `colr`→`rloc`, `Layr`→`ryaL`, …), while
  names and tagged-block payloads keep their natural order. ca-pa detects that
  shape, forward-decodes the record stream and normalises each record back to
  standard PSD form — recovering **every** field a normal PSD keeps (names,
  nesting, kind, colour, visibility, opacity, blend mode, alpha/vector masks and
  effects) so the result matches the equivalent PSD exactly — then seeks straight
  over the large image-data blocks, keeping even a +1 GB file at a few
  megabytes. An informative warning notes the non-standard block.

* **Standard embedded TIFF** — other writers put a normal, *non-swapped* "Adobe
  Photoshop Document Data Block" (ordinary `8BIM` resources, including a `Layr`
  block) in the same `37724` tag. Its layer records are **byte-identical to a
  PSD's**, so ca-pa reuses the exact PSD record reader and reproduces the file
  perfectly — names, nesting, kinds, colours, visibility, opacity, blends, masks
  and effects all match the equivalent PSD. The multi-hundred-MB trailing image
  data is still only sought over, never read. This is the format of the
  `sample2.tif` reference file.
* **Dispatch** — `ca_pa.parser.parse_file()` picks the reader by extension and,
  more reliably, by magic bytes (`8BPS` for PSD/PSB, `II*\0`/`MM\0*` for
  TIFF), so a mis-named file still opens correctly. A file that can't be read
  raises `ca_pa.errors.ParseError` with a useful message.

The data model is two plain dataclasses — `DocumentInfo` (canvas + layer tree)
and `LayerNode` (one layer) — with `to_dict()`/`to_json()` for scripting.


```mermaid
classDiagram
    class DocumentInfo {
        +String path
        +String format
        +Int size_bytes
        +Int width
        +Int height
        +Int bit_depth
        +String color_mode
        +Int channels
        +Bool has_alpha
        +Bool flat
        +List~String~ warnings
        +List~LayerNode~ layers
        +int layer_count()
        +int folder_count()
        +dict to_dict()
        +String to_json()
    }

    class LayerNode {
        +String name
        +String kind
        +String kind_label
        +String adjustment
        +String color
        +int opacity
        +String blend_mode
        +Bool visible
        +Bool collapsed
        +Bool has_alpha_mask
        +Bool has_vector_mask
        +List~String~ effects
        +List~LayerNode~ children
    }

    DocumentInfo "1" *-- "many" LayerNode : layers
    LayerNode "1" *-- "many" LayerNode : children
```


---

## Getting the app

The easiest way is a prebuilt bundle from the
[releases](https://github.com/cuarentaysiete/ca-pa/releases) page — pick the
zip for your machine:

| Your machine is… | Download |
|---|---|
| macOS, Apple Silicon (M1/M2/M3/M4, 2020+) | `ca-pa-macos-arm64.zip` → `ca-pa.app` |
| macOS, Intel (or Apple Silicon via Rosetta 2) | `ca-pa-macos-intel.zip` → `ca-pa.app` |
| Windows 10/11 (64-bit) | `ca-pa-windows-x64.zip` → `ca-pa/` folder |

**macOS:** unzip and open `ca-pa.app`. **Windows:** unzip and run
`ca-pa\ca-pa.exe`; if SmartScreen warns, choose *More info* → *Run anyway*
(the builds are not code-signed).


<details>
<summary><b>Building from source</b></summary>

```bash
git clone https://github.com/cuarentaysiete/ca-pa
cd ca-pa
python3 -m venv .venv
.venv/bin/pip install -e ".[app]"          # app + PySide6 + psd-tools + pyinstaller
.venv/bin/pyinstaller --clean --noconfirm ca-pa.spec
```

On macOS this produces `dist/ca-pa.app`; on Windows, `dist/ca-pa/ca-pa.exe`.
On Windows use `.venv\Scripts\pyinstaller` instead of `.venv/bin/pyinstaller`.
Because PyInstaller does not build across platforms, each platform's bundle
must be assembled on that platform (CI does exactly that on `macos-latest`
and `windows-latest`). See [`docs/packaging.md`](docs/packaging.md) for the
full guide, including the optional high-resolution icon and code signing.
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
| PSD | 8-bit per channel, any colour mode. |
| PSB | Big PSD (files > 300 MB / > 30 000 px). |
| Layered TIFF | The classic Photoshop `37724`/`37725`/`37726` triple (little- or big-endian). |
| TIFF (byte-swapped) | The non-standard, byte-swapped embedded-PSD variant some tools write. |
| TIFF (standard embedded) | A normal `8BIM` `Layr` block in `37724`; layer metadata matches the PSD exactly. |
| Flat TIFF / PSD | Detected; shown as one merged layer with a note. |

**Bit depth** — for now ca-pa only reads **8-bit-per-channel** files; 16- and
32-bit files are not supported yet.

## Project layout

```mermaid
graph LR
    subgraph Interfaces
        RUN[run_app.py<br/>frozen entry]
        CLI[CLI<br/>__main__.py]
        GUI[PySide6 GUI<br/>gui.py]
    end

    subgraph Core parsing
        DISP[Dispatcher<br/>parser.py]
        PSD[PSD / PSB reader<br/>psd_parser.py]
        TIFF[TIFF reader<br/>tiff_parser.py]
    end

    subgraph Data and helpers
        MOD[Data model<br/>model.py]
        LBL[Labels and enums<br/>labels.py]
        ERR[Exceptions<br/>errors.py]
    end

    RUN --> GUI
    CLI --> DISP
    CLI -.-> GUI
    GUI --> DISP
    GUI --> MOD
    GUI -.-> LBL
    GUI -.-> ERR
    DISP --> PSD
    DISP --> TIFF
    DISP --> MOD
    DISP -.-> ERR
    PSD --> MOD
    TIFF --> MOD
    PSD -.-> LBL
    TIFF -.-> LBL
    PSD -.-> ERR
    TIFF -.-> ERR
```


```
src/ca_pa/
    __main__.py     CLI + GUI entry point (python -m ca_pa)
    run_app.py      single frozen entry point for the PyInstaller bundle
    parser.py       dispatch by extension + magic bytes
    psd_parser.py   PSD/PSB reader (metadata only, seeks past pixels)
    tiff_parser.py  layered-TIFF reader (classic triple + embedded MIB8/standard block)
    model.py        DocumentInfo / LayerNode dataclasses
    labels.py       blend modes, sheet colours, adjustments, effect names
    errors.py       ParseError
    bootstrap.py    the pre-Qt native splash (child process, ctypes only)
    gui.py          the PySide6 window (toolbar, sidebar, tree, status, splash)
    run_app.py      GUI launcher (frozen entry point; orchestrates the splash)
    resources/      the app icon (PNG + .icns + .ico)
tests/
    make_samples.py synthesises PSD/TIFF files with known metadata
    test_parser.py  parser + memory-bound tests
    test_gui.py     GUI smoke tests (offscreen)
    test_bootstrap.py  the splash child plumbing
ca-pa.spec          PyInstaller specification (folder bundle on every platform)
.github/workflows/  CI: test + build + release on macOS and Windows
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

A GitHub Actions workflow (`.github/workflows/build.yml`) runs the test
suite on **macOS and Windows** (headless, via the offscreen Qt platform),
then **builds with PyInstaller** on the matching platform runners —
PyInstaller is not cross-platform, so each bundle must be assembled where
it will run.  The build matrix has three legs:

- **macos-arm64** on `macos-latest` (native Apple Silicon);
- **macos-intel** on the same runner under Rosetta 2, using a pinned
  [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
  x86_64 interpreter (the runner images no longer ship one that
  `actions/setup-python` will resolve); a `file` check confirms the
  architecture before zipping;
- **windows-x64** on `windows-latest`, producing the `ca-pa/` folder
  bundle (a one-file PySide6 app would re-extract itself to `%TEMP%` on
  every start).

Pushing a `v*` tag then publishes a single GitHub Release with all three
zips.  Manual runs are available from the *Actions* tab and keep their
builds as run artifacts.


## License

See [LICENSE](LICENSE).
