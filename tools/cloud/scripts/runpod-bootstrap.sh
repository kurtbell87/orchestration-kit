#!/bin/bash
# RunPod bootstrap script — runs as the pod's docker_args entrypoint.
# Environment variables set by RunPodBackend.provision():
#   RUN_ID, S3_BUCKET, S3_PREFIX, AWS_DEFAULT_REGION,
#   EXPERIMENT_COMMAND, MAX_HOURS,
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (for S3 access)

set -euo pipefail

WORKDIR="/workspace/experiment"
LOGFILE="/workspace/experiment.log"
S3_BASE="s3://${S3_BUCKET}/${S3_PREFIX}"

exec > >(tee -a "$LOGFILE") 2>&1
echo "=== RunPod bootstrap start: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "RUN_ID=$RUN_ID"
echo "EXPERIMENT_COMMAND=$EXPERIMENT_COMMAND"
echo "MAX_HOURS=$MAX_HOURS"

# -----------------------------------------------------------------------
# 1. Install AWS CLI (for S3 access)
# -----------------------------------------------------------------------
if ! command -v aws &>/dev/null; then
    echo "=== Installing AWS CLI ==="
    pip install -q awscli 2>/dev/null || {
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
    pip install --no-cache-dir -q -r "${WORKDIR}/requirements.txt"
fi

# -----------------------------------------------------------------------
# 5. Watchdog: auto-terminate after MAX_HOURS
# -----------------------------------------------------------------------
MAX_SECONDS=$(echo "$MAX_HOURS * 3600" | bc | cut -d. -f1)
echo "=== Watchdog: will terminate after ${MAX_HOURS}h (${MAX_SECONDS}s) ==="
(
    sleep "$MAX_SECONDS"
    echo "=== WATCHDOG: Max hours ($MAX_HOURS) reached. Terminating. ==="
    if [ -d "${WORKDIR}/results" ]; then
        aws s3 sync "${WORKDIR}/results/" "${S3_BASE}/results/" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    fi
    aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    echo "124" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION"
    # RunPod: signal stop via API if available, otherwise just exit
    runpodctl stop pod "$RUNPOD_POD_ID" 2>/dev/null || exit 124
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

aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION"
echo "$EXIT_CODE" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION"

echo "=== Bootstrap complete: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# RunPod: stop the pod (billed per second, don't waste money)
runpodctl stop pod "$RUNPOD_POD_ID" 2>/dev/null || true
