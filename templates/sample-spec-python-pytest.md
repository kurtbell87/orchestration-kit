# Calculator — Python + pytest

## Overview

Build a simple calculator library in Python with pytest tests.

## Language & Tooling

- **Language:** Python 3
- **Test framework:** pytest
- **Build command:** `true` (no build step needed for pure Python)
- **Test command:** `pytest`

## Build Steps

### Step 1: Core Operations

Implement `src/calculator.py` with functions:

- `add(a, b)` — returns `a + b`
- `subtract(a, b)` — returns `a - b`
- `multiply(a, b)` — returns `a * b`
- `divide(a, b)` — returns `a / b`, raises `ZeroDivisionError` if `b == 0`

**Acceptance criteria:**
- All functions accept int or float arguments and return float-compatible results.
- `divide(1, 0)` raises `ZeroDivisionError`.
- Tests in `tests/test_calculator.py` cover each function with at least 2 cases, including edge cases (negative numbers, zero).

### Step 2: Expression Evaluation

Implement `src/evaluator.py` with:

- `evaluate(expression: str) -> float` — parses and evaluates simple arithmetic expressions containing `+`, `-`, `*`, `/` and parentheses.
- Uses the calculator module for actual computation.
- Raises `ValueError` for malformed expressions.

**Acceptance criteria:**
- `evaluate("2 + 3")` returns `5.0`
- `evaluate("(10 - 4) * 2")` returns `12.0`
- `evaluate("10 / 0")` raises `ZeroDivisionError`
- `evaluate("2 +")` raises `ValueError`
- Tests in `tests/test_evaluator.py` cover the above cases plus at least one nested-parentheses case.
