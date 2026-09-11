from __future__ import annotations

__version__ = "5.0.0"

# Bumped whenever extraction output changes shape or content. Index rows carry
# the version they were written with so a change can invalidate just the
# affected formats instead of the whole index.
EXTRACTOR_VERSION = "5.0"

# Bumped when the OCR pipeline (render scale, engine usage, preprocessing)
# changes in a way that can change recognised text.
OCR_VERSION = "5.0"
