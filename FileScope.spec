# PyInstaller spec: onedir build (preferred over onefile -- see THIRD_PARTY_NOTICES.md).
# Build with:  pyinstaller FileScope.spec --noconfirm --clean

from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH)

datas = [
    (str(project_root / "README.md"), "."),
    (str(project_root / "ARCHITECTURE.md"), "."),
    (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
]

hiddenimports = [
    "openpyxl",
    "xlrd",
    "pyxlsb",
    "pypdf",
    "docx",
    "pptx",
    "PIL",
]

analysis = Analysis(
    [str(project_root / "FileScope.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "ruff", "xlwt"],
    cipher=block_cipher,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="FileScope",
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    name="FileScope",
)
