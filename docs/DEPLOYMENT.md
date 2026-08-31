# Deployment Guide

English | [中文](DEPLOYMENT_CN.md)

Four ways to run PaperSqueeze as a public, anonymous, stateless compression
site. All of them use the same container image and the same
[configuration variables](../README.md#9--configuration).

- [Docker (any platform)](#docker-any-platform)
- [Modal — free tier](#modal--free-tier)
- [Hugging Face Spaces](#hugging-face-spaces)
- [Google Cloud Run](#google-cloud-run)

---

## Docker (any platform)

Run the published image (built, smoke-tested, and pushed by CI on every `main`
merge; releases also get `vX.Y.Z` / `X.Y.Z` tags):

```bash
docker pull ghcr.io/asimfish/pdf-image-compressor:latest
docker run --rm -p 8080:8080 \
  -e PDF_COMPRESSOR_PUBLIC_MODE=1 \
  -e PDF_COMPRESSOR_MAX_UPLOAD_MB=30 \
  ghcr.io/asimfish/pdf-image-compressor:latest
```

Or build locally:

```bash
docker build -t papersqueeze .
docker run --rm -p 8080:8080 -e PDF_COMPRESSOR_PUBLIC_MODE=1 papersqueeze
```

Open <http://127.0.0.1:8080>; the health check is `GET /api/health`.
The image is platform-neutral OCI — deploy it to any container platform.

To password-protect a public instance, add
`-e PDF_COMPRESSOR_ACCESS_PASSWORD=your-password`.

## Modal — free tier

Modal's Starter plan currently includes $30/month of compute; containers scale
to zero when idle, which suits a low-traffic public site.

```bash
# 1. One-time account setup
uvx modal setup

# 2. Deploy from the repository root
uvx modal deploy deploy_modal.py
```

The script requests 2 CPUs, 2 GiB memory, and at most 1 container; the app
allows 1 compression job at a time. It scales down after 60 s idle and keeps no
uploaded files. Modal prints a stable `modal.run` HTTPS URL on success.

```bash
uvx modal billing                 # check usage
uvx modal app stop papersqueeze   # stop the app
```

Free-tier terms may change — set a budget alert if your account has a payment
method attached. Cold starts and requests longer than 150 s may see redirects
or delays.

## Hugging Face Spaces

Docker Spaces require a PRO subscription for `cpu-basic` at the moment; free
accounts can only create Static Spaces, which cannot run this Python backend.

```bash
# 1. Log in
uvx --from huggingface_hub hf auth login

# 2. Deploy (creates a public <username>/papersqueeze Space)
uv run --no-project deploy_huggingface_space.py

# Custom org/name
HF_SPACE_ID=your-org/your-space uv run --no-project deploy_huggingface_space.py
```

The Space uses the repository Dockerfile, listens on port 8080, and enables
public stateless mode by default. The script deploys a full snapshot — files
added manually to the Space are removed. Instances may sleep; the first visit
after idling waits for a cold start.

## Google Cloud Run

A repeatable deploy script ships with the repository. Defaults: Tokyo region,
1 GiB memory, concurrency 1, max 1 instance, scale-to-zero.

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
bash deploy_cloud_run.sh

# Override defaults
CLOUD_RUN_REGION=asia-east1 CLOUD_RUN_SERVICE=my-pdf-compressor bash deploy_cloud_run.sh
```

Notes:

- Cloud Run's HTTP/1 request cap is 32 MiB, so the public default upload limit
  of 30 MiB fits.
- 1 GiB memory is a safe floor based on a real ~689 MiB peak sample.
- `--max-instances 1` and `--concurrency 1` bound the cost of an anonymous
  public service; also set a budget alert in Google Cloud.
- The script enables the Cloud Run, Cloud Build, and Artifact Registry APIs;
  the first build may incur small charges.
- Anonymous mode has no per-user quota. For long-running high-traffic
  deployments, put Cloud Armor, API Gateway, or another rate-limiting or
  authentication layer in front — or set `PDF_COMPRESSOR_ACCESS_PASSWORD`.
