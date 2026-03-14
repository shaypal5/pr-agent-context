from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from coverage import Coverage
from coverage.exceptions import NoSource

from pr_agent_context.coverage.git_diff import normalize_repo_path
from pr_agent_context.domain.models import CoverageFileGap, PatchCoverageSummary

_IGNORED_INFERRED_ROOTS = {"test", "tests", "testing", "spec", "specs"}


@dataclass(frozen=True)
class _CoverageScopeContext:
    measured_map: Mapping[str, str]
    measured_paths: tuple[str, ...]
    inferred_source_roots: tuple[str, ...]
    source_entries: tuple[str, ...]
    source_packages: tuple[str, ...]
    has_coverage_artifacts: bool
    scope_strategy: str
    warnings: tuple[str, ...]


def compute_patch_coverage(
    *,
    workspace: Path,
    changed_lines_by_file: Mapping[str, list[int]],
    coverage: Coverage,
    target_percent: float,
    has_coverage_artifacts: bool = False,
) -> PatchCoverageSummary:
    scope_context = _build_scope_context(
        coverage=coverage,
        workspace=workspace,
        has_coverage_artifacts=has_coverage_artifacts,
    )

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
        if not _is_in_coverage_scope(
            coverage,
            absolute_path,
            workspace,
            scope_context.measured_map,
            inferred_source_roots=scope_context.inferred_source_roots,
            has_coverage_artifacts=scope_context.has_coverage_artifacts,
        ):
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
                in scope_context.measured_map,
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


def describe_patch_coverage_scope(
    *,
    workspace: Path,
    coverage: Coverage,
    has_coverage_artifacts: bool,
) -> dict[str, object]:
    scope_context = _build_scope_context(
        coverage=coverage,
        workspace=workspace,
        has_coverage_artifacts=has_coverage_artifacts,
    )
    return {
        "has_coverage_artifacts": scope_context.has_coverage_artifacts,
        "scope_strategy": scope_context.scope_strategy,
        "measured_file_count": len(scope_context.measured_paths),
        "measured_file_sample": list(scope_context.measured_paths[:10]),
        "inferred_source_roots": list(scope_context.inferred_source_roots),
        "explicit_source": list(scope_context.source_entries),
        "explicit_source_pkgs": list(scope_context.source_packages),
        "warnings": list(scope_context.warnings),
    }


def _is_in_coverage_scope(
    coverage: Coverage,
    file_path: Path,
    workspace: Path,
    measured_map: Mapping[str, str],
    *,
    inferred_source_roots: tuple[str, ...] | None = None,
    has_coverage_artifacts: bool = False,
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
        if inferred_source_roots is None:
            inferred_source_roots = _infer_measured_source_roots(measured_map)
        if inferred_source_roots:
            return _matches_inferred_measured_roots(relative_path, inferred_source_roots)
        if not measured_map and not has_coverage_artifacts:
            return True
        return False

    parts = PurePosixPath(relative_path).parts
    for entry in [*source_entries, *source_packages]:
        normalized_entry = normalize_repo_path(str(entry))
        if _matches_source_entry(file_path, relative_path, parts, workspace, normalized_entry):
            return True
    return False


def _build_scope_context(
    *,
    coverage: Coverage,
    workspace: Path,
    has_coverage_artifacts: bool,
) -> _CoverageScopeContext:
    measured_files = set(coverage.get_data().measured_files())
    measured_map = {_normalize_compare_path(path, workspace): path for path in measured_files}
    measured_paths = tuple(sorted(path for path in measured_map if path not in {"", "."}))
    inferred_source_roots = _infer_measured_source_roots(measured_map)
    source_entries = tuple(
        normalize_repo_path(str(entry)) for entry in (coverage.config.source or [])
    )
    source_packages = tuple(
        normalize_repo_path(str(entry))
        for entry in (getattr(coverage.config, "source_pkgs", []) or [])
    )
    warnings: list[str] = []

    if source_entries or source_packages:
        scope_strategy = "explicit_config"
    elif inferred_source_roots:
        scope_strategy = "measured_root_inference"
    elif measured_paths:
        scope_strategy = "measured_files_without_inferred_roots"
        warnings.append(
            "Measured coverage files were loaded, but no non-test source roots could be inferred."
        )
    elif has_coverage_artifacts:
        scope_strategy = "artifacts_without_measured_files"
        warnings.append(
            "Coverage artifacts were found, but the combined coverage data "
            "contained no measured files. "
            "Patch coverage was treated as N/A instead of 0%."
        )
    else:
        scope_strategy = "no_artifacts_fallback"

    return _CoverageScopeContext(
        measured_map=measured_map,
        measured_paths=measured_paths,
        inferred_source_roots=inferred_source_roots,
        source_entries=source_entries,
        source_packages=source_packages,
        has_coverage_artifacts=has_coverage_artifacts,
        scope_strategy=scope_strategy,
        warnings=tuple(warnings),
    )


def _infer_measured_source_roots(measured_map: Mapping[str, str]) -> tuple[str, ...]:
    roots = {
        root
        for measured_path in measured_map
        if (root := _infer_source_root(PurePosixPath(measured_path).parts)) is not None
    }
    return tuple(sorted(roots))


def _infer_source_root(parts: tuple[str, ...]) -> str | None:
    if not parts:
        return None
    first = parts[0]
    if first in {"", "."}:
        return None
    if first in _IGNORED_INFERRED_ROOTS:
        return None
    if first == "src":
        if len(parts) >= 2 and parts[1] in _IGNORED_INFERRED_ROOTS:
            return None
        if len(parts) >= 3 and _looks_like_package_root(parts[1]):
            return normalize_repo_path("/".join(parts[:2]))
        return "src"
    if _looks_like_package_root(first):
        return first
    return first


def _matches_inferred_measured_roots(
    relative_path: str,
    inferred_source_roots: tuple[str, ...],
) -> bool:
    path_parts = PurePosixPath(relative_path).parts
    if not path_parts:
        return False

    if not inferred_source_roots:
        return True

    return any(
        relative_path == root or relative_path.startswith(f"{root}/")
        for root in inferred_source_roots
    )


def _looks_like_package_root(part: str) -> bool:
    return bool(part) and not part.endswith(".py") and part not in {"", "."}


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
