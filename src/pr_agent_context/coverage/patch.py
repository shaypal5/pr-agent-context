from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from coverage import Coverage
from coverage.exceptions import NoSource

from pr_agent_context.coverage.combine import (
    _find_coverage_config_file,
    coverage_working_directory,
)
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
    coverage_source_pending: bool
    scope_strategy: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _XmlCoveredFile:
    executable_lines: frozenset[int]
    covered_lines: frozenset[int]


def compute_patch_coverage(
    *,
    workspace: Path,
    changed_lines_by_file: Mapping[str, list[int]],
    coverage: Coverage,
    target_percent: float,
    has_coverage_artifacts: bool = False,
    coverage_source_pending: bool = False,
) -> PatchCoverageSummary:
    scope_context = _build_scope_context(
        coverage=coverage,
        workspace=workspace,
        has_coverage_artifacts=has_coverage_artifacts,
        coverage_source_pending=coverage_source_pending,
    )

    if scope_context.coverage_source_pending:
        return PatchCoverageSummary(
            target_percent=target_percent,
            actual_percent=None,
            total_changed_executable_lines=0,
            covered_changed_executable_lines=0,
            files=[],
            actionable=False,
            is_na=True,
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
            with coverage_working_directory(workspace):
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


def compute_patch_coverage_from_xml_reports(
    *,
    workspace: Path,
    changed_lines_by_file: Mapping[str, list[int]],
    report_files: list[Path],
    target_percent: float,
) -> tuple[PatchCoverageSummary, dict[str, object]]:
    report_data, debug = _parse_xml_coverage_reports(
        workspace=workspace,
        report_files=report_files,
    )
    coverage = Coverage(
        config_file=(
            str(config_file)
            if (config_file := _find_coverage_config_file(workspace)) is not None
            else False
        )
    )
    scope_context = _build_scope_context_from_measured_map(
        coverage=coverage,
        workspace=workspace,
        measured_map={path: path for path in report_data},
        has_coverage_artifacts=bool(report_files),
        coverage_source_pending=False,
    )
    debug.update(
        {
            "measured_file_count": len(scope_context.measured_paths),
            "measured_file_sample": list(scope_context.measured_paths[:10]),
            "inferred_source_roots": list(scope_context.inferred_source_roots),
            "explicit_source": list(scope_context.source_entries),
            "explicit_source_pkgs": list(scope_context.source_packages),
            "scope_strategy": scope_context.scope_strategy,
            "scope_warnings": list(scope_context.warnings),
        }
    )
    if not report_data:
        return (
            PatchCoverageSummary(
                target_percent=target_percent,
                actual_percent=None,
                total_changed_executable_lines=0,
                covered_changed_executable_lines=0,
                files=[],
                actionable=False,
                is_na=True,
            ),
            debug,
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

        reported = report_data.get(relative_path)
        if reported is not None:
            executable_lines = sorted(reported.executable_lines.intersection(changed_added_lines))
            if not executable_lines:
                continue
            covered_lines = sorted(reported.covered_lines.intersection(executable_lines))
            uncovered_lines = [
                line for line in executable_lines if line not in reported.covered_lines
            ]
            has_measured_data = True
        else:
            executable_lines = _discover_executable_lines_without_data(
                coverage=coverage,
                absolute_path=absolute_path,
                changed_added_lines=changed_added_lines,
                workspace=workspace,
            )
            if not executable_lines:
                continue
            covered_lines = []
            uncovered_lines = executable_lines
            has_measured_data = False

        total_executable += len(executable_lines)
        total_covered += len(covered_lines)
        file_gaps.append(
            CoverageFileGap(
                path=relative_path,
                changed_added_lines=changed_added_lines,
                changed_executable_lines=executable_lines,
                covered_changed_executable_lines=covered_lines,
                uncovered_changed_executable_lines=uncovered_lines,
                has_measured_data=has_measured_data,
            )
        )

    if total_executable == 0:
        return (
            PatchCoverageSummary(
                target_percent=target_percent,
                actual_percent=None,
                total_changed_executable_lines=0,
                covered_changed_executable_lines=0,
                files=[],
                actionable=False,
                is_na=True,
            ),
            debug,
        )

    actual_percent = (total_covered / total_executable) * 100
    files_with_gaps = [
        file_gap for file_gap in file_gaps if file_gap.uncovered_changed_executable_lines
    ]
    return (
        PatchCoverageSummary(
            target_percent=target_percent,
            actual_percent=actual_percent,
            total_changed_executable_lines=total_executable,
            covered_changed_executable_lines=total_covered,
            files=files_with_gaps,
            actionable=actual_percent < target_percent,
            is_na=False,
        ),
        debug,
    )


def describe_patch_coverage_scope(
    *,
    workspace: Path,
    coverage: Coverage,
    has_coverage_artifacts: bool,
    coverage_source_pending: bool = False,
) -> dict[str, object]:
    scope_context = _build_scope_context(
        coverage=coverage,
        workspace=workspace,
        has_coverage_artifacts=has_coverage_artifacts,
        coverage_source_pending=coverage_source_pending,
    )
    return {
        "has_coverage_artifacts": scope_context.has_coverage_artifacts,
        "coverage_source_pending": scope_context.coverage_source_pending,
        "scope_strategy": scope_context.scope_strategy,
        "measured_file_count": len(scope_context.measured_paths),
        "measured_file_sample": list(scope_context.measured_paths[:10]),
        "inferred_source_roots": list(scope_context.inferred_source_roots),
        "explicit_source": list(scope_context.source_entries),
        "explicit_source_pkgs": list(scope_context.source_packages),
        "warnings": list(scope_context.warnings),
    }


def describe_patch_coverage_scope_from_xml_reports(
    *,
    workspace: Path,
    report_files: list[Path],
) -> dict[str, object]:
    _summary, debug = compute_patch_coverage_from_xml_reports(
        workspace=workspace,
        changed_lines_by_file={},
        report_files=report_files,
        target_percent=100.0,
    )
    return debug


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


def _discover_executable_lines_without_data(
    *,
    coverage: Coverage,
    absolute_path: Path,
    changed_added_lines: list[int],
    workspace: Path,
) -> list[int]:
    try:
        with coverage_working_directory(workspace):
            _filename, statements, _excluded, _missing, _missing_formatted = coverage.analysis2(
                str(absolute_path)
            )
    except NoSource:
        return []
    return sorted(set(statements).intersection(changed_added_lines))


def _build_scope_context(
    *,
    coverage: Coverage,
    workspace: Path,
    has_coverage_artifacts: bool,
    coverage_source_pending: bool,
) -> _CoverageScopeContext:
    measured_files = set(coverage.get_data().measured_files())
    measured_map = {_normalize_compare_path(path, workspace): path for path in measured_files}
    return _build_scope_context_from_measured_map(
        coverage=coverage,
        workspace=workspace,
        measured_map=measured_map,
        has_coverage_artifacts=has_coverage_artifacts,
        coverage_source_pending=coverage_source_pending,
    )


def _build_scope_context_from_measured_map(
    *,
    coverage: Coverage,
    workspace: Path,
    measured_map: Mapping[str, str],
    has_coverage_artifacts: bool,
    coverage_source_pending: bool,
) -> _CoverageScopeContext:
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

    if coverage_source_pending and not measured_paths:
        scope_strategy = "coverage_source_pending"
        warnings.append(
            "Coverage for this head SHA is not available yet. "
            "Patch coverage was treated as N/A instead of 0%."
        )
    elif source_entries or source_packages:
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
        coverage_source_pending=coverage_source_pending,
        scope_strategy=scope_strategy,
        warnings=tuple(warnings),
    )


def _parse_xml_coverage_reports(
    *,
    workspace: Path,
    report_files: list[Path],
) -> tuple[dict[str, _XmlCoveredFile], dict[str, object]]:
    debug: dict[str, object] = {
        "parser_format": "coverage.py_xml",
        "report_files": [str(path) for path in report_files],
        "selected_report": None,
        "normalized_report_file_sample": [],
        "warnings": [],
    }
    warnings: list[str] = debug["warnings"]
    if not report_files:
        debug["resolution"] = "no_report_files"
        return {}, debug
    if len(report_files) > 1:
        warnings.append("Multiple coverage reports were found; using the first matching file.")

    report_file = sorted(report_files)[0]
    debug["selected_report"] = str(report_file)
    try:
        root = ET.parse(report_file).getroot()
    except (ET.ParseError, OSError) as error:
        warnings.append(f"Unable to parse coverage XML report: {error}")
        debug["resolution"] = "report_parse_error"
        return {}, debug

    source_entries = [
        text.strip()
        for source in root.findall(".//sources/source")
        if (text := source.text or "").strip()
    ]
    line_map: dict[str, tuple[set[int], set[int]]] = {}
    for class_node in root.findall(".//class"):
        filename = str(class_node.get("filename") or "").strip()
        if not filename:
            continue
        normalized_path = _normalize_report_file_path(
            filename=filename,
            source_entries=source_entries,
            workspace=workspace,
        )
        if normalized_path is None:
            warnings.append(f"Unable to map coverage report path to workspace: {filename}")
            continue

        executable_lines, covered_lines = line_map.setdefault(normalized_path, (set(), set()))
        for line_node in class_node.findall("./lines/line"):
            line_number = int(line_node.get("number") or 0)
            if line_number <= 0:
                continue
            executable_lines.add(line_number)
            if _xml_line_is_fully_covered(line_node):
                covered_lines.add(line_number)

    parsed = {
        path: _XmlCoveredFile(
            executable_lines=frozenset(executable_lines),
            covered_lines=frozenset(covered_lines),
        )
        for path, (executable_lines, covered_lines) in line_map.items()
        if executable_lines
    }
    debug["normalized_report_file_sample"] = list(sorted(parsed)[:10])
    debug["source_entries"] = source_entries
    debug["resolution"] = "report_loaded" if parsed else "report_without_measured_files"
    return parsed, debug


def _xml_line_is_fully_covered(line_node: ET.Element) -> bool:
    if int(line_node.get("hits") or 0) <= 0:
        return False

    if str(line_node.get("branch") or "").lower() != "true":
        return True

    condition_coverage = str(line_node.get("condition-coverage") or "").strip()
    if not condition_coverage:
        return True

    percent_text = condition_coverage.split("%", 1)[0].strip()
    try:
        return float(percent_text) >= 100.0
    except ValueError:
        return True


def _normalize_report_file_path(
    *,
    filename: str,
    source_entries: list[str],
    workspace: Path,
) -> str | None:
    file_path = Path(filename)
    candidates: list[Path] = []
    if file_path.is_absolute():
        candidates.append(file_path)
    else:
        candidates.append(workspace / file_path)
        for source_entry in source_entries:
            source_path = Path(source_entry)
            if source_path.is_absolute():
                candidates.append(source_path / file_path)
                candidates.extend(
                    _workspace_source_suffix_candidates(source_path, file_path, workspace)
                )
            else:
                candidates.append(workspace / source_path / file_path)

    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            relative_candidate = candidate.resolve().relative_to(workspace.resolve()).as_posix()
            return normalize_repo_path(relative_candidate)
        except ValueError:
            continue
        except OSError:
            continue

    if not file_path.is_absolute():
        return normalize_repo_path(file_path.as_posix())
    return None


def _workspace_source_suffix_candidates(
    source_path: Path,
    file_path: Path,
    workspace: Path,
) -> list[Path]:
    suffix_candidates: list[Path] = []
    source_parts = source_path.parts
    # Try mapping absolute XML <source> entries back into the checked-out repo by
    # preserving the deepest existing repo-relative suffix, such as "foldermix" or "src/pkg".
    for start_index in range(len(source_parts)):
        suffix = Path(*source_parts[start_index:])
        candidate = workspace / suffix / file_path
        if candidate not in suffix_candidates:
            suffix_candidates.append(candidate)
    return suffix_candidates


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
