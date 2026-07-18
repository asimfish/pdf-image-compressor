#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["huggingface_hub>=0.36.0"]
# ///
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPACE_FILES = ("Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock", "LICENSE")
SPACE_DIRECTORIES = ("src",)

SPACE_README = """---
title: PaperSqueeze
emoji: 📄
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 8080
pinned: false
license: mit
short_description: Target-aware PDF compression that preserves searchable text
---

# PaperSqueeze

Compress PDFs to a target size while preserving searchable text, links, formulas,
and vector content whenever possible.

Source code and documentation:
https://github.com/asimfish/pdf-image-compressor
"""


def _prepare_space_source(destination: Path) -> None:
    required = [ROOT / item for item in (*SPACE_FILES, *SPACE_DIRECTORIES)]
    missing = [path.name for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Required Space sources not found: {', '.join(missing)}")

    for filename in SPACE_FILES:
        shutil.copy2(ROOT / filename, destination / filename)
    for directory in SPACE_DIRECTORIES:
        shutil.copytree(
            ROOT / directory,
            destination / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.egg-info", ".DS_Store"),
        )
    (destination / "README.md").write_text(SPACE_README, encoding="utf-8")


def main() -> None:
    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    api = HfApi()
    try:
        account = api.whoami()
    except Exception:
        raise SystemExit(
            "Unable to authenticate with or reach Hugging Face. Run "
            "`uvx --from huggingface_hub hf auth login` and retry."
        ) from None
    username = account.get("name") or account.get("username")
    if not isinstance(username, str) or not username:
        raise SystemExit("Hugging Face did not return an account username.")
    repo_id = os.getenv("HF_SPACE_ID", f"{username}/papersqueeze")

    try:
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="docker",
            private=False,
            exist_ok=True,
        )
    except HfHubHTTPError as exc:
        if exc.response is not None and exc.response.status_code == 402:
            raise SystemExit(
                "Hugging Face requires a PRO subscription for Docker Spaces on cpu-basic. "
                "Upgrade the account at https://huggingface.co/pro and retry."
            ) from None
        raise
    with tempfile.TemporaryDirectory(prefix="papersqueeze_hf_") as temp_dir:
        source = Path(temp_dir)
        _prepare_space_source(source)
        api.upload_folder(
            folder_path=source,
            repo_id=repo_id,
            repo_type="space",
            commit_message="Deploy PaperSqueeze",
            delete_patterns=["*"],
        )

    print(f"https://huggingface.co/spaces/{repo_id}")


if __name__ == "__main__":
    main()
