#!/bin/bash
# EC2 bootstrap for GPU instances using PyTorch Deep Learning AMI.
# No Docker — experiment runs directly on the host using /opt/pytorch venv.
#
# Variables injected by AWSBackend._render_user_data():
#   RUN_ID, S3_BUCKET, S3_PREFIX, AWS_DEFAULT_REGION,
#   EXPERIMENT_COMMAND, MAX_HOURS, EBS_DATA_DEVICE (optional)

set -euo pipefail

LOGFILE="/var/log/experiment.log"
S3_BASE="s3://${S3_BUCKET}/${S3_PREFIX}"

trap '_ec=${FINAL_EXIT_CODE:-$?}; aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION" 2>/dev/null; echo "$_ec" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION" 2>/dev/null; exit $_ec' EXIT

exec > >(tee -a "$LOGFILE") 2>&1
echo "=== Bootstrap start (GPU/AMI mode): $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "RUN_ID=$RUN_ID  MAX_HOURS=$MAX_HOURS"

# 1. Signal start
echo "started" | aws s3 cp - "${S3_BASE}/status" --region "$AWS_DEFAULT_REGION"

# 2. Mount EBS data volume (if configured)
if [ -n "${EBS_DATA_DEVICE:-}" ]; then
    echo "=== Mounting EBS data volume ==="
    DATA_DEVICE="$EBS_DATA_DEVICE"
    for i in $(seq 1 30); do
        [ -b "$DATA_DEVICE" ] && break
        for dev in /dev/nvme1n1 /dev/nvme2n1 /dev/xvdf; do
            [ -b "$dev" ] && DATA_DEVICE="$dev" && break 2
        done
        sleep 2
    done
    mkdir -p /data
    mount -o ro "$DATA_DEVICE" /data
    echo "Mounted $DATA_DEVICE at /data ($(df -h /data | tail -1 | awk '{print $3}') used)"
fi

# 3. Activate PyTorch environment
# AWS DL AMIs install PyTorch in /opt/pytorch venv (not conda).
# Put it first on PATH so python3/pip resolve to the venv versions.
echo "=== Activating PyTorch environment ==="
if [ -d /opt/pytorch/bin ]; then
    export PATH="/opt/pytorch/bin:$PATH"
    echo "Activated /opt/pytorch venv"
elif [ -d /opt/conda ]; then
    export PATH="/opt/conda/bin:$PATH"
    source /opt/conda/etc/profile.d/conda.sh 2>/dev/null || true
    conda activate pytorch 2>/dev/null || conda activate base 2>/dev/null || true
    echo "Activated conda pytorch env"
fi

# Report environment
python3 -c "
import torch
print(f'PyTorch {torch.__version__}')
print(f'CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'GPU: {torch.cuda.get_device_name(0)}')
    props = torch.cuda.get_device_properties(0)
    vram = getattr(props, 'total_memory', getattr(props, 'total_mem', 0))
    print(f'VRAM: {vram / 1e9:.1f} GB')
" || echo "WARNING: PyTorch not found in current environment"

# 4. Install extra dependencies
echo "=== Installing dependencies ==="
pip install -q boto3 polars pandas scikit-learn xgboost matplotlib seaborn tqdm 2>&1

# 5. Create work directory
mkdir -p /work/results
cd /work

# 6. Watchdog
MAX_SECONDS=$(awk "BEGIN{printf \"%d\", $MAX_HOURS * 3600}")
(
    sleep "$MAX_SECONDS"
    echo "=== WATCHDOG: ${MAX_HOURS}h reached ==="
    aws s3 sync /work/results/ "${S3_BASE}/results/" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION" 2>/dev/null || true
    echo "124" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION"
    shutdown -h now
) &
WATCHDOG_PID=$!

# 7. Run experiment
echo "=== Running: $EXPERIMENT_COMMAND ==="
EXIT_CODE=0
eval "$EXPERIMENT_COMMAND" >> "$LOGFILE" 2>&1 || EXIT_CODE=$?

echo "=== Experiment finished: exit code $EXIT_CODE ==="
kill $WATCHDOG_PID 2>/dev/null || true

# 8. Upload results + shutdown
aws s3 sync /work/results/ "${S3_BASE}/results/" --region "$AWS_DEFAULT_REGION" || true
aws s3 cp "$LOGFILE" "${S3_BASE}/experiment.log" --region "$AWS_DEFAULT_REGION"
echo "$EXIT_CODE" | aws s3 cp - "${S3_BASE}/exit_code" --region "$AWS_DEFAULT_REGION"

FINAL_EXIT_CODE=$EXIT_CODE
shutdown -h now
