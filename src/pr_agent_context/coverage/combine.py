from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from coverage import Coverage, CoverageData
from coverage.exceptions import DataError

KNOWN_COVERAGE_CONFIG_FILES = (
    ".coveragerc",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
)


@contextmanager
def coverage_working_directory(workspace: Path):
    previous_cwd = Path.cwd()
    target_cwd = workspace.resolve()
    os.chdir(target_cwd)
    try:
        yield target_cwd
    finally:
        os.chdir(previous_cwd)


def build_combined_coverage(*, workspace: Path, coverage_files: list[Path]) -> Coverage:
    config_file = _find_coverage_config_file(workspace)
    combined_output_dir = Path(tempfile.mkdtemp(prefix="pr-agent-context-coverage-"))
    coverage = Coverage(
        config_file=str(config_file) if config_file else False,
        data_file=str(combined_output_dir / ".coverage"),
    )
    with coverage_working_directory(workspace):
        normalized_coverage_files = _normalize_coverage_files(
            coverage_files=coverage_files,
            config_file=config_file,
        )
        if normalized_coverage_files:
            coverage.combine(
                data_paths=[str(path) for path in normalized_coverage_files],
                strict=False,
            )
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


def _normalize_coverage_files(
    *,
    coverage_files: list[Path],
    config_file: Path | None,
) -> list[Path]:
    normalized_output_dir = Path(tempfile.mkdtemp(prefix="pr-agent-context-coverage-inputs-"))
    normalized_files: list[Path] = []
    for index, path in enumerate(_filter_valid_coverage_files(coverage_files), start=1):
        normalized_path = normalized_output_dir / f".coverage.normalized.{index}"
        coverage = Coverage(
            config_file=str(config_file) if config_file else False,
            data_file=str(normalized_path),
        )
        try:
            coverage.combine(data_paths=[str(path)], strict=False)
            coverage.save()
            normalized_files.append(normalized_path)
        except DataError:
            continue
    return normalized_files


def _is_valid_coverage_file(path: Path) -> bool:
    try:
        coverage_data = CoverageData(basename=str(path))
        coverage_data.read()
        coverage_data.measured_files()
    except (DataError, OSError):
        return False
    return True
