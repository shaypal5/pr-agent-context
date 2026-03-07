from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path, PurePosixPath

_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")


def collect_changed_lines(
    workspace: Path,
    *,
    base_sha: str,
    head_sha: str,
) -> dict[str, list[int]]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "diff",
            "--unified=0",
            f"{base_sha}...{head_sha}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_unified_diff(result.stdout)


def parse_unified_diff(diff_text: str) -> dict[str, list[int]]:
    changed_lines: dict[str, set[int]] = defaultdict(set)
    current_path: str | None = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ "):
            current_path = _parse_new_path(raw_line[4:])
            continue
        if current_path is None or not raw_line.startswith("@@ "):
            continue
        match = _HUNK_HEADER_RE.match(raw_line)
        if not match:
            continue
        start = int(match.group("start"))
        count = int(match.group("count") or "1")
        if count <= 0:
            continue
        changed_lines[current_path].update(range(start, start + count))

    return {path: sorted(lines) for path, lines in sorted(changed_lines.items()) if lines}


def _parse_new_path(raw_path: str) -> str | None:
    path = raw_path.strip()
    if path == "/dev/null":
        return None
    if path.startswith("b/"):
        path = path[2:]
    return normalize_repo_path(path)


def normalize_repo_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return PurePosixPath(normalized).as_posix()
