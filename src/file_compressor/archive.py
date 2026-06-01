from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def create_zip(source_dir: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file() and path.resolve() != output.resolve():
                archive.write(path, path.relative_to(source_dir))
    return output
