"""Command line front-end: print the layer tree of one or more files.

    python -m ca_pa file.tiff [file.psd ...] [--json]
"""

from __future__ import annotations

import argparse
import sys

from .errors import ParseError
from .parser import parse_file


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
    ap = argparse.ArgumentParser(
        prog="ca-pa",
        description="Show the layer structure of large layered TIFF/PSD files "
        "without loading the pixels.",
    )
    ap.add_argument("files", nargs="+", help="TIFF, PSD or PSB files")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a tree")
    args = ap.parse_args(argv)

    status = 0
    for path in args.files:
        try:
            doc = parse_file(path)
        except ParseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            status = 1
            continue
        if args.json:
            print(doc.to_json())
        else:
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


if __name__ == "__main__":
    raise SystemExit(main())
