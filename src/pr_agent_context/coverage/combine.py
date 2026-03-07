from __future__ import annotations

import tempfile
from pathlib import Path

from coverage import Coverage

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
    if coverage_files:
        coverage.combine(data_paths=[str(path) for path in coverage_files], strict=False)
        coverage.save()
        coverage.load()
    return coverage


def _find_coverage_config_file(workspace: Path) -> Path | None:
    for candidate_name in KNOWN_COVERAGE_CONFIG_FILES:
        candidate = workspace / candidate_name
        if candidate.exists():
            return candidate
    return None
