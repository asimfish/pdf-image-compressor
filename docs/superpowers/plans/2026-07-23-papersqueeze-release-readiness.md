# PaperSqueeze Release Readiness Plan

Date: 2026-07-23
Branch: `feat/pypi-trusted-publishing`

## Current status

- Compression correctness: complete. The merged `main` history includes the public-mode hardening, final PDF fidelity fixes, soft-mask preservation, and cleanup-race fix.
- Tests: 537 passed in the release worktree; `ruff check` passes; `pip-audit --local --skip-editable` reports no known vulnerabilities.
- Website: deployed. `GET https://liyufeng854--papersqueeze-serve.modal.run/api/health` returns `{"status":"ok","mode":"public","compression_slots":1,"rate_limit_per_minute":12}`.
- Distribution: `papersqueeze 0.2.0` wheel and sdist build cleanly, pass `twine check`, smoke-test both CLI names, and stage for first PyPI publication with `publish=true`.

## Remaining gaps

### Agent-executable after confirmation

- [ ] Push `feat/pypi-trusted-publishing` and open the release PR to `main`.
- [ ] Wait for PR CI, address any failures, and merge only after the manual trust setup below is confirmed.
- [ ] After merge and green `main` CI, create and push annotated tag `v0.2.0`.
- [ ] Verify the Release workflow end-to-end: GitHub Release assets, GHCR `v0.2.0`/`0.2.0`, PyPI files and hashes, clean `uv tool install papersqueeze==0.2.0`, and Modal health.

### User/manual external setup

- [ ] Configure the PyPI pending trusted publisher with exactly: project `papersqueeze`, owner `asimfish`, repository `pdf-image-compressor`, workflow `release.yml`, environment `pypi`.
- [ ] Create/restrict the GitHub `pypi` environment to version tags; optionally require manual approval.
- [ ] Revoke the two previously exposed Modal tokens and create the final replacement token.
- [ ] Confirm the Modal deployment uses the final token and remains healthy.

### Deferred hygiene, not release blockers

- [ ] Install or pin `actionlint` in a separate CI-hardening change; local validation currently uses Ruby Psych because `actionlint` is not installed.
- [ ] Run a formatting-only PR for the 25 pre-existing files reported by `ruff format --check`; keep it separate from behavior changes.
- [ ] Re-run `gitnexus analyze` for the primary checkout when convenient; the release worktree index is current.
- [ ] Leave unrelated primary-worktree files (`.claude/scheduled_tasks*`, `.mcp.json`) out of the release PR.

## Execution order

1. User approves push/PR.
2. Push branch, open PR with test/build evidence and manual setup checklist.
3. User completes PyPI publisher, GitHub environment, and Modal token rotation.
4. Merge PR after green CI and confirmed setup.
5. Tag `v0.2.0`, then run post-release verification.
