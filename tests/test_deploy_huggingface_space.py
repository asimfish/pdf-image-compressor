from pathlib import Path

import pytest

import deploy_huggingface_space as deploy


def _make_project(root: Path) -> None:
    for filename in deploy.SPACE_FILES:
        (root / filename).write_text(filename, encoding="utf-8")
    package = root / "src" / "file_compressor"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"cache")
    (root / "src" / "standalone.pyc").write_bytes(b"cache")
    egg_info = root / "src" / "pdf_image_compressor.egg-info"
    egg_info.mkdir()
    (egg_info / "PKG-INFO").write_text("generated", encoding="utf-8")


def test_prepare_space_source_copies_only_runtime_files(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    _make_project(project)
    destination = tmp_path / "space"
    destination.mkdir()
    monkeypatch.setattr(deploy, "ROOT", project)

    deploy._prepare_space_source(destination)

    assert (destination / "Dockerfile").is_file()
    assert (destination / "src" / "file_compressor" / "__init__.py").is_file()
    assert not (destination / "src" / "file_compressor" / "__pycache__").exists()
    assert not (destination / "src" / "standalone.pyc").exists()
    assert not (destination / "src" / "pdf_image_compressor.egg-info").exists()
    assert "sdk: docker" in (destination / "README.md").read_text(encoding="utf-8")


def test_prepare_space_source_reports_missing_inputs(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    destination = tmp_path / "space"
    destination.mkdir()
    monkeypatch.setattr(deploy, "ROOT", project)

    with pytest.raises(FileNotFoundError, match="Dockerfile"):
        deploy._prepare_space_source(destination)
