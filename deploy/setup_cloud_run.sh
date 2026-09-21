#!/usr/bin/env bash
# Phase 2 of the Streamlit Cloud -> Cloud Run migration (Sept 2026).
#
# Secrets note: the original migration plan called for this script to
# interactively prompt for VOYAGE_API_KEY / ANTHROPIC_API_KEY /
# SUPABASE_DB_URL. That would have created a second place these values
# get typed, on top of the existing single source of truth
# (~/.streamlit/secrets.toml, loaded into the shell by ~/.fathom_env).
# Instead this script reads them from the environment it's run in and
# fails loudly if any are missing -- it never echoes a value and never
# writes one to a file. Run it exactly like any other script that needs
# these keys: source ~/.fathom_env first.
set -euo pipefail

PROJECT_ID="project-dbe3feed-c30f-4b89-a65"
REGION="us-west1"
SERVICE_NAME="fathom-polaris"
REPO_NAME="fathom-polaris"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:latest"

# Dedicated runtime service account (Sept 2026 IAM hardening) -- NOT the
# Compute Engine default SA. Using the shared default SA as the app's
# runtime identity would let it read every secret and resource that SA
# is or ever becomes entangled with project-wide. This SA's only grants
# are secretAccessor on exactly the 3 secrets below, plus logWriter/
# metricWriter (required for a custom Cloud Run runtime SA to emit logs
# at all -- the default SA gets these implicitly, a dedicated one does
# not). Do not remove --service-account from the deploy command below --
# omitting it on a redeploy falls back to the default SA and silently
# undoes this hardening.
RUN_SA="fathom-polaris-run@${PROJECT_ID}.iam.gserviceaccount.com"

for var in VOYAGE_API_KEY ANTHROPIC_API_KEY SUPABASE_DB_URL; do
  if [ -z "${!var:-}" ]; then
    echo "ERROR: \$${var} is not set in this shell." >&2
    echo "Run: source ~/.fathom_env" >&2
    exit 1
  fi
done

gcloud config set project "$PROJECT_ID"

echo "==> Enabling required APIs..."
gcloud services enable \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com

echo "==> Creating Artifact Registry repo (if it doesn't exist)..."
gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Fathom - Polaris Cloud Run images"

echo "==> Creating dedicated Cloud Run runtime service account (if it doesn't exist)..."
gcloud iam service-accounts describe "$RUN_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$(echo "$RUN_SA" | cut -d@ -f1)" \
    --display-name="Fathom Polaris Cloud Run runtime"

for role in roles/logging.logWriter roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${RUN_SA}" \
    --role="$role" \
    --condition=None >/dev/null
done

create_or_update_secret() {
  local secret_name="$1"
  local secret_value="$2"
  if gcloud secrets describe "$secret_name" >/dev/null 2>&1; then
    echo "==> Secret $secret_name already exists, adding a new version..."
    printf '%s' "$secret_value" | gcloud secrets versions add "$secret_name" --data-file=-
  else
    echo "==> Creating secret $secret_name..."
    printf '%s' "$secret_value" | gcloud secrets create "$secret_name" --data-file=- --replication-policy=automatic
  fi
}

create_or_update_secret "VOYAGE_API_KEY" "$VOYAGE_API_KEY"
create_or_update_secret "ANTHROPIC_API_KEY" "$ANTHROPIC_API_KEY"
create_or_update_secret "SUPABASE_DB_URL" "$SUPABASE_DB_URL"

echo "==> Granting the runtime SA read access to each secret individually (not project-level)..."
for secret in VOYAGE_API_KEY ANTHROPIC_API_KEY SUPABASE_DB_URL; do
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:${RUN_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None >/dev/null
done

echo "==> Building and pushing Docker image via Cloud Build..."
gcloud builds submit --tag "$IMAGE" .

echo "==> Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
  --image="$IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --min-instances=1 \
  --max-instances=3 \
  --memory=512Mi \
  --cpu=1 \
  --port=8080 \
  --service-account="$RUN_SA" \
  --set-secrets="VOYAGE_API_KEY=VOYAGE_API_KEY:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,SUPABASE_DB_URL=SUPABASE_DB_URL:latest"

echo "==> Done. Service URL:"
gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format="value(status.url)"
