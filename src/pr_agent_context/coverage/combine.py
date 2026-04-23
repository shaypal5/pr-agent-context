from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

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
            _add_workspace_relative_filename_aliases(coverage, workspace)
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


def _add_workspace_relative_filename_aliases(coverage: Coverage, workspace: Path) -> None:
    workspace_root = workspace.resolve()
    tracers_to_add: dict[str, str] = {}
    data = coverage.get_data()
    uses_arcs = data.has_arcs()
    lines_to_add: dict[str, list[int]] = {}
    arcs_to_add: dict[str, list[tuple[int, int]]] = {}

    for measured_path in data.measured_files():
        aliased_path = _rebase_measured_path_to_workspace(measured_path, workspace_root)
        if aliased_path is None or aliased_path == measured_path:
            continue
        if aliased_path in data.measured_files():
            continue

        if uses_arcs:
            if arcs := data.arcs(measured_path):
                arcs_to_add[aliased_path] = list(arcs)
        else:
            if lines := data.lines(measured_path):
                lines_to_add[aliased_path] = list(lines)
        if tracer := data.file_tracer(measured_path):
            tracers_to_add[aliased_path] = tracer

    if arcs_to_add:
        data.add_arcs(arcs_to_add)
    if lines_to_add:
        data.add_lines(lines_to_add)
    if tracers_to_add:
        data.add_file_tracers(tracers_to_add)


def _rebase_measured_path_to_workspace(path: str, workspace_root: Path) -> str | None:
    measured_path = Path(path)
    try:
        resolved_measured_path = measured_path.resolve()
    except OSError:
        return None

    try:
        relative_path = resolved_measured_path.relative_to(workspace_root).as_posix()
    except ValueError:
        pass
    else:
        return str((workspace_root / relative_path).resolve())

    suffix = _find_workspace_relative_suffix(measured_path, workspace_root)
    if suffix is None:
        return None
    return str((workspace_root / suffix).resolve())


def _find_workspace_relative_suffix(measured_path: Path, workspace_root: Path) -> str | None:
    path_parts = measured_path.parts
    for start_index in range(1, len(path_parts)):
        suffix = PurePosixPath(*path_parts[start_index:]).as_posix()
        candidate = workspace_root / suffix
        if candidate.exists():
            return suffix
    return None
