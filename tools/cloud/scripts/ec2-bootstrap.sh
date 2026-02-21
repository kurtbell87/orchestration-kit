#!/bin/bash
# EC2 bootstrap script — runs as user-data on ECS-optimized AMI.
# Docker is pre-installed. Data is on an EBS volume from snapshot.
#
# Variables injected by AWSBackend._render_user_data():
#   RUN_ID, S3_BUCKET, S3_PREFIX, AWS_DEFAULT_REGION,
#   EXPERIMENT_COMMAND, MAX_HOURS, IMAGE_URI, EBS_DATA_DEVICE

set -euo pipefail

LOGFILE="/var/log/experiment.log"
S3_BASE="s3://${S3_BUCKET}/${S3_PREFIX}"

trap '_ec=${FINAL_EXIT_CODE:-$?}; aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION" 2>/dev/null; echo "$_ec" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION" 2>/dev/null; exit $_ec' EXIT

exec > >(tee -a "$LOGFILE") 2>&1
echo "=== Bootstrap start: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "RUN_ID=$RUN_ID  IMAGE_URI=$IMAGE_URI  MAX_HOURS=$MAX_HOURS"

# 1. Signal start
echo "started" | aws s3 cp - "${S3_BASE}/status" --region "$AWS_DEFAULT_REGION"

# 2. Mount EBS data volume
# Nitro instances map /dev/xvdf to /dev/nvme*n1 — find the actual device
echo "=== Mounting EBS data volume ==="
DATA_DEVICE="$EBS_DATA_DEVICE"
for i in $(seq 1 30); do
    [ -b "$DATA_DEVICE" ] && break
    # Fallback: find non-root NVMe device (root is nvme0n1)
    for dev in /dev/nvme1n1 /dev/nvme2n1 /dev/xvdf; do
        [ -b "$dev" ] && DATA_DEVICE="$dev" && break 2
    done
    sleep 2
done
mkdir -p /data
mount -o ro "$DATA_DEVICE" /data
echo "Mounted $DATA_DEVICE at /data ($(df -h /data | tail -1 | awk '{print $3}') used)"

# 3. ECR login (IAM instance profile provides auth)
REGISTRY=$(echo "$IMAGE_URI" | cut -d/ -f1)
aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$REGISTRY"

# 4. Pull image (retry up to 3 times — ECR can have transient timeouts)
echo "=== Pulling $IMAGE_URI ==="
for attempt in 1 2 3; do
    docker pull "$IMAGE_URI" && break
    echo "Pull attempt $attempt failed, retrying in 10s..."
    sleep 10
done

# 5. Watchdog
MAX_SECONDS=$(awk "BEGIN{printf \"%d\", $MAX_HOURS * 3600}")
(
    sleep "$MAX_SECONDS"
    echo "=== WATCHDOG: ${MAX_HOURS}h reached ==="
    aws s3 sync /opt/results/ "${S3_BASE}/results/" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    echo "124" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION"
    shutdown -h now
) &
WATCHDOG_PID=$!

# 6. Run experiment
echo "=== Running: $EXPERIMENT_COMMAND ==="
mkdir -p /opt/results
EXIT_CODE=0
docker run --rm \
    -v /data:/data:ro \
    -v /opt/results:/work/results \
    -e AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
    -e RUN_ID="$RUN_ID" \
    "$IMAGE_URI" \
    bash -c "$EXPERIMENT_COMMAND" \
    >> "$LOGFILE" 2>&1 || EXIT_CODE=$?

echo "=== Experiment finished: exit code $EXIT_CODE ==="
kill $WATCHDOG_PID 2>/dev/null || true

# 7. Upload results + shutdown
aws s3 sync /opt/results/ "${S3_BASE}/results/" --region "$AWS_DEFAULT_REGION" || true
aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION"
echo "$EXIT_CODE" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION"

FINAL_EXIT_CODE=$EXIT_CODE
shutdown -h now
