# Experiment: [descriptive name]

## Hypothesis
<!-- A falsifiable statement with direction AND magnitude.
NOT: "X might help"
YES: "X will improve Y by at least Z% over baseline B" -->

## Independent Variables
<!-- What you are changing. Be specific — exact parameter names, value ranges. -->

## Controls
<!-- What stays fixed — and WHY each control is necessary.
Include software version, random seed strategy, hardware. -->

## Metrics (ALL must be reported)

### Primary
<!-- The metric(s) that directly test the hypothesis. Max 2. -->

### Secondary
<!-- Metrics that help interpret the primary result. -->

### Sanity Checks
<!-- Metrics that should NOT change, or should change in a known direction. -->

## Baselines
<!-- What you are comparing against. Where the baseline numbers come from. -->

## Success Criteria (immutable once RUN begins)
- [ ] _Criterion 1: [metric] [direction] [threshold] over [baseline]_
- [ ] _Criterion 2: ..._
- [ ] No regression on sanity checks beyond _[tolerance]_
- [ ] Results reproducible across _[N]_ seeds with std < _[threshold]_

## Minimum Viable Experiment
<!-- Smallest version that meaningfully tests the hypothesis.
E.g., "overfit to one sample" or "run on smallest environment first."
The RUN agent executes this BEFORE the full protocol. -->

## Full Protocol
<!-- Step-by-step instructions for the RUN agent.
1. Reproduce the baseline
2. Run the minimum viable experiment
3. If MVE passes sanity checks, run the full experiment
4. ... -->

## Resource Budget
<!-- Tier determines protocol complexity. Quick ≤15min, Standard ≤2h, Heavy ≤24h.
Estimate wall time from actual data size before committing. -->
**Tier:** _Quick | Standard | Heavy_
- Max GPU-hours: _N_
- Max wall-clock time: _N_
- Max training runs: _N_
- Max seeds per configuration: _N_

### Compute Profile
<!-- Parsed by tools/preflight to recommend local vs cloud execution.
     Fill in what you know; leave unknowns as 0 or "none".
     compute_type: cpu (XGBoost, sklearn, polars) or gpu (PyTorch, CUDA workloads)
     estimated_wall_hours: rough estimate based on data size and fit count -->
```yaml
compute_type: cpu
estimated_rows: 0
model_type: other
sequential_fits: 0
parallelizable: false
memory_gb: 0
gpu_type: none
estimated_wall_hours: 0
```

## Abort Criteria
<!-- When to stop early. Per-run time thresholds must use 3-5x expected
time based on actual data size. Unrealistically tight time aborts cause
kill-restart cycles that waste more time than they save.
- Loss diverges (NaN or > threshold) for N consecutive steps
- Primary metric shows no improvement over baseline after N% of budget
- Sanity check metric regresses beyond tolerance -->

## Confounds to Watch For
<!-- Known risks to validity. What could make a positive result misleading? -->
