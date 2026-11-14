# AGENT.md — working agreements for ca-pa

Guidance for humans **and** AI agents editing this repository. Read this before
touching the parsers or the build.

## What ca-pa is

A small macOS/Windows utility that opens big layered image files — **layered TIFF**,
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
ca-pa.spec        PyInstaller spec -> folder bundle (ca-pa.app / ca-pa) on every platform
.github/workflows/build.yml   CI: test + smoke (macOS+Windows), build all bundles, publish on v* tag
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
  if the underlying graph shares/cycles nodes even when the document reports
  few layers. Use `id()`-guarded traversal or the model's own flat
  representation.
- **`psd-tools` readers need a seekable stream.** For embedded regions, wrap the
  file window in a seekable object (`_FileWindow`) rather than a one-shot
  reader.
- **Large-file forensics:** extract the interesting blob once to `/tmp` and work
  on that instead of re-parsing 100 MB+ files repeatedly (OOM risk).

## GUI display rules

These are intentional product decisions — keep them when refactoring `gui.py`:

- **Colour lives in the Name cell background** — never reintroduce a Colour
  column. The Name cell's *text* picks near-black or near-white against that
  background (`_contrast_fg`), so it stays readable in both light and dark
  system themes.
- **Hidden rows** are attenuated by blending the text 55% toward its background
  (palette-derived via `_dim_colors`), not a fixed grey — a fixed grey
  disappears on dark swatches in dark mode.
- **Uncoloured folders** get a neutral container fill (`#ebebf5`).
- **Layers and folders are counted separately**: `DocumentInfo.layer_count()`
  excludes groups, `folder_count()` is the groups.  Don't merge them.
- Files close via the per-row ✕ button, the **Close** toolbar action, the
  Delete key or **Close all**; a parse that finishes for an already-closed file
  is dropped, never re-added to the sidebar.
- The start-up splash is **two-stage**.  `run_app.main()` re-launches itself
  with `--splash-child`; that child draws a *native* window — Win32 on
  Windows, Cocoa on macOS, via `ctypes` only, in `bootstrap.py` — **before
  Qt is imported**, and stays up until the parent dies.  The parent kills
  the child the moment the real window is on screen
  (`gui.run(on_ready=...)`).  The in-window Qt splash
  (`gui._make_splash()`) is only the *fallback* for when the child cannot
  run (Linux, headless, `CA_PA_NO_SPLASH=1`).  The child is fully
  best-effort: every failure exits silently 0 — never let it break the app.
- **Import Qt only inside functions, never at module top level** in anything
  the splash child could import (`bootstrap.py` must stay stdlib-only);
  the child's whole job is to be up *before* Qt loads.
- `CA_PA_SMOKE=1` makes the app quit itself ~2 s after the window shows;
  the workflow runs the *built* bundle with it, headless
  (`QT_QPA_PLATFORM=offscreen`), so a broken bundle fails the build.
- The bundle is a **folder** on every platform (never one-file): a
  one-file PySide6 app re-extracts to a temp dir on each start, and the
  splash child — which re-runs the same executable — would pay that cost
  twice.

## Release process

- Bump the version in **both** `pyproject.toml` and `src/ca_pa/__init__.py`.
- Commit + push; then create and push an annotated `vX.Y.Z` tag — that triggers
  `.github/workflows/build.yml`, which runs the tests on macOS **and** Windows
  and builds all three bundles.
- The build matrix has three legs: **macos-arm64** (native) and
  **macos-intel** (Rosetta 2) on `macos-latest`, plus **windows-x64** on
  `windows-latest`.  The x86_64 leg does **not** use `actions/setup-python`
  (current runner images no longer resolve an `x86_64` 3.11); it downloads a
  pinned python-build-standalone CPython 3.11 x86_64 tarball instead — keep
  that URL/version working if you bump it.
- All three zips (`ca-pa-macos-arm64`, `ca-pa-macos-intel`,
  `ca-pa-windows-x64`) are attached to the release; the release body must list
  them all.
- **Do not build or publish a binary/tag unless explicitly asked.** Fix and test
  first; releasing is a separate, requested step.

## Low-memory readers (post-0.1.5 work)

The readers never materialise pixel bytes, which is what makes multi-gigabyte
files cheap:

* **PSD/PSB** (`psd_parser.py`): `_read_record_light` mirrors
  `LayerRecord.read` field-for-field but seeks over each record's mask pixel
  block; `_read_tagged_blocks_light` caps per-block payloads
  (`_TAG_CAP_*`, big keys kept for classification, payloads dropped).
  Two exporter quirks are handled: the layer-record signature may say
  `8BIM` instead of `Layr`, and one 16/32-bit exporter variant keeps the
  whole layer info inside an `8BIM Lr16/Lr32` frame with the standard
  LayerInfo length/count fields zeroed (`_framed_layer_info`).
* **Classic layered TIFF** (`tiff_parser.py`): tag values are window-read
  (`_tag_value`, 4 MB cap) — only `LayerState` is exempt because it must
  carry the embedded records.
* **MIB8 legacy TIFF**: sections ≤ 512 MB use the full-blob forward decoder;
  bigger ones use the streaming walk (`_mib8_records_stream`, 1 MB chunks).
  `_peel_tags` recovers each record's tags by peeling MIB8 frames
  *backwards* from the end of the region, so the multi-GB mask/channel data
  between the name and the tags is seeked past, never read.
  `_last_frame_ending_at` returns the *first* candidate whose declared length
  lands exactly on the frontier: a nested zero-length frame inside another
  frame's payload also ends on the frontier and must not win.  The 16/32-bit
  records live in `Lr16`/`Lr32` frames (`_LAYER_INFO_KEYS`).
* `_mib8_pascal` locates the Pascal name backwards from the tagged run and
  skips any number of trailing pad zeros.

Measured RSS peaks: 8-bit PSD/TIFF set ≈ 38 MB; sample2 PSD/PSB/TIFF
(≈550 MB files) ≈ 42–48 MB; 2.6 GB 16-bit MIB8 TIFF ≈ 37 MB; synthetic
20–25 GB standard-embedded and MIB8 TIFFs ≈ 40–48 MB, <0.2 s.

### The multi-gigabyte "infinite loading" trap (fixed post-0.1.6)

Field report: a 14596×9730, 49-layer uncompressed TIFF (21 GB; its crops
and its PSB twin are fine) spun at 100 % CPU and grew past 20 GB of RAM
forever.  Lesson encoded as code — **nothing in the TIFF path may ever be
bounded by a user-controlled 32-bit count**:

* Classic-side tag values go through `get_capped` (`_CLASSIC_STATE_MAX`,
  64 MB): a foreign payload declared in 37724/37725/37726 is skipped with
  a warning instead of being read into RAM.  The classic layer-state loop
  itself is capped too (`_parse_state` builds at most 100k records).
* `_embed_kind` peeks 43 bytes at the declared offset.  When that peek
  finds nothing but the tag is large, `_relayout_embed_entry` does a
  *bounded* scan (≤ 8 MB, 1 MB chunks) for the real section head behind
  padding or a variant header and re-points the IFD entry in place; the
  standard/MIB8 streamers then run unchanged.  Total extra I/O: a few MB.
* Synthetic verification: a 20.9 GB standard-embedded TIFF (wrapped u32
  counts, 79 records at full canvas) parses in ~0.15 s at ~40 MB RSS;
  the same file with 2 MB of padding before the marker is recovered by the
  re-layout scan; a 3 GB marker-less blob degrades to a flat document
  instantly.

Note the classic-TIFF constraint behind all of this: every IFD offset must
fit in 32 bits, so in a 21 GB file the embedded section always sits below
the 4.29 GB mark while the *counts* (37724 size, Layr length) wrap around
— the readers must treat wrapped counts as untrusted hints, never as
allocation sizes.
