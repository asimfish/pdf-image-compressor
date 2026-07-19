#!/usr/bin/env bash
set -euo pipefail

service="${CLOUD_RUN_SERVICE:-papersqueeze}"
region="${CLOUD_RUN_REGION:-asia-northeast1}"
project="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"

if [[ -z "$project" || "$project" == "(unset)" ]]; then
  echo "Set GOOGLE_CLOUD_PROJECT or run: gcloud config set project PROJECT_ID" >&2
  exit 1
fi

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project "$project" \
  --quiet

gcloud run deploy "$service" \
  --project "$project" \
  --region "$region" \
  --source . \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --concurrency 1 \
  --min-instances 0 \
  --max-instances 1 \
  --timeout 900 \
  --set-env-vars "PDF_COMPRESSOR_PUBLIC_MODE=1,PDF_COMPRESSOR_MAX_UPLOAD_MB=30,PDF_COMPRESSOR_MAX_PAGES=100,PDF_COMPRESSOR_UPLOAD_TIMEOUT_SECONDS=120,PDF_COMPRESSOR_PROCESSING_TIMEOUT_SECONDS=300,PDF_COMPRESSOR_DOWNLOAD_TIMEOUT_SECONDS=120,PDF_COMPRESSOR_RATE_LIMIT_PER_MINUTE=12,PDF_COMPRESSOR_CONCURRENCY=1" \
  --quiet

gcloud run services describe "$service" \
  --project "$project" \
  --region "$region" \
  --format="value(status.url)"
