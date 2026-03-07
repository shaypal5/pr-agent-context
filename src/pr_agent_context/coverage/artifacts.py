from __future__ import annotations

from pathlib import Path


def discover_coverage_files(root: Path | None) -> list[Path]:
    if root is None or not root.exists():
        return []
    coverage_files = [
        path
        for path in root.rglob(".coverage*")
        if path.is_file() and not _is_transient_coverage_file(path)
    ]
    return sorted(coverage_files)


def _is_transient_coverage_file(path: Path) -> bool:
    return path.name.endswith(("-shm", "-wal", ".lock"))
