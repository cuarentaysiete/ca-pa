# AGENT.md — working agreements for ca-pa

Guidance for humans **and** AI agents editing this repository. Read this before
touching the parsers or the build.

## What ca-pa is

A small macOS utility that opens big layered image files — **layered TIFF**,
**PSD**, **PSB** — and shows the *layer structure* (folders, pixel/shape/text/
smart-object/adjustment layers) with per-layer metadata, **without decoding a
single pixel**. A multi-hundred-MB / multi-GB file must open in a couple of
seconds using tens of MB of RAM.

The name is a nod to *capas* ("layers").

## Hard constraint: never decode pixels

This is the whole point of the app. Parsers may read:

- headers, IFDs / tag tables, resource records, layer records,
- small per-layer metadata blocks (names, colours, masks, effects, dividers),
- section lengths.

…and then **seek straight over** anything that is channel image data, merged
image data, or TIFF strips/tiles. Do **not** add code that reads pixel payloads
into memory. If a change would require reading pixels, stop and reconsider.

The memory-bound test (`tests/test_parser.py::test_memory_bound`) exists to
enforce this — keep it green.

## File formats and how each is parsed

Dispatch is by extension **and** magic bytes (`ca_pa.parser.parse_file`), so a
mis-named file still opens. Formats:

| Format | Reader | Notes |
| ------ | ------ | ----- |
| PSD / PSB | `psd_parser.py` | Walk header + resources + layer records; seek past channel/merged image data. Reuses `psd-tools` low-level readers. |
| Layered TIFF (classic) | `tiff_parser.py` | Photoshop triple: `37724` (state), `37725` (names), `37726` (extra info). Little- or big-endian. |
| TIFF (standard embedded) | `tiff_parser.py` | Tag `37724` holds a normal "Adobe Photoshop Document Data Block" (ordinary `8BIM`, incl. a `Layr`). Records are **byte-identical to a PSD**; parse with the same `psd-tools` reader over a seekable `_FileWindow`. This is `sample2.tif`. |
| TIFF (byte-swapped / MIB8) | `tiff_parser.py` | Tag `37724` holds a **byte-swapped copy** of the PSD layer section (`MIB8` blocks). See below. |
| Flat TIFF / PSD | both | No layer info → shown as one merged layer with a note, not an error. |

### The byte-swapped MIB8 format (important — do not re-derive from scratch)

Some exporters embed, in TIFF tag `37724`, a transformed copy of the PSD layer
section instead of the classic `37725`/`37726` triple. The transformation:

- Multi-byte **integers are little-endian** (vs. PSD's big-endian).
- Every **four-character key is stored reversed**: `8BIM`→`MIB8`,
  `norm`→`mron`, `colr`→`rloc`, `sat `→` tas`, `Layr`→`ryaL`, `lclr`→`rlcl`,
  `curv`→`vruc`, `brit`→`tirb`, `hue2`→`2euh`, `selc`→`cles`, `lsct`→`tcsl`,
  `luni`→`inul`, `PlLd`→`dLlP`, `SoLd`→`dLoS`, …
- **Names and tagged-block payloads are NOT swapped** (they stay in natural
  order; UTF-16LE names, ASCII Pascal names, etc.).

The record stream sits right after a `MIB8 ryaL` frame (reversed `Layr`) and a
u16 record count. Within each record's trailing `extra` region the layout is
`MaskData, BlendingRanges, PascalName, TaggedBlocks` — the tagged blocks are the
run of `MIB8`-framed entries **at the end**, not necessarily at offset 0.

`ca_pa.tiff_parser._mib8_records()` forward-decodes this stream and normalises
each record back to standard form, recovering **every** field a PSD keeps (name,
nesting, kind, colour, visibility, opacity, blend mode, alpha/vector masks,
effects). The reference set 1 (`layered_PSD.psd` vs the two `layered_TIFF-*`
files) is a **full field-for-field match** — treat any regression there as a bug.

Useful field anchors when debugging: `CC` = 80% opacity + `Color` blend; a
`lighten` = 50%; `EYE HELP` group is hidden; `Hue/Saturation 3` = `Saturation`
blend + alpha mask.

## Repository layout

```
src/ca_pa/        parser + model + labels + errors + PySide6 GUI
tests/            make_samples.py (synthesises fixtures), test_parser.py, test_gui.py
docs/             screenshot.png, packaging.md, icons
ca-pa.spec        PyInstaller spec -> ca-pa.app
.github/workflows/build.yml   CI: test, build arm64 .app, publish on v* tag
```

## Commands

Set up:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"          # dev deps (pytest, psd-tools, PySide6, Pillow)
```

Run everything:

```bash
# Parser tests use synthetic samples only. To also run the real-file parity
# tests, point the suite at the (uncommitted) reference samples:
CA_PA_SAMPLES=/ruta/a/samples QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
```

CLI:

```bash
PYTHONPATH=src .venv/bin/python -m ca_pa --text file.psd
PYTHONPATH=src .venv/bin/python -m ca_pa --json file.tif
```

GUI tests **must** run offscreen (`QT_QPA_PLATFORM=offscreen`); `tests/conftest.py`
already sets it — do not import PySide6 in a test without that platform.

Reference samples live outside the repo (they are 13–550 MB) and are **not**
committed; `.gitignore` excludes `*.psd/psb/tif/tiff` and `samples/`.

## Environment quirks (read these)

- **Developing on Linux aarch64.** The `.app` cannot be built here —
  PyInstaller does not cross-compile. macOS builds happen on GitHub Actions.
- **The `edit` tool has been observed to inject stray `\r` characters** into
  edited files in this environment. Prefer patching via `bash` + a small Python
  `str.replace`/index-splice script (or a full rewrite with `write`), then
  verify: `grep -c $'\r' <file>` must print `0`.
- **Do not recurse naively over parsed PSD/TIFF object graphs.** A plain
  recursive walk of `node.children` can explode (hundreds of millions of rows)
  if the underlying graph shares/cycles nodes even when `doc.layer_count()` is
  small. Use `id()`-guarded traversal or the model's own flat representation.
- **`psd-tools` readers need a seekable stream.** For embedded regions, wrap the
  file window in a seekable object (`_FileWindow`) rather than a one-shot
  reader.
- **Large-file forensics:** extract the interesting blob once to `/tmp` and work
  on that instead of re-parsing 100 MB+ files repeatedly (OOM risk).

## Release process

- Bump the version in **both** `pyproject.toml` and `src/ca_pa/__init__.py`.
- Commit + push; then create and push an annotated `vX.Y.Z` tag — that triggers
  `.github/workflows/build.yml`, which runs the tests, builds `ca-pa.app` on an
  Apple Silicon runner and publishes `ca-pa-macos-arm64.zip`.
- The build matrix has an **arm64** leg and an **x86_64** leg (Rosetta 2).  The
  x86_64 leg does **not** use `actions/setup-python` (current runner images no
  longer resolve an `x86_64` 3.11); it downloads a pinned python-build-standalone
  CPython 3.11 x86_64 tarball instead — keep that URL/version working if you
  bump it.
- Both zips are attached to the release; the release body must list them both.
- **Do not build or publish a binary/tag unless explicitly asked.** Fix and test
  first; releasing is a separate, requested step.
