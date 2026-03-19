from __future__ import annotations

import io
import os
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from coverage import Coverage
from coverage.exceptions import DataError, NoSource

from pr_agent_context.coverage import combine as combine_module
from pr_agent_context.coverage.artifacts import (
    discover_coverage_files,
    discover_coverage_report_files,
    resolve_coverage_files,
)
from pr_agent_context.coverage.combine import build_combined_coverage
from pr_agent_context.coverage.patch import (
    _discover_executable_lines_without_data,
    _infer_measured_source_roots,
    _infer_source_root,
    _is_in_coverage_scope,
    _matches_any_pattern,
    _matches_inferred_measured_roots,
    _matches_source_entry,
    _normalize_compare_path,
    _normalize_report_file_path,
    _parse_xml_coverage_reports,
    _xml_line_is_fully_covered,
    compute_patch_coverage,
    compute_patch_coverage_from_xml_reports,
    describe_patch_coverage_scope,
    describe_patch_coverage_scope_from_xml_reports,
)


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_coverage_data(data_file: Path, scripts: list[tuple[Path, str]]) -> None:
    coverage = Coverage(config_file=False, data_file=str(data_file))
    coverage.start()
    for script_path, invocation in scripts:
        globals_dict = {"__name__": "__main__"}
        exec(
            compile(script_path.read_text(encoding="utf-8"), str(script_path), "exec"),
            globals_dict,
        )
        exec(invocation, globals_dict)
    coverage.stop()
    coverage.save()


def _build_coverage_data_with_workspace_config(
    *,
    workspace: Path,
    data_file: Path,
    scripts: list[tuple[Path, str]],
) -> None:
    previous_cwd = Path.cwd()
    os.chdir(workspace)
    try:
        coverage = Coverage(config_file=True, data_file=str(data_file))
        coverage.start()
        for script_path, invocation in scripts:
            globals_dict = {"__name__": "__main__"}
            exec(
                compile(script_path.read_text(encoding="utf-8"), str(script_path), "exec"),
                globals_dict,
            )
            exec(invocation, globals_dict)
        coverage.stop()
        coverage.save()
    finally:
        os.chdir(previous_cwd)


def _coverage_zip_bytes(source_file: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(".coverage.py312", source_file.read_bytes())
    return buffer.getvalue()


def _coverage_report_zip_bytes(content: str, *, report_filename: str = "coverage.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(report_filename, content)
    return buffer.getvalue()


def _coverage_xml_text(
    *,
    filename: str,
    covered_lines: list[int],
    uncovered_lines: list[int],
    partial_lines: list[int] | None = None,
    source_entries: list[str] | None = None,
) -> str:
    partial_lines = partial_lines or []
    line_nodes_list: list[str] = []
    for line in covered_lines:
        line_nodes_list.append(f'          <line number="{line}" hits="1"/>')
    for line in uncovered_lines:
        line_nodes_list.append(f'          <line number="{line}" hits="0"/>')
    for line in partial_lines:
        line_nodes_list.append(
            f'          <line number="{line}" hits="1" branch="true" '
            'condition-coverage="50% (1/2)" missing-branches="next"/>'
        )
    line_nodes = "\n".join(line_nodes_list)
    if source_entries is None:
        source_entries = ["src"]
    source_nodes = "\n".join(
        f"    <source>{source_entry}</source>" for source_entry in source_entries
    )
    return (
        '<?xml version="1.0" ?>\n'
        '<coverage version="7.6">\n'
        "  <sources>\n"
        f"{source_nodes}\n"
        "  </sources>\n"
        "  <packages>\n"
        '    <package name="pkg">\n'
        "      <classes>\n"
        f'        <class name="module" filename="{filename}">\n'
        "          <lines>\n"
        f"{line_nodes}\n"
        "          </lines>\n"
        "        </class>\n"
        "      </classes>\n"
        "    </package>\n"
        "  </packages>\n"
        "</coverage>\n"
    )


class CoverageSourceClient:
    def __init__(
        self,
        *,
        workflow_runs: dict[str, object],
        artifacts_by_run: dict[int, dict[str, object]],
        zip_bytes_by_artifact: dict[int, bytes],
    ) -> None:
        self.workflow_runs = workflow_runs
        self.artifacts_by_run = artifacts_by_run
        self.zip_bytes_by_artifact = zip_bytes_by_artifact

    def request_json(self, method: str, path: str, params=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            return self.workflow_runs
        if "/actions/runs/" in path and path.endswith("/artifacts"):
            run_id = int(path.split("/actions/runs/")[1].split("/")[0])
            return self.artifacts_by_run.get(run_id, {"artifacts": []})
        raise AssertionError(f"Unexpected JSON request: {path}")

    def request_bytes(self, method: str, path: str) -> bytes:
        assert method == "GET"
        if "/actions/artifacts/" in path and path.endswith("/zip"):
            artifact_id = int(path.split("/actions/artifacts/")[1].split("/")[0])
            return self.zip_bytes_by_artifact[artifact_id]
        raise AssertionError(f"Unexpected bytes request: {path}")

    def request_bytes_following_redirect_without_auth(self, method: str, path: str) -> bytes:
        return self.request_bytes(method, path)


def test_build_combined_coverage_merges_multiple_data_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    module_path = repo / "src" / "pkg" / "module.py"
    _write_file(
        module_path,
        "def alpha(flag):\n"
        "    if flag:\n"
        "        return 1\n"
        "    return 2\n\n"
        "def beta(flag):\n"
        "    if flag:\n"
        "        return 3\n"
        "    return 4\n",
    )

    artifacts = tmp_path / "artifacts"
    first = artifacts / "linux" / ".coverage.py311"
    second = artifacts / "macos" / ".coverage.py312"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    _build_coverage_data(first, [(module_path, "alpha(True)")])
    _build_coverage_data(second, [(module_path, "beta(True)")])

    combined = build_combined_coverage(workspace=repo, coverage_files=[first, second])
    _filename, statements, _excluded, missing, _formatted = combined.analysis2(str(module_path))

    assert statements == [1, 2, 3, 4, 6, 7, 8, 9]
    assert missing == [4, 9]


def test_build_combined_coverage_skips_malformed_data_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    module_path = repo / "src" / "pkg" / "module.py"
    _write_file(
        module_path,
        "def alpha(flag):\n    if flag:\n        return 1\n    return 2\n",
    )

    valid_coverage = tmp_path / ".coverage.valid"
    malformed_coverage = tmp_path / ".coverage.malformed"
    _build_coverage_data(valid_coverage, [(module_path, "alpha(True)")])
    malformed_coverage.write_bytes(b"this is not a sqlite coverage database")

    combined = build_combined_coverage(
        workspace=repo,
        coverage_files=[valid_coverage, malformed_coverage],
    )
    _filename, statements, _excluded, missing, _formatted = combined.analysis2(str(module_path))

    assert statements == [1, 2, 3, 4]
    assert missing == [4]


def test_build_combined_coverage_skips_files_that_fail_during_combine(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    module_path = repo / "src" / "pkg" / "module.py"
    _write_file(
        module_path,
        "def alpha(flag):\n"
        "    if flag:\n"
        "        return 1\n"
        "    return 2\n\n"
        "def beta(flag):\n"
        "    if flag:\n"
        "        return 3\n"
        "    return 4\n",
    )

    first = tmp_path / ".coverage.first"
    second = tmp_path / ".coverage.second"
    _build_coverage_data(first, [(module_path, "alpha(True)")])
    _build_coverage_data(second, [(module_path, "beta(True)")])

    real_combine = combine_module.Coverage.combine

    def flaky_combine(self, data_paths=None, strict=False, keep=False):
        if data_paths == [str(second)]:
            raise DataError("database disk image is malformed")
        return real_combine(self, data_paths=data_paths, strict=strict, keep=keep)

    monkeypatch.setattr(combine_module.Coverage, "combine", flaky_combine)

    combined = build_combined_coverage(workspace=repo, coverage_files=[first, second])
    _filename, statements, _excluded, missing, _formatted = combined.analysis2(str(module_path))

    assert statements == [1, 2, 3, 4, 6, 7, 8, 9]
    assert missing == [4, 7, 8, 9]


def test_compute_patch_coverage_reports_explicit_uncovered_lines(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    module_path = repo / "src" / "pkg" / "module.py"
    _write_file(
        module_path,
        "def compute(flag):\n"
        "    if flag:\n"
        "        value = 1\n"
        "    else:\n"
        "        value = 2\n"
        "    return value\n",
    )
    coverage_file = tmp_path / ".coverage"
    _build_coverage_data(coverage_file, [(module_path, "compute(True)")])
    combined = build_combined_coverage(workspace=repo, coverage_files=[coverage_file])

    summary = compute_patch_coverage(
        workspace=repo,
        changed_lines_by_file={"src/pkg/module.py": [2, 3, 4, 5, 6]},
        coverage=combined,
        target_percent=100,
    )

    assert summary.actual_percent == 75
    assert summary.total_changed_executable_lines == 4
    assert summary.covered_changed_executable_lines == 3
    assert summary.actionable is True
    assert summary.files[0].path == "src/pkg/module.py"
    assert summary.files[0].uncovered_changed_executable_lines == [5]


def test_compute_patch_coverage_treats_unmeasured_changed_files_as_uncovered(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    measured = repo / "src" / "pkg" / "measured.py"
    missing = repo / "src" / "pkg" / "missing.py"
    _write_file(measured, "def ok():\n    return 1\n")
    _write_file(missing, "def later(flag):\n    if flag:\n        return 1\n    return 2\n")
    coverage_file = tmp_path / ".coverage"
    _build_coverage_data(coverage_file, [(measured, "ok()")])
    combined = build_combined_coverage(workspace=repo, coverage_files=[coverage_file])

    summary = compute_patch_coverage(
        workspace=repo,
        changed_lines_by_file={"src/pkg/missing.py": [1, 2, 3, 4]},
        coverage=combined,
        target_percent=100,
        has_coverage_artifacts=True,
    )

    assert summary.actual_percent == 0
    assert summary.total_changed_executable_lines == 4
    assert summary.files[0].has_measured_data is False
    assert summary.files[0].uncovered_changed_executable_lines == [1, 2, 3, 4]


def test_compute_patch_coverage_is_na_when_only_non_executable_lines_changed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    notes = repo / "src" / "pkg" / "notes.py"
    _write_file(notes, "# comment one\n# comment two\n")
    combined = build_combined_coverage(workspace=repo, coverage_files=[])

    summary = compute_patch_coverage(
        workspace=repo,
        changed_lines_by_file={"src/pkg/notes.py": [1, 2]},
        coverage=combined,
        target_percent=100,
    )

    assert summary.is_na is True
    assert summary.actual_percent is None
    assert summary.files == []


def test_compute_patch_coverage_from_xml_reports_excludes_changed_tests(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "src" / "denbust" / "sources" / "mako.py"
    test_path = repo / "tests" / "integration" / "test_scrapers.py"
    _write_file(
        source_path,
        "def parse(flag):\n"
        "    if flag:\n"
        "        value = 1\n"
        "    else:\n"
        "        value = 2\n"
        "    return value\n",
    )
    _write_file(test_path, "def test_scraper():\n    assert True\n")
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(
        _coverage_xml_text(
            filename="denbust/sources/mako.py",
            covered_lines=[1, 2, 3],
            uncovered_lines=[5],
        ),
        encoding="utf-8",
    )

    summary, debug = compute_patch_coverage_from_xml_reports(
        workspace=repo,
        changed_lines_by_file={
            "src/denbust/sources/mako.py": [1, 2, 3, 4, 5, 6],
            "tests/integration/test_scrapers.py": [1, 2],
        },
        report_files=[report_file],
        target_percent=100,
    )

    assert summary.actual_percent == 75
    assert summary.total_changed_executable_lines == 4
    assert summary.files[0].path == "src/denbust/sources/mako.py"
    assert summary.files[0].uncovered_changed_executable_lines == [5]
    assert all(file_gap.path != "tests/integration/test_scrapers.py" for file_gap in summary.files)
    assert debug["scope_strategy"] == "measured_root_inference"
    assert debug["inferred_source_roots"] == ["src/denbust"]


def test_compute_patch_coverage_from_xml_reports_preserves_package_root_from_absolute_source(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "foldermix" / "converters" / "image_ocr.py"
    _write_file(
        source_path,
        "def convert(flag):\n    if flag:\n        return 1\n    return 2\n",
    )
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(
        _coverage_xml_text(
            filename="converters/image_ocr.py",
            covered_lines=[1, 2, 3],
            uncovered_lines=[4],
            source_entries=[str((tmp_path / "outside" / "foldermix").resolve())],
        ),
        encoding="utf-8",
    )

    summary, debug = compute_patch_coverage_from_xml_reports(
        workspace=repo,
        changed_lines_by_file={"foldermix/converters/image_ocr.py": [1, 2, 3, 4]},
        report_files=[report_file],
        target_percent=100,
    )

    assert summary.is_na is False
    assert summary.actual_percent == 75
    assert summary.total_changed_executable_lines == 4
    assert summary.files[0].path == "foldermix/converters/image_ocr.py"
    assert summary.files[0].uncovered_changed_executable_lines == [4]
    assert "foldermix/converters/image_ocr.py" in debug["normalized_report_file_sample"]
    assert debug["inferred_source_roots"] == ["foldermix"]


def test_compute_patch_coverage_from_xml_reports_treats_partial_branch_lines_as_uncovered(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "foldermix" / "converters" / "image_ocr.py"
    _write_file(
        source_path,
        "def convert(flag):\n"
        "    if flag:\n"
        "        return 1\n"
        "    return 2\n",
    )
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(
        _coverage_xml_text(
            filename="converters/image_ocr.py",
            covered_lines=[1, 3],
            uncovered_lines=[4],
            partial_lines=[2],
            source_entries=[str((tmp_path / "outside" / "foldermix").resolve())],
        ),
        encoding="utf-8",
    )

    summary, _debug = compute_patch_coverage_from_xml_reports(
        workspace=repo,
        changed_lines_by_file={"foldermix/converters/image_ocr.py": [1, 2, 3, 4]},
        report_files=[report_file],
        target_percent=100,
    )

    assert summary.is_na is False
    assert summary.actual_percent == 50
    assert summary.total_changed_executable_lines == 4
    assert summary.covered_changed_executable_lines == 2
    assert summary.files[0].path == "foldermix/converters/image_ocr.py"
    assert summary.files[0].uncovered_changed_executable_lines == [2, 4]


def test_xml_line_is_fully_covered_allows_branch_lines_without_condition_coverage():
    line_node = ET.fromstring('<line number="10" hits="1" branch="true"/>')

    assert _xml_line_is_fully_covered(line_node) is True


def test_xml_line_is_fully_covered_tolerates_unparseable_condition_coverage():
    line_node = ET.fromstring(
        '<line number="10" hits="1" branch="true" condition-coverage="maybe"/>'
    )

    assert _xml_line_is_fully_covered(line_node) is True


def test_compute_patch_coverage_from_xml_reports_treats_missing_changed_source_as_uncovered(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    measured_path = repo / "src" / "pkg" / "module.py"
    new_path = repo / "src" / "pkg" / "new_file.py"
    _write_file(measured_path, "def covered():\n    return 1\n")
    _write_file(new_path, "def added(flag):\n    if flag:\n        return 1\n    return 2\n")
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(
        _coverage_xml_text(
            filename="pkg/module.py",
            covered_lines=[1, 2],
            uncovered_lines=[],
        ),
        encoding="utf-8",
    )

    summary, _debug = compute_patch_coverage_from_xml_reports(
        workspace=repo,
        changed_lines_by_file={"src/pkg/new_file.py": [1, 2, 3, 4]},
        report_files=[report_file],
        target_percent=100,
    )

    assert summary.actual_percent == 0
    assert summary.files[0].path == "src/pkg/new_file.py"
    assert summary.files[0].has_measured_data is False
    assert summary.files[0].uncovered_changed_executable_lines == [1, 2, 3, 4]


def test_compute_patch_coverage_from_xml_reports_returns_na_for_unusable_xml(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    report_file = tmp_path / "coverage.xml"
    report_file.write_text("<coverage><broken></coverage>", encoding="utf-8")

    summary, debug = compute_patch_coverage_from_xml_reports(
        workspace=repo,
        changed_lines_by_file={"src/pkg/module.py": [1]},
        report_files=[report_file],
        target_percent=100,
    )

    assert summary.is_na is True
    assert summary.actual_percent is None
    assert debug["resolution"] == "report_parse_error"
    assert debug["warnings"]


def test_compute_patch_coverage_from_xml_reports_returns_na_when_only_non_python_files_change(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(
        _coverage_xml_text(
            filename="pkg/module.py",
            covered_lines=[1],
            uncovered_lines=[],
        ),
        encoding="utf-8",
    )

    summary, debug = compute_patch_coverage_from_xml_reports(
        workspace=repo,
        changed_lines_by_file={"README.md": [1]},
        report_files=[report_file],
        target_percent=100,
    )

    assert summary.is_na is True
    assert debug["resolution"] == "report_loaded"


def test_compute_patch_coverage_from_xml_reports_skips_missing_changed_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(
        _coverage_xml_text(
            filename="pkg/module.py",
            covered_lines=[1],
            uncovered_lines=[],
        ),
        encoding="utf-8",
    )

    summary, _debug = compute_patch_coverage_from_xml_reports(
        workspace=repo,
        changed_lines_by_file={"src/pkg/missing.py": [1, 2]},
        report_files=[report_file],
        target_percent=100,
    )

    assert summary.is_na is True


def test_compute_patch_coverage_from_xml_reports_skips_empty_changed_line_sets(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "src" / "pkg" / "module.py"
    _write_file(source_path, "def parse(flag):\n    return 1 if flag else 2\n")
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(
        _coverage_xml_text(
            filename="pkg/module.py",
            covered_lines=[1, 2],
            uncovered_lines=[],
        ),
        encoding="utf-8",
    )

    summary, _debug = compute_patch_coverage_from_xml_reports(
        workspace=repo,
        changed_lines_by_file={"src/pkg/module.py": []},
        report_files=[report_file],
        target_percent=100,
    )

    assert summary.is_na is True


def test_compute_patch_coverage_from_xml_reports_skips_non_executable_unmeasured_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    measured_path = repo / "src" / "pkg" / "module.py"
    comments_only_path = repo / "src" / "pkg" / "notes.py"
    _write_file(measured_path, "def parse(flag):\n    return 1 if flag else 2\n")
    _write_file(comments_only_path, "# comment one\n# comment two\n")
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(
        _coverage_xml_text(
            filename="pkg/module.py",
            covered_lines=[1, 2],
            uncovered_lines=[],
        ),
        encoding="utf-8",
    )

    summary, _debug = compute_patch_coverage_from_xml_reports(
        workspace=repo,
        changed_lines_by_file={"src/pkg/notes.py": [1, 2]},
        report_files=[report_file],
        target_percent=100,
    )

    assert summary.is_na is True


def test_compute_patch_coverage_from_xml_reports_skips_files_without_changed_executable_lines(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "src" / "pkg" / "module.py"
    _write_file(source_path, "def parse(flag):\n    if flag:\n        return 1\n    return 2\n")
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(
        _coverage_xml_text(
            filename="pkg/module.py",
            covered_lines=[1, 2, 3, 4],
            uncovered_lines=[],
        ),
        encoding="utf-8",
    )

    summary, _debug = compute_patch_coverage_from_xml_reports(
        workspace=repo,
        changed_lines_by_file={"src/pkg/module.py": [99]},
        report_files=[report_file],
        target_percent=100,
    )

    assert summary.is_na is True


def test_describe_patch_coverage_scope_from_xml_reports_handles_no_reports(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    debug = describe_patch_coverage_scope_from_xml_reports(
        workspace=repo,
        report_files=[],
    )

    assert debug["resolution"] == "no_report_files"


def test_parse_xml_coverage_reports_records_multiple_reports_and_unmappable_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    first = tmp_path / "a.xml"
    second = tmp_path / "b.xml"
    xml = (
        '<?xml version="1.0" ?>\n'
        '<coverage version="7.6">\n'
        "  <sources>\n"
        f"    <source>{(tmp_path / 'outside').resolve()}</source>\n"
        "  </sources>\n"
        "  <packages>\n"
        '    <package name="pkg">\n'
        "      <classes>\n"
        '        <class name="skip" filename="">\n'
        '          <lines><line number="0" hits="0"/></lines>\n'
        "        </class>\n"
        '        <class name="missing" filename="pkg/module.py">\n'
        '          <lines><line number="0" hits="0"/></lines>\n'
        "        </class>\n"
        "      </classes>\n"
        "    </package>\n"
        "  </packages>\n"
        "</coverage>\n"
    )
    first.write_text(xml, encoding="utf-8")
    second.write_text(xml, encoding="utf-8")

    parsed, debug = _parse_xml_coverage_reports(workspace=repo, report_files=[first, second])

    assert parsed == {}
    assert debug["resolution"] == "report_without_measured_files"
    assert any("Multiple coverage reports were found" in warning for warning in debug["warnings"])


def test_normalize_report_file_path_handles_absolute_and_relative_fallbacks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "src" / "pkg" / "module.py"
    _write_file(source_path, "def covered():\n    return 1\n")

    assert (
        _normalize_report_file_path(
            filename=str(source_path.resolve()),
            source_entries=[],
            workspace=repo,
        )
        == "src/pkg/module.py"
    )
    assert (
        _normalize_report_file_path(
            filename="pkg/missing.py",
            source_entries=[str((repo / "src").resolve())],
            workspace=repo,
        )
        == "pkg/missing.py"
    )


def test_normalize_report_file_path_returns_none_for_absolute_path_outside_workspace(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def covered():\n    return 1\n", encoding="utf-8")

    assert (
        _normalize_report_file_path(
            filename=str(outside.resolve()),
            source_entries=[],
            workspace=repo,
        )
        is None
    )


def test_normalize_report_file_path_skips_resolve_errors(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "src" / "pkg" / "module.py"
    _write_file(source_path, "def covered():\n    return 1\n")

    original_resolve = Path.resolve

    def flaky_resolve(self: Path, *args, **kwargs):
        if self == source_path:
            raise OSError("boom")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", flaky_resolve)

    assert (
        _normalize_report_file_path(
            filename="src/pkg/module.py",
            source_entries=[],
            workspace=repo,
        )
        == "src/pkg/module.py"
    )


def test_parse_xml_coverage_reports_warns_for_unmappable_absolute_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def covered():\n    return 1\n", encoding="utf-8")
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(
        (
            '<?xml version="1.0" ?>\n'
            '<coverage version="7.6">\n'
            "  <packages>\n"
            '    <package name="pkg">\n'
            "      <classes>\n"
            f'        <class name="module" filename="{outside.resolve()}">\n'
            "          <lines>\n"
            '            <line number="1" hits="1"/>\n'
            "          </lines>\n"
            "        </class>\n"
            "      </classes>\n"
            "    </package>\n"
            "  </packages>\n"
            "</coverage>\n"
        ),
        encoding="utf-8",
    )

    parsed, debug = _parse_xml_coverage_reports(workspace=repo, report_files=[report_file])

    assert parsed == {}
    assert debug["resolution"] == "report_without_measured_files"
    assert debug["warnings"] == [
        f"Unable to map coverage report path to workspace: {outside.resolve()}"
    ]


def test_discover_executable_lines_without_data_handles_missing_source(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    coverage = Coverage(config_file=False)

    executable_lines = _discover_executable_lines_without_data(
        coverage=coverage,
        absolute_path=repo / "missing.py",
        changed_added_lines=[1, 2],
        workspace=repo,
    )

    assert executable_lines == []


def test_compute_patch_coverage_ignores_changed_python_files_outside_measured_roots(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    measured = repo / "src" / "pkg" / "module.py"
    test_file = repo / "tests" / "test_module.py"
    _write_file(measured, "def covered():\n    return 1\n")
    _write_file(test_file, "def test_case():\n    assert True\n")
    coverage_file = tmp_path / ".coverage"
    _build_coverage_data(coverage_file, [(measured, "covered()")])
    combined = build_combined_coverage(workspace=repo, coverage_files=[coverage_file])

    summary = compute_patch_coverage(
        workspace=repo,
        changed_lines_by_file={"tests/test_module.py": [1, 2]},
        coverage=combined,
        target_percent=100,
    )

    assert summary.is_na is True
    assert summary.actual_percent is None
    assert summary.files == []


def test_compute_patch_coverage_skips_non_python_deleted_and_empty_inputs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    existing = repo / "src" / "pkg" / "module.py"
    _write_file(existing, "def covered():\n    return 1\n")
    combined = build_combined_coverage(workspace=repo, coverage_files=[])

    summary = compute_patch_coverage(
        workspace=repo,
        changed_lines_by_file={
            "README.md": [1],
            "src/pkg/deleted.py": [1, 2],
            "src/pkg/module.py": [],
        },
        coverage=combined,
        target_percent=100,
    )

    assert summary.is_na is True
    assert summary.files == []


def test_compute_patch_coverage_skips_files_with_no_source(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    module_path = repo / "src" / "pkg" / "module.py"
    _write_file(module_path, "def covered():\n    return 1\n")
    coverage_file = tmp_path / ".coverage"
    _build_coverage_data(coverage_file, [(module_path, "covered()")])
    combined = build_combined_coverage(workspace=repo, coverage_files=[coverage_file])

    def missing_source(path):  # noqa: ARG001
        raise NoSource("missing source")

    monkeypatch.setattr(combined, "analysis2", missing_source)

    summary = compute_patch_coverage(
        workspace=repo,
        changed_lines_by_file={"src/pkg/module.py": [1, 2]},
        coverage=combined,
        target_percent=100,
    )

    assert summary.is_na is True
    assert summary.total_changed_executable_lines == 0


def test_patch_scope_helper_respects_omit_source_and_source_pkgs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    module_path = repo / "src" / "pkg" / "module.py"
    _write_file(module_path, "def covered():\n    return 1\n")
    combined = build_combined_coverage(workspace=repo, coverage_files=[])

    combined.config.run_omit = ["src/pkg/module.py"]
    assert _is_in_coverage_scope(combined, module_path, repo, {}) is False

    combined.config.run_omit = []
    combined.config.source = ["src"]
    combined.config.source_pkgs = []
    assert _is_in_coverage_scope(combined, module_path, repo, {}) is True

    combined.config.source = []
    combined.config.source_pkgs = ["pkg"]
    assert _is_in_coverage_scope(combined, module_path, repo, {}) is True

    combined.config.source = ["docs"]
    combined.config.source_pkgs = []
    assert _is_in_coverage_scope(combined, module_path, repo, {}) is False


def test_patch_helper_functions_cover_edge_cases(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    module_path = repo / "src" / "pkg" / "module.py"
    _write_file(module_path, "def covered():\n    return 1\n")

    relative_path = "src/pkg/module.py"
    parts = ("src", "pkg", "module.py")

    assert _matches_inferred_measured_roots("", ("src/pkg",)) is False
    assert _matches_inferred_measured_roots("src/pkg/module.py", ()) is True
    assert _infer_measured_source_roots({"src/pkg/module.py": "value"}) == ("src/pkg",)
    assert _infer_measured_source_roots({"pkg/module.py": "value"}) == ("pkg",)
    assert _infer_measured_source_roots({"tests/test_module.py": "value"}) == ()
    assert _infer_source_root(()) is None
    assert _infer_source_root((".", "pkg", "module.py")) is None
    assert _infer_source_root(("src", "tests", "test_module.py")) is None
    assert _infer_source_root(("src", "main.py")) == "src"
    assert _infer_source_root(("pkg.py",)) == "pkg.py"
    assert _matches_any_pattern(relative_path, ["src/*"]) is True
    assert _matches_source_entry(module_path, relative_path, parts, repo, "") is False
    assert (
        _matches_source_entry(
            module_path,
            relative_path,
            parts,
            repo,
            str((repo / "src").resolve()),
        )
        is True
    )
    assert (
        _matches_source_entry(
            module_path,
            relative_path,
            parts,
            repo,
            str((repo.parent / "outside").resolve()),
        )
        is False
    )
    assert (
        _matches_source_entry(module_path, relative_path, parts, repo, "src/pkg/module.py") is True
    )
    assert _matches_source_entry(module_path, relative_path, parts, repo, "pkg") is True
    assert (
        _matches_source_entry(
            module_path,
            str((repo / "src" / "pkg" / "module.py").resolve()),
            parts,
            repo,
            "src/pkg",
        )
        is True
    )
    assert _matches_source_entry(module_path, relative_path, parts, repo, "module.py") is False
    assert _normalize_compare_path(str(repo.resolve()), repo) == "."
    assert _normalize_compare_path("/tmp/elsewhere/module.py", repo) == "/tmp/elsewhere/module.py"


def test_patch_scope_helper_builds_inferred_roots_when_not_precomputed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    module_path = repo / "src" / "pkg" / "module.py"
    sibling_path = repo / "src" / "pkg" / "sibling.py"
    _write_file(module_path, "def covered():\n    return 1\n")
    _write_file(sibling_path, "def other():\n    return 2\n")
    combined = build_combined_coverage(workspace=repo, coverage_files=[])

    assert (
        _is_in_coverage_scope(
            combined,
            sibling_path,
            repo,
            {"src/pkg/module.py": "value"},
            has_coverage_artifacts=True,
        )
        is True
    )


def test_describe_patch_coverage_scope_reports_explicit_config_and_test_only_measured_files(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    module_path = repo / "src" / "pkg" / "module.py"
    test_path = repo / "tests" / "test_module.py"
    _write_file(module_path, "def covered():\n    return 1\n")
    _write_file(test_path, "def test_case():\n    assert True\n")

    coverage_file = tmp_path / ".coverage"
    _build_coverage_data(coverage_file, [(test_path, "test_case()")])
    combined = build_combined_coverage(workspace=repo, coverage_files=[coverage_file])

    combined.config.source = ["src"]
    explicit_debug = describe_patch_coverage_scope(
        workspace=repo,
        coverage=combined,
        has_coverage_artifacts=True,
    )
    assert explicit_debug["scope_strategy"] == "explicit_config"
    assert explicit_debug["explicit_source"] == ["src"]

    combined.config.source = []
    test_only_debug = describe_patch_coverage_scope(
        workspace=repo,
        coverage=combined,
        has_coverage_artifacts=True,
    )
    assert test_only_debug["scope_strategy"] == "measured_files_without_inferred_roots"
    assert test_only_debug["warnings"] == [
        "Measured coverage files were loaded, but no non-test source roots could be inferred."
    ]


def test_compute_patch_coverage_cli_only_src_layout_excludes_changed_tests(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "src" / "denbust" / "sources" / "mako.py"
    test_path = repo / "tests" / "integration" / "test_scrapers.py"
    _write_file(
        source_path,
        "def parse(flag):\n    if flag:\n        return 1\n    return 2\n",
    )
    _write_file(test_path, "def test_scraper():\n    assert True\n")

    coverage_file = tmp_path / ".coverage"
    _build_coverage_data(coverage_file, [(source_path, "parse(True)")])
    combined = build_combined_coverage(workspace=repo, coverage_files=[coverage_file])

    summary = compute_patch_coverage(
        workspace=repo,
        changed_lines_by_file={
            "src/denbust/sources/mako.py": [1, 2, 3, 4],
            "tests/integration/test_scrapers.py": [1, 2],
        },
        coverage=combined,
        target_percent=100,
        has_coverage_artifacts=True,
    )
    scope_debug = describe_patch_coverage_scope(
        workspace=repo,
        coverage=combined,
        has_coverage_artifacts=True,
    )

    assert summary.actual_percent == 75
    assert summary.total_changed_executable_lines == 4
    assert [file_gap.path for file_gap in summary.files] == ["src/denbust/sources/mako.py"]
    assert scope_debug["scope_strategy"] == "measured_root_inference"
    assert scope_debug["inferred_source_roots"] == ["src/denbust"]


def test_compute_patch_coverage_is_na_when_artifacts_have_no_measured_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "src" / "pkg" / "module.py"
    _write_file(source_path, "def branch(flag):\n    if flag:\n        return 1\n    return 2\n")
    combined = build_combined_coverage(workspace=repo, coverage_files=[])

    summary = compute_patch_coverage(
        workspace=repo,
        changed_lines_by_file={"src/pkg/module.py": [1, 2, 3, 4]},
        coverage=combined,
        target_percent=100,
        has_coverage_artifacts=True,
    )
    scope_debug = describe_patch_coverage_scope(
        workspace=repo,
        coverage=combined,
        has_coverage_artifacts=True,
    )

    assert summary.is_na is True
    assert summary.actual_percent is None
    assert scope_debug["scope_strategy"] == "artifacts_without_measured_files"
    assert scope_debug["warnings"] == [
        "Coverage artifacts were found, but the combined coverage data "
        "contained no measured files. "
        "Patch coverage was treated as N/A instead of 0%."
    ]


def test_compute_patch_coverage_is_na_when_coverage_source_is_pending(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "src" / "pkg" / "module.py"
    _write_file(source_path, "def branch(flag):\n    if flag:\n        return 1\n    return 2\n")
    combined = build_combined_coverage(workspace=repo, coverage_files=[])

    summary = compute_patch_coverage(
        workspace=repo,
        changed_lines_by_file={"src/pkg/module.py": [1, 2, 3, 4]},
        coverage=combined,
        target_percent=100,
        has_coverage_artifacts=False,
        coverage_source_pending=True,
    )
    scope_debug = describe_patch_coverage_scope(
        workspace=repo,
        coverage=combined,
        has_coverage_artifacts=False,
        coverage_source_pending=True,
    )

    assert summary.is_na is True
    assert summary.actual_percent is None
    assert summary.actionable is False
    assert scope_debug["coverage_source_pending"] is True
    assert scope_debug["scope_strategy"] == "coverage_source_pending"
    assert scope_debug["warnings"] == [
        "Coverage for this head SHA is not available yet. "
        "Patch coverage was treated as N/A instead of 0%."
    ]


def test_build_combined_coverage_honors_relative_files_from_workspace_root(tmp_path, monkeypatch):
    job_workspace = tmp_path / "workspace"
    repo = job_workspace / "caller-repo"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        "[tool.coverage.run]\n"
        "relative_files = true\n"
        'source = ["src/denbust"]\n'
        "\n"
        "[tool.coverage.paths]\n"
        "source = [\n"
        '  "src/denbust",\n'
        '  "/home/runner/work/tfht_enforce_idx/tfht_enforce_idx/src/denbust",\n'
        "]\n",
        encoding="utf-8",
    )
    source_path = repo / "src" / "denbust" / "sources" / "mako.py"
    test_path = repo / "tests" / "integration" / "test_scrapers.py"
    _write_file(
        source_path,
        "def parse(flag):\n    if flag:\n        return 1\n    return 2\n",
    )
    _write_file(test_path, "def test_scraper():\n    assert True\n")

    coverage_dir = job_workspace / "coverage-artifacts" / "linux"
    coverage_dir.mkdir(parents=True)
    coverage_file = coverage_dir / ".coverage.py312"
    _build_coverage_data_with_workspace_config(
        workspace=repo,
        data_file=coverage_file,
        scripts=[(source_path, "parse(True)")],
    )

    monkeypatch.chdir(job_workspace)

    combined = build_combined_coverage(workspace=repo, coverage_files=[coverage_file])
    summary = compute_patch_coverage(
        workspace=repo,
        changed_lines_by_file={
            "src/denbust/sources/mako.py": [1, 2, 3, 4],
            "tests/integration/test_scrapers.py": [1, 2],
        },
        coverage=combined,
        target_percent=100,
        has_coverage_artifacts=True,
    )
    scope_debug = describe_patch_coverage_scope(
        workspace=repo,
        coverage=combined,
        has_coverage_artifacts=True,
    )

    assert combined.get_data().measured_files()
    assert summary.actual_percent == 75
    assert summary.total_changed_executable_lines == 4
    assert [file_gap.path for file_gap in summary.files] == ["src/denbust/sources/mako.py"]
    assert scope_debug["scope_strategy"] == "explicit_config"
    assert scope_debug["explicit_source"] == ["src/denbust"]


def test_find_coverage_config_file_prefers_existing_project_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[tool.coverage.run]\n", encoding="utf-8")

    assert combine_module._find_coverage_config_file(repo) == repo / "pyproject.toml"


def test_resolve_coverage_files_selects_latest_successful_matching_workflow(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    module_path = repo / "src" / "pkg" / "module.py"
    _write_file(module_path, "def covered():\n    return 1\n")
    coverage_file = tmp_path / ".coverage.producer"
    _build_coverage_data(coverage_file, [(module_path, "covered()")])

    client = CoverageSourceClient(
        workflow_runs={
            "workflow_runs": [
                {
                    "id": 10,
                    "name": "Other workflow",
                    "conclusion": "success",
                    "updated_at": "2026-03-10T10:00:00Z",
                },
                {
                    "id": 20,
                    "name": "CI",
                    "conclusion": "failure",
                    "updated_at": "2026-03-10T11:00:00Z",
                },
                {
                    "id": 30,
                    "name": "CI",
                    "conclusion": "success",
                    "updated_at": "2026-03-10T12:00:00Z",
                },
            ]
        },
        artifacts_by_run={
            10: {"artifacts": [{"id": 1001, "name": "pr-agent-context-coverage-old"}]},
            20: {"artifacts": [{"id": 2001, "name": "pr-agent-context-coverage-fail"}]},
            30: {"artifacts": [{"id": 3001, "name": "pr-agent-context-coverage-linux"}]},
        },
        zip_bytes_by_artifact={
            1001: _coverage_zip_bytes(coverage_file),
            2001: _coverage_zip_bytes(coverage_file),
            3001: _coverage_zip_bytes(coverage_file),
        },
    )

    files, debug = resolve_coverage_files(
        client=client,
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        local_artifacts_dir=tmp_path / "downloaded",
        artifact_prefix="pr-agent-context-coverage",
        enable_cross_run_lookup=True,
        execution_mode="refresh",
        workflow_names=("CI",),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=20,
    )

    assert len(files) == 1
    assert files[0].name.startswith(".coverage")
    assert debug["resolution"] == "cross_run_downloaded"
    assert debug["selected_run"] == {
        "id": 30,
        "name": "CI",
        "status": "",
        "conclusion": "success",
        "updated_at": "2026-03-10T12:00:00Z",
    }
    assert [candidate["id"] for candidate in debug["candidate_runs"]] == [30, 20, 10]
    assert debug["candidate_runs"][1]["reasons"] == ["conclusion_filtered"]
    assert debug["candidate_runs"][2]["reasons"] == ["workflow_name_filtered"]


def test_resolve_coverage_files_reports_missing_suitable_producer_run(tmp_path):
    client = CoverageSourceClient(
        workflow_runs={
            "workflow_runs": [
                {
                    "id": 10,
                    "name": "CI",
                    "conclusion": "success",
                    "updated_at": "2026-03-10T10:00:00Z",
                }
            ]
        },
        artifacts_by_run={10: {"artifacts": [{"id": 1001, "name": "unrelated-artifact"}]}},
        zip_bytes_by_artifact={},
    )

    files, debug = resolve_coverage_files(
        client=client,
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        local_artifacts_dir=None,
        artifact_prefix="pr-agent-context-coverage",
        enable_cross_run_lookup=True,
        execution_mode="refresh",
        workflow_names=(),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=20,
    )

    assert files == []
    assert debug["resolution"] == "no_suitable_coverage_source"
    assert debug["selected_run"] is None
    assert debug["candidate_runs"][0]["reasons"] == ["no_matching_artifacts"]


def test_resolve_coverage_files_reports_pending_matching_producer_run(tmp_path):
    client = CoverageSourceClient(
        workflow_runs={
            "workflow_runs": [
                {
                    "id": 30,
                    "name": "CI",
                    "status": "in_progress",
                    "conclusion": None,
                    "updated_at": "2026-03-10T12:00:00Z",
                }
            ]
        },
        artifacts_by_run={},
        zip_bytes_by_artifact={},
    )

    files, debug = resolve_coverage_files(
        client=client,
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        local_artifacts_dir=None,
        artifact_prefix="pr-agent-context-coverage",
        enable_cross_run_lookup=True,
        execution_mode="refresh",
        workflow_names=("CI",),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=20,
    )

    assert files == []
    assert debug["resolution"] == "coverage_source_pending"
    assert debug["coverage_source_pending"] is True
    assert debug["selected_run"] is None
    assert debug["candidate_runs"][0]["reasons"] == ["not_completed_yet"]


def test_discover_coverage_files_ignores_transient_sqlite_sidecars(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / ".coverage").write_text("main", encoding="utf-8")
    (artifacts / ".coverage-shm").write_text("shm", encoding="utf-8")
    (artifacts / ".coverage-wal").write_text("wal", encoding="utf-8")
    (artifacts / ".coverage.lock").write_text("lock", encoding="utf-8")

    files = discover_coverage_files(artifacts)

    assert files == [artifacts / ".coverage"]


def test_discover_coverage_report_files_ignores_non_matching_files(tmp_path):
    artifacts = tmp_path / "artifacts"
    nested = artifacts / "nested"
    nested.mkdir(parents=True)
    matching = nested / "coverage.xml"
    matching.write_text("<coverage />", encoding="utf-8")
    (artifacts / "notes.txt").write_text("ignore me", encoding="utf-8")

    files = discover_coverage_report_files(artifacts, report_filename="coverage.xml")

    assert files == [matching]


def test_resolve_coverage_files_prefers_local_ci_artifacts_when_present(tmp_path):
    local_dir = tmp_path / "artifacts"
    local_dir.mkdir()
    local_file = local_dir / ".coverage.py312"
    local_file.write_text("local", encoding="utf-8")

    files, debug = resolve_coverage_files(
        client=CoverageSourceClient(
            workflow_runs={"workflow_runs": []},
            artifacts_by_run={},
            zip_bytes_by_artifact={},
        ),
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        local_artifacts_dir=local_dir,
        artifact_prefix="pr-agent-context-coverage",
        enable_cross_run_lookup=True,
        execution_mode="ci",
        workflow_names=(),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=20,
    )

    assert files == [local_file]
    assert debug["resolution"] == "local_current_run_artifacts"


def test_resolve_coverage_files_returns_local_files_when_cross_run_lookup_disabled(tmp_path):
    local_dir = tmp_path / "artifacts"
    local_dir.mkdir()
    local_file = local_dir / ".coverage.py312"
    local_file.write_text("local", encoding="utf-8")

    files, debug = resolve_coverage_files(
        client=CoverageSourceClient(
            workflow_runs={"workflow_runs": []},
            artifacts_by_run={},
            zip_bytes_by_artifact={},
        ),
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        local_artifacts_dir=local_dir,
        artifact_prefix="pr-agent-context-coverage",
        enable_cross_run_lookup=False,
        execution_mode="refresh",
        workflow_names=(),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=20,
    )

    assert files == [local_file]
    assert debug["resolution"] == "cross_run_lookup_disabled"


def test_resolve_coverage_files_falls_back_to_local_files_when_no_cross_run_source_exists(tmp_path):
    local_dir = tmp_path / "artifacts"
    local_dir.mkdir()
    local_file = local_dir / ".coverage.py312"
    local_file.write_text("local", encoding="utf-8")

    files, debug = resolve_coverage_files(
        client=CoverageSourceClient(
            workflow_runs={"workflow_runs": []},
            artifacts_by_run={},
            zip_bytes_by_artifact={},
        ),
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        local_artifacts_dir=local_dir,
        artifact_prefix="pr-agent-context-coverage",
        enable_cross_run_lookup=True,
        execution_mode="refresh",
        workflow_names=("CI",),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=20,
    )

    assert files == [local_file]
    assert debug["resolution"] == "local_current_run_artifacts_fallback"


def test_resolve_coverage_files_reports_selected_run_without_coverage_files(tmp_path):
    empty_zip = io.BytesIO()
    with zipfile.ZipFile(empty_zip, "w"):
        pass
    client = CoverageSourceClient(
        workflow_runs={
            "workflow_runs": [
                {
                    "id": 30,
                    "name": "CI",
                    "conclusion": "success",
                    "updated_at": "2026-03-10T12:00:00Z",
                }
            ]
        },
        artifacts_by_run={
            30: {"artifacts": [{"id": 3001, "name": "pr-agent-context-coverage-linux"}]}
        },
        zip_bytes_by_artifact={3001: empty_zip.getvalue()},
    )

    files, debug = resolve_coverage_files(
        client=client,
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        local_artifacts_dir=tmp_path / "downloaded",
        artifact_prefix="pr-agent-context-coverage",
        enable_cross_run_lookup=True,
        execution_mode="refresh",
        workflow_names=("CI",),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=20,
    )

    assert files == []
    assert debug["resolution"] == "selected_run_without_coverage_files"


def test_resolve_coverage_files_selects_configured_xml_report_artifact(tmp_path):
    client = CoverageSourceClient(
        workflow_runs={
            "workflow_runs": [
                {
                    "id": 30,
                    "name": "CI",
                    "conclusion": "success",
                    "updated_at": "2026-03-10T12:00:00Z",
                }
            ]
        },
        artifacts_by_run={30: {"artifacts": [{"id": 3001, "name": "coverage-xml"}]}},
        zip_bytes_by_artifact={
            3001: _coverage_report_zip_bytes(
                _coverage_xml_text(
                    filename="pkg/module.py",
                    covered_lines=[1, 2],
                    uncovered_lines=[],
                )
            )
        },
    )

    files, debug = resolve_coverage_files(
        client=client,
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        local_artifacts_dir=tmp_path / "downloaded",
        patch_coverage_source_mode="coverage_xml_artifact",
        artifact_prefix="pr-agent-context-coverage",
        coverage_report_artifact_name="coverage-xml",
        coverage_report_filename="coverage.xml",
        enable_cross_run_lookup=True,
        execution_mode="refresh",
        workflow_names=("CI",),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=20,
    )

    assert len(files) == 1
    assert files[0].name == "coverage.xml"
    assert debug["resolution"] == "cross_run_downloaded"
    assert debug["selected_artifacts"] == [{"id": 3001, "name": "coverage-xml", "size_in_bytes": 0}]


def test_resolve_coverage_files_prefers_local_xml_report_in_ci_mode(tmp_path):
    local_dir = tmp_path / "artifacts"
    local_dir.mkdir()
    report_file = local_dir / "coverage.xml"
    report_file.write_text(
        _coverage_xml_text(
            filename="pkg/module.py",
            covered_lines=[1],
            uncovered_lines=[],
        ),
        encoding="utf-8",
    )

    files, debug = resolve_coverage_files(
        client=CoverageSourceClient(
            workflow_runs={"workflow_runs": []},
            artifacts_by_run={},
            zip_bytes_by_artifact={},
        ),
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        local_artifacts_dir=local_dir,
        patch_coverage_source_mode="coverage_xml_artifact",
        artifact_prefix="pr-agent-context-coverage",
        coverage_report_artifact_name="coverage-xml",
        coverage_report_filename="coverage.xml",
        enable_cross_run_lookup=True,
        execution_mode="ci",
        workflow_names=(),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=20,
    )

    assert files == [report_file]
    assert debug["resolution"] == "local_current_run_artifacts"


def test_resolve_coverage_files_rejects_unsupported_selection_strategy(tmp_path):
    with pytest.raises(ValueError, match="Unsupported coverage selection strategy"):
        resolve_coverage_files(
            client=CoverageSourceClient(
                workflow_runs={"workflow_runs": []},
                artifacts_by_run={},
                zip_bytes_by_artifact={},
            ),
            owner="shaypal5",
            repo="example",
            head_sha="deadbeef",
            local_artifacts_dir=tmp_path / "downloaded",
            artifact_prefix="pr-agent-context-coverage",
            enable_cross_run_lookup=True,
            execution_mode="refresh",
            workflow_names=(),
            allowed_conclusions=("success",),
            selection_strategy="oldest",
            max_candidate_runs=20,
        )
