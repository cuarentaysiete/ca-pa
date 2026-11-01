"""Shared label tables shared by the PSD and TIFF parsers."""

from __future__ import annotations

import struct

#: Photoshop blend-mode keys (4 chars) -> human readable names, in the
#: order Photoshop lists them.
BLEND_MODE_NAMES: dict[bytes, str] = {
    b"pass": "Pass Through",
    b"norm": "Normal",
    b"diss": "Dissolve",
    b"dark": "Darken",
    b"mul ": "Multiply",
    b"idiv": "Color Burn",
    b"lbrn": "Linear Burn",
    b"dkCl": "Darker Color",
    b"lite": "Lighten",
    b"scrn": "Screen",
    b"div ": "Color Dodge",
    b"lddg": "Linear Dodge (Add)",
    b"lgCl": "Lighter Color",
    b"over": "Overlay",
    b"sLit": "Soft Light",
    b"hLit": "Hard Light",
    b"vLit": "Vivid Light",
    b"lLit": "Linear Light",
    b"pLit": "Pin Light",
    b"hMix": "Hard Mix",
    b"diff": "Difference",
    b"smud": "Exclusion",
    b"fsub": "Subtract",
    b"fdiv": "Divide",
    b"hue ": "Hue",
    b"sat ": "Saturation",
    b"colr": "Color",
    b"lum ": "Luminosity",
}

#: Photoshop colour labels ('lclr' sheet colour / layer swatch).
#: Photoshop layer "sheet" colours (the ``lclr`` / ``rlcl`` block, a 0-11 code
#: matching psd-tools' ``SheetColorType``).  0 is "no colour".
SHEET_COLORS: dict[int, str] = {
    # Exact tones sampled from samples/screenshots/colors.png (the app palette).
    1: "#e78679",   # Red
    2: "#eab267",   # Orange
    3: "#e4dd74",   # Yellow
    4: "#b4de8e",   # Green
    5: "#a1c4f6",   # Blue
    6: "#a499dd",   # Violet
    7: "#b3b3b3",   # Gray
    8: "#5b9ba1",   # Seafoam
    9: "#6d76e9",   # Indigo
    10: "#c35196",  # Magenta
    11: "#af53cc",  # Fuchsia
}

#: Photoshop adjustment tag -> label.
ADJUSTMENT_NAMES: dict[bytes, str] = {
    b"brit": "Brightness/Contrast",
    b"blwh": "Black & White",
    b"blnc": "Color Balance",
    b"clrL": "Color Lookup",
    b"mixr": "Channel Mixer",
    b"curv": "Curves",
    b"grdm": "Gradient Map",
    b"expA": "Exposure",
    b"hue2": "Hue/Saturation",
    b"hue ": "Hue/Saturation",
    b"levl": "Levels",
    b"phfl": "Photo Filter",
    b"selc": "Selective Color",
    b"thrs": "Threshold",
    b"post": "Posterize",
    b"vibA": "Vibrance",
    b"nvrt": "Invert",
}
# Keys that merely *look* like adjustment blocks but are not -- notably
# 'infx' is the blend-interior-elements block, present on many plain layers.
NON_ADJUSTMENT_KEYS: frozenset[bytes] = frozenset({b"infx", b"clbl", b"knko"})


#: Photoshop layer-effect class names (from 'lfx2') -> friendly labels.
EFFECT_LABELS: dict[str, str] = {
    "DropShadow": "Drop shadow",
    "InnerShadow": "Inner shadow",
    "OuterGlow": "Outer glow",
    "InnerGlow": "Inner glow",
    "SigmoidCurve": "Sigmoid curve",
    "BevelEmboss": "Bevel & emboss",
    "Contour": "Contour",
    "SmartShadow": "Smart shadow",
    "Stroke": "Stroke",
    "ColorOverlay": "Color overlay",
    "GradientOverlay": "Gradient overlay",
    "PatternOverlay": "Pattern overlay",
    "Saturation": "Saturation",
    "HueSaturation": "Hue/saturation",
    "SolidFill": "Solid colour fill",
}


def blend_mode_name(key: bytes | str | None) -> str:
    if key is None:
        return "Normal"
    if isinstance(key, str):
        key = key.encode("latin-1")[:4]
    return BLEND_MODE_NAMES.get(key, key.decode("latin-1", "replace").strip() or "Normal")


def rgba_to_hex(rgba: bytes) -> str | None:
    """'lsdk' 4-byte RGBA -> #rrggbb, or None for the "no colour" value."""
    if len(rgba) < 4:
        return None
    r, g, b, a = rgba[:4]
    if a == 0 and r == g == b == 0:
        return None
    return "#%02x%02x%02x" % (r, g, b)


#: Legacy ``fxly`` (8017/8052) effect keys -> friendly labels.  Modern files
#: use the ``lfx2`` descriptor instead; this only covers the pre-CS "fxly"
#: block that older layered TIFFs may still carry.
FXLY_KEY_NAMES: dict[int, str] = {
    8424: "Drop shadow",
    8425: "Inner shadow",
    8426: "Outer glow",
    8427: "Inner glow",
    8428: "Bevel & emboss",
    8429: "Satin",
    8430: "Colour overlay",
    8431: "Pattern overlay",
    8432: "Gradient overlay",
    8433: "Saturate",
    8434: "Stroke",
    8435: "Smart shadow",
}


def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]
