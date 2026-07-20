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
import json
import re
import shutil
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from email import message_from_string
from pathlib import Path
from typing import Callable

# ── Public types ──────────────────────────────────────────────────────────────

MANIFEST_SCHEMA_VERSION = 1

_CHUNK = 65536  # 64 KiB chunks for SHA-256 streaming


class ReleaseStateError(RuntimeError):
    """Raised when local or remote release state is inconsistent or unsafe."""


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


def _check_wheel_members_safe(members: list[str], wheel_name: str) -> None:
    """Raise ReleaseStateError if any ZIP member has unsafe path components."""
    for member in members:
        if member.startswith("/"):
            raise ReleaseStateError(
                f"Wheel {wheel_name}: unsafe absolute-path entry: {member!r}"
            )
        parts = member.replace("\\", "/").split("/")
        if ".." in parts:
            raise ReleaseStateError(
                f"Wheel {wheel_name}: unsafe path-traversal entry: {member!r}"
            )


def _read_wheel_metadata(path: Path) -> str:
    """Extract exactly one *.dist-info/METADATA from a wheel (ZIP). No extraction."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            members = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise ReleaseStateError(
            f"Wheel is not a valid ZIP: {path.name}: {exc}"
        ) from exc

    _check_wheel_members_safe(members, path.name)

    metadata_members = [
        m for m in members if re.match(r"[^/]+\.dist-info/METADATA$", m)
    ]
    if len(metadata_members) == 0:
        raise ReleaseStateError(f"Wheel {path.name}: no *.dist-info/METADATA found")
    if len(metadata_members) > 1:
        raise ReleaseStateError(
            f"Wheel {path.name}: multiple *.dist-info/METADATA entries: {metadata_members}"
        )

    with zipfile.ZipFile(path, "r") as zf:
        return zf.read(metadata_members[0]).decode("utf-8", errors="replace")


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


def _read_sdist_pkginfo(path: Path) -> str:
    """Extract exactly one top-level */PKG-INFO from an sdist (tar.gz). No extraction."""
    try:
        with tarfile.open(path, "r:gz") as tf:
            members = tf.getmembers()
    except tarfile.TarError as exc:
        raise ReleaseStateError(
            f"sdist is not a valid tar.gz: {path.name}: {exc}"
        ) from exc

    _check_tarinfo_safe(members, path.name)

    # top-level: exactly one directory component before PKG-INFO
    pkg_info_members = [
        m
        for m in members
        if re.match(r"[^/]+/PKG-INFO$", m.name)
        and not m.name.startswith("/")
        and ".." not in m.name.split("/")
    ]
    if len(pkg_info_members) == 0:
        raise ReleaseStateError(f"sdist {path.name}: no top-level PKG-INFO found")
    if len(pkg_info_members) > 1:
        raise ReleaseStateError(
            f"sdist {path.name}: multiple top-level PKG-INFO entries: "
            f"{[m.name for m in pkg_info_members]}"
        )

    with tarfile.open(path, "r:gz") as tf:
        fobj = tf.extractfile(pkg_info_members[0])
        if fobj is None:
            raise ReleaseStateError(
                f"sdist {path.name}: PKG-INFO member is not a regular file"
            )
        return fobj.read().decode("utf-8", errors="replace")


def _parse_metadata(text: str) -> tuple[str, str]:
    """Parse RFC 5322-style metadata; return (Name, Version) or raise."""
    msg = message_from_string(text)
    name = msg.get("Name")
    version = msg.get("Version")
    if not name:
        raise ReleaseStateError("Metadata missing 'Name' header")
    if not version:
        raise ReleaseStateError("Metadata missing 'Version' header")
    return name, version


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
    if not source.is_dir():
        raise ReleaseStateError(f"Source directory does not exist: {source}")

    wheels = sorted(source.glob("*.whl"))
    sdists = sorted(p for p in source.glob("*.tar.gz"))

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
    wheel_meta_text = _read_wheel_metadata(wheel_path)
    w_name, w_version = _parse_metadata(wheel_meta_text)
    if normalize_project_name(w_name) != norm_project:
        raise ReleaseStateError(
            f"Wheel Name '{w_name}' does not match project '{project}'"
        )
    if w_version != version:
        raise ReleaseStateError(
            f"Wheel Version '{w_version}' does not match expected '{version}'"
        )

    # Validate sdist metadata
    sdist_meta_text = _read_sdist_pkginfo(sdist_path)
    s_name, s_version = _parse_metadata(sdist_meta_text)
    if normalize_project_name(s_name) != norm_project:
        raise ReleaseStateError(
            f"sdist Name '{s_name}' does not match project '{project}'"
        )
    if s_version != version:
        raise ReleaseStateError(
            f"sdist Version '{s_version}' does not match expected '{version}'"
        )

    return [
        DistributionFile(
            path=wheel_path,
            filename=wheel_path.name,
            sha256=sha256_file(wheel_path),
        ),
        DistributionFile(
            path=sdist_path,
            filename=sdist_path.name,
            sha256=sha256_file(sdist_path),
        ),
    ]


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
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ReleaseStateError(f"Network error querying PyPI: {exc}") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ReleaseStateError(f"PyPI returned malformed JSON: {exc}") from exc

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

        fn_lower = filename.lower()
        if fn_lower in result:
            if result[fn_lower] != sha256:
                raise ReleaseStateError(
                    f"Duplicate remote filename '{filename}' with conflicting hashes"
                )
            # Same hash: silently ignore duplicate
        else:
            result[fn_lower] = sha256

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
        fn = dist_file.filename.lower()
        remote_hash = remote.get(fn)
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
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    # Copy only the files that need uploading
    for dist_file in missing_files:
        shutil.copy2(dist_file.path, staging / dist_file.filename)

    # Build manifest with ALL local files (not just staged subset)
    manifest: dict = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "project": project,
        "version": version,
        "files": {f.filename: f.sha256 for f in local_files},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Append GitHub Actions output variable
    if github_output is not None:
        publish_value = "true" if missing_files else "false"
        with github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"publish={publish_value}\n")


# ── verify ────────────────────────────────────────────────────────────────────


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
    import time as _time_mod

    if _sleep is None:
        _sleep = _time_mod.sleep

    if not manifest_path.exists():
        raise ReleaseStateError(f"Manifest file not found: {manifest_path}")

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReleaseStateError(f"Manifest is not valid JSON: {exc}") from exc

    schema_version = data.get("schema_version")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise ReleaseStateError(
            f"Unsupported manifest schema_version: {schema_version!r} "
            f"(expected {MANIFEST_SCHEMA_VERSION})"
        )

    project = data.get("project")
    version = data.get("version")
    files: dict[str, str] = data.get("files", {})

    if not isinstance(project, str) or not project:
        raise ReleaseStateError("Manifest missing 'project'")
    if not isinstance(version, str) or not version:
        raise ReleaseStateError("Manifest missing 'version'")
    if not isinstance(files, dict) or not files:
        raise ReleaseStateError("Manifest 'files' is empty or invalid")

    for attempt in range(1, attempts + 1):
        try:
            remote = query_pypi_version(project, version, _fetch=_fetch)
        except ReleaseStateError as exc:
            msg = str(exc)
            # Immediate failure on hash conflicts
            if "conflict" in msg.lower() or "Hash conflict" in msg:
                raise
            # Transient fetch error: retry unless last attempt
            if attempt < attempts:
                _sleep(delay_seconds)
                continue
            raise ReleaseStateError(
                f"All {attempts} verification attempts failed; last error: {exc}"
            ) from exc

        # Check each expected file
        all_present = True
        for filename, expected_sha in files.items():
            fn_lower = filename.lower()
            remote_sha = remote.get(fn_lower)
            if remote_sha is None:
                all_present = False
            elif remote_sha != expected_sha:
                raise ReleaseStateError(
                    f"Hash conflict for '{filename}': "
                    f"expected={expected_sha!r} remote={remote_sha!r}"
                )
            # else: matches, good

        if all_present:
            return  # Success

        if attempt < attempts:
            _sleep(delay_seconds)

    raise ReleaseStateError(
        f"Files not confirmed on PyPI after {attempts} attempts: {list(files.keys())}"
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
