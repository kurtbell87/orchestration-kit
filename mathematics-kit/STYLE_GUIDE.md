# Mathlib4 Style & Review Standards Reference

Compiled from official Mathlib contribution guidelines for use in AI-assisted formalization pipelines.

Sources:
- https://leanprover-community.github.io/contribute/style.html
- https://leanprover-community.github.io/contribute/naming.html
- https://leanprover-community.github.io/contribute/doc.html
- https://leanprover-community.github.io/contribute/commit.html
- https://leanprover-community.github.io/contribute/pr-review.html

---

## 1. File Structure

### 1.1 File Header (REQUIRED)

Every file must begin with:

```lean
/-
Copyright (c) 2026 Kenoma Labs LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Brandon [LastName]
-/
import Mathlib.Some.Import
import Mathlib.Another.Import
```

Rules:
- Use `Authors` (plural) even for a single author
- No period at end of Authors line
- Separate author names with commas (no `and`)
- Imports immediately after header, no blank line, one per line

### 1.2 Module Docstring (REQUIRED)

Immediately after imports, add a module docstring:

```lean
/-!
# Title of the File

Summary of contents.

## Main definitions

- `myDef`: description

## Main results

- `my_theorem`: description (indent continuation lines by 2 spaces)

## Notation

- `|_|` : description

## References

See [Author2024] for details.

## Tags

keyword1, keyword2, keyword3
-/
```

Section order: Main definitions → Main statements → Notation → Implementation notes → References → Tags.
Use `/-!` and `-/` on their own lines. First-level header for title. Second-level for sections.

### 1.3 File Names

Use `UpperCamelCase.lean`. Rare exceptions for specifically lowercased objects (e.g., `lp.lean`).

---

## 2. Naming Conventions

### 2.1 Capitalization Rules

| Kind | Convention | Example |
|------|-----------|---------|
| Theorems/lemmas (terms of `Prop`) | `snake_case` | `mul_comm`, `add_le_add_left` |
| Types, structures, classes (`Prop`/`Type`/`Sort`) | `UpperCamelCase` | `CommMonoid`, `IsTopologicalRing` |
| Functions | Same as return type | If returns `Prop` → `snake_case`; if returns `Type` → `UpperCamelCase` |
| Other terms of `Type` | `lowerCamelCase` | `toFun`, `instOrderBot` |
| UpperCamelCase inside snake_case | `lowerCamelCase` | `MonoidHom.toOneHom_injective` |
| Acronyms (e.g., `LE`) | Grouped upper/lower | `LE.trans` but `le_iff_lt_or_eq` |

### 2.2 Theorem Naming Dictionary

#### Logic
| Symbol | Name |
|--------|------|
| `∨` | `or` |
| `∧` | `and` |
| `→` | `of` / `imp` (conclusion first, hypotheses often omitted) |
| `↔` | `iff` |
| `¬` | `not` |
| `∃` | `exists` |
| `∀` | `all` / `forall` |
| `=` | `eq` (often omitted) |
| `≠` | `ne` |
| `∘` | `comp` |

#### Algebra
| Symbol | Name |
|--------|------|
| `0` | `zero` |
| `+` | `add` |
| `-` (unary) | `neg` |
| `-` (binary) | `sub` |
| `1` | `one` |
| `*` | `mul` |
| `^` | `pow` |
| `/` | `div` |
| `•` | `smul` |
| `⁻¹` | `inv` |
| `∣` | `dvd` |

#### Lattices/Order
| Symbol | Name |
|--------|------|
| `<` | `lt` / `gt` |
| `≤` | `le` / `ge` |
| `⊔` | `sup` |
| `⊓` | `inf` |
| `⊥` | `bot` |
| `⊤` | `top` |

Use `ge`/`gt` when: arguments are swapped from first `≤`/`<` occurrence, matching argument order of `=`/`≠`, or second argument is "more variable."

#### Set
| Symbol | Name |
|--------|------|
| `∈` | `mem` |
| `∪` | `union` |
| `∩` | `inter` |
| `ᶜ` | `compl` |
| `\` | `sdiff` |
| `⋃` | `iUnion` / `biUnion` |
| `⋂` | `iInter` / `biInter` |

### 2.3 Naming Patterns

- Hypotheses listed in order they appear: `A → B → C` → `C_of_A_of_B`
- Abbreviations: `pos` (not `zero_lt`), `neg`, `nonpos`, `nonneg`
- `left`/`right` for variants: `add_le_add_left`, `add_le_add_right`
- Infix follows expression order: `neg_mul_neg` not `mul_neg_neg`

### 2.4 Structural Lemma Names

- `(∀ x, f x = g x) → f = g` → `.ext` (tag with `@[ext]`)
- `f = g ↔ ∀ x, f x = g x` → `.ext_iff`
- Injectivity: `Function.Injective f` → `f_injective`; `f x = f y ↔ x = y` → `f_inj`
- Axiomatic names: `refl`, `symm`, `trans`, `comm`, `assoc`, `left_comm`, `right_comm`, `congr`

### 2.5 Prop-valued Classes

- Nouns: prefix with `Is` (e.g., `IsTopologicalRing`)
- Adjectives: `Is` optional (e.g., `Normal` for normal subgroups is fine)

### 2.6 Spelling

Use **American English** in declaration names: `factorization` not `factorisation`, `Localization` not `Localisation`.

---

## 3. Code Style

### 3.1 Line Length

**Maximum 100 characters per line.**

### 3.2 Formatting Rules

- Spaces on both sides of `:`, `:=`, and infix operators
- Put `:`, `:=` before line breaks, not at start of next line
- Indent proof body by 2 spaces
- Indent continuation of theorem statement by 4 spaces
- Proof still indented only 2 spaces (not 4+2)
- `by` goes at end of previous line, never on its own line (`:= by`)
- Don't orphan parentheses
- No empty lines inside declarations (linter enforced)
- Use `fun x ↦ ...` not `λ x ↦ ...`
- Use `<|` not `$`

### 3.3 Tactic Mode

- Each tactic on its own line (generally)
- Focusing dot `·` not indented; content after dot is indented
- `<;>` for applying tactic to all goals: one line or indent next
- Short single-line tactic proofs with semicolons are acceptable
- `swap` or `pick_goal` for short side goals to avoid deep indentation

### 3.4 Calc Blocks

- `calc` keyword at end of preceding line
- Relations aligned across lines
- Underscores `_` left-justified
- Justifications after `:=`

### 3.5 Structure/Class Definitions

- Use `where` syntax for instances
- Fields indented 2 spaces
- Every field must have a docstring

### 3.6 Hypotheses Position

Prefer arguments left of colon over universal quantifiers when proof starts by introducing them:

```lean
-- PREFERRED:
example (n : ℝ) (h : 1 < n) : 0 < n := by linarith

-- AVOID:
example (n : ℝ) : 1 < n → 0 < n := fun h ↦ by linarith
```

### 3.7 Simp Calls

**Do NOT squeeze terminal `simp` calls** (ones that close the goal or are followed only by `ring`/`field_simp`/`aesop`). Reasons:
- Squeezed calls are verbose and drown useful info
- They break on lemma renames (maintenance burden)

### 3.8 Variable Conventions

| Variable | Usage |
|----------|-------|
| `u`, `v`, `w` | universes |
| `α`, `β`, `γ` | generic types |
| `a`, `b`, `c` | propositions |
| `x`, `y`, `z` | elements of generic type |
| `h`, `h₁` | assumptions |
| `p`, `q`, `r` | predicates and relations |
| `s`, `t` | lists, sets |
| `m`, `n`, `k` | natural numbers |
| `i`, `j`, `k` | integers |
| `G` | group, `R` ring, `K`/`𝕜` field, `E` vector space |

### 3.9 Use `Type*` Not `Type _`

Always `α β : Type*` for arbitrary universe levels. `Type _` causes unification performance issues.

---

## 4. Documentation Requirements

### 4.1 Doc Strings

- **Every definition MUST have a doc string** (linter: `docBlame`)
- Important theorems SHOULD have doc strings (linter: `docBlameThm`)
- Delimited with `/-- ... -/`
- Do not indent subsequent lines
- Complete sentences end with periods
- Named theorems in **bold**: `**mean value theorem**`
- May "lie slightly" about implementation to convey mathematical meaning
- Use backticks for Lean identifiers: `` `myDef` ``
- Use fully qualified names for auto-linking in docs: `` `Finset.card_pos` ``
- LaTeX: `$ ... $` inline, `$$ ... $$` display mode
- Raw URLs in angle brackets: `<https://example.com>`

### 4.2 Sectioning Comments

Use `/-! ... -/` for section headers (these appear in generated docs).
Use third-level headers `###` inside sectioning comments.
Use `/- ... -/` for technical comments (TODOs, implementation notes).
Use `--` for inline comments.

### 4.3 Proof Documentation

Complex proofs should have interspersed comments explaining the strategy. This is one of the most common review requests for non-trivial proofs.

---

## 5. PR/Commit Conventions

### 5.1 Title Format

```
<type>(<optional-scope>): <subject>
```

Types: `feat`, `fix`, `doc`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`

Scope: Module path without `Mathlib` prefix (e.g., `Probability/Distributions/CLT`)

Subject: imperative present tense, no capital first letter, no trailing period.

### 5.2 Description

- Imperative present tense
- Motivation and contrast with previous behavior
- Breaking changes in footer
- Issue references: `Closes #123`
- Dependencies: `- [ ] depends on: #XXXX`
- Discussion questions go after `---` (excluded from git history)

### 5.3 PR Size

**Small, self-contained PRs** are strongly preferred. Split large additions into multiple PRs. This is especially important for new contributors.

---

## 6. Review Checklist (What Reviewers Check)

### 6.1 Style (Easy — Caught First)
- [ ] Code formatting matches style guide
- [ ] Naming conventions followed
- [ ] PR title/description informative
- [ ] Lines ≤ 100 characters
- [ ] `Type*` not `Type _`

### 6.2 Documentation (Easy-Medium)
- [ ] All definitions have doc strings
- [ ] Important theorems have doc strings
- [ ] Module docstring present with title, summary, main results
- [ ] Cross-references to related declarations
- [ ] Complex proofs have interspersed comments
- [ ] Literature references added to `references.bib` if applicable

### 6.3 Location (Medium)
- [ ] Declarations in appropriate files (`#find_home` check)
- [ ] Results don't already exist (`exact?` / `apply?` check)
- [ ] No unnecessary imports introduced
- [ ] File not too long (>1000 lines → consider splitting)

### 6.4 Improvements (Medium-Hard)
- [ ] Long proofs split into supporting lemmas
- [ ] Good tactic choices (e.g., `gcongr` over manual `mul_le_mul_of_nonneg_left`)
- [ ] No unnecessary squeezing of terminal `simp`
- [ ] Proof structure isn't needlessly complex

### 6.5 Library Integration (Hard)
- [ ] Sensible API: `@[simp]`, `@[ext]`, `@[gcongr]` attributes where appropriate
- [ ] New definitions come with basic API lemmas (at minimum `@[simps]`)
- [ ] Sufficiently general (not duplicating existing results at lower generality)
- [ ] No instance diamonds introduced
- [ ] Follows existing library design patterns (`FunLike`, `SetLike`, bundled morphisms)

### 6.6 Performance
- [ ] No significant performance regressions (use `!bench` on PR)
- [ ] Consider transparency: default `semireducible` unless documented reason
- [ ] Avoid `erw` / `rfl` after `simp`/`rw` (indicates missing API)

---

## 7. Deprecation Protocol

When renaming or removing declarations:

```lean
theorem new_name : ... := ...
@[deprecated (since := "YYYY-MM-DD")] alias old_name := new_name
```

Or with message:
```lean
@[deprecated "Use X with Y instead" (since := "YYYY-MM-DD")]
theorem old_thing ...
```

Named instances don't require deprecations. Deprecated declarations deleted after 6 months.

---

## 8. Normal Forms

Mathlib settles on canonical forms for equivalent statements:
- `s.Nonempty` over other nonemptiness formulations
- `(a : Option α)` over `Some a`
- In types with `⊥`: use `x ≠ ⊥` in **assumptions**, `⊥ < x` in **conclusions**
- In types with `⊤`: use `x ≠ ⊤` in **assumptions**, `x < ⊤` in **conclusions**
- Register `simp` lemmas to convert non-normal forms to normal forms

---

## 9. Pipeline Integration Notes

For an AI formalization system producing Mathlib-ready output, the following post-processing checks should be automated:

1. **Header generation**: Auto-generate copyright header with correct date and authors
2. **Naming lint**: Validate all declaration names against capitalization rules (Prop → snake_case, Type → UpperCamelCase)
3. **Doc string enforcement**: Every `def`, `structure`, `class`, `instance` must have `/-- ... -/`
4. **Line length check**: No line exceeds 100 characters
5. **Import minimization**: Use `#find_home` / `#minimize_imports` to verify imports aren't pulling too much
6. **Duplicate detection**: Run `exact?` on theorem statements to check for pre-existing results
7. **Style linter**: Run `#lint` at end of file to catch all automated checks
8. **Module docstring**: Ensure `/-! ... -/` block present with title and main results
9. **Tactic style**: Verify `by` placement (end of previous line), focusing dot usage, no `λ`
10. **Terminal simp**: Don't squeeze terminal `simp` calls

### Recommended Hook Additions

```bash
# Post-PROVE phase: Mathlib compliance check
mathlib_lint() {
  local file="$1"

  # Check line length
  awk 'length > 100 {print NR": "length" chars: "$0}' "$file"

  # Check for lambda instead of fun
  grep -nE '\\bλ\\b' "$file" && echo "ERROR: Use 'fun' not 'λ'"

  # Check for Type _ instead of Type*
  grep -nE 'Type _' "$file" && echo "WARNING: Use Type* not Type _"

  # Check doc strings on definitions
  # (simplified - real check would need AST parsing)
  grep -nE '^(def|structure|class|instance) ' "$file" | while read line; do
    lineno=$(echo "$line" | cut -d: -f1)
    prev=$((lineno - 1))
    sed -n "${prev}p" "$file" | grep -qE '^\s*-/' || \
      echo "WARNING: Line $lineno: definition may be missing doc string"
  done

  # Check module docstring exists
  grep -qE '^/-!' "$file" || echo "ERROR: Missing module docstring /-! ... -/"

  # Check copyright header
  head -1 "$file" | grep -qE '^/-$' || echo "ERROR: Missing copyright header"
}
```
