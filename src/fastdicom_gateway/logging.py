"""Minimal, safe-by-construction logging setup.

Every call site elsewhere in this project passes only counts, categories,
and timings as log arguments -- never raw request bytes or DICOM element
values. See README.md "Logging" for the full list of what must never
appear in these logs. This module only configures *where* logs go (stdout,
never a file); it deliberately does not try to auto-redact content, because
the actual safety property comes from never constructing an unsafe message
in the first place.
"""

from __future__ import annotations

import logging
import sys

_configured_loggers: set[str] = set()


def get_logger(name: str) -> logging.Logger:
    """Returns a stdlib logger that writes plain lines to stdout only.

    No FileHandler is ever attached -- logs are not written to any
    filesystem path by this project.
    """
    logger = logging.getLogger(name)
    if name not in _configured_loggers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        _configured_loggers.add(name)
    return logger
