from __future__ import annotations

from conftest import load_text_fixture
from pr_agent_context.coverage.git_diff import parse_unified_diff


def test_parse_unified_diff_tracks_added_lines_across_multiple_hunks():
    parsed = parse_unified_diff(load_text_fixture("coverage/git_diff.patch"))

    assert parsed == {
        "docs/readme.md": [3, 4],
        "src/pkg/module.py": [2, 3, 11, 12, 13],
        "src/pkg/notes.py": [1, 2],
    }
