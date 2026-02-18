#!/bin/bash
# RunPod bootstrap script — runs as the pod's docker_args entrypoint.
# Environment variables set by RunPodBackend.provision():
#   RUN_ID, S3_BUCKET, S3_PREFIX, AWS_DEFAULT_REGION,
#   EXPERIMENT_COMMAND, MAX_HOURS,
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (for S3 access)

set -uo pipefail

WORKDIR="/workspace/experiment"
LOGFILE="/workspace/experiment.log"
S3_BASE="s3://${S3_BUCKET}/${S3_PREFIX}"
EXIT_CODE=1

# Trap: on ANY exit, upload log + exit_code, then stop the pod.
cleanup() {
    echo "=== Cleanup: uploading log and exit_code ($EXIT_CODE) ==="
    aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    echo "$EXIT_CODE" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    # Stop the pod via RunPod API (runpodctl isn't in the container)
    if [ -n "${RUNPOD_POD_ID:-}" ] && [ -n "${RUNPOD_API_KEY:-}" ]; then
        curl -s -X POST https://api.runpod.io/graphql \
            -H "Authorization: Bearer $RUNPOD_API_KEY" \
            -H "Content-Type: application/json" \
            -d "{\"query\": \"mutation { podStop(input: {podId: \\\"$RUNPOD_POD_ID\\\"}) { id desiredStatus }}\"}" \
            > /dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

exec > >(tee -a "$LOGFILE") 2>&1
echo "=== RunPod bootstrap start: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "RUN_ID=$RUN_ID"
echo "EXPERIMENT_COMMAND=$EXPERIMENT_COMMAND"
echo "MAX_HOURS=$MAX_HOURS"

# -----------------------------------------------------------------------
# 1. Install AWS CLI (for S3 access)
# -----------------------------------------------------------------------
echo "=== Installing uv ==="
pip install -q uv 2>/dev/null || true

if ! command -v aws &>/dev/null; then
    echo "=== Installing AWS CLI ==="
    uv pip install --system -q awscli 2>/dev/null || {
        curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
        cd /tmp && unzip -q awscliv2.zip && ./aws/install --update
        cd /workspace
    }
fi

# -----------------------------------------------------------------------
# 2. Signal start
# -----------------------------------------------------------------------
echo "started" | aws s3 cp - "${S3_BASE}/status" --region "$AWS_DEFAULT_REGION"

# -----------------------------------------------------------------------
# 3. Pull code and data from S3
# -----------------------------------------------------------------------
echo "=== Pulling code from S3 ==="
mkdir -p "$WORKDIR"
aws s3 cp "${S3_BASE}/code.tar.gz" /tmp/code.tar.gz --region "$AWS_DEFAULT_REGION"
tar xzf /tmp/code.tar.gz -C "$WORKDIR"
rm /tmp/code.tar.gz

echo "=== Pulling data from S3 ==="
if aws s3 ls "${S3_BASE}/data/" --region "$AWS_DEFAULT_REGION" 2>/dev/null; then
    mkdir -p "${WORKDIR}/data"
    aws s3 sync "${S3_BASE}/data/" "${WORKDIR}/data/" --region "$AWS_DEFAULT_REGION"
fi

# -----------------------------------------------------------------------
# 4. Install Python dependencies
# -----------------------------------------------------------------------
if [ -f "${WORKDIR}/requirements.txt" ]; then
    echo "=== Installing dependencies ==="
    uv pip install --system --no-cache-dir -q -r "${WORKDIR}/requirements.txt"
fi

# -----------------------------------------------------------------------
# 5. Watchdog: auto-terminate after MAX_HOURS
# -----------------------------------------------------------------------
MAX_SECONDS=$(python3 -c "print(int(float('$MAX_HOURS') * 3600))")
echo "=== Watchdog: will terminate after ${MAX_HOURS}h (${MAX_SECONDS}s) ==="
(
    sleep "$MAX_SECONDS"
    echo "=== WATCHDOG: Max hours ($MAX_HOURS) reached. Terminating. ==="
    if [ -d "${WORKDIR}/results" ]; then
        aws s3 sync "${WORKDIR}/results/" "${S3_BASE}/results/" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    fi
    EXIT_CODE=124
    exit 124
) &
WATCHDOG_PID=$!

# -----------------------------------------------------------------------
# 6. Run experiment
# -----------------------------------------------------------------------
echo "=== Running experiment ==="
echo "Command: $EXPERIMENT_COMMAND"
cd "$WORKDIR"

EXIT_CODE=0
eval "$EXPERIMENT_COMMAND" >> "$LOGFILE" 2>&1 || EXIT_CODE=$?

echo "=== Experiment finished with exit code: $EXIT_CODE ==="

# Kill watchdog
kill $WATCHDOG_PID 2>/dev/null || true

# -----------------------------------------------------------------------
# 7. Upload results to S3
# -----------------------------------------------------------------------
echo "=== Uploading results to S3 ==="
if [ -d "${WORKDIR}/results" ]; then
    aws s3 sync "${WORKDIR}/results/" "${S3_BASE}/results/" --region "$AWS_DEFAULT_REGION"
fi

echo "=== Bootstrap complete: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
# EXIT_CODE is set; cleanup trap will upload it and stop the pod.
