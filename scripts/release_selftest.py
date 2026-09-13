"""Run the FileScope release self test.

Safe for a user PC: everything happens in a temporary folder and no user
document is opened. Tesseract checks are SKIPped (not failed) when the engine
is not installed.

    python scripts/release_selftest.py
    python scripts/release_selftest.py --keep-workspace
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filescope.selftest import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
