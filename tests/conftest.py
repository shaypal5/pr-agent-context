from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def load_json_fixture(relative_path: str):
    with (FIXTURES_ROOT / relative_path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_text_fixture(relative_path: str) -> str:
    return (FIXTURES_ROOT / relative_path).read_text(encoding="utf-8")


@pytest.fixture
def review_threads_payload():
    return load_json_fixture("github/review_threads.json")


@pytest.fixture
def workflow_jobs_payload():
    return load_json_fixture("github/workflow_jobs.json")


@pytest.fixture
def issue_comments_payload():
    return load_json_fixture("github/issue_comments.json")
