"""Copy upstream notice files from installed runtime wheels into a portable build."""

import re
import shutil
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from packaging.requirements import Requirement


def main():
    destination = Path(sys.argv[1])
    requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()
    pending = [Requirement(line) for line in requirements if line.strip() and not line.startswith("#")]
    pending.append(Requirement("pyinstaller"))
    seen = set()
    versions = []
    while pending:
        req = pending.pop()
        name = re.sub(r"[-_.]+", "-", req.name).lower()
        if name in seen or (req.marker and not req.marker.evaluate({"extra": ""})):
            continue
        try:
            package = distribution(req.name)
        except PackageNotFoundError:
            raise SystemExit(f"Missing packaged dependency: {req.name}") from None
        seen.add(name)
        versions.append(f"{name} {package.version}")
        for item in package.files or ():
            filename = item.name.casefold()
            if filename.startswith(("license", "copying", "notice", "copyright")) or "licenses" in item.parts:
                source = package.locate_file(item)
                if source.is_file():
                    relative = Path(*item.parts[1:]) if len(item.parts) > 1 else Path(item.name)
                    if ".." not in relative.parts:
                        target = destination / name / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
        pending.extend(Requirement(item) for item in package.requires or ())
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "VERSIONS.txt").write_text("\n".join(sorted(versions)) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
