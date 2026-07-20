# Changelog

All notable changes to PaperSqueeze are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Automated tag-to-release publishing with version checks, quality gates, Python distribution validation, SHA-256 checksums, and versioned GHCR image tags.

### Security

- Isolated untrusted build and test code from release write tokens, locked the package build toolchain, and prevented commit/version container tags from being overwritten.

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

[Unreleased]: https://github.com/asimfish/pdf-image-compressor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/asimfish/pdf-image-compressor/releases/tag/v0.1.0
