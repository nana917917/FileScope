"""Shared OOXML (ZIP container) helpers.

Office files hide text outside the main document part: headers, footers,
footnotes, comments, text boxes, chart titles, SmartArt. Reading those parts
directly is the only way to find them without a full Office engine.

All reads are streamed from the archive and bounded; DTD/entity declarations
are rejected so a hostile document cannot expand entities here.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable, Iterator
from xml.etree import ElementTree

MAX_PART_BYTES = 32 * 1024 * 1024
MAX_TEXT_PER_PART = 4 * 1024 * 1024
FORBIDDEN = (b"<!DOCTYPE", b"<!ENTITY")


def open_zip(path: str) -> zipfile.ZipFile | None:
    try:
        return zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return None


def iter_parts(
    archive: zipfile.ZipFile,
    wanted: Callable[[str], bool],
    *,
    max_parts: int = 400,
    max_bytes: int = MAX_PART_BYTES,
) -> Iterator[tuple[str, bytes]]:
    """Yield ``(name, data)`` for matching archive members."""
    emitted = 0
    try:
        infos = archive.infolist()
    except (OSError, zipfile.BadZipFile):
        return
    for info in infos:
        if info.is_dir() or not wanted(info.filename):
            continue
        if info.file_size > max_bytes:
            continue
        try:
            data = archive.read(info.filename)
        except (OSError, zipfile.BadZipFile, RuntimeError):
            continue
        if any(marker in data[:4096] for marker in FORBIDDEN):
            continue
        emitted += 1
        yield info.filename, data
        if emitted >= max_parts:
            return


def xml_texts(data: bytes, local_names: set[str]) -> list[str]:
    """Return text of every element whose local tag is in ``local_names``."""
    out: list[str] = []
    total = 0
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return out
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in local_names:
            continue
        text = element.text
        if not text:
            continue
        value = text.strip()
        if not value:
            continue
        out.append(value)
        total += len(value)
        if total > MAX_TEXT_PER_PART:
            break
    return out


def part_basename(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def rels_targets(data: bytes) -> list[str]:
    """Return ``Target`` attributes from a ``.rels`` part."""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return []
    targets: list[str] = []
    for element in root.iter():
        target = element.attrib.get("Target")
        if target:
            targets.append(target)
    return targets
