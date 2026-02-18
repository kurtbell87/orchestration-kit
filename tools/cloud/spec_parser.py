"""Parse compute profile YAML block from an experiment spec markdown file."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ComputeProfile:
    """Structured compute profile extracted from an experiment spec."""

    compute_type: str = "cpu"                # cpu | gpu
    estimated_rows: int = 0
    model_type: str = "other"                # xgboost, sklearn, pytorch, polars, other
    sequential_fits: int = 0
    parallelizable: bool = False
    memory_gb: float = 0.0
    gpu_type: str = "none"                   # none, any, A100, H100
    estimated_wall_hours: float = 0.0

    # Resource budget fields (from parent section)
    tier: str = "Quick"                      # Quick, Standard, Heavy
    max_gpu_hours: float = 0.0
    max_wall_clock: str = ""
    max_training_runs: int = 0


def parse_spec(spec_path: str | Path) -> ComputeProfile:
    """Extract compute profile from an experiment spec file.

    Looks for a fenced YAML block (```yaml ... ```) under a
    '### Compute Profile' heading. Also extracts the tier from
    the '## Resource Budget' section.
    """
    text = Path(spec_path).read_text()
    profile = ComputeProfile()

    # --- Extract tier from Resource Budget section ---
    tier_match = re.search(r"\*\*Tier:\*\*\s*(\w+)", text)
    if tier_match:
        profile.tier = tier_match.group(1).strip()

    # --- Extract max wall-clock ---
    wall_match = re.search(r"Max wall-clock time:\s*(.+)", text)
    if wall_match:
        profile.max_wall_clock = wall_match.group(1).strip().rstrip("_")

    # --- Extract max GPU-hours ---
    gpu_match = re.search(r"Max GPU-hours:\s*(\d+(?:\.\d+)?)", text)
    if gpu_match:
        profile.max_gpu_hours = float(gpu_match.group(1))

    # --- Extract max training runs ---
    runs_match = re.search(r"Max training runs:\s*(\d+)", text)
    if runs_match:
        profile.max_training_runs = int(runs_match.group(1))

    # --- Extract YAML compute profile block ---
    # Match a fenced YAML block after "### Compute Profile"
    yaml_pattern = re.compile(
        r"###\s*Compute\s+Profile.*?```ya?ml\s*\n(.*?)```",
        re.DOTALL | re.IGNORECASE,
    )
    yaml_match = yaml_pattern.search(text)
    if not yaml_match:
        raise ValueError(
            f"Spec '{spec_path}' is missing a '### Compute Profile' YAML block. "
            "This is mandatory for all experiment specs. Add a fenced ```yaml block "
            "under '### Compute Profile' in the Resource Budget section. "
            "See research-kit/templates/experiment-spec.md for the required format."
        )

    yaml_text = yaml_match.group(1)

    # Parse YAML — use yaml if available, fall back to simple regex parsing
    try:
        import yaml
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            return profile
    except ImportError:
        data = _parse_yaml_simple(yaml_text)

    # Map YAML fields to profile
    if "compute_type" in data:
        profile.compute_type = str(data["compute_type"]).lower()
    if "estimated_rows" in data:
        profile.estimated_rows = int(data["estimated_rows"])
    if "model_type" in data:
        profile.model_type = str(data["model_type"]).lower()
    if "sequential_fits" in data:
        profile.sequential_fits = int(data["sequential_fits"])
    if "parallelizable" in data:
        profile.parallelizable = _parse_bool(data["parallelizable"])
    if "memory_gb" in data:
        profile.memory_gb = float(data["memory_gb"])
    if "gpu_type" in data:
        profile.gpu_type = str(data["gpu_type"]).lower()
    if "estimated_wall_hours" in data:
        profile.estimated_wall_hours = float(data["estimated_wall_hours"])

    return profile


def _parse_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "yes", "1")


def _parse_yaml_simple(text: str) -> dict:
    """Fallback YAML parser for simple key: value lines (no nesting)."""
    data = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        # Attempt numeric conversion
        if val.lower() in ("true", "false"):
            data[key] = val.lower() == "true"
        else:
            try:
                data[key] = int(val)
            except ValueError:
                try:
                    data[key] = float(val)
                except ValueError:
                    data[key] = val
    return data
