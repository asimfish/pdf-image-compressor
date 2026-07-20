from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _load() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def test_project_name():
    data = _load()
    assert data["project"]["name"] == "papersqueeze"


def test_project_version():
    data = _load()
    assert data["project"]["version"] == "0.2.0"


def test_scripts_both_commands():
    data = _load()
    scripts = data["project"]["scripts"]
    assert scripts.get("papersqueeze") == "file_compressor.cli:main"
    assert scripts.get("file-compressor") == "file_compressor.cli:main"


def test_scripts_no_extra_keys():
    data = _load()
    scripts = data["project"]["scripts"]
    assert set(scripts.keys()) == {"papersqueeze", "file-compressor"}


def test_package_data_key():
    data = _load()
    pkg_data = data["tool"]["setuptools"]["package-data"]
    assert "file_compressor" in pkg_data
