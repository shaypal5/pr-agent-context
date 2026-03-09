from __future__ import annotations

from pathlib import Path

from coverage import Coverage
from coverage.exceptions import DataError

from pr_agent_context.coverage import combine as combine_module
from pr_agent_context.coverage.combine import build_combined_coverage
from pr_agent_context.coverage.patch import compute_patch_coverage


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
