from __future__ import annotations

import os
import sys
from contextlib import suppress

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture
def anyio_backend() -> str:  # pragma: no cover - placeholder for runner symmetry
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch, request):
    """Never let a test block on a modal message box.

    The application uses ``messagebox`` for query errors and confirmations; in a
    test run they would wait for a click that never comes.
    """
    tkinter = sys.modules.get("tkinter")
    if tkinter is None:
        return
    try:
        from tkinter import filedialog, messagebox
    except Exception:  # pragma: no cover - tkinter unavailable
        return

    recorded: list[tuple[str, str]] = []

    def record(kind: str):
        def handler(*args, **kwargs):
            recorded.append((kind, str(args[0] if args else "")))
            return "ok"

        return handler

    for name in ("showerror", "showinfo", "showwarning", "askyesno", "askokcancel", "askstring"):
        monkeypatch.setattr(messagebox, name, record(name), raising=False)
    monkeypatch.setattr(filedialog, "askdirectory", lambda **kwargs: "", raising=False)
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kwargs: "", raising=False)
    request.node._modal_messages = recorded  # type: ignore[attr-defined]


@pytest.fixture
def modal_messages(request) -> list[tuple[str, str]]:
    return getattr(request.node, "_modal_messages", [])


@pytest.fixture(scope="session")
def tk_session_root():
    """One Tk root for the whole test session.

    Windows can refuse to create a new root after another one was destroyed, so
    the UI tests share a single hidden root instead of building one per module.
    """
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:  # pragma: no cover - headless
        pytest.skip("Tk display unavailable")
    root.withdraw()
    yield root
    with suppress(tk.TclError):
        root.destroy()


@pytest.fixture
def tk_root(tk_session_root):
    """Per-test view of the shared root: children are cleared either side."""
    for child in tk_session_root.winfo_children():
        child.destroy()
    yield tk_session_root
    tk_session_root.update()
    for child in tk_session_root.winfo_children():
        child.destroy()
