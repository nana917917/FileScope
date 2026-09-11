"""FileScope launcher.

Keeps the familiar ``python FileScope.py`` entry point while the implementation
lives in the ``filescope`` package.
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    from filescope.app import main as app_main

    return app_main(sys.argv[1:])


if __name__ == "__main__":
    # PDF/OCR work can be moved to a helper process; frozen builds must not start
    # a second copy of the whole application.
    multiprocessing.freeze_support()
    raise SystemExit(main())
