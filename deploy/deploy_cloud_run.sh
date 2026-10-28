#!/usr/bin/env bash
# Deploy CycleSite Copilot to Cloud Run with the official ADK deployer.
#
#   GOOGLE_CLOUD_PROJECT=my-project ./deploy/deploy_cloud_run.sh
#
# The core library (cyclesite/) is shipped next to the agent with --extra_packages, because
# `adk deploy` otherwise copies only the agent folder.
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-cyclesite-copilot}"
DATASET="${CYCLESITE_DATASET:-cyclesite}"
SA="cyclesite-agent@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
cd "$(dirname "$0")/.."

# 1. Run the tests first: never ship a red build.
python -m pytest -q

# 2. One-time, idempotent: a least-privilege service account for the running agent.
if ! gcloud iam service-accounts describe "$SA" --project "$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create cyclesite-agent --project "$GOOGLE_CLOUD_PROJECT" \
    --display-name "CycleSite Copilot runtime"
fi
for role in roles/aiplatform.user roles/bigquery.jobUser roles/mcp.toolUser \
            roles/logging.logWriter roles/cloudtrace.agent; do
  gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
    --member "serviceAccount:${SA}" --role "$role" --condition=None --quiet >/dev/null
done
# NOTE: the agent reads our own dataset with a dataset-level grant, not a project-wide one.
# Give ${SA} the BigQuery Data Viewer role on dataset "${DATASET}" only (see docs/DEPLOYMENT.md).
echo "Reminder: grant ${SA} 'BigQuery Data Viewer' on dataset ${DATASET} (docs/DEPLOYMENT.md, step 4)."

# 3. Deploy. Authentication is required: the service is NOT public.
adk deploy cloud_run \
  --project "$GOOGLE_CLOUD_PROJECT" --region "$REGION" \
  --service_name "$SERVICE" --app_name cycle_agent \
  --with_ui --otel_to_cloud --trace_to_cloud \
  --extra_packages ./cyclesite \
  --env "GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}" \
  --env "GOOGLE_CLOUD_LOCATION=global" \
  --env "GOOGLE_GENAI_USE_ENTERPRISE=True" \
  --env "CYCLESITE_BACKEND=bigquery" \
  --env "CYCLESITE_DATASET=${DATASET}" \
  --env "CYCLESITE_MAX_BYTES_BILLED=${CYCLESITE_MAX_BYTES_BILLED:-10000000000}" \
  ./cycle_agent \
  -- --no-allow-unauthenticated --service-account "$SA" \
     --min-instances 0 --max-instances 3 --concurrency 20 --memory 1Gi
echo "Deployed. Open it with: gcloud run services proxy ${SERVICE} --region ${REGION} --project ${GOOGLE_CLOUD_PROJECT}"
