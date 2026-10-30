"""ca-pa entry point.

With no flags the interactive window opens (dropping files on it shows their
layer trees).  ``--text`` / ``--json`` print the tree of the given files and
exit, for scripting.

    python -m ca_pa                 # open the GUI
    python -m ca_pa a.tiff b.psd    # open the GUI with those files loaded
    python -m ca_pa --text a.tiff   # print the layer tree as text
    python -m ca_pa --json a.tiff   # print the layer tree as JSON
"""

from __future__ import annotations

import argparse
import sys


def _text_main(argv: list[str]) -> int:
    from .errors import ParseError
    from .parser import parse_file

    status = 0
    for path in argv:
        try:
            doc = parse_file(path)
        except ParseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            status = 1
            continue
        if _JSON:
            print(doc.to_json())
            continue
        print(
            "%s  %dx%d %s-bit %s %d layers (%.1f MB)"
            % (
                doc.path,
                doc.width,
                doc.height,
                doc.bit_depth,
                doc.color_mode,
                doc.layer_count(),
                doc.size_bytes / 1e6,
            )
        )
        for warning in doc.warnings:
            print(f"  warning: {warning}")
        _print_tree(doc.layers)
    return status


_JSON = False


def _flags(layer) -> str:
    flags = ""
    if not layer.visible:
        flags += " [hidden]"
    if layer.opacity < 255:
        flags += " [opacity %d%%]" % (layer.opacity * 100 // 255)
    if layer.blend_mode != "Normal":
        flags += " [%s]" % layer.blend_mode
    if layer.color:
        flags += " [%s]" % layer.color
    if layer.has_alpha_mask:
        flags += " [mask]"
    if layer.has_vector_mask:
        flags += " [vector mask]"
    if layer.effects:
        flags += " [fx: " + ", ".join(layer.effects) + "]"
    return flags


def _label(layer) -> str:
    if layer.kind == "adjustment" and layer.adjustment:
        return "Adjustment: " + layer.adjustment
    return layer.kind_label


def _print_tree(layers, indent: int = 0) -> None:
    prefix = "  " * indent
    for layer in layers:
        print(f"{prefix}{layer.name}  ({_label(layer)}){_flags(layer)}")
        _print_tree(layer.children, indent + 1)


def main(argv: list[str] | None = None) -> int:
    global _JSON
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog="ca-pa",
        description="Show the layer structure of large layered TIFF/PSD files "
        "without loading the pixels.",
    )
    ap.add_argument("files", nargs="*", help="TIFF, PSD or PSB files to open")
    ap.add_argument("--text", action="store_true", help="print a text tree and exit")
    ap.add_argument("--json", action="store_true", help="print JSON and exit")
    args = ap.parse_args(argv)
    _JSON = args.json

    if args.text or args.json:
        if not args.files:
            ap.error("--text/--json need at least one file")
        return _text_main(args.files)

    # Default: the interactive window.
    from . import gui

    return gui.run(args.files)


if __name__ == "__main__":
    raise SystemExit(main())
