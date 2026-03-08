from __future__ import annotations

import tempfile
from pathlib import Path

from coverage import Coverage, CoverageData
from coverage.exceptions import DataError

KNOWN_COVERAGE_CONFIG_FILES = (
    ".coveragerc",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
)


def build_combined_coverage(*, workspace: Path, coverage_files: list[Path]) -> Coverage:
    config_file = _find_coverage_config_file(workspace)
    combined_output_dir = Path(tempfile.mkdtemp(prefix="pr-agent-context-coverage-"))
    coverage = Coverage(
        config_file=str(config_file) if config_file else False,
        data_file=str(combined_output_dir / ".coverage"),
    )
    valid_coverage_files = _filter_valid_coverage_files(coverage_files)
    if valid_coverage_files:
        coverage.combine(data_paths=[str(path) for path in valid_coverage_files], strict=False)
        coverage.save()
        coverage.load()
    return coverage


def _find_coverage_config_file(workspace: Path) -> Path | None:
    for candidate_name in KNOWN_COVERAGE_CONFIG_FILES:
        candidate = workspace / candidate_name
        if candidate.exists():
            return candidate
    return None


def _filter_valid_coverage_files(coverage_files: list[Path]) -> list[Path]:
    valid_files: list[Path] = []
    for path in coverage_files:
        if _is_valid_coverage_file(path):
            valid_files.append(path)
    return valid_files


def _is_valid_coverage_file(path: Path) -> bool:
    try:
        coverage_data = CoverageData(basename=str(path))
        coverage_data.read()
        coverage_data.measured_files()
    except (DataError, OSError):
        return False
    return True
