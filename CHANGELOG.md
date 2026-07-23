# Changelog

All notable changes to PaperSqueeze are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-07-23

### Added

- Renamed the Python distribution to `papersqueeze` while keeping the `file_compressor` import package and adding a `papersqueeze` CLI alias alongside `file-compressor`.
- Automated tag-to-release publishing with main-ancestry checks, quality gates, Python distribution validation, SHA-256 checksums, versioned GHCR image tags, and digest-verified PyPI publication.
- Added tokenless PyPI Trusted Publishing through GitHub OIDC, staging only missing files and re-verifying remote SHA-256 digests after publication.
- Added a tag-dispatched PyPI repair path that reuses verified GitHub Release assets without rebuilding or executing code from the old tag.

### Security

- Isolated untrusted build and test code from release write tokens, locked the package build toolchain, prevented commit/version container tags from being overwritten, and kept PyPI publication free of stored API tokens.

## [0.1.0] - 2026-07-20

### Added

- Target-aware PDF compression with lossless, text-preserving, automatic, and raster modes.
- CLI, local library UI, and anonymous stateless public web interface.
- Deployments for Docker/OCI, Modal, Hugging Face Spaces, and Google Cloud Run.
- RGB SSIM benchmark tooling for size, page geometry, text, links, and transparency checks.
- CI coverage for Python 3.10 and 3.12, dependency auditing, package builds, container smoke tests, and GHCR publication.

### Fixed

- Preserved external PDF soft masks when images are recompressed, including partial alpha, shared masks, indirect image subtypes, and masks with `Matte`.
- Prevented black backgrounds and white boxes around transparent annotations.
- Prevented temporary response files from being deleted before ASGI file streaming completes.

### Security

- Added public-mode upload, page, timeout, rate, and concurrency limits.
- Isolated compression in a worker process and removed temporary files after each response.
- Added restrictive browser security headers and disabled private library routes in public mode.

[Unreleased]: https://github.com/asimfish/pdf-image-compressor/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/asimfish/pdf-image-compressor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/asimfish/pdf-image-compressor/releases/tag/v0.1.0
