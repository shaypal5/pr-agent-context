from __future__ import annotations

from conftest import load_text_fixture
from pr_agent_context.coverage.git_diff import normalize_repo_path, parse_unified_diff


def test_parse_unified_diff_tracks_added_lines_across_multiple_hunks():
    parsed = parse_unified_diff(load_text_fixture("coverage/git_diff.patch"))

    assert parsed == {
        "docs/readme.md": [3, 4],
        "src/pkg/module.py": [2, 3, 11, 12, 13],
        "src/pkg/notes.py": [1, 2],
    }


def test_parse_unified_diff_ignores_malformed_hunks_and_deleted_targets():
    parsed = parse_unified_diff(
        "\n".join(
            [
                "+++ /dev/null",
                "@@ -1 +0,0 @@",
                "+++ b/src/pkg/module.py",
                "@@ malformed @@",
                "@@ -1 +2,0 @@",
                "@@ -0,0 +4,2 @@",
                "+++ ./src\\pkg\\notes.py",
                "@@ -0,0 +7 @@",
            ]
        )
    )

    assert parsed == {
        "src/pkg/module.py": [4, 5],
        "src/pkg/notes.py": [7],
    }


def test_normalize_repo_path_strips_leading_dot_segments():
    assert normalize_repo_path("././src\\pkg\\module.py") == "src/pkg/module.py"
