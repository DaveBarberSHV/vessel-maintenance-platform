#!/usr/bin/env bash
# One-time setup for Phase 4: lets GitHub Actions deploy to Cloud Run
# without a downloadable service account key (Workload Identity
# Federation instead). Run this once, by hand, before the
# .github/workflows/deploy.yml workflow can work. Prints the values
# to paste into GitHub repo Settings -> Secrets and variables -> Actions
# as WIF_PROVIDER and WIF_SERVICE_ACCOUNT. Nothing here is a secret
# value in the credential sense (no API key, no private key file) --
# these are resource identifiers, safe to store as repo secrets or even
# read on screen.
set -euo pipefail

PROJECT_ID="project-dbe3feed-c30f-4b89-a65"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
POOL_NAME="github-pool"
PROVIDER_NAME="github-provider"
SA_NAME="github-deployer"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
GITHUB_REPO="DaveBarberSHV/vessel-maintenance-platform"

gcloud config set project "$PROJECT_ID"

echo "==> Enabling IAM Credentials API..."
gcloud services enable iamcredentials.googleapis.com

echo "==> Creating deployer service account (if it doesn't exist)..."
gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name="GitHub Actions deployer"

echo "==> Granting deploy permissions to the service account..."
for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$role" \
    --condition=None >/dev/null
done

echo "==> Creating Workload Identity Pool (if it doesn't exist)..."
gcloud iam workload-identity-pools describe "$POOL_NAME" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL_NAME" \
    --location=global \
    --display-name="GitHub Actions pool"

echo "==> Creating OIDC provider restricted to this repo (if it doesn't exist)..."
gcloud iam workload-identity-pools providers describe "$PROVIDER_NAME" \
  --location=global --workload-identity-pool="$POOL_NAME" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_NAME" \
    --location=global \
    --workload-identity-pool="$POOL_NAME" \
    --display-name="GitHub provider" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='${GITHUB_REPO}'" \
    --issuer-uri="https://token.actions.githubusercontent.com"

echo "==> Allowing this repo's workflow to impersonate the deployer service account..."
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_NAME}/attribute.repository/${GITHUB_REPO}"

WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_NAME}/providers/${PROVIDER_NAME}"

echo
echo "==> Add these as GitHub repo secrets (Settings > Secrets and variables > Actions):"
echo "WIF_PROVIDER=${WIF_PROVIDER}"
echo "WIF_SERVICE_ACCOUNT=${SA_EMAIL}"
