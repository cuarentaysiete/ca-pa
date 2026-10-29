"""Data model: plain, JSON-serialisable descriptions of a document's layers.

Everything in this module is lightweight metadata: a :class:`LayerNode`
never carries pixel data, and a full :class:`DocumentInfo` for a 2 GB file
is a few kilobytes at most.

Layer order convention (same as Photoshop's Layers panel): ``layers[0]`` is
the top-most layer, and ``children[0]`` is the top-most child of a group.
Layer coordinates are the document-space bounding box ``(left, top,
right, bottom)``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Human readable labels for layer kinds.
KIND_LABELS: dict[str, str] = {
    "pixel": "Pixel",
    "shape": "Shape",
    "text": "Text",
    "smartobject": "Smart Object",
    "group": "Folder",
    "adjustment": "Adjustment",
    "fill": "Fill",
}


@dataclass
class LayerNode:
    """One layer (or folder) in the hierarchy."""

    name: str
    kind: str = "pixel"  # pixel | shape | text | smartobject | group | adjustment | fill
    kind_label: str = "Pixel"
    adjustment: str | None = None  # e.g. "Curves" when kind == "adjustment"
    color: str | None = None  # layer colour label as #rrggbb, or None
    opacity: int = 255  # 0..255
    blend_mode: str = "Normal"
    visible: bool = True
    collapsed: bool = False  # groups only: is the folder drawn open or closed
    bounds: tuple[int, int, int, int] | None = None  # left, top, right, bottom
    has_alpha_mask: bool = False
    has_vector_mask: bool = False
    effects: list[str] = field(default_factory=list)  # effect names, e.g. "Drop shadow"
    children: list[LayerNode] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return self.kind == "group"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "kindLabel": self.kind_label,
            "adjustment": self.adjustment,
            "color": self.color,
            "opacity": self.opacity,
            "blendMode": self.blend_mode,
            "visible": self.visible,
            "collapsed": self.collapsed,
            "bounds": list(self.bounds) if self.bounds else None,
            "hasAlphaMask": self.has_alpha_mask,
            "hasVectorMask": self.has_vector_mask,
            "effects": list(self.effects),
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class DocumentInfo:
    """Metadata of one opened document: canvas + layer tree."""

    path: str
    format: str  # "psd" | "psb" | "tiff"
    size_bytes: int
    width: int
    height: int
    bit_depth: int
    color_mode: str  # "RGB" | "CMYK" | "Grayscale" | ...
    channels: int
    has_alpha: bool
    layers: list[LayerNode] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None  # set when the file could not be fully read
    flat: bool = False  # True when the file carries no layer information

    @property
    def is_flat(self) -> bool:
        return self.flat

    def layer_count(self) -> int:
        total = 0
        for layer in self.layers:
            total += 1 + layer._count_descendants()
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "format": self.format,
            "sizeBytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "bitDepth": self.bit_depth,
            "colorMode": self.color_mode,
            "channels": self.channels,
            "hasAlpha": self.has_alpha,
            "layers": [l.to_dict() for l in self.layers],
            "warnings": list(self.warnings),
            "error": self.error,
            "flat": self.flat,
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def _count_descendants(self: LayerNode) -> int:
    return sum(1 + _count_descendants(c) for c in self.children)


# Bind the helper onto the dataclass (avoids a self-referential default).
LayerNode._count_descendants = _count_descendants  # type: ignore[attr-defined]
