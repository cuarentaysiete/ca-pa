"""Exceptions raised by the ca-pa parsers."""


class ParseError(Exception):
    """The file could not be parsed (corrupt, truncated or unsupported)."""


class UnsupportedFormatError(ParseError):
    """The file is not a PSD, PSB or TIFF that ca-pa can read."""
