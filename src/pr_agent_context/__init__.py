"""Reusable PR handoff context generator."""

from __future__ import annotations

from importlib import metadata as _metadata
from pathlib import Path

__all__ = ["__version__"]


def _read_pyproject_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject_path.exists():
        raise RuntimeError("Unable to determine package version from source checkout.")

    for line in pyproject_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("version = "):
            _, _, raw_value = stripped.partition("=")
            return raw_value.strip().strip('"').strip("'")

    raise RuntimeError("Unable to determine package version from pyproject.toml.")


try:
    __version__ = _metadata.version("pr-agent-context")
except _metadata.PackageNotFoundError:
    __version__ = _read_pyproject_version()
