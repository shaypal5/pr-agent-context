from __future__ import annotations

import runpy
import sys

import pytest


def test_module_entrypoint_raises_system_exit_with_main_result(monkeypatch):
    monkeypatch.setattr("pr_agent_context.cli.main", lambda: 7)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("pr_agent_context.__main__", run_name="__main__")

    assert exc_info.value.code == 7


def test_module_import_does_not_execute_main(monkeypatch):
    monkeypatch.setattr(
        "pr_agent_context.cli.main",
        lambda: (_ for _ in ()).throw(AssertionError("main should not run on import")),
    )
    sys.modules.pop("pr_agent_context.__main__", None)

    __import__("pr_agent_context.__main__")
