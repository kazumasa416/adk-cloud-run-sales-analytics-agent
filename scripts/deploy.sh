#!/usr/bin/env bash
# Deploy the agent service to a private Cloud Run service with Agents CLI.
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project ID.}"
: "${REGION:=asia-northeast1}"
: "${SERVICE_NAME:=sales-analytics-agent}"
: "${ROOT_MODEL:=gemini-4-hydrogen}"
: "${GOOGLE_CLOUD_LOCATION:=global}"
: "${DATA_AGENT_LOCATION:=us}"
: "${CATALOG_LOCATION:=us}"
: "${MAX_INSTANCES:=1}"

command -v agents-cli >/dev/null || {
  echo "ERROR: agents-cli is not installed. Run: uvx google-agents-cli setup" >&2
  exit 1
}

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
AGENT_SA="${AGENT_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

ENV_VARS="GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
ENV_VARS="${ENV_VARS},GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION},ROOT_MODEL=${ROOT_MODEL}"
ENV_VARS="${ENV_VARS},DATA_AGENT_LOCATION=${DATA_AGENT_LOCATION},CATALOG_LOCATION=${CATALOG_LOCATION}"
for key in DATA_AGENT_ID_RAW DATA_AGENT_ID_DEFINED; do
  if [[ -n "${!key:-}" ]]; then ENV_VARS="${ENV_VARS},${key}=${!key}"; fi
done
# Cloud Run forwards a mostly-unsampled trace context. Without always_on the
# default parent-based sampler drops every agent span.
ENV_VARS="${ENV_VARS},TRACE_TO_CLOUD=true,OTEL_TRACES_SAMPLER=always_on"
if [[ -n "${SESSION_SERVICE_URI:-}" ]]; then
  ENV_VARS="${ENV_VARS},SESSION_SERVICE_URI=${SESSION_SERVICE_URI}"
fi

agents-cli deploy \
  --deployment-target cloud_run \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --service-name "${SERVICE_NAME}" \
  --service-account "${AGENT_SA}" \
  --no-agent-identity \
  --max-instances "${MAX_INSTANCES}" \
  --memory 1Gi \
  --timeout 600 \
  --env-vars "${ENV_VARS}" \
  "$@"
