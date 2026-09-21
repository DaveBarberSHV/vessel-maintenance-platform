#!/usr/bin/env bash
# Phase 3 of the Streamlit Cloud -> Cloud Run migration (Sept 2026).
# Run this AFTER setup_cloud_run.sh has succeeded and you've verified
# the Cloud Run URL works on its own. Maps polaris.fathomvessel.com to
# the fathom-polaris Cloud Run service and prints the DNS record to add
# in GoDaddy. Google auto-provisions the SSL cert once DNS resolves
# (usually 24-48 hours).
set -euo pipefail

PROJECT_ID="project-dbe3feed-c30f-4b89-a65"
REGION="us-west1"
SERVICE_NAME="fathom-polaris"
DOMAIN="polaris.fathomvessel.com"

gcloud config set project "$PROJECT_ID"

echo "==> Mapping ${DOMAIN} to Cloud Run service ${SERVICE_NAME}..."
gcloud beta run domain-mappings create \
  --service="$SERVICE_NAME" \
  --domain="$DOMAIN" \
  --region="$REGION"

echo
echo "==> DNS record to add in GoDaddy for ${DOMAIN}:"
gcloud beta run domain-mappings describe \
  --domain="$DOMAIN" \
  --region="$REGION" \
  --format="table(status.resourceRecords[].name, status.resourceRecords[].type, status.resourceRecords[].rrdata)"

echo
echo "Add the record shown above in GoDaddy's DNS management for fathomvessel.com."
echo "SSL cert auto-provisions once DNS resolves (usually 24-48 hours)."
