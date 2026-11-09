"""ca-pa: layer inspector for large layered TIFF/PSD files.

The public entry point is :func:`ca_pa.parser.parse_file`, which reads a
PSD/PSB or layered TIFF file and returns a :class:`~ca_pa.model.DocumentInfo`
describing the layer hierarchy. No pixel data is ever decoded or held in
memory: only headers, layer records, tags and image resource blocks are read.
"""

from .model import DocumentInfo, LayerNode
from .parser import parse_file
from . import errors

__all__ = ["parse_file", "DocumentInfo", "LayerNode", "errors"]
__version__ = "0.1.5"


def main(argv: list[str] | None = None) -> int:
    """Console entry point (mirrors ``python -m ca_pa``)."""
    from ca_pa.__main__ import main as _main

    return _main(argv)
