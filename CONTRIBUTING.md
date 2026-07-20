# Contributing to PaperSqueeze

Thanks for helping improve PaperSqueeze. Bug reports, focused fixes, tests, and
compression-quality measurements are welcome.

## Development setup

PaperSqueeze requires Python 3.10 or newer and
[uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/asimfish/pdf-image-compressor.git
cd pdf-image-compressor
uv sync --locked --extra dev
```

Run the local web interface with:

```bash
uv run file-compressor web
```

## Before opening a pull request

Run the same checks used by CI:

```bash
uv run ruff check src tests scripts deploy_huggingface_space.py deploy_modal.py
uv run pip-audit --local --skip-editable
uv run python -m pytest -q
uv build
```

Keep changes focused and include a regression test for every bug fix. Avoid
including private, copyrighted, or sensitive PDFs in commits.

Changes to compression behavior should report:

1. Original and compressed file sizes.
2. Whether page geometry, searchable text, links, and soft masks are preserved.
3. A visual metric such as RGB SSIM or PSNR.
4. Runtime and peak memory when performance changes materially.
5. The new or updated regression tests.

Use the bundled benchmark for reproducible comparisons:

```bash
uv run scripts/compare_pdf_quality.py original.pdf compressed.pdf \
  --reference reference.pdf \
  --dpi 96
```

## Pull requests

- Explain the user-visible problem and why the change solves it.
- Keep formatting-only changes separate from behavior changes.
- Update `README.md` and `CHANGELOG.md` when public behavior changes.
- Confirm that no API keys, tokens, private documents, or generated outputs are included.

For security vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of
opening a public issue.
