# PaperSqueeze PyPI Trusted Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the official project as `papersqueeze` on PyPI through tokenless, digest-verified Trusted Publishing while retaining the existing import and CLI compatibility surface.

**Architecture:** Rename only the distribution, add a branded CLI alias, and keep `file_compressor` as the import package. A tested standard-library helper will inspect distributions, compare them with PyPI's version endpoint, stage only missing files, and verify final remote digests. GitHub Actions will keep build, preflight, OIDC publication, and post-publication verification in separate least-privilege jobs, with an explicit tag-based repair path.

**Tech Stack:** Python 3.10+, `argparse`, `urllib`, `zipfile`, `tarfile`, `pytest`, `uv`, GitHub Actions, PyPI Trusted Publishing/OIDC, `pypa/gh-action-pypi-publish`.

## File map

- `pyproject.toml`: change the distribution identity/version and expose both CLI commands.
- `uv.lock`: lock the renamed local project at version `0.2.0`.
- `src/file_compressor/cli.py`: let `argparse` display the command actually invoked.
- `tests/test_cli.py`: cover the branded and compatibility help names.
- `tests/test_package_metadata.py`: lock the public distribution name, version, package namespace, and script aliases.
- `scripts/pypi_release.py`: inspect artifacts, query PyPI, plan/stage uploads, emit a manifest, and verify published hashes.
- `tests/test_pypi_release.py`: cover first publish, idempotency, conflicts, partial recovery, malformed responses, metadata mismatch, and retry behavior.
- `.github/workflows/release.yml`: enforce main ancestry and add push and dispatch PyPI publication pipelines.
- `README.md`: make PyPI installation and the `papersqueeze` command the primary user flow.
- `CONTRIBUTING.md`: document pending publisher setup, release sequencing, and tag-based repair.
- `CHANGELOG.md`: add the `0.2.0` release and links.

### Task 1: Rename the distribution and add a compatible CLI alias

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/file_compressor/cli.py:23-26`
- Modify: `tests/test_cli.py`
- Create: `tests/test_package_metadata.py`

- [ ] **Step 1: Add failing metadata and CLI-name tests**

Add to `tests/test_cli.py`:

```python
def test_parser_uses_requested_program_name(capsys: pytest.CaptureFixture[str]):
    parser = build_parser(prog="papersqueeze")

    with pytest.raises(SystemExit, match="0"):
        parser.parse_args(["--help"])

    assert capsys.readouterr().out.startswith("usage: papersqueeze")


def test_parser_keeps_legacy_program_name(capsys: pytest.CaptureFixture[str]):
    parser = build_parser(prog="file-compressor")

    with pytest.raises(SystemExit, match="0"):
        parser.parse_args(["--help"])

    assert capsys.readouterr().out.startswith("usage: file-compressor")
```

Create `tests/test_package_metadata.py`:

```python
from pathlib import Path
import tomllib


def test_public_distribution_metadata():
    with Path("pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]

    assert project["name"] == "papersqueeze"
    assert project["version"] == "0.2.0"
    assert project["scripts"] == {
        "papersqueeze": "file_compressor.cli:main",
        "file-compressor": "file_compressor.cli:main",
    }


def test_import_package_stays_compatible():
    with Path("pyproject.toml").open("rb") as file:
        metadata = tomllib.load(file)

    assert metadata["tool"]["setuptools"]["package-data"].keys() == {"file_compressor"}
```

- [ ] **Step 2: Run the focused tests and confirm the intended failures**

Run:

```bash
uv run pytest -q \
  tests/test_cli.py::test_parser_uses_requested_program_name \
  tests/test_cli.py::test_parser_keeps_legacy_program_name \
  tests/test_package_metadata.py
```

Expected: failures because `build_parser` has no `prog` parameter and metadata still says `pdf-image-compressor` `0.1.0`.

- [ ] **Step 3: Implement the minimal compatibility changes**

Change the parser factory to:

```python
def build_parser(*, prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
```

Update `pyproject.toml`:

```toml
[project]
name = "papersqueeze"
version = "0.2.0"

[project.scripts]
papersqueeze = "file_compressor.cli:main"
file-compressor = "file_compressor.cli:main"
```

Keep the source package and package-data key named `file_compressor`.

- [ ] **Step 4: Regenerate the lockfile**

Run:

```bash
uv lock
```

Expected: `uv.lock` records the root package as `papersqueeze==0.2.0` without changing unrelated dependency versions.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest -q tests/test_cli.py tests/test_package_metadata.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit the distribution compatibility change**

```bash
git add pyproject.toml uv.lock src/file_compressor/cli.py \
  tests/test_cli.py tests/test_package_metadata.py
git commit -m "feat: publish under the PaperSqueeze package name"
```

### Task 2: Build a tested PyPI artifact preflight and verification helper

**Files:**
- Create: `scripts/pypi_release.py`
- Create: `tests/test_pypi_release.py`

- [ ] **Step 1: Write failing tests for distribution inspection**

In `tests/test_pypi_release.py`, build tiny synthetic wheel and sdist fixtures
containing `METADATA` and `PKG-INFO`. Test that:

```python
def test_collect_distributions_accepts_matching_wheel_and_sdist(...):
    files = pypi_release.collect_distributions(
        source_dir,
        project="papersqueeze",
        version="0.2.0",
    )
    assert {item.filename for item in files} == {
        "papersqueeze-0.2.0-py3-none-any.whl",
        "papersqueeze-0.2.0.tar.gz",
    }


@pytest.mark.parametrize(
    ("metadata_name", "metadata_version"),
    [("other-project", "0.2.0"), ("papersqueeze", "9.9.9")],
)
def test_collect_distributions_rejects_metadata_mismatch(...):
    with pytest.raises(pypi_release.ReleaseStateError):
        pypi_release.collect_distributions(...)
```

Also cover duplicate wheels, missing sdists, malformed metadata, and unsafe/multiple metadata members.

- [ ] **Step 2: Run the inspection tests and confirm they fail**

Run:

```bash
uv run pytest -q tests/test_pypi_release.py -k "collect_distributions"
```

Expected: import or attribute failure because the helper does not exist.

- [ ] **Step 3: Implement artifact inspection and hashing**

Create `scripts/pypi_release.py` with:

```python
@dataclass(frozen=True)
class DistributionFile:
    path: Path
    filename: str
    sha256: str


class ReleaseStateError(RuntimeError):
    pass
```

Implement:

- `normalize_project_name(value)` using `re.sub(r"[-_.]+", "-", value).lower()`;
- `sha256_file(path)` using chunked reads;
- wheel metadata reading from exactly one `*.dist-info/METADATA` member;
- sdist metadata reading from exactly one top-level `*/PKG-INFO` member;
- `email.parser.BytesParser(policy=email.policy.default)` for `Name` and `Version`;
- `collect_distributions(source, project, version)` requiring exactly one wheel and one `.tar.gz`, matching normalized name and exact version.

Do not extract archive members to disk and do not execute artifact code.

- [ ] **Step 4: Run inspection tests**

Run:

```bash
uv run pytest -q tests/test_pypi_release.py -k "collect_distributions"
```

Expected: all inspection tests pass.

- [ ] **Step 5: Write failing tests for PyPI state and staging**

Add fake `urlopen` responses and errors covering:

- target-version HTTP 404 returns an empty remote mapping;
- valid JSON maps `urls[].filename` to `urls[].digests.sha256`;
- HTTP 500, timeout, malformed JSON, missing `urls`, or invalid digest raises `ReleaseStateError`;
- identical remote files stage nothing;
- a partial remote version stages only the missing file;
- same filename with another hash fails;
- a new version stages both files;
- prepare emits a manifest with every expected filename/hash and a `publish=true|false` GitHub output.

- [ ] **Step 6: Implement version-specific lookup and upload planning**

Use only the standard library. Query:

```text
https://pypi.org/pypi/{quoted_project}/{quoted_version}/json
```

Treat only `urllib.error.HTTPError(code=404)` as an absent version. Convert other
HTTP/network/timeout/JSON/schema failures to `ReleaseStateError`.

Implement pure upload planning:

```python
def files_to_upload(
    local: Sequence[DistributionFile],
    remote: Mapping[str, str],
) -> list[DistributionFile]:
    ...
```

If a remote filename matches but its digest differs, fail immediately. Copy only
missing distributions into a clean staging directory. Write a schema-versioned
JSON manifest containing project, version, and all expected local hashes.

- [ ] **Step 7: Write failing post-publication verification tests**

Cover:

- all expected hashes present on the first query;
- missing files followed by success after bounded retries;
- missing files for every attempt raises;
- any observed conflicting digest raises immediately;
- malformed/network failures are retried only up to the configured limit;
- injected `sleep` receives the expected delay and tests never actually wait.

- [ ] **Step 8: Implement bounded remote verification and CLI subcommands**

Expose:

```bash
python3 -I scripts/pypi_release.py prepare \
  --source dist \
  --staging pypi-dist \
  --project papersqueeze \
  --version 0.2.0 \
  --manifest pypi-manifest.json \
  --github-output "$GITHUB_OUTPUT"

python3 -I scripts/pypi_release.py verify \
  --manifest pypi-manifest.json \
  --attempts 6 \
  --delay-seconds 10
```

`verify` must compare every expected filename and digest. Missing files may retry;
a conflicting digest fails without retry. Use a fixed or bounded delay, not
unbounded exponential sleep.

- [ ] **Step 9: Run all helper tests and lint**

Run:

```bash
uv run pytest -q tests/test_pypi_release.py
uv run ruff check scripts/pypi_release.py tests/test_pypi_release.py
uv run ruff format --check scripts/pypi_release.py tests/test_pypi_release.py
```

Expected: all pass.

- [ ] **Step 10: Commit the helper**

```bash
git add scripts/pypi_release.py tests/test_pypi_release.py
git commit -m "feat: verify PyPI release artifacts"
```

### Task 3: Add least-privilege PyPI publishing to tag releases

**Files:**
- Modify: `.github/workflows/release.yml`
- Test: `tests/test_pypi_release.py`

- [ ] **Step 1: Require release tags to point into `main` history**

After checking out the tag in `validate`, fetch the public main ref and verify:

```bash
git fetch --no-tags origin main:refs/remotes/origin/main
if ! git merge-base --is-ancestor HEAD origin/main; then
  echo "$RELEASE_TAG is not reachable from main; refusing to release." >&2
  exit 1
fi
```

Keep tag/version validation under `python3 -I -`.

- [ ] **Step 2: Add the read-only push preflight job**

Add `pypi_preflight` after both `validate` and `publish` with:

- `if: github.event_name == 'push'`;
- `permissions: contents: read`;
- checkout of the exact tag with credentials disabled;
- download of `python-distributions`;
- `prepare` invocation for `papersqueeze` and `${RELEASE_TAG#v}`;
- an always-uploaded manifest artifact;
- a package artifact uploaded only when `publish=true`;
- a job output carrying that boolean.

Do not grant `id-token`, `contents: write`, or `packages: write`.

- [ ] **Step 3: Add the minimal OIDC publication job**

Add `pypi_publish`:

```yaml
needs: pypi_preflight
if: needs.pypi_preflight.outputs.publish == 'true'
runs-on: ubuntu-latest
environment:
  name: pypi
  url: https://pypi.org/p/papersqueeze
permissions:
  id-token: write
```

Its only steps are downloading the staged distributions and:

```yaml
- name: Publish distributions to PyPI
  uses: pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # release/v1
  with:
    packages-dir: pypi-dist/
    skip-existing: true
```

Do not check out the repository or run shell/Python in this job.

- [ ] **Step 4: Add read-only post-publication verification**

Add `pypi_verify` with `needs: [pypi_preflight, pypi_publish]` and:

```yaml
if: >-
  always() &&
  needs.pypi_preflight.result == 'success' &&
  (needs.pypi_publish.result == 'success' ||
   needs.pypi_publish.result == 'skipped')
```

Use `contents: read`, check out the exact tag, download the manifest, and run the
bounded `verify` command. This job must run for both a new upload and an already
complete version.

- [ ] **Step 5: Validate workflow structure**

Run an installed `actionlint` if available:

```bash
actionlint .github/workflows/release.yml
```

If unavailable, run the repository's YAML/workflow checks and install a pinned
local `actionlint` binary only after informing the user. Expected: no findings.

- [ ] **Step 6: Commit the tag publication path**

```bash
git add .github/workflows/release.yml
git commit -m "ci: publish releases to PyPI with OIDC"
```

### Task 4: Add a safe manual PyPI repair path

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `tests/test_pypi_release.py`

- [ ] **Step 1: Add an explicit repair input and tag-ref guard**

Add:

```yaml
repair_pypi:
  description: Repair the PyPI publication from existing GitHub Release assets
  required: false
  default: false
  type: boolean
```

When it is true, require:

```bash
[[ "$GITHUB_REF" == "refs/tags/$RELEASE_TAG" ]] || {
  echo "PyPI repair must be dispatched from ref $RELEASE_TAG." >&2
  exit 1
}
```

Document that ordinary checksum/GHCR backfills can still run without this input.

- [ ] **Step 2: Add a read-only repair preflight**

After `resolve_backfill` and `backfill` succeed, conditionally:

1. check out `refs/tags/${{ inputs.tag }}` with credentials disabled;
2. use read-only `GITHUB_TOKEN`/`gh release view` to require exactly one wheel and one sdist;
3. download both assets;
4. compare their bytes with GitHub's recorded `sha256:` asset digests;
5. run `pypi_release.py prepare`, which also rejects any distribution whose metadata name is not `papersqueeze` or whose version differs from the tag;
6. upload the repair manifest and only missing distributions as Actions artifacts.

Never rebuild or import code from the released artifacts.

- [ ] **Step 3: Add minimal repair publish and verification jobs**

Mirror the normal minimal OIDC publication job, but run only when
`inputs.repair_pypi == true` and the repair preflight reports missing files.
Then run the same read-only bounded digest verification whether publication was
needed or skipped.

The OIDC job must still contain no checkout or shell step.

- [ ] **Step 4: Test old and mismatched distributions are rejected**

Extend helper tests so `pdf_image_compressor-0.1.0` metadata and any wrong tag
version fail before a repair artifact can reach the OIDC job.

Run:

```bash
uv run pytest -q tests/test_pypi_release.py
actionlint .github/workflows/release.yml
```

Expected: all pass.

- [ ] **Step 5: Commit the repair path**

```bash
git add .github/workflows/release.yml tests/test_pypi_release.py
git commit -m "ci: repair partial PyPI releases safely"
```

### Task 5: Document the public package and maintainer setup

**Files:**
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update end-user installation and command examples**

Add a PyPI version badge and make this the primary install path:

```bash
uv tool install papersqueeze
papersqueeze --help
papersqueeze web
```

Also show `uv add papersqueeze` for library users and state that imports remain
under `file_compressor`. Keep the GitHub wheel instructions as an alternative,
but update their filename glob to `papersqueeze-*.whl`.

Change primary CLI examples from `uv run file-compressor` to
`uv run papersqueeze`; mention the old command remains supported.

- [ ] **Step 2: Document the one-time Trusted Publisher setup**

In `CONTRIBUTING.md`, list these exact PyPI pending publisher fields:

```text
PyPI project: papersqueeze
Owner: asimfish
Repository: pdf-image-compressor
Workflow: release.yml
Environment: pypi
```

Document the GitHub `pypi` environment, version-tag restrictions, optional
required reviewer, and the release order: merge, wait for main CI, verify the
publisher configuration, then push the annotated tag.

Document repair:

```bash
gh workflow run release.yml \
  --ref v0.2.0 \
  -f tag=v0.2.0 \
  -f repair_pypi=true
```

- [ ] **Step 3: Finalize the `0.2.0` changelog**

Move current unreleased release-automation entries into `0.2.0`, add the PyPI
distribution rename, CLI alias, OIDC publishing, digest verification, and repair
path, then update links:

```markdown
[Unreleased]: https://github.com/asimfish/pdf-image-compressor/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/asimfish/pdf-image-compressor/compare/v0.1.0...v0.2.0
```

- [ ] **Step 4: Review documentation commands against actual metadata**

Verify every install command, executable name, version, workflow input, and URL
matches `pyproject.toml` and `.github/workflows/release.yml`.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md CONTRIBUTING.md CHANGELOG.md
git commit -m "docs: publish PaperSqueeze installation guide"
```

### Task 6: Verify, review, and prepare the `v0.2.0` release

**Files:**
- Verify all files changed above
- Do not create the tag until the PyPI pending publisher and GitHub environment exist

- [ ] **Step 1: Run code quality and dependency checks**

```bash
uv run ruff check src tests scripts deploy_huggingface_space.py deploy_modal.py
uv run ruff format --check src tests scripts deploy_huggingface_space.py deploy_modal.py
uv run pip-audit --local --skip-editable
```

Expected: zero findings.

- [ ] **Step 2: Run the full test suite**

```bash
uv run python -m pytest -q
```

Expected: all tests pass with no warnings promoted to errors.

- [ ] **Step 3: Build and validate distributions**

```bash
rm -rf dist
uv build --no-build-isolation
uv run twine check dist/*
```

Expected: exactly one `papersqueeze-0.2.0-*.whl`, one
`papersqueeze-0.2.0.tar.gz`, and both pass `twine check`.

- [ ] **Step 4: Smoke-test both commands from the built wheel**

Create an isolated temporary virtual environment, install only the wheel, and
run:

```bash
papersqueeze --help
file-compressor --help
```

Expected: both exit 0; each usage line displays the invoked command name.
Remove the temporary environment afterward.

- [ ] **Step 5: Run release helper against the real local artifacts**

Run `prepare` with a temporary staging directory and manifest. Because
`papersqueeze 0.2.0` should still be absent before release, expect both files to
be staged. Do not invoke the publish action locally.

- [ ] **Step 6: Run required reviews**

Run the general code review, Python review, and security review over the full
branch diff. Fix every actionable finding, then rerun affected tests and reviews
until clean.

- [ ] **Step 7: Push and open a pull request**

Push `feat/pypi-trusted-publishing`, create a PR against `main`, include the
test/build evidence and the required one-time maintainer setup, and wait for all
CI checks to pass.

- [ ] **Step 8: Complete the manual trust setup**

Before tagging, the maintainer must:

1. create/confirm a PyPI account with 2FA;
2. add the pending trusted publisher using the exact five fields above;
3. create the GitHub `pypi` environment and restrict it to version tags;
4. optionally require manual deployment approval;
5. revoke the two previously exposed Modal tokens and create a clean replacement.

- [ ] **Step 9: Merge, tag, and verify public publication**

After setup and green `main` CI:

```bash
git tag -a v0.2.0 -m "PaperSqueeze v0.2.0"
git push origin v0.2.0
```

Verify:

- the Release workflow is green;
- the GitHub Release contains wheel, sdist, and `SHA256SUMS`;
- GHCR `v0.2.0` and `0.2.0` match the tested commit image;
- `https://pypi.org/project/papersqueeze/0.2.0/` lists both files with expected hashes;
- `uv tool install papersqueeze==0.2.0` works in a clean environment;
- the Modal health endpoint remains healthy.
