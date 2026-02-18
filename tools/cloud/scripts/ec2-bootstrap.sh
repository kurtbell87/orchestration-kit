#!/bin/bash
# EC2 bootstrap script — runs as user-data on instance launch.
# Variables injected by AWSBackend._render_user_data():
#   RUN_ID, S3_BUCKET, S3_PREFIX, AWS_DEFAULT_REGION,
#   EXPERIMENT_COMMAND, MAX_HOURS

set -euo pipefail

WORKDIR="/opt/experiment"
LOGFILE="/var/log/experiment.log"
S3_BASE="s3://${S3_BUCKET}/${S3_PREFIX}"

# Trap: ensure exit_code is always written, even on unexpected failure
trap '_ec=${FINAL_EXIT_CODE:-$?}; echo "$_ec" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION" 2>/dev/null; exit $_ec' EXIT

exec > >(tee -a "$LOGFILE") 2>&1
echo "=== Bootstrap start: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "RUN_ID=$RUN_ID"
echo "EXPERIMENT_COMMAND=$EXPERIMENT_COMMAND"
echo "MAX_HOURS=$MAX_HOURS"

# -----------------------------------------------------------------------
# 1. Signal that the instance has started
# -----------------------------------------------------------------------
echo "started" | aws s3 cp - "${S3_BASE}/status" --region "$AWS_DEFAULT_REGION"

# -----------------------------------------------------------------------
# 2. Install Docker
# -----------------------------------------------------------------------
echo "=== Installing Docker ==="
yum update -y -q
yum install -y -q docker
systemctl start docker
systemctl enable docker

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
# 4. Watchdog: auto-terminate after MAX_HOURS
# -----------------------------------------------------------------------
MAX_SECONDS=$(echo "$MAX_HOURS * 3600" | bc | cut -d. -f1)
echo "=== Watchdog: will terminate after ${MAX_HOURS}h (${MAX_SECONDS}s) ==="
(
    sleep "$MAX_SECONDS"
    echo "=== WATCHDOG: Max hours ($MAX_HOURS) reached. Terminating. ==="

    # Upload partial results before shutdown
    if [ -d "${WORKDIR}/results" ]; then
        aws s3 sync "${WORKDIR}/results/" "${S3_BASE}/results/" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    fi
    aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    echo "124" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION"

    shutdown -h now
) &
WATCHDOG_PID=$!

# -----------------------------------------------------------------------
# 5. Run experiment in Docker container
# -----------------------------------------------------------------------
echo "=== Running experiment ==="
echo "Command: $EXPERIMENT_COMMAND"

# Build a requirements install step if requirements.txt exists
INSTALL_CMD=""
if [ -f "${WORKDIR}/requirements.txt" ]; then
    INSTALL_CMD="pip install -q uv && uv pip install --system --no-cache-dir -q -r /work/requirements.txt &&"
fi

EXIT_CODE=0
docker run --rm \
    -v "${WORKDIR}:/work" \
    -w /work \
    -e AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
    -e RUN_ID="$RUN_ID" \
    python:3.12-slim \
    bash -c "${INSTALL_CMD} ${EXPERIMENT_COMMAND}" \
    >> "$LOGFILE" 2>&1 || EXIT_CODE=$?

echo "=== Experiment finished with exit code: $EXIT_CODE ==="

# Kill watchdog
kill $WATCHDOG_PID 2>/dev/null || true

# -----------------------------------------------------------------------
# 6. Upload results to S3
# -----------------------------------------------------------------------
echo "=== Uploading results to S3 ==="
if [ -d "${WORKDIR}/results" ]; then
    aws s3 sync "${WORKDIR}/results/" "${S3_BASE}/results/" --region "$AWS_DEFAULT_REGION"
fi

# Upload full log
aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION"

# Write exit code marker (polled by local cloud-run)
echo "$EXIT_CODE" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION"

echo "=== Bootstrap complete: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# -----------------------------------------------------------------------
# 7. Shutdown (instance-initiated shutdown behavior = terminate)
# -----------------------------------------------------------------------
FINAL_EXIT_CODE=$EXIT_CODE
shutdown -h now
