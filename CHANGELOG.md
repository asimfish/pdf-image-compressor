# Changelog

All notable changes to PaperSqueeze are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `fidelity` compression mode as the new default: quality-first, never rasterizes, and prefers the lossless candidate when it fits the target size.
- Optional password access gate for public deployments via `PDF_COMPRESSOR_ACCESS_PASSWORD`, with a 30-day per-browser cookie.
- Bilingual documentation: English `README.md` with Chinese `README_CN.md`, deployment guides `docs/DEPLOYMENT.md` / `docs/DEPLOYMENT_CN.md`, a commented `.env.example`, and `CITATION.cff`.
- Automated tag-to-release publishing with version checks, quality gates, Python distribution validation, SHA-256 checksums, versioned GHCR image tags, and a non-executing repair path for existing releases.

### Changed

- `auto` mode no longer rasterizes pages to hit a target size; it returns the closest text-preserving result instead. Only the explicit `raster` mode trades away the text layer.
- `fidelity` mode now honors a byte budget: after the lossless and near-lossless passes it walks the keep-text quality ladder (from q95) and returns the highest quality that fits, downscaling only when the budget cannot be met at native resolution. Previously it gave up as soon as q92 did not fit and returned a file far above the target.
- The keep-text target search now refines quality between ladder rungs (binary search), refines the resolution step together with quality on the lower rungs, climbs above the top rung when the budget allows, and locates the fitting rung with a galloping bisection instead of walking every rung.
- Lower ladder rungs cap image resolution relative to each image's largest placement on the page (300 → 72 DPI) instead of downscaling every image uniformly, so icons drawn at native size keep every pixel while oversampled figures give up invisible detail.
- When the budget is unreachable without rasterizing, the search returns the highest-quality rung within 10% of the smallest achievable size instead of the lowest rung.
- CMYK images are converted through MuPDF's colour pipeline (ICC-aware) rather than Pillow's naive formula; measured closest to Quartz, Ghostscript and MuPDF renderings of the original.
- Each image is extracted once per document and re-encoded on a thread pool for every ladder rung (scanned-book benchmark 738 s → 132 s).

### Fixed

- Re-encoded images are now rewritten in place instead of through `page.replace_image`, which left literal `null` dictionary entries (`/Interpolate null`, `/Intent null`, …). Ghostscript type-checks those entries and dropped the whole image, rendering blank pages; the rewrite also keeps `/SMask`, `/Interpolate`, `/Intent`, `/OC`, `/StructParent` and a still-valid ICC `/ColorSpace` untouched.
- Wide-gamut images (e.g. Display P3 notes exports) kept their ICC colourspace; previously every re-encoded image was rebound to a generic sRGB profile and rendered washed out.
- Palette (`/Indexed`), Separation/DeviceN, Lab, `/ImageMask` stencil, `/Mask`-keyed and inverted-`/Decode` DCT images are no longer re-encoded into mismatched colourspaces; their colours were scrambled before.
- A partially rewritten image object aborts compression instead of saving a corrupt document.

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
