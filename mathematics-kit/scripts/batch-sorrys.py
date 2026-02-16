#!/usr/bin/env python3
"""
batch-sorrys.py — Group sorrys into provable batches.

Usage: ./scripts/enumerate-sorrys.sh | python3 scripts/batch-sorrys.py [--batch-size N]

Groups sorrys by file, then splits into batches of N (default 5).
Outputs JSON: one batch per line.
"""

import json
import sys
from collections import defaultdict

batch_size = 5
args = sys.argv[1:]
if '--batch-size' in args:
    idx = args.index('--batch-size')
    batch_size = int(args[idx + 1])

sorrys = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        sorrys.append(json.loads(line))
    except json.JSONDecodeError:
        continue

by_file = defaultdict(list)
for s in sorrys:
    by_file[s['file']].append(s)

batches = []
current_batch = []
current_files = set()

for filepath, file_sorrys in sorted(by_file.items()):
    if len(current_batch) + len(file_sorrys) > batch_size and current_batch:
        batches.append({
            'batch': len(batches) + 1,
            'sorrys': current_batch,
            'files': sorted(current_files),
        })
        current_batch = []
        current_files = set()

    current_batch.extend(file_sorrys)
    current_files.add(filepath)

if current_batch:
    batches.append({
        'batch': len(batches) + 1,
        'sorrys': current_batch,
        'files': sorted(current_files),
    })

for batch in batches:
    print(json.dumps(batch))
