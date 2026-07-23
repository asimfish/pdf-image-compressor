"""
scripts/pypi_release.py

Standard-library-only helper to safely inspect built Python distributions,
compare them with the PyPI JSON endpoint, stage only missing files, emit a
digest manifest with GitHub Actions output, and verify final remote hashes
with bounded retries.

Usage:
  python3 -I scripts/pypi_release.py prepare \
      --source dist --staging pypi-dist \
      --project papersqueeze --version 0.2.0 \
      --manifest pypi-manifest.json [--github-output PATH]

  python3 -I scripts/pypi_release.py verify \
      --manifest pypi-manifest.json --attempts 6 --delay-seconds 10
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import re
import shutil
import stat
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Callable

# ── Public types ──────────────────────────────────────────────────────────────

MANIFEST_SCHEMA_VERSION = 1

_CHUNK = 65536  # 64 KiB chunks for SHA-256 streaming


class ReleaseStateError(RuntimeError):
    """Raised when local or remote release state is inconsistent or unsafe."""


class _RemoteHashConflictError(ReleaseStateError):
    """Raised when PyPI reports a filename with an unexpected digest."""


@dataclass(frozen=True)
class DistributionFile:
    path: Path
    filename: str
    sha256: str


# ── Pure utilities ────────────────────────────────────────────────────────────


def normalize_project_name(name: str) -> str:
    """PEP 503 normalisation: collapse [-_.] runs to single '-', lowercase."""
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_file(path: Path) -> str:
    """Return lowercase hex SHA-256 digest of a file, reading in chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


# ── Distribution collection ───────────────────────────────────────────────────


def _check_wheel_members_safe(members: list[zipfile.ZipInfo], wheel_name: str) -> None:
    """Raise ReleaseStateError if any ZIP member has unsafe path components."""
    for member in members:
        name = member.filename
        if name.startswith("/"):
            raise ReleaseStateError(
                f"Wheel {wheel_name}: unsafe absolute-path entry: {name!r}"
            )
        parts = name.replace("\\", "/").split("/")
        if ".." in parts:
            raise ReleaseStateError(
                f"Wheel {wheel_name}: unsafe path-traversal entry: {name!r}"
            )


def _check_wheel_metadata_regular(
    metadata_member: zipfile.ZipInfo, wheel_name: str
) -> None:
    """Reject Unix special-file metadata while accepting unspecified type bits."""
    if metadata_member.create_system != 3:
        return

    unix_mode = metadata_member.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if file_type not in (0, stat.S_IFREG):
        raise ReleaseStateError(
            f"Wheel {wheel_name}: METADATA member is not a regular file"
        )


def _has_noncanonical_archive_path_characters(name: str) -> bool:
    return (
        "\\" in name
        or not name.isprintable()
        or any(character.isspace() for character in name)
    )


def _is_exact_metadata_path(
    name: str,
    *,
    metadata_filename: str,
    parent_suffix: str | None = None,
) -> bool:
    if _has_noncanonical_archive_path_characters(name):
        return False

    parts = name.split("/")
    if len(parts) != 2:
        return False

    parent, filename = parts
    if parent in {"", ".", ".."} or filename != metadata_filename:
        return False
    if parent_suffix is None:
        return True
    if not parent.endswith(parent_suffix):
        return False

    parent_stem = parent[: -len(parent_suffix)]
    return parent_stem not in {"", ".", ".."}


def _looks_like_metadata_path(
    name: str,
    *,
    metadata_filename: str,
    parent_suffix: str | None = None,
) -> bool:
    normalized = name.replace("\\", "/")
    parts = [
        component.strip() for component in normalized.split("/") if component.strip()
    ]
    if not parts or parts[-1] != metadata_filename:
        return False
    if parent_suffix is None:
        return True
    return any(component.endswith(parent_suffix) for component in parts[:-1])


def _read_wheel_metadata(path: Path) -> bytes:
    """Extract exactly one *.dist-info/METADATA from a wheel (ZIP). No extraction."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            members = zf.infolist()
            _check_wheel_members_safe(members, path.name)

            metadata_candidates = [
                member
                for member in members
                if _looks_like_metadata_path(
                    member.filename,
                    metadata_filename="METADATA",
                    parent_suffix=".dist-info",
                )
            ]
            for member in metadata_candidates:
                if not _is_exact_metadata_path(
                    member.filename,
                    metadata_filename="METADATA",
                    parent_suffix=".dist-info",
                ):
                    raise ReleaseStateError(
                        f"Wheel {path.name}: noncanonical METADATA path "
                        f"{member.filename!r}"
                    )

            if len(metadata_candidates) == 0:
                raise ReleaseStateError(
                    f"Wheel {path.name}: no *.dist-info/METADATA found"
                )
            if len(metadata_candidates) > 1:
                raise ReleaseStateError(
                    f"Wheel {path.name}: multiple *.dist-info/METADATA entries: "
                    f"{[member.filename for member in metadata_candidates]}"
                )
            metadata_member = metadata_candidates[0]
            _check_wheel_metadata_regular(metadata_member, path.name)
            return zf.read(metadata_member)
    except ReleaseStateError:
        raise
    except (
        zipfile.BadZipFile,
        zlib.error,
        EOFError,
        OSError,
        RuntimeError,
        KeyError,
    ) as exc:
        raise ReleaseStateError(
            f"Unable to read wheel metadata from {path}: {exc}"
        ) from exc


def _check_tarinfo_safe(members: list[tarfile.TarInfo], sdist_name: str) -> None:
    """Raise ReleaseStateError if any tarfile member has unsafe path components."""
    for m in members:
        name = m.name
        if name.startswith("/"):
            raise ReleaseStateError(
                f"sdist {sdist_name}: unsafe absolute-path member: {name!r}"
            )
        parts = name.replace("\\", "/").split("/")
        if ".." in parts:
            raise ReleaseStateError(
                f"sdist {sdist_name}: unsafe path-traversal member: {name!r}"
            )


def _read_sdist_pkginfo(path: Path) -> bytes:
    """Extract exactly one top-level */PKG-INFO from an sdist (tar.gz). No extraction."""
    try:
        with tarfile.open(path, "r:gz") as tf:
            members = tf.getmembers()
            _check_tarinfo_safe(members, path.name)

            pkg_info_candidates = [
                member
                for member in members
                if _looks_like_metadata_path(
                    member.name,
                    metadata_filename="PKG-INFO",
                )
            ]
            exact_pkg_info_candidates: list[tarfile.TarInfo] = []
            for member in pkg_info_candidates:
                if _is_exact_metadata_path(
                    member.name,
                    metadata_filename="PKG-INFO",
                ):
                    exact_pkg_info_candidates.append(member)
                    continue
                parts = member.name.split("/")
                if len(parts) >= 2 and parts[-2].endswith(".egg-info"):
                    continue
                raise ReleaseStateError(
                    f"sdist {path.name}: noncanonical PKG-INFO path {member.name!r}"
                )

            if len(exact_pkg_info_candidates) == 0:
                raise ReleaseStateError(
                    f"sdist {path.name}: no top-level PKG-INFO found"
                )
            if len(exact_pkg_info_candidates) > 1:
                raise ReleaseStateError(
                    f"sdist {path.name}: multiple top-level PKG-INFO entries: "
                    f"{[member.name for member in exact_pkg_info_candidates]}"
                )

            metadata_member = exact_pkg_info_candidates[0]
            if not metadata_member.isfile():
                raise ReleaseStateError(
                    f"sdist {path.name}: PKG-INFO member is not a regular file"
                )

            metadata_file = tf.extractfile(metadata_member)
            if metadata_file is None:
                raise ReleaseStateError(
                    f"sdist {path.name}: unable to read regular PKG-INFO member"
                )
            with metadata_file:
                return metadata_file.read()
    except ReleaseStateError:
        raise
    except (tarfile.TarError, OSError, EOFError, KeyError) as exc:
        raise ReleaseStateError(
            f"Unable to read sdist metadata from {path}: {exc}"
        ) from exc


def _parse_metadata(raw_metadata: bytes) -> tuple[str, str]:
    """Parse RFC 5322-style metadata; return (Name, Version) or raise."""
    msg = BytesParser(policy=policy.default).parsebytes(raw_metadata)
    name = msg.get("Name")
    version = msg.get("Version")
    if not name:
        raise ReleaseStateError("Metadata missing 'Name' header")
    if not version:
        raise ReleaseStateError("Metadata missing 'Version' header")
    return str(name), str(version)


def collect_distributions(
    source: Path, project: str, version: str
) -> list[DistributionFile]:
    """
    Locate exactly one wheel (.whl) and one source distribution (.tar.gz) under
    *source*, verify their embedded metadata matches *project* and *version*,
    and return a list of two :class:`DistributionFile` objects.

    Raises :class:`ReleaseStateError` for any structural or metadata problem.
    Never extracts or executes package code.
    """
    try:
        source_mode = source.stat().st_mode
    except (OSError, ValueError) as exc:
        raise ReleaseStateError(
            f"Unable to inspect source directory {source}: {exc}"
        ) from exc
    if not stat.S_ISDIR(source_mode):
        raise ReleaseStateError(f"Source directory does not exist: {source}")

    try:
        wheels = sorted(source.glob("*.whl"))
        sdists = sorted(source.glob("*.tar.gz"))
    except (OSError, ValueError) as exc:
        raise ReleaseStateError(
            f"Unable to discover distributions in {source}: {exc}"
        ) from exc

    # Reject duplicates
    if len(wheels) == 0:
        raise ReleaseStateError(f"Wheel (.whl) not found in {source}")
    if len(wheels) > 1:
        raise ReleaseStateError(
            f"Multiple wheel files found in {source}: {[w.name for w in wheels]}"
        )
    if len(sdists) == 0:
        raise ReleaseStateError(f"sdist (.tar.gz) not found in {source}")
    if len(sdists) > 1:
        raise ReleaseStateError(
            f"Multiple sdist files found in {source}: {[s.name for s in sdists]}"
        )

    wheel_path = wheels[0]
    sdist_path = sdists[0]

    norm_project = normalize_project_name(project)

    # Validate wheel metadata
    wheel_metadata = _read_wheel_metadata(wheel_path)
    w_name, w_version = _parse_metadata(wheel_metadata)
    if normalize_project_name(w_name) != norm_project:
        raise ReleaseStateError(
            f"Wheel Name '{w_name}' does not match project '{project}'"
        )
    if w_version != version:
        raise ReleaseStateError(
            f"Wheel Version '{w_version}' does not match expected '{version}'"
        )

    # Validate sdist metadata
    sdist_metadata = _read_sdist_pkginfo(sdist_path)
    s_name, s_version = _parse_metadata(sdist_metadata)
    if normalize_project_name(s_name) != norm_project:
        raise ReleaseStateError(
            f"sdist Name '{s_name}' does not match project '{project}'"
        )
    if s_version != version:
        raise ReleaseStateError(
            f"sdist Version '{s_version}' does not match expected '{version}'"
        )

    distribution_files: list[DistributionFile] = []
    for path in (wheel_path, sdist_path):
        try:
            digest = sha256_file(path)
        except (OSError, ValueError) as exc:
            raise ReleaseStateError(
                f"Unable to hash distribution artifact {path}: {exc}"
            ) from exc
        distribution_files.append(
            DistributionFile(path=path, filename=path.name, sha256=digest)
        )
    return distribution_files


# ── PyPI query ────────────────────────────────────────────────────────────────


def _default_fetch(url: str) -> bytes:
    """Fetch *url* via HTTP and return the response body. No auth, no cookies."""
    req = urllib.request.Request(url, headers={"User-Agent": "pypi_release/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def query_pypi_version(
    project: str,
    version: str,
    *,
    _fetch: Callable[[str], bytes] | None = None,
) -> dict[str, str]:
    """
    Query ``https://pypi.org/pypi/{project}/{version}/json`` and return a
    mapping of ``filename -> sha256`` for all files listed.

    * HTTP 404 means the version does not yet exist; return ``{}``.
    * Any other HTTP error, network failure, JSON decode error, schema
      violation, or duplicate conflicting entry raises :class:`ReleaseStateError`.
    * Duplicate entries with the *same* hash are silently deduplicated.
    """
    if _fetch is None:
        _fetch = _default_fetch

    quoted_project = urllib.parse.quote(project, safe="")
    quoted_version = urllib.parse.quote(version, safe="")
    url = f"https://pypi.org/pypi/{quoted_project}/{quoted_version}/json"

    try:
        body = _fetch(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise ReleaseStateError(
            f"PyPI HTTP error {exc.code} for {url}: {exc.reason}"
        ) from exc
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        OSError,
        TimeoutError,
    ) as exc:
        raise ReleaseStateError(f"Network error querying PyPI: {exc}") from exc

    if not isinstance(body, (bytes, bytearray)):
        raise ReleaseStateError(
            f"PyPI response body must be bytes, got {type(body).__name__}"
        )

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ReleaseStateError(f"PyPI returned malformed JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ReleaseStateError(
            f"PyPI JSON root must be an object, got {type(data).__name__}"
        )

    urls = data.get("urls")
    if not isinstance(urls, list):
        raise ReleaseStateError(
            f"PyPI JSON missing or invalid 'urls' field: {type(urls)}"
        )

    result: dict[str, str] = {}
    for entry in urls:
        if not isinstance(entry, dict):
            raise ReleaseStateError(f"PyPI 'urls' entry is not a dict: {entry!r}")
        filename = entry.get("filename")
        if not isinstance(filename, str) or not filename:
            raise ReleaseStateError(f"PyPI 'urls' entry missing 'filename': {entry!r}")
        digests = entry.get("digests")
        if not isinstance(digests, dict):
            raise ReleaseStateError(f"PyPI 'urls' entry missing 'digests': {entry!r}")
        sha256 = digests.get("sha256")
        if not isinstance(sha256, str):
            raise ReleaseStateError(f"PyPI digests missing 'sha256' for {filename!r}")
        sha256 = sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ReleaseStateError(
                f"PyPI sha256 for {filename!r} is not a valid 64-hex string: {sha256!r}"
            )

        if filename in result:
            if result[filename] != sha256:
                raise _RemoteHashConflictError(
                    f"Duplicate remote filename '{filename}' with conflicting hashes"
                )
            # Same hash: silently ignore duplicate
        else:
            result[filename] = sha256

    return result


# ── Staging logic ─────────────────────────────────────────────────────────────


def files_to_upload(
    local: list[DistributionFile],
    remote: dict[str, str],
) -> list[DistributionFile]:
    """
    Return subset of *local* files that are absent from *remote*.

    * If a local file has the same name and hash as remote: skip (already uploaded).
    * If a local file has the same name but a different hash: raise immediately.
    """
    to_upload: list[DistributionFile] = []
    for dist_file in local:
        remote_hash = remote.get(dist_file.filename)
        if remote_hash is None:
            to_upload.append(dist_file)
        elif remote_hash == dist_file.sha256:
            pass  # identical, skip
        else:
            raise ReleaseStateError(
                f"Hash conflict for '{dist_file.filename}': "
                f"local={dist_file.sha256!r} remote={remote_hash!r}"
            )
    return to_upload


# ── prepare ───────────────────────────────────────────────────────────────────


def prepare(
    source: Path,
    project: str,
    version: str,
    staging: Path,
    manifest_path: Path,
    github_output: Path | None = None,
    *,
    _fetch: Callable[[str], bytes] | None = None,
) -> None:
    """
    1. Collect and validate local distributions from *source*.
    2. Query PyPI for the existing release state.
    3. Determine which files need to be uploaded.
    4. Clean/create *staging* and copy only missing files there.
    5. Write a schema-versioned JSON manifest containing ALL local file hashes.
    6. Append ``publish=true`` or ``publish=false`` to *github_output* if given.
    """
    local_files = collect_distributions(source, project, version)
    remote_state = query_pypi_version(project, version, _fetch=_fetch)
    missing_files = files_to_upload(local_files, remote_state)

    # Clean and recreate staging directory
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
    except (OSError, ValueError, shutil.Error) as exc:
        raise ReleaseStateError(
            f"Unable to prepare staging directory {staging}: {exc}"
        ) from exc

    # Copy only the files that need uploading
    for dist_file in missing_files:
        destination = staging / dist_file.filename
        try:
            shutil.copy2(dist_file.path, destination)
        except (OSError, ValueError, shutil.Error) as exc:
            raise ReleaseStateError(
                f"Unable to copy {dist_file.filename} to staging: {exc}"
            ) from exc

    # Build manifest with ALL local files (not just staged subset)
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "project": project,
        "version": version,
        "files": {f.filename: f.sha256 for f in local_files},
    }
    manifest_text = json.dumps(manifest, indent=2) + "\n"
    try:
        manifest_path.write_text(manifest_text, encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReleaseStateError(
            f"Unable to write manifest {manifest_path}: {exc}"
        ) from exc

    # Append GitHub Actions output variable
    if github_output is not None:
        publish_value = "true" if missing_files else "false"
        try:
            with github_output.open("a", encoding="utf-8") as fh:
                fh.write(f"publish={publish_value}\n")
        except (OSError, UnicodeError, ValueError) as exc:
            raise ReleaseStateError(
                f"Unable to append GitHub output {github_output}: {exc}"
            ) from exc


# ── verify ────────────────────────────────────────────────────────────────────


def _load_manifest(manifest_path: Path) -> tuple[str, str, dict[str, str]]:
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReleaseStateError(
            f"Unable to read manifest {manifest_path}: {exc}"
        ) from exc

    try:
        data = json.loads(manifest_text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ReleaseStateError(f"Manifest is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ReleaseStateError(
            f"Manifest root must be an object, got {type(data).__name__}"
        )

    schema_version = data.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != MANIFEST_SCHEMA_VERSION
    ):
        raise ReleaseStateError(
            f"Unsupported manifest schema_version: {schema_version!r} "
            f"(expected {MANIFEST_SCHEMA_VERSION})"
        )

    project = data.get("project")
    version = data.get("version")
    raw_files = data.get("files")
    if not isinstance(project, str) or not project:
        raise ReleaseStateError("Manifest missing or invalid 'project'")
    if not isinstance(version, str) or not version:
        raise ReleaseStateError("Manifest missing or invalid 'version'")
    if not isinstance(raw_files, dict) or not raw_files:
        raise ReleaseStateError("Manifest 'files' is empty or invalid")

    files: dict[str, str] = {}
    for filename, digest in raw_files.items():
        if not isinstance(filename, str) or not filename:
            raise ReleaseStateError(
                f"Manifest contains an invalid filename: {filename!r}"
            )
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ReleaseStateError(
                f"Manifest sha256 for {filename!r} is not a lowercase 64-hex string"
            )
        files[filename] = digest

    return project, version, files


def verify(
    manifest_path: Path,
    attempts: int = 6,
    delay_seconds: float = 10.0,
    *,
    _fetch: Callable[[str], bytes] | None = None,
    _sleep: Callable[[float], None] | None = None,
) -> None:
    """
    Read *manifest_path* and confirm every listed file/hash exists on PyPI.

    Retries up to *attempts* times with *delay_seconds* between each attempt.
    * Missing files and transient errors may retry.
    * A hash conflict raises :class:`ReleaseStateError` immediately.
    * Exhausting all attempts raises :class:`ReleaseStateError`.
    """
    if _sleep is None:
        _sleep = time.sleep

    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        raise ReleaseStateError(
            "Verification attempts must be an integer of at least 1"
        )
    if (
        isinstance(delay_seconds, bool)
        or not isinstance(delay_seconds, (int, float))
        or not math.isfinite(delay_seconds)
        or delay_seconds < 0
    ):
        raise ReleaseStateError(
            "Verification delay_seconds must be a finite non-negative number"
        )

    project, version, files = _load_manifest(manifest_path)
    missing_filenames: list[str] = []

    for attempt in range(1, attempts + 1):
        try:
            remote = query_pypi_version(project, version, _fetch=_fetch)
        except _RemoteHashConflictError:
            raise
        except ReleaseStateError as exc:
            # Transient fetch error: retry unless last attempt
            if attempt < attempts:
                _sleep(delay_seconds)
                continue
            raise ReleaseStateError(
                f"All {attempts} verification attempts failed; last error: {exc}"
            ) from exc

        # Check each expected file
        missing_filenames = []
        for filename, expected_sha in files.items():
            remote_sha = remote.get(filename)
            if remote_sha is None:
                missing_filenames.append(filename)
            elif remote_sha != expected_sha:
                raise _RemoteHashConflictError(
                    f"Hash conflict for '{filename}': "
                    f"expected={expected_sha!r} remote={remote_sha!r}"
                )
            # else: matches, good

        if not missing_filenames:
            return  # Success

        if attempt < attempts:
            _sleep(delay_seconds)

    raise ReleaseStateError(
        f"Files not confirmed on PyPI after {attempts} attempts: {missing_filenames}"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pypi_release",
        description="Safely stage and verify PyPI distribution artifacts.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # prepare subcommand
    p_prepare = sub.add_parser("prepare", help="Stage distributions for upload")
    p_prepare.add_argument("--source", required=True, type=Path, metavar="DIR")
    p_prepare.add_argument("--staging", required=True, type=Path, metavar="DIR")
    p_prepare.add_argument("--project", required=True)
    p_prepare.add_argument("--version", required=True)
    p_prepare.add_argument("--manifest", required=True, type=Path, metavar="FILE")
    p_prepare.add_argument("--github-output", type=Path, metavar="FILE", default=None)

    # verify subcommand
    p_verify = sub.add_parser("verify", help="Verify remote hashes against manifest")
    p_verify.add_argument("--manifest", required=True, type=Path, metavar="FILE")
    p_verify.add_argument("--attempts", type=int, default=6, metavar="N")
    p_verify.add_argument("--delay-seconds", type=float, default=10.0, metavar="SEC")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_usage(sys.stderr)
        sys.exit(1)

    try:
        if args.command == "prepare":
            prepare(
                source=args.source,
                project=args.project,
                version=args.version,
                staging=args.staging,
                manifest_path=args.manifest,
                github_output=args.github_output,
            )
        elif args.command == "verify":
            verify(
                manifest_path=args.manifest,
                attempts=args.attempts,
                delay_seconds=args.delay_seconds,
            )
    except ReleaseStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
