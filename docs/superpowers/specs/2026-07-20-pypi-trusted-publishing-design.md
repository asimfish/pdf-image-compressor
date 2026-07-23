# PaperSqueeze PyPI Trusted Publishing Design

## Goal

Publish PaperSqueeze as an installable Python distribution without storing a
PyPI API token, while preserving compatibility with the existing GitHub release,
Python import path, and CLI.

Success means:

- `pip install papersqueeze` and `uv tool install papersqueeze` install the
  official project;
- both `papersqueeze` and the existing `file-compressor` commands work;
- each command reports its own invoked name in `--help`;
- existing `from file_compressor ...` imports remain valid;
- a `v0.2.0` tag publishes the same validated wheel and source distribution to
  GitHub Releases and PyPI;
- rerunning a release skips identical PyPI files and rejects conflicting files;
- no long-lived PyPI credential is stored in GitHub.

## Naming and compatibility

The current PyPI project name, `pdf-image-compressor`, is owned by an unrelated
publisher and cannot be used by this repository. The distribution will therefore
be renamed to `papersqueeze`, which is available at design time.

Only the distribution name changes. The implementation package remains
`file_compressor`, so existing imports keep working. A new `papersqueeze` console
script will point to the same entry point as `file-compressor`; the old command
will remain available as a compatibility alias.

The first PaperSqueeze PyPI release will be `0.2.0`. This avoids pretending that
the already published GitHub `v0.1.0` artifact had the new distribution identity.

## Release architecture

The existing tag workflow remains the source of release artifacts:

1. `validate` checks that the version tag points to a commit contained in
   `main`, installs locked dependencies, runs quality gates, and builds exactly
   one wheel and one source distribution.
2. `publish` creates or verifies the GitHub Release and versioned GHCR aliases.
3. A new read-only `pypi_preflight` job downloads the validated distributions
   only after `publish` succeeds. It compares each local file name and SHA-256
   digest with PyPI's version-specific JSON endpoint,
   `/pypi/papersqueeze/<version>/json`.
4. If PyPI has no copy, the file is staged for upload. If an identical file
   exists, it is skipped. If the same file name has a different digest, the
   workflow fails without publishing.
5. A minimal `pypi_publish` job receives only the staged artifacts and invokes
   the digest-pinned official PyPI publishing action with `skip-existing` as
   defense against short-lived PyPI CDN lag. It has `id-token: write`, uses the
   protected `pypi` GitHub environment, and does not check out or execute
   repository code.
6. A final read-only `pypi_verify` job queries the version-specific JSON
   endpoint with bounded retries and verifies every expected filename and
   SHA-256 digest. A duplicate hidden by `skip-existing`, a conflicting digest,
   or a response that remains stale after the retry window fails the workflow.

The preflight comparison will live in a small standard-library Python script so
its version parsing, HTTP responses, partial-release recovery, and digest checks
can be unit tested. The publishing job itself remains free of repository code.

The existing manual `workflow_dispatch` repair path will gain an explicit
`repair_pypi` input. When enabled for `v0.2.0` or later, a read-only preparation
job downloads the wheel and source distribution from the existing GitHub
Release, verifies them against GitHub's recorded SHA-256 digests, and feeds them
through the same PyPI preflight. It never rebuilds or executes code from the old
tag. This provides a recovery path after artifact retention expires or after a
partially successful PyPI upload. Releases whose distribution metadata is not
`papersqueeze`, including `v0.1.0`, are rejected from PyPI repair.

Because the `pypi` environment is restricted to version tags, a PyPI repair must
dispatch the workflow from the target tag rather than from the default branch.
The workflow will require `github.ref == refs/tags/<inputs.tag>` whenever
`repair_pypi` is enabled. The documented command will therefore include both
the tag input and `--ref <tag>`.

## Trust boundaries

PyPI authentication uses Trusted Publishing through GitHub OIDC. No PyPI token
or password is added to repository secrets.

Before the first release, the maintainer must configure a pending publisher on
PyPI with:

- project: `papersqueeze`;
- owner: `asimfish`;
- repository: `pdf-image-compressor`;
- workflow: `release.yml`;
- environment: `pypi`.

The matching GitHub environment should restrict deployment to version tags and
may require manual approval. The workflow also rejects tags whose commit is not
reachable from `origin/main`.

PyPI does not expose a reliable public API for checking a pending publisher
before the first upload. The release documentation and GitHub environment
approval notice will therefore make this a hard precondition and list the exact
five matching fields. A missing or mismatched publisher fails only the isolated
PyPI job and can be repaired through the manual dispatch path after correcting
the configuration.

Release artifacts are immutable. Existing files are accepted only when their
SHA-256 digests match the locally validated build; conflicting content causes a
hard failure.

## Documentation and user flow

The README will make PyPI the primary installation path and retain GitHub wheel
and GHCR instructions as alternatives. The CLI parser will derive its displayed
program name from the invoked console script instead of hardcoding
`file-compressor`. The primary flow will show:

```bash
uv tool install papersqueeze
papersqueeze web
```

The contributor guide will document the one-time Trusted Publisher setup and
the release order: merge the version change, wait for `main` CI, verify the
publisher configuration, then push the annotated tag.

The changelog will record the distribution rename, CLI alias, and tokenless PyPI
publishing. Package metadata and the lockfile will be updated together.

## Failure handling

- Target-version 404, including the first release of a pending project: treat as
  an empty remote version and stage both files.
- Network error, timeout, malformed response, or PyPI 5xx: fail before requesting
  publication.
- Existing file with a different digest: fail and require maintainer review.
- Existing complete version with matching digests: succeed without invoking the
  publishing action.
- Existing partial version: upload only the missing validated file.
- Stale PyPI JSON after a recent upload: let the publishing action's
  `skip-existing` check safely ignore the duplicate, then require the read-only
  verification job to observe the expected digest within a bounded retry
  window.
- OIDC or publisher configuration failure: leave GitHub Release and GHCR
  artifacts intact, report the PyPI job failure, and allow a safe workflow rerun
  or a later `repair_pypi` dispatch using verified GitHub Release assets.

## Verification

Automated checks will cover:

- distribution metadata is named `papersqueeze` at version `0.2.0`;
- both CLI entry points resolve to the existing command implementation;
- both CLI entry points display the correct invoked command name;
- PyPI preflight behavior for absent project/version 404, identical,
  conflicting, partial, malformed, timed-out, and unavailable remote releases;
- manual repair accepts verified `papersqueeze` release assets and rejects old
  or mismatched distributions;
- wheel and source distribution metadata via `twine check`;
- the full existing test, lint, dependency-audit, and container smoke suites;
- GitHub Actions syntax and permission review.

Before tagging, the release candidate will also be installed from its wheel in a
clean environment and both CLI names will be smoke tested.
