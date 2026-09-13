"""Mutation probe as a test: the suite must notice broken critical logic."""

from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import mutation_probe


def test_all_probed_mutations_are_detected() -> None:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = mutation_probe.main()
    assert exit_code == 0, buffer.getvalue()
    assert "undetected: 0" in buffer.getvalue()
