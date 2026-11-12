#!/usr/bin/env bash
# Cloud deploy entry point. Does nothing unless ENABLE_CLOUD_DEPLOY=true.
set -euo pipefail
FLAG="${ENABLE_CLOUD_DEPLOY:-false}"
if [[ "${FLAG}" != "true" ]]; then
  echo "Cloud deploy disabled by feature flag (ENABLE_CLOUD_DEPLOY=${FLAG}). Nothing applied."
  exit 1
fi
cd "$(dirname "$0")/terraform"
terraform init -input=false
terraform apply -input=false -auto-approve -var "enable_cloud_deploy=true"
