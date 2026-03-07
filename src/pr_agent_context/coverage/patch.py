from __future__ import annotations

from collections.abc import Mapping
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from coverage import Coverage
from coverage.exceptions import NoSource

from pr_agent_context.coverage.git_diff import normalize_repo_path
from pr_agent_context.domain.models import CoverageFileGap, PatchCoverageSummary


def compute_patch_coverage(
    *,
    workspace: Path,
    changed_lines_by_file: Mapping[str, list[int]],
    coverage: Coverage,
    target_percent: float,
) -> PatchCoverageSummary:
    measured_files = set(coverage.get_data().measured_files())
    measured_map = {_normalize_compare_path(path, workspace): path for path in measured_files}

    file_gaps: list[CoverageFileGap] = []
    total_executable = 0
    total_covered = 0

    for path in sorted(changed_lines_by_file):
        if not path.endswith(".py"):
            continue
        relative_path = normalize_repo_path(path)
        absolute_path = workspace / relative_path
        if not absolute_path.exists():
            continue
        if not _is_in_coverage_scope(coverage, absolute_path, workspace, measured_map):
            continue

        changed_added_lines = sorted(set(changed_lines_by_file[path]))
        if not changed_added_lines:
            continue

        try:
            _filename, statements, _excluded, missing, _missing_formatted = coverage.analysis2(
                str(absolute_path)
            )
        except NoSource:
            continue

        executable_lines = sorted(set(statements).intersection(changed_added_lines))
        if not executable_lines:
            continue

        missing_lines = set(missing)
        uncovered_lines = [line for line in executable_lines if line in missing_lines]
        covered_lines = [line for line in executable_lines if line not in missing_lines]

        total_executable += len(executable_lines)
        total_covered += len(covered_lines)
        file_gaps.append(
            CoverageFileGap(
                path=relative_path,
                changed_added_lines=changed_added_lines,
                changed_executable_lines=executable_lines,
                covered_changed_executable_lines=covered_lines,
                uncovered_changed_executable_lines=uncovered_lines,
                has_measured_data=_normalize_compare_path(str(absolute_path), workspace)
                in measured_map,
            )
        )

    if total_executable == 0:
        return PatchCoverageSummary(
            target_percent=target_percent,
            actual_percent=None,
            total_changed_executable_lines=0,
            covered_changed_executable_lines=0,
            files=[],
            actionable=False,
            is_na=True,
        )

    actual_percent = (total_covered / total_executable) * 100
    files_with_gaps = [
        file_gap for file_gap in file_gaps if file_gap.uncovered_changed_executable_lines
    ]
    return PatchCoverageSummary(
        target_percent=target_percent,
        actual_percent=actual_percent,
        total_changed_executable_lines=total_executable,
        covered_changed_executable_lines=total_covered,
        files=files_with_gaps,
        actionable=actual_percent < target_percent,
        is_na=False,
    )


def _is_in_coverage_scope(
    coverage: Coverage,
    file_path: Path,
    workspace: Path,
    measured_map: Mapping[str, str],
) -> bool:
    relative_path = normalize_repo_path(file_path.relative_to(workspace).as_posix())
    compare_path = _normalize_compare_path(str(file_path), workspace)
    if compare_path in measured_map:
        return True
    if _matches_any_pattern(relative_path, coverage.config.run_omit):
        return False

    source_entries = list(coverage.config.source or [])
    source_packages = list(getattr(coverage.config, "source_pkgs", []) or [])
    if not source_entries and not source_packages:
        return True

    parts = PurePosixPath(relative_path).parts
    for entry in [*source_entries, *source_packages]:
        normalized_entry = normalize_repo_path(str(entry))
        if _matches_source_entry(file_path, relative_path, parts, workspace, normalized_entry):
            return True
    return False


def _matches_any_pattern(relative_path: str, patterns: list[str] | None) -> bool:
    if not patterns:
        return False
    return any(fnmatch(relative_path, pattern) for pattern in patterns)


def _matches_source_entry(
    file_path: Path,
    relative_path: str,
    parts: tuple[str, ...],
    workspace: Path,
    entry: str,
) -> bool:
    if not entry:
        return False
    source_path = Path(entry)
    if source_path.is_absolute():
        try:
            file_path.resolve().relative_to(source_path.resolve())
            return True
        except ValueError:
            return False

    normalized_workspace_entry = normalize_repo_path((workspace / source_path).as_posix())
    if relative_path == entry or relative_path.startswith(f"{entry}/"):
        return True
    if relative_path == normalized_workspace_entry or relative_path.startswith(
        f"{normalized_workspace_entry}/"
    ):
        return True
    if entry in parts:
        index = parts.index(entry)
        return index < len(parts) - 1
    return False


def _normalize_compare_path(path: str, workspace: Path) -> str:
    normalized = normalize_repo_path(path)
    workspace_path = normalize_repo_path(str(workspace.resolve()))
    if normalized == workspace_path:
        return "."
    if normalized.startswith(f"{workspace_path}/"):
        return normalized[len(workspace_path) + 1 :]
    return normalized
