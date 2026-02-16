# Construction Spec: [Name]

## Domain
<!-- What mathematical objects/structures are we working with? -->
<!-- e.g., "Partially ordered sets with a least element" -->


## Goal
<!-- What construction or theorem are we building? -->
<!-- e.g., "Construct a complete lattice from a directed-complete partial order" -->


## Required Properties
<!-- Each property stated precisely in mathematical English. Number them. -->
<!-- e.g., -->
<!-- 1. For all x, y in S: x ≤ y ∧ y ≤ x → x = y (antisymmetry) -->
<!-- 2. The supremum of any directed subset exists -->

1.
2.
3.

## Cases & Edge Cases
<!-- Boundary conditions, degenerate cases, special values -->
<!-- e.g., "Empty set case", "Singleton set", "Infinite ascending chain" -->

-
-

## Constraints
<!-- What must NOT hold (negative requirements) -->
<!-- e.g., "Must NOT assume decidable equality" -->

-

## Mathlib Dependencies
<!-- Expected Mathlib imports and key types -->
<!-- e.g., Mathlib.Order.CompleteLattice, Mathlib.Order.Directed -->

-

## Success Criteria
<!-- How do we know the formalization is complete? -->
<!-- e.g., "All theorems compile without sorry, axiom, or native_decide" -->

- [ ] All required properties are formalized as Lean4 theorems
- [ ] `lake build` succeeds with zero `sorry` warnings
- [ ] Zero `axiom` declarations
- [ ] Zero `native_decide` usage
- [ ] All edge cases covered by theorems or decidability proofs
