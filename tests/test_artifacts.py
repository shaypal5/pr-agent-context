from __future__ import annotations

from pr_agent_context.coverage.artifacts import discover_coverage_files


def test_discover_coverage_files_recurses_and_ignores_transient_files(tmp_path):
    coverage_root = tmp_path / "coverage-artifacts"
    nested = coverage_root / "linux" / "py312"
    nested.mkdir(parents=True)
    expected = nested / ".coverage.py312"
    expected.write_text("data", encoding="utf-8")
    (nested / ".coverage.py312-wal").write_text("ignore", encoding="utf-8")
    (nested / ".coverage.py312-shm").write_text("ignore", encoding="utf-8")

    discovered = discover_coverage_files(coverage_root)

    assert discovered == [expected]
