"""
tests/test_pypi_release.py
TDD suite for scripts/pypi_release.py

Covers all 10 required test categories:
 1. Valid wheel+sdist inspection and hashes
 2. Wrong name/version, duplicates, malformed/multiple metadata rejection
 3. First-release 404 -> empty mapping, both files staged
 4. Full identical version -> no staging / publish=false
 5. Partial identical version -> only missing file staged
 6. Conflicting digest -> hard failure
 7. HTTP 500, URLError, malformed JSON, missing/wrong schema, duplicate conflict
 8. Manifest has all expected hashes; GitHub output is correct
 9. Verify: immediate success, stale->success, exhaustion, transient retry,
    immediate conflict; assert sleep calls without real delays
10. CLI argument / error behaviour
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import stat
import sys
import tarfile
import urllib.error
import zipfile
import zlib
from pathlib import Path

import pytest

# Make scripts/ importable without modifying project configuration.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import pypi_release as release_module  # noqa: E402

from pypi_release import (  # noqa: E402
    DistributionFile,
    ReleaseStateError,
    collect_distributions,
    files_to_upload,
    main,
    normalize_project_name,
    prepare,
    query_pypi_version,
    sha256_file,
    verify,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_wheel(
    name: str = "papersqueeze",
    version: str = "0.2.0",
    *,
    dist_tag: str = "py3-none-any",
    metadata_extra: str = "",
    extra_entries: dict[str, bytes] | None = None,
    duplicate_metadata: bool = False,
) -> bytes:
    """Build a minimal wheel (ZIP) with a single *.dist-info/METADATA entry."""
    buf = io.BytesIO()
    metadata_content = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{metadata_extra}"
    )
    dist_info = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{dist_info}/METADATA", metadata_content)
        if duplicate_metadata:
            zf.writestr("other-1.0.dist-info/METADATA", metadata_content)
        if extra_entries:
            for path, data in extra_entries.items():
                zf.writestr(path, data)
    return buf.getvalue()


def _make_sdist(
    name: str = "papersqueeze",
    version: str = "0.2.0",
    *,
    metadata_extra: str = "",
    extra_members: list[tuple[str, bytes]] | None = None,
    duplicate_pkg_info: bool = False,
) -> bytes:
    """Build a minimal sdist (tar.gz) with a single top-level PKG-INFO."""
    buf = io.BytesIO()
    pkg_info_content = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{metadata_extra}"
    ).encode()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:

        def _add(member_name: str, data: bytes) -> None:
            info = tarfile.TarInfo(member_name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        _add(f"{name}-{version}/PKG-INFO", pkg_info_content)
        if duplicate_pkg_info:
            _add(f"other-{version}/PKG-INFO", pkg_info_content)
        if extra_members:
            for member_name, data in extra_members:
                _add(member_name, data)
    return buf.getvalue()


def _make_wheel_with_metadata(
    metadata: bytes,
    *,
    metadata_path: str = "papersqueeze-0.2.0.dist-info/METADATA",
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(metadata_path, metadata)
    return buf.getvalue()


def _make_wheel_with_metadata_mode(mode: int) -> bytes:
    metadata = b"Metadata-Version: 2.1\nName: papersqueeze\nVersion: 0.2.0\n"
    metadata_info = zipfile.ZipInfo("papersqueeze-0.2.0.dist-info/METADATA")
    metadata_info.create_system = 3
    metadata_info.external_attr = mode << 16
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(metadata_info, metadata)
    return buf.getvalue()


def _make_sdist_with_metadata(
    metadata: bytes,
    *,
    metadata_type: bytes = tarfile.REGTYPE,
    linkname: str = "",
    metadata_path: str = "papersqueeze-0.2.0/PKG-INFO",
) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        if linkname:
            target = tarfile.TarInfo(linkname)
            target.size = len(metadata)
            tf.addfile(target, io.BytesIO(metadata))

        info = tarfile.TarInfo(metadata_path)
        info.type = metadata_type
        info.linkname = linkname
        if info.isfile():
            info.size = len(metadata)
            tf.addfile(info, io.BytesIO(metadata))
        else:
            tf.addfile(info)
    return buf.getvalue()


def _write_dist(
    directory: Path,
    wheel_name: str = "papersqueeze-0.2.0-py3-none-any.whl",
    sdist_name: str = "papersqueeze-0.2.0.tar.gz",
    wheel_data: bytes | None = None,
    sdist_data: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Write wheel+sdist to directory; return (wheel_data, sdist_data)."""
    if wheel_data is None:
        wheel_data = _make_wheel()
    if sdist_data is None:
        sdist_data = _make_sdist()
    (directory / wheel_name).write_bytes(wheel_data)
    (directory / sdist_name).write_bytes(sdist_data)
    return wheel_data, sdist_data


def _mock_fetch_json(payload: dict):
    """Return a _fetch callable that always returns the given JSON dict."""

    def _fetch(url: str) -> bytes:
        return json.dumps(payload).encode()

    return _fetch


def _mock_fetch_http_error(code: int, reason: str = "Error"):
    def _fetch(url: str) -> bytes:
        raise urllib.error.HTTPError(url, code, reason, {}, None)

    return _fetch


def _remote_state(wheel_sha: str, sdist_sha: str) -> dict:
    return {
        "urls": [
            {
                "filename": "papersqueeze-0.2.0-py3-none-any.whl",
                "digests": {"sha256": wheel_sha},
            },
            {
                "filename": "papersqueeze-0.2.0.tar.gz",
                "digests": {"sha256": sdist_sha},
            },
        ]
    }


# ── 1. Valid wheel+sdist inspection and hashes ───────────────────────────────


def test_collect_distributions_valid(tmp_path):
    """collect_distributions returns one wheel and one sdist with correct hashes."""
    wdata, sdata = _write_dist(tmp_path)
    files = collect_distributions(tmp_path, "papersqueeze", "0.2.0")

    assert len(files) == 2
    wf = next(f for f in files if f.filename.endswith(".whl"))
    sf = next(f for f in files if f.filename.endswith(".tar.gz"))

    assert wf.filename == "papersqueeze-0.2.0-py3-none-any.whl"
    assert sf.filename == "papersqueeze-0.2.0.tar.gz"
    assert wf.sha256 == _sha256_bytes(wdata)
    assert sf.sha256 == _sha256_bytes(sdata)
    assert len(wf.sha256) == 64
    assert all(c in "0123456789abcdef" for c in wf.sha256)


def test_distribution_file_is_immutable(tmp_path):
    """DistributionFile must be frozen (immutable)."""
    f = tmp_path / "x.whl"
    f.write_bytes(b"x")
    df = DistributionFile(path=f, filename="x.whl", sha256="a" * 64)
    with pytest.raises(AttributeError):
        df.sha256 = "changed"  # type: ignore[misc]


def test_sha256_file_correctness(tmp_path):
    data = b"hello world" * 1000
    p = tmp_path / "f.bin"
    p.write_bytes(data)
    assert sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_normalize_project_name():
    assert normalize_project_name("papersqueeze") == "papersqueeze"
    assert normalize_project_name("PaperSqueeze") == "papersqueeze"
    assert normalize_project_name("paper_squeeze") == "paper-squeeze"
    assert normalize_project_name("paper--squeeze") == "paper-squeeze"
    assert normalize_project_name("my.project_name") == "my-project-name"
    assert normalize_project_name("MY_PROJECT") == "my-project"


def test_collect_case_insensitive_name_match(tmp_path):
    """Normalized metadata name matching project name succeeds."""
    wdata = _make_wheel(name="PaperSqueeze")
    sdata = _make_sdist(name="PaperSqueeze")
    _write_dist(tmp_path, wheel_data=wdata, sdist_data=sdata)
    files = collect_distributions(tmp_path, "papersqueeze", "0.2.0")
    assert len(files) == 2


def test_collect_parses_raw_metadata_bytes_with_default_email_policy(tmp_path):
    """RFC 2047 metadata headers are decoded from raw bytes by BytesParser."""
    metadata = (
        b"Metadata-Version: 2.1\nName: =?utf-8?q?PaperSqueeze?=\nVersion: 0.2.0\n"
    )
    _write_dist(
        tmp_path,
        wheel_data=_make_wheel_with_metadata(metadata),
        sdist_data=_make_sdist_with_metadata(metadata),
    )

    files = collect_distributions(tmp_path, "papersqueeze", "0.2.0")

    assert len(files) == 2


# ── 2. Rejection: wrong name/version, duplicates, malformed metadata ─────────


def test_collect_wrong_wheel_name(tmp_path):
    wdata = _make_wheel(name="wrong-project")
    _write_dist(tmp_path, wheel_data=wdata)
    with pytest.raises(ReleaseStateError, match="[Nn]ame"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_wrong_wheel_version(tmp_path):
    wdata = _make_wheel(version="9.9.9")
    _write_dist(tmp_path, wheel_data=wdata)
    with pytest.raises(ReleaseStateError, match="[Vv]ersion"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_wrong_sdist_name(tmp_path):
    sdata = _make_sdist(name="wrong-project")
    _write_dist(tmp_path, sdist_data=sdata)
    with pytest.raises(ReleaseStateError, match="[Nn]ame"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_wrong_sdist_version(tmp_path):
    sdata = _make_sdist(version="9.9.9")
    _write_dist(tmp_path, sdist_data=sdata)
    with pytest.raises(ReleaseStateError, match="[Vv]ersion"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_missing_wheel(tmp_path):
    """No wheel file -> ReleaseStateError."""
    sdata = _make_sdist()
    (tmp_path / "papersqueeze-0.2.0.tar.gz").write_bytes(sdata)
    with pytest.raises(ReleaseStateError, match="[Ww]heel"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_missing_sdist(tmp_path):
    """No sdist -> ReleaseStateError."""
    wdata = _make_wheel()
    (tmp_path / "papersqueeze-0.2.0-py3-none-any.whl").write_bytes(wdata)
    with pytest.raises(ReleaseStateError, match="[Ss]dist|tar"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_duplicate_wheel(tmp_path):
    wdata = _make_wheel()
    (tmp_path / "papersqueeze-0.2.0-py3-none-any.whl").write_bytes(wdata)
    (tmp_path / "papersqueeze-0.2.0-cp310-cp310-linux.whl").write_bytes(wdata)
    (tmp_path / "papersqueeze-0.2.0.tar.gz").write_bytes(_make_sdist())
    with pytest.raises(ReleaseStateError, match="[Mm]ultiple"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_duplicate_sdist(tmp_path):
    sdata = _make_sdist()
    (tmp_path / "papersqueeze-0.2.0-py3-none-any.whl").write_bytes(_make_wheel())
    (tmp_path / "papersqueeze-0.2.0.tar.gz").write_bytes(sdata)
    (tmp_path / "papersqueeze-0.2.0.zip").write_bytes(sdata)  # not .tar.gz
    # Only one .tar.gz -> still valid unless we add another .tar.gz
    (tmp_path / "papersqueeze-0.2.0b1.tar.gz").write_bytes(sdata)
    with pytest.raises(ReleaseStateError, match="[Mm]ultiple"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_malformed_wheel_metadata(tmp_path):
    """METADATA without Name header is rejected."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("papersqueeze-0.2.0.dist-info/METADATA", "Metadata-Version: 2.1\n")
    wdata = buf.getvalue()
    _write_dist(tmp_path, wheel_data=wdata)
    with pytest.raises(ReleaseStateError, match="[Nn]ame"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_multiple_dist_info_metadata_in_wheel(tmp_path):
    """Multiple *.dist-info/METADATA entries are rejected."""
    wdata = _make_wheel(duplicate_metadata=True)
    _write_dist(tmp_path, wheel_data=wdata)
    with pytest.raises(ReleaseStateError, match="[Mm]ultiple"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_multiple_top_level_pkg_info_in_sdist(tmp_path):
    """Multiple top-level */PKG-INFO entries in sdist are rejected."""
    sdata = _make_sdist(duplicate_pkg_info=True)
    _write_dist(tmp_path, sdist_data=sdata)
    with pytest.raises(ReleaseStateError, match="[Mm]ultiple"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_missing_pkg_info_in_sdist(tmp_path):
    """sdist without top-level PKG-INFO is rejected."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("papersqueeze-0.2.0/src/nested/PKG-INFO")
        data = b"Metadata-Version: 2.1\nName: papersqueeze\nVersion: 0.2.0\n"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    sdata = buf.getvalue()
    _write_dist(tmp_path, sdist_data=sdata)
    with pytest.raises(ReleaseStateError, match="PKG-INFO"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_wheel_unsafe_absolute_path(tmp_path):
    """Wheel with absolute-path entry is rejected."""
    wdata = _make_wheel(extra_entries={"/etc/passwd": b"evil"})
    _write_dist(tmp_path, wheel_data=wdata)
    with pytest.raises(ReleaseStateError, match="[Uu]nsafe|[Aa]bsolute"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_wheel_dotdot_path(tmp_path):
    """Wheel with path-traversal entry is rejected."""
    wdata = _make_wheel(extra_entries={"../evil": b"bad"})
    _write_dist(tmp_path, wheel_data=wdata)
    with pytest.raises(ReleaseStateError, match="[Uu]nsafe"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_accepts_wheel_metadata_with_unset_file_type_mode(tmp_path):
    """Ordinary ZIP entries without Unix file-type bits remain valid."""
    _write_dist(tmp_path, wheel_data=_make_wheel_with_metadata_mode(0))

    files = collect_distributions(tmp_path, "papersqueeze", "0.2.0")

    assert len(files) == 2


def test_collect_rejects_wheel_symlink_metadata(tmp_path):
    """A Unix symlink masquerading as METADATA is never followed or accepted."""
    mode = stat.S_IFLNK | 0o777
    _write_dist(tmp_path, wheel_data=_make_wheel_with_metadata_mode(mode))

    with pytest.raises(ReleaseStateError, match="[Rr]egular|[Ss]ymlink"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_rejects_wheel_special_file_metadata(tmp_path):
    """Non-regular Unix special modes such as FIFOs are rejected."""
    mode = stat.S_IFIFO | 0o600
    _write_dist(tmp_path, wheel_data=_make_wheel_with_metadata_mode(mode))

    with pytest.raises(ReleaseStateError, match="[Rr]egular|[Ss]pecial"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


@pytest.mark.parametrize(
    "metadata_path",
    [
        "papersqueeze-0.2.0.dist-info/METADATA\n",
        " papersqueeze-0.2.0.dist-info/METADATA",
        "papersqueeze-0.2.0.dist-info/METADATA ",
        "papersqueeze-0.2.0.dist-info//METADATA",
        "evil\\papersqueeze-0.2.0.dist-info/METADATA",
        "./papersqueeze-0.2.0.dist-info/METADATA",
        "\x01papersqueeze-0.2.0.dist-info/METADATA",
    ],
)
def test_collect_rejects_noncanonical_wheel_metadata_path(tmp_path, metadata_path):
    metadata = b"Metadata-Version: 2.1\nName: papersqueeze\nVersion: 0.2.0\n"
    wheel_data = _make_wheel_with_metadata(
        metadata,
        metadata_path=metadata_path,
    )
    _write_dist(tmp_path, wheel_data=wheel_data)

    with pytest.raises(ReleaseStateError, match="METADATA"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


@pytest.mark.parametrize(
    "read_error",
    [
        zlib.error("corrupt deflate stream"),
        zipfile.BadZipFile("Bad CRC-32 for METADATA"),
        EOFError("truncated compressed data"),
    ],
)
def test_collect_corrupt_wheel_metadata_read_is_release_error(
    tmp_path, monkeypatch, read_error
):
    _write_dist(tmp_path)

    def _fail_metadata_read(archive, member, *args, **kwargs):
        raise read_error

    monkeypatch.setattr(zipfile.ZipFile, "read", _fail_metadata_read)

    with pytest.raises(ReleaseStateError, match="[Ww]heel metadata"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_sdist_dotdot_path(tmp_path):
    """sdist with path-traversal member is rejected."""
    sdata = _make_sdist(extra_members=[("papersqueeze-0.2.0/../evil.txt", b"bad")])
    _write_dist(tmp_path, sdist_data=sdata)
    with pytest.raises(ReleaseStateError, match="[Uu]nsafe"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


@pytest.mark.parametrize(
    "metadata_path",
    [
        "papersqueeze-0.2.0/PKG-INFO\n",
        " papersqueeze-0.2.0/PKG-INFO",
        "papersqueeze-0.2.0/PKG-INFO ",
        "papersqueeze-0.2.0//PKG-INFO",
        "evil\\papersqueeze-0.2.0/PKG-INFO",
        "./papersqueeze-0.2.0/PKG-INFO",
        "\x01papersqueeze-0.2.0/PKG-INFO",
    ],
)
def test_collect_rejects_noncanonical_sdist_metadata_path(tmp_path, metadata_path):
    metadata = b"Metadata-Version: 2.1\nName: papersqueeze\nVersion: 0.2.0\n"
    sdist_data = _make_sdist_with_metadata(
        metadata,
        metadata_path=metadata_path,
    )
    _write_dist(tmp_path, sdist_data=sdist_data)

    with pytest.raises(ReleaseStateError, match="PKG-INFO"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_source_stat_failure_is_release_error(tmp_path):
    """Source stat failures include source-path action context."""

    class StatFailingPath(type(Path())):
        def stat(self, *, follow_symlinks=True):
            raise PermissionError("stat denied")

    source = StatFailingPath(tmp_path)

    with pytest.raises(ReleaseStateError, match="[Ii]nspect.*source|[Ss]tat"):
        collect_distributions(source, "papersqueeze", "0.2.0")


def test_collect_source_iteration_failure_is_release_error(tmp_path):
    """Source directory iteration failures never leak raw OSError."""

    class GlobFailingPath(type(Path())):
        def glob(self, pattern):
            raise PermissionError("iteration denied")

    source = GlobFailingPath(tmp_path)

    with pytest.raises(ReleaseStateError, match="[Dd]iscover|[Ii]terat"):
        collect_distributions(source, "papersqueeze", "0.2.0")


def test_collect_hash_oserror_is_release_error(tmp_path, monkeypatch):
    """Hash open/read failures include the affected artifact path."""
    _write_dist(tmp_path)

    def _fail_hash(path):
        raise PermissionError("hash read denied")

    monkeypatch.setattr(release_module, "sha256_file", _fail_hash)

    with pytest.raises(ReleaseStateError, match="[Hh]ash.*papersqueeze"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


def test_collect_artifact_disappearing_before_hash_is_release_error(
    tmp_path, monkeypatch
):
    """An artifact removed after inspection is reported as a hash input error."""
    _write_dist(tmp_path)
    original_sha256_file = release_module.sha256_file

    def _remove_then_hash(path):
        if path.suffix == ".whl":
            path.unlink()
        return original_sha256_file(path)

    monkeypatch.setattr(release_module, "sha256_file", _remove_then_hash)

    with pytest.raises(ReleaseStateError, match="[Hh]ash.*\\.whl"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


@pytest.mark.parametrize(
    ("metadata_type", "kind"),
    [
        (tarfile.SYMTYPE, "symlink"),
        (tarfile.LNKTYPE, "hardlink"),
    ],
)
def test_collect_rejects_linked_sdist_metadata(tmp_path, metadata_type, kind):
    """PKG-INFO must be a regular file, never a symlink or hardlink."""
    metadata = b"Metadata-Version: 2.1\nName: papersqueeze\nVersion: 0.2.0\n"
    sdata = _make_sdist_with_metadata(
        metadata,
        metadata_type=metadata_type,
        linkname="metadata-target",
    )
    _write_dist(tmp_path, sdist_data=sdata)

    with pytest.raises(ReleaseStateError, match="[Rr]egular"):
        collect_distributions(tmp_path, "papersqueeze", "0.2.0")


# ── 3. First-release 404 -> both files staged ─────────────────────────────────


def test_query_pypi_version_404_returns_empty(tmp_path):
    """HTTP 404 from PyPI signals version absent; returns empty mapping."""
    result = query_pypi_version(
        "papersqueeze", "0.2.0", _fetch=_mock_fetch_http_error(404)
    )
    assert result == {}


def test_prepare_first_release_stages_both(tmp_path):
    """When PyPI has no release yet, both wheel and sdist are staged."""
    src = tmp_path / "dist"
    src.mkdir()
    wdata, sdata = _write_dist(src)

    staging = tmp_path / "staging"
    manifest_path = tmp_path / "manifest.json"

    prepare(
        source=src,
        project="papersqueeze",
        version="0.2.0",
        staging=staging,
        manifest_path=manifest_path,
        _fetch=_mock_fetch_http_error(404),
    )

    staged = {p.name for p in staging.iterdir()}
    assert "papersqueeze-0.2.0-py3-none-any.whl" in staged
    assert "papersqueeze-0.2.0.tar.gz" in staged
    assert len(staged) == 2


# ── 4. Full identical version -> no staging / publish=false ──────────────────


def test_prepare_full_identical_no_staging(tmp_path):
    """When all local hashes match remote, nothing is staged, publish=false."""
    src = tmp_path / "dist"
    src.mkdir()
    wdata, sdata = _write_dist(src)
    wsha = _sha256_bytes(wdata)
    ssha = _sha256_bytes(sdata)

    staging = tmp_path / "staging"
    manifest_path = tmp_path / "manifest.json"
    gho = tmp_path / "gho"
    gho.write_text("")

    prepare(
        source=src,
        project="papersqueeze",
        version="0.2.0",
        staging=staging,
        manifest_path=manifest_path,
        github_output=gho,
        _fetch=_mock_fetch_json(_remote_state(wsha, ssha)),
    )

    assert staging.exists()
    assert list(staging.iterdir()) == []
    assert "publish=false" in gho.read_text()


# ── 5. Partial identical version -> only missing file staged ─────────────────


def test_prepare_partial_stages_only_missing(tmp_path):
    """When wheel is already on PyPI, only sdist is staged."""
    src = tmp_path / "dist"
    src.mkdir()
    wdata, sdata = _write_dist(src)
    wsha = _sha256_bytes(wdata)

    staging = tmp_path / "staging"
    manifest_path = tmp_path / "manifest.json"

    remote = {
        "urls": [
            {
                "filename": "papersqueeze-0.2.0-py3-none-any.whl",
                "digests": {"sha256": wsha},
            }
        ]
    }

    prepare(
        source=src,
        project="papersqueeze",
        version="0.2.0",
        staging=staging,
        manifest_path=manifest_path,
        _fetch=_mock_fetch_json(remote),
    )

    staged = {p.name for p in staging.iterdir()}
    assert staged == {"papersqueeze-0.2.0.tar.gz"}


def test_prepare_case_only_remote_name_stages_exact_local_file(tmp_path):
    """A case-only remote name is distinct and cannot satisfy the local wheel."""
    src = tmp_path / "dist"
    src.mkdir()
    _, sdata = _write_dist(src)
    remote = {
        "urls": [
            {
                "filename": "PaperSqueeze-0.2.0-py3-none-any.whl",
                "digests": {"sha256": "c" * 64},
            },
            {
                "filename": "papersqueeze-0.2.0.tar.gz",
                "digests": {"sha256": _sha256_bytes(sdata)},
            },
        ]
    }
    staging = tmp_path / "staging"

    prepare(
        source=src,
        project="papersqueeze",
        version="0.2.0",
        staging=staging,
        manifest_path=tmp_path / "manifest.json",
        _fetch=_mock_fetch_json(remote),
    )

    assert {path.name for path in staging.iterdir()} == {
        "papersqueeze-0.2.0-py3-none-any.whl"
    }


# ── 6. Conflicting digest -> hard failure ────────────────────────────────────


def test_files_to_upload_conflict_raises(tmp_path):
    """Same filename but different hash must raise ReleaseStateError."""
    f = tmp_path / "papersqueeze-0.2.0-py3-none-any.whl"
    f.write_bytes(b"x")
    local = [DistributionFile(path=f, filename=f.name, sha256="a" * 64)]
    remote = {f.name: "b" * 64}
    with pytest.raises(ReleaseStateError, match="[Hh]ash|[Cc]onflict"):
        files_to_upload(local, remote)


def test_prepare_conflicting_digest_raises(tmp_path):
    """prepare() propagates conflict via files_to_upload."""
    src = tmp_path / "dist"
    src.mkdir()
    wdata, sdata = _write_dist(src)
    # give remote a wrong hash for wheel
    remote = {
        "urls": [
            {
                "filename": "papersqueeze-0.2.0-py3-none-any.whl",
                "digests": {"sha256": "c" * 64},
            }
        ]
    }
    with pytest.raises(ReleaseStateError, match="[Hh]ash|[Cc]onflict"):
        prepare(
            source=src,
            project="papersqueeze",
            version="0.2.0",
            staging=tmp_path / "staging",
            manifest_path=tmp_path / "manifest.json",
            _fetch=_mock_fetch_json(remote),
        )


# ── 7. HTTP / network / JSON / schema errors ─────────────────────────────────


def test_query_pypi_http_500(tmp_path):
    with pytest.raises(ReleaseStateError, match="500|[Hh]TTP"):
        query_pypi_version("papersqueeze", "0.2.0", _fetch=_mock_fetch_http_error(500))


def test_query_pypi_url_error():
    def _fetch(url: str) -> bytes:
        raise urllib.error.URLError("connection refused")

    with pytest.raises(ReleaseStateError, match="[Nn]etwork|[Cc]onnection"):
        query_pypi_version("papersqueeze", "0.2.0", _fetch=_fetch)


def test_query_pypi_malformed_json():
    def _fetch(url: str) -> bytes:
        return b"not json{"

    with pytest.raises(ReleaseStateError, match="[Jj]SON"):
        query_pypi_version("papersqueeze", "0.2.0", _fetch=_fetch)


def test_query_pypi_missing_urls_field():
    with pytest.raises(ReleaseStateError, match="urls"):
        query_pypi_version(
            "papersqueeze", "0.2.0", _fetch=_mock_fetch_json({"info": {}})
        )


def test_query_pypi_missing_digests_field():
    with pytest.raises(ReleaseStateError, match="[Dd]igest"):
        query_pypi_version(
            "papersqueeze",
            "0.2.0",
            _fetch=_mock_fetch_json(
                {
                    "urls": [
                        {
                            "filename": "papersqueeze-0.2.0.whl",
                            "digests": {},
                        }
                    ]
                }
            ),
        )


def test_query_pypi_duplicate_filename_conflict():
    """Two remote entries for same filename with different hashes -> error."""
    payload = {
        "urls": [
            {"filename": "papersqueeze-0.2.0.whl", "digests": {"sha256": "a" * 64}},
            {"filename": "papersqueeze-0.2.0.whl", "digests": {"sha256": "b" * 64}},
        ]
    }
    with pytest.raises(ReleaseStateError, match="[Dd]uplicate|[Cc]onflict"):
        query_pypi_version("papersqueeze", "0.2.0", _fetch=_mock_fetch_json(payload))


def test_query_pypi_duplicate_filename_same_hash_ok():
    """Two remote entries for same filename with SAME hash is silently deduplicated."""
    sha = "a" * 64
    payload = {
        "urls": [
            {"filename": "papersqueeze-0.2.0.whl", "digests": {"sha256": sha}},
            {"filename": "papersqueeze-0.2.0.whl", "digests": {"sha256": sha}},
        ]
    }
    result = query_pypi_version(
        "papersqueeze", "0.2.0", _fetch=_mock_fetch_json(payload)
    )
    assert result == {"papersqueeze-0.2.0.whl": sha}


def test_query_pypi_timeout():
    def _fetch(url: str) -> bytes:
        raise TimeoutError("timed out")

    with pytest.raises(ReleaseStateError):
        query_pypi_version("papersqueeze", "0.2.0", _fetch=_fetch)


@pytest.mark.parametrize(
    "read_error",
    [
        http.client.IncompleteRead(b"partial", 100),
        http.client.HTTPException("response read failed"),
    ],
)
def test_query_pypi_http_client_read_error(read_error):
    """Truncated and failed HTTP response reads become ReleaseStateError."""

    def _fetch(url: str) -> bytes:
        raise read_error

    with pytest.raises(ReleaseStateError, match="[Nn]etwork|HTTP"):
        query_pypi_version("papersqueeze", "0.2.0", _fetch=_fetch)


def test_query_pypi_invalid_sha256_format():
    """sha256 that is not 64 lowercase hex chars is rejected as schema error."""
    payload = {
        "urls": [
            {
                "filename": "papersqueeze-0.2.0.whl",
                "digests": {"sha256": "NOTAHEX"},
            }
        ]
    }
    with pytest.raises(ReleaseStateError):
        query_pypi_version("papersqueeze", "0.2.0", _fetch=_mock_fetch_json(payload))


@pytest.mark.parametrize(
    "body",
    [
        b"\xff",
        json.dumps([]).encode(),
        json.dumps(None).encode(),
        json.dumps({"urls": [None]}).encode(),
        json.dumps(
            {"urls": [{"filename": 7, "digests": {"sha256": "a" * 64}}]}
        ).encode(),
        json.dumps({"urls": [{"filename": "x.whl", "digests": []}]}).encode(),
        json.dumps(
            {"urls": [{"filename": "x.whl", "digests": {"sha256": 7}}]}
        ).encode(),
    ],
)
def test_query_pypi_malformed_response_types_raise_release_error(body):
    """Malformed response bytes and containers never leak parser exceptions."""

    def _fetch(url: str) -> bytes:
        return body

    with pytest.raises(ReleaseStateError):
        query_pypi_version("papersqueeze", "0.2.0", _fetch=_fetch)


def test_query_pypi_non_json_body_type_raises_release_error():
    """A fetch implementation returning the wrong value type is schema failure."""

    def _fetch(url: str) -> bytes:
        return {"urls": []}  # type: ignore[return-value]

    with pytest.raises(ReleaseStateError):
        query_pypi_version("papersqueeze", "0.2.0", _fetch=_fetch)


def test_query_pypi_deep_json_recursion_is_release_error(monkeypatch):
    """Adversarial JSON nesting cannot leak RecursionError."""
    deeply_nested = b"[" * 2000 + b"0" + b"]" * 2000
    original_loads = release_module.json.loads

    def _loads_with_depth_limit(value):
        if value == deeply_nested:
            raise RecursionError("maximum JSON depth exceeded")
        return original_loads(value)

    monkeypatch.setattr(release_module.json, "loads", _loads_with_depth_limit)

    with pytest.raises(ReleaseStateError, match="[Jj]SON"):
        query_pypi_version(
            "papersqueeze",
            "0.2.0",
            _fetch=lambda url: deeply_nested,
        )


def test_query_pypi_preserves_case_distinct_filenames():
    """Remote artifact names are exact identifiers, not normalized project names."""
    payload = {
        "urls": [
            {"filename": "Package.whl", "digests": {"sha256": "a" * 64}},
            {"filename": "package.whl", "digests": {"sha256": "b" * 64}},
        ]
    }

    result = query_pypi_version(
        "papersqueeze", "0.2.0", _fetch=_mock_fetch_json(payload)
    )

    assert result == {
        "Package.whl": "a" * 64,
        "package.whl": "b" * 64,
    }


# ── 8. Manifest has all expected hashes; GitHub output correctness ────────────


def test_prepare_manifest_has_all_files_regardless_of_staging(tmp_path):
    """Manifest always lists ALL local files, even if some are already on PyPI."""
    src = tmp_path / "dist"
    src.mkdir()
    wdata, sdata = _write_dist(src)
    wsha = _sha256_bytes(wdata)
    ssha = _sha256_bytes(sdata)

    # Only wheel is already on PyPI
    remote = {
        "urls": [
            {
                "filename": "papersqueeze-0.2.0-py3-none-any.whl",
                "digests": {"sha256": wsha},
            }
        ]
    }

    staging = tmp_path / "staging"
    manifest_path = tmp_path / "manifest.json"
    gho = tmp_path / "gho"
    gho.write_text("")

    prepare(
        source=src,
        project="papersqueeze",
        version="0.2.0",
        staging=staging,
        manifest_path=manifest_path,
        github_output=gho,
        _fetch=_mock_fetch_json(remote),
    )

    m = json.loads(manifest_path.read_text())
    assert m["schema_version"] == 1
    assert m["project"] == "papersqueeze"
    assert m["version"] == "0.2.0"
    assert set(m["files"].keys()) == {
        "papersqueeze-0.2.0-py3-none-any.whl",
        "papersqueeze-0.2.0.tar.gz",
    }
    assert m["files"]["papersqueeze-0.2.0-py3-none-any.whl"] == wsha
    assert m["files"]["papersqueeze-0.2.0.tar.gz"] == ssha

    # sdist is missing -> publish=true
    assert "publish=true" in gho.read_text()


def test_prepare_github_output_publish_false(tmp_path):
    """When nothing needs uploading, publish=false is appended."""
    src = tmp_path / "dist"
    src.mkdir()
    wdata, sdata = _write_dist(src)
    wsha = _sha256_bytes(wdata)
    ssha = _sha256_bytes(sdata)

    gho = tmp_path / "gho"
    gho.write_text("PREVIOUS=value\n")

    prepare(
        source=src,
        project="papersqueeze",
        version="0.2.0",
        staging=tmp_path / "staging",
        manifest_path=tmp_path / "manifest.json",
        github_output=gho,
        _fetch=_mock_fetch_json(_remote_state(wsha, ssha)),
    )

    content = gho.read_text()
    assert "PREVIOUS=value" in content  # existing content preserved
    assert "publish=false" in content


def test_prepare_staging_cleanup_failure_is_release_error(tmp_path, monkeypatch):
    src = tmp_path / "dist"
    src.mkdir()
    _write_dist(src)
    staging = tmp_path / "staging"
    staging.mkdir()

    def _fail_cleanup(path):
        raise PermissionError("cleanup denied")

    monkeypatch.setattr(release_module.shutil, "rmtree", _fail_cleanup)

    with pytest.raises(ReleaseStateError, match="[Ss]tag"):
        prepare(
            source=src,
            project="papersqueeze",
            version="0.2.0",
            staging=staging,
            manifest_path=tmp_path / "manifest.json",
            _fetch=_mock_fetch_http_error(404),
        )


def test_prepare_staging_create_failure_is_release_error(tmp_path, monkeypatch):
    src = tmp_path / "dist"
    src.mkdir()
    _write_dist(src)
    staging = tmp_path / "staging"
    original_mkdir = Path.mkdir

    def _fail_staging_mkdir(path, *args, **kwargs):
        if path == staging:
            raise PermissionError("mkdir denied")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _fail_staging_mkdir)

    with pytest.raises(ReleaseStateError, match="[Ss]tag"):
        prepare(
            source=src,
            project="papersqueeze",
            version="0.2.0",
            staging=staging,
            manifest_path=tmp_path / "manifest.json",
            _fetch=_mock_fetch_http_error(404),
        )


def test_prepare_copy_failure_is_release_error(tmp_path, monkeypatch):
    src = tmp_path / "dist"
    src.mkdir()
    _write_dist(src)

    def _fail_copy(source, destination):
        raise PermissionError("copy denied")

    monkeypatch.setattr(release_module.shutil, "copy2", _fail_copy)

    with pytest.raises(ReleaseStateError, match="[Cc]opy"):
        prepare(
            source=src,
            project="papersqueeze",
            version="0.2.0",
            staging=tmp_path / "staging",
            manifest_path=tmp_path / "manifest.json",
            _fetch=_mock_fetch_http_error(404),
        )


def test_prepare_manifest_write_failure_is_release_error(tmp_path, monkeypatch):
    src = tmp_path / "dist"
    src.mkdir()
    _write_dist(src)
    manifest_path = tmp_path / "manifest.json"
    original_write_text = Path.write_text

    def _fail_manifest_write(path, *args, **kwargs):
        if path == manifest_path:
            raise PermissionError("manifest write denied")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _fail_manifest_write)

    with pytest.raises(ReleaseStateError, match="[Mm]anifest"):
        prepare(
            source=src,
            project="papersqueeze",
            version="0.2.0",
            staging=tmp_path / "staging",
            manifest_path=manifest_path,
            _fetch=_mock_fetch_http_error(404),
        )


def test_prepare_github_output_failure_is_release_error(tmp_path, monkeypatch):
    src = tmp_path / "dist"
    src.mkdir()
    _write_dist(src)
    github_output = tmp_path / "github-output"
    original_open = Path.open

    def _fail_output_open(path, mode="r", *args, **kwargs):
        if path == github_output and mode == "a":
            raise PermissionError("output append denied")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _fail_output_open)

    with pytest.raises(ReleaseStateError, match="GitHub"):
        prepare(
            source=src,
            project="papersqueeze",
            version="0.2.0",
            staging=tmp_path / "staging",
            manifest_path=tmp_path / "manifest.json",
            github_output=github_output,
            _fetch=_mock_fetch_http_error(404),
        )


# ── 9. Verify scenarios ───────────────────────────────────────────────────────


def _make_manifest(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    if files is None:
        files = {
            "papersqueeze-0.2.0-py3-none-any.whl": "a" * 64,
            "papersqueeze-0.2.0.tar.gz": "b" * 64,
        }
    m = {
        "schema_version": 1,
        "project": "papersqueeze",
        "version": "0.2.0",
        "files": files,
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(m))
    return p


def test_verify_immediate_success(tmp_path):
    """verify() returns immediately when all hashes match on first attempt."""
    wsha, ssha = "a" * 64, "b" * 64
    manifest_path = _make_manifest(
        tmp_path,
        {
            "papersqueeze-0.2.0-py3-none-any.whl": wsha,
            "papersqueeze-0.2.0.tar.gz": ssha,
        },
    )
    sleep_calls: list[float] = []

    verify(
        manifest_path,
        attempts=6,
        delay_seconds=10.0,
        _fetch=_mock_fetch_json(_remote_state(wsha, ssha)),
        _sleep=sleep_calls.append,
    )

    assert sleep_calls == [], "No sleeps on immediate success"


def test_verify_stale_then_success(tmp_path):
    """verify() retries when files are initially absent, then succeeds."""
    wsha, ssha = "a" * 64, "b" * 64
    manifest_path = _make_manifest(
        tmp_path,
        {
            "papersqueeze-0.2.0-py3-none-any.whl": wsha,
            "papersqueeze-0.2.0.tar.gz": ssha,
        },
    )

    call_count = 0

    def _fetch(url: str) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return json.dumps({"urls": []}).encode()
        return json.dumps(_remote_state(wsha, ssha)).encode()

    sleep_calls: list[float] = []
    verify(
        manifest_path,
        attempts=6,
        delay_seconds=5.0,
        _fetch=_fetch,
        _sleep=sleep_calls.append,
    )

    assert call_count == 3
    assert sleep_calls == [5.0, 5.0]


def test_verify_exhaustion_raises(tmp_path):
    """verify() raises after all attempts are consumed."""
    manifest_path = _make_manifest(tmp_path)

    call_count = 0

    def _fetch(url: str) -> bytes:
        nonlocal call_count
        call_count += 1
        return json.dumps({"urls": []}).encode()  # never has the files

    sleep_calls: list[float] = []
    with pytest.raises(ReleaseStateError):
        verify(
            manifest_path,
            attempts=3,
            delay_seconds=5.0,
            _fetch=_fetch,
            _sleep=sleep_calls.append,
        )

    assert call_count == 3
    assert sleep_calls == [5.0, 5.0]


def test_verify_transient_failure_retry(tmp_path):
    """verify() retries on transient lookup failures (HTTP 500)."""
    wsha, ssha = "a" * 64, "b" * 64
    manifest_path = _make_manifest(
        tmp_path,
        {
            "papersqueeze-0.2.0-py3-none-any.whl": wsha,
            "papersqueeze-0.2.0.tar.gz": ssha,
        },
    )

    call_count = 0

    def _fetch(url: str) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)
        return json.dumps(_remote_state(wsha, ssha)).encode()

    sleep_calls: list[float] = []
    verify(
        manifest_path,
        attempts=6,
        delay_seconds=7.0,
        _fetch=_fetch,
        _sleep=sleep_calls.append,
    )

    assert call_count == 2
    assert sleep_calls == [7.0]


def test_verify_malformed_json_exhausts_bounded_attempts(tmp_path):
    """Malformed remote JSON retries exactly attempts times without real sleep."""
    manifest_path = _make_manifest(tmp_path)
    fetch_calls = 0

    def _fetch(url: str) -> bytes:
        nonlocal fetch_calls
        fetch_calls += 1
        return b"{not-json"

    sleep_calls: list[float] = []
    with pytest.raises(ReleaseStateError):
        verify(
            manifest_path,
            attempts=3,
            delay_seconds=2.5,
            _fetch=_fetch,
            _sleep=sleep_calls.append,
        )

    assert fetch_calls == 3
    assert sleep_calls == [2.5, 2.5]


def test_verify_url_error_exhausts_bounded_attempts(tmp_path):
    """Network failures retry exactly attempts times without real sleep."""
    manifest_path = _make_manifest(tmp_path)
    fetch_calls = 0

    def _fetch(url: str) -> bytes:
        nonlocal fetch_calls
        fetch_calls += 1
        raise urllib.error.URLError("offline")

    sleep_calls: list[float] = []
    with pytest.raises(ReleaseStateError):
        verify(
            manifest_path,
            attempts=4,
            delay_seconds=1.25,
            _fetch=_fetch,
            _sleep=sleep_calls.append,
        )

    assert fetch_calls == 4
    assert sleep_calls == [1.25, 1.25, 1.25]


def test_verify_incomplete_read_retries_then_succeeds(tmp_path):
    """A truncated read retries once and succeeds without real sleeping."""
    manifest_path = _make_manifest(tmp_path)
    fetch_calls = 0

    def _fetch(url: str) -> bytes:
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            raise http.client.IncompleteRead(b"partial", 100)
        return json.dumps(_remote_state("a" * 64, "b" * 64)).encode()

    sleep_calls: list[float] = []
    verify(
        manifest_path,
        attempts=3,
        delay_seconds=4.0,
        _fetch=_fetch,
        _sleep=sleep_calls.append,
    )

    assert fetch_calls == 2
    assert sleep_calls == [4.0]


def test_verify_incomplete_read_exhausts_bounded_attempts(tmp_path):
    """Persistent truncated reads exhaust attempts with exactly N-1 sleeps."""
    manifest_path = _make_manifest(tmp_path)
    fetch_calls = 0

    def _fetch(url: str) -> bytes:
        nonlocal fetch_calls
        fetch_calls += 1
        raise http.client.IncompleteRead(b"partial", 100)

    sleep_calls: list[float] = []
    with pytest.raises(ReleaseStateError):
        verify(
            manifest_path,
            attempts=3,
            delay_seconds=4.0,
            _fetch=_fetch,
            _sleep=sleep_calls.append,
        )

    assert fetch_calls == 3
    assert sleep_calls == [4.0, 4.0]


def test_verify_deep_json_recursion_exhausts_bounded_attempts(tmp_path, monkeypatch):
    """Deep remote JSON retries only to the configured bound without real sleep."""
    manifest_path = _make_manifest(tmp_path)
    deeply_nested = b"[" * 2000 + b"0" + b"]" * 2000
    fetch_calls = 0
    original_loads = release_module.json.loads

    def _loads_with_depth_limit(value):
        if value == deeply_nested:
            raise RecursionError("maximum JSON depth exceeded")
        return original_loads(value)

    monkeypatch.setattr(release_module.json, "loads", _loads_with_depth_limit)

    def _fetch(url: str) -> bytes:
        nonlocal fetch_calls
        fetch_calls += 1
        return deeply_nested

    sleep_calls: list[float] = []
    with pytest.raises(ReleaseStateError):
        verify(
            manifest_path,
            attempts=3,
            delay_seconds=3.5,
            _fetch=_fetch,
            _sleep=sleep_calls.append,
        )

    assert fetch_calls == 3
    assert sleep_calls == [3.5, 3.5]


def test_verify_case_only_remote_name_exhausts_attempts(tmp_path):
    """A case-only remote filename never satisfies an exact manifest filename."""
    manifest_path = _make_manifest(tmp_path)
    remote = {
        "urls": [
            {
                "filename": "PaperSqueeze-0.2.0-py3-none-any.whl",
                "digests": {"sha256": "a" * 64},
            },
            {
                "filename": "papersqueeze-0.2.0.tar.gz",
                "digests": {"sha256": "b" * 64},
            },
        ]
    }
    fetch_calls = 0

    def _fetch(url: str) -> bytes:
        nonlocal fetch_calls
        fetch_calls += 1
        return json.dumps(remote).encode()

    sleep_calls: list[float] = []
    with pytest.raises(ReleaseStateError):
        verify(
            manifest_path,
            attempts=3,
            delay_seconds=6.0,
            _fetch=_fetch,
            _sleep=sleep_calls.append,
        )

    assert fetch_calls == 3
    assert sleep_calls == [6.0, 6.0]


def test_verify_immediate_conflict_failure(tmp_path):
    """verify() raises immediately (no retry) when remote hash conflicts."""
    wsha = "a" * 64
    ssha = "b" * 64
    manifest_path = _make_manifest(
        tmp_path,
        {
            "papersqueeze-0.2.0-py3-none-any.whl": wsha,
            "papersqueeze-0.2.0.tar.gz": ssha,
        },
    )

    def _fetch(url: str) -> bytes:
        # Remote has wrong hash for wheel
        return json.dumps(_remote_state("c" * 64, ssha)).encode()

    sleep_calls: list[float] = []
    with pytest.raises(ReleaseStateError, match="[Hh]ash|[Cc]onflict"):
        verify(
            manifest_path,
            attempts=6,
            delay_seconds=10.0,
            _fetch=_fetch,
            _sleep=sleep_calls.append,
        )

    assert sleep_calls == [], "No sleeps before conflict failure"


def test_verify_invalid_manifest_schema(tmp_path):
    """verify() raises on manifest with wrong schema_version."""
    bad = {"schema_version": 99, "project": "p", "version": "1.0", "files": {}}
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ReleaseStateError, match="[Ss]chema"):
        verify(p, attempts=1, delay_seconds=0, _fetch=_mock_fetch_http_error(404))


def test_verify_missing_manifest_raises(tmp_path):
    with pytest.raises(ReleaseStateError, match="[Mm]anifest"):
        verify(
            tmp_path / "nonexistent.json",
            attempts=1,
            delay_seconds=0,
            _fetch=_mock_fetch_http_error(404),
        )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {"schema_version": 1, "version": "0.2.0", "files": {"x.whl": "a" * 64}},
        {
            "schema_version": 1,
            "project": 7,
            "version": "0.2.0",
            "files": {"x.whl": "a" * 64},
        },
        {
            "schema_version": 1,
            "project": "papersqueeze",
            "version": [],
            "files": {"x.whl": "a" * 64},
        },
        {
            "schema_version": 1,
            "project": "papersqueeze",
            "version": "0.2.0",
            "files": {"": "a" * 64},
        },
        {
            "schema_version": 1,
            "project": "papersqueeze",
            "version": "0.2.0",
            "files": {"x.whl": 7},
        },
        {
            "schema_version": 1,
            "project": "papersqueeze",
            "version": "0.2.0",
            "files": {"x.whl": "not-a-sha256"},
        },
    ],
)
def test_verify_malformed_manifest_schema_raises_release_error(tmp_path, payload):
    """Wrong manifest containers, fields, and digest values are rejected."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload))

    with pytest.raises(ReleaseStateError):
        verify(
            manifest_path,
            attempts=1,
            delay_seconds=0,
            _fetch=_mock_fetch_json({"urls": []}),
            _sleep=lambda _: pytest.fail("invalid manifest must not sleep"),
        )


@pytest.mark.parametrize("content", [b"{not-json", b"\xff"])
def test_verify_malformed_manifest_encoding_or_json(tmp_path, content):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(content)

    with pytest.raises(ReleaseStateError, match="[Mm]anifest"):
        verify(
            manifest_path,
            attempts=1,
            delay_seconds=0,
            _fetch=_mock_fetch_json({"urls": []}),
        )


def test_verify_deeply_nested_manifest_is_release_error(tmp_path, monkeypatch):
    """Adversarial manifest nesting cannot leak RecursionError."""
    manifest_path = tmp_path / "manifest.json"
    deeply_nested = "[" * 2000 + "0" + "]" * 2000
    manifest_path.write_text(deeply_nested)
    original_loads = release_module.json.loads

    def _loads_with_depth_limit(value):
        if value == deeply_nested:
            raise RecursionError("maximum JSON depth exceeded")
        return original_loads(value)

    monkeypatch.setattr(release_module.json, "loads", _loads_with_depth_limit)

    with pytest.raises(ReleaseStateError, match="[Mm]anifest"):
        verify(
            manifest_path,
            attempts=1,
            delay_seconds=0,
            _fetch=_mock_fetch_json({"urls": []}),
        )


def test_verify_unreadable_manifest_path_raises_release_error(tmp_path):
    """A directory path is reported as a manifest error, not raw OSError."""
    with pytest.raises(ReleaseStateError, match="[Mm]anifest"):
        verify(
            tmp_path,
            attempts=1,
            delay_seconds=0,
            _fetch=_mock_fetch_json({"urls": []}),
        )


def test_verify_malformed_manifest_path_raises_release_error():
    """An invalid filesystem path is reported without leaking ValueError."""
    with pytest.raises(ReleaseStateError, match="[Mm]anifest"):
        verify(
            Path("\0"),
            attempts=1,
            delay_seconds=0,
            _fetch=_mock_fetch_json({"urls": []}),
        )


@pytest.mark.parametrize(
    ("attempts", "delay_seconds"),
    [(0, 0), (-1, 0), (1, -0.1)],
)
def test_verify_rejects_invalid_retry_configuration(tmp_path, attempts, delay_seconds):
    manifest_path = _make_manifest(tmp_path)

    with pytest.raises(ReleaseStateError, match="[Aa]ttempt|[Dd]elay"):
        verify(
            manifest_path,
            attempts=attempts,
            delay_seconds=delay_seconds,
            _fetch=_mock_fetch_json({"urls": []}),
            _sleep=lambda _: pytest.fail("invalid retry settings must not sleep"),
        )


# ── 10. CLI argument / error behaviour ───────────────────────────────────────


def test_cli_prepare_missing_required_args():
    """Missing required args -> argparse exits with code 2."""
    with pytest.raises(SystemExit) as exc_info:
        main(["prepare", "--source", "dist"])
    assert exc_info.value.code == 2


def test_cli_verify_missing_manifest_arg():
    """Missing --manifest for verify -> argparse exits with code 2."""
    with pytest.raises(SystemExit) as exc_info:
        main(["verify"])
    assert exc_info.value.code == 2


def test_cli_no_subcommand():
    """No subcommand -> exits with nonzero code."""
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code != 0


def test_cli_prepare_release_state_error_exits_1(tmp_path, capsys):
    """ReleaseStateError during prepare exits 1 with stderr message."""
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "prepare",
                "--source",
                str(tmp_path / "empty"),  # nonexistent -> no wheel found
                "--staging",
                str(tmp_path / "staging"),
                "--project",
                "papersqueeze",
                "--version",
                "0.2.0",
                "--manifest",
                str(tmp_path / "m.json"),
            ]
        )
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err


def test_cli_verify_missing_manifest_file_exits_1(tmp_path, capsys):
    """verify with a nonexistent manifest file exits 1 with stderr."""
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "verify",
                "--manifest",
                str(tmp_path / "ghost.json"),
                "--attempts",
                "1",
                "--delay-seconds",
                "0",
            ]
        )
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err


def test_cli_verify_malformed_manifest_exits_1_without_traceback(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"\xff")

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "verify",
                "--manifest",
                str(manifest_path),
                "--attempts",
                "1",
                "--delay-seconds",
                "0",
            ]
        )

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error:")
    assert "Traceback" not in captured.err


def test_cli_prepare_filesystem_error_exits_1_without_traceback(
    tmp_path, monkeypatch, capsys
):
    src = tmp_path / "dist"
    src.mkdir()
    _write_dist(src)

    def _always_404(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    def _fail_copy(source, destination):
        raise PermissionError("copy denied")

    monkeypatch.setattr(release_module, "_default_fetch", _always_404)
    monkeypatch.setattr(release_module.shutil, "copy2", _fail_copy)

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "prepare",
                "--source",
                str(src),
                "--staging",
                str(tmp_path / "staging"),
                "--project",
                "papersqueeze",
                "--version",
                "0.2.0",
                "--manifest",
                str(tmp_path / "manifest.json"),
            ]
        )

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error:")
    assert "Traceback" not in captured.err


def test_cli_prepare_end_to_end(tmp_path):
    """Full prepare CLI invocation with injected network (via module-level patch)."""
    src = tmp_path / "dist"
    src.mkdir()
    wdata, sdata = _write_dist(src)

    staging = tmp_path / "staging"
    manifest_path = tmp_path / "manifest.json"

    import pypi_release as _mod

    _original = _mod._default_fetch

    def _always_404(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    _mod._default_fetch = _always_404
    try:
        main(
            [
                "prepare",
                "--source",
                str(src),
                "--staging",
                str(staging),
                "--project",
                "papersqueeze",
                "--version",
                "0.2.0",
                "--manifest",
                str(manifest_path),
            ]
        )
    finally:
        _mod._default_fetch = _original

    staged = {p.name for p in staging.iterdir()}
    assert "papersqueeze-0.2.0-py3-none-any.whl" in staged
    assert "papersqueeze-0.2.0.tar.gz" in staged
    m = json.loads(manifest_path.read_text())
    assert m["project"] == "papersqueeze"
