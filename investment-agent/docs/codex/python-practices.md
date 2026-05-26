# Codex Python Practices

Created: 2026-05-26
Project: `investment-agent`
Owner: Codex-side practice document

## Purpose

This file defines the default Python quality bar for Codex work.
It adapts Google Python Style Guide and Google Engineering Practices.
It is not a replacement for local workflow runbooks.
It is not a Python environment guide.
Use `docs/codex/uv-setup.md` for interpreter and uv rules.
Use this file for Python code shape, review, tests, and maintainability.
Do not edit Claude-owned `CLAUDE.md` for Codex-only Python rules.

## Primary Sources

- Google Python Style Guide: https://google.github.io/styleguide/pyguide.html
- Google Engineering Practices: https://google.github.io/eng-practices/
- Code review overview: https://google.github.io/eng-practices/review/
- Standard of Code Review: https://google.github.io/eng-practices/review/reviewer/standard.html
- What to Look For in a Code Review: https://google.github.io/eng-practices/review/reviewer/looking-for.html
- Writing Good CL Descriptions: https://google.github.io/eng-practices/review/developer/cl-descriptions.html
- Small CLs: https://google.github.io/eng-practices/review/developer/small-cls.html

## Core Bar

- Code must be importable without running the workflow.
- Top-level code must not connect to BQ, GCS, APIs, or write files.
- Executable scripts must put operational logic under `main()`.
- Executable scripts must use `if __name__ == "__main__":`.
- Functions must have clear inputs and outputs.
- External state must be passed in or isolated at the edge.
- Hidden global state must be avoided.
- Mutable global state must not control workflow behavior.
- Re-run behavior must be explicit for batch jobs.
- Failure behavior must be visible in logs, exceptions, or output artifacts.

## Change Size

- One change should address one thing.
- Separate refactoring from behavior changes.
- Separate formatting-only changes from behavior changes.
- Keep review diffs small enough to understand.
- A 100-line core diff is usually easier to review.
- A 1000-line core diff should normally be split.
- Include related tests with the production change.
- Include a usage example when adding a new public API.
- Do not add unused APIs for speculative future work.
- If a large change is unavoidable, document the boundaries first.

## Design

- Read the local runbook before changing workflow code.
- Check existing helpers before writing a new helper.
- Check existing data models before creating new shapes.
- Keep ingestion, transformation, validation, and output separate.
- Keep CLI parsing separate from business logic.
- Keep BQ and GCS clients at the boundary.
- Keep pure transformation functions testable.
- Avoid class hierarchies for one-off scripts.
- Add abstraction only when it removes real complexity.
- Do not introduce a framework for a small script.

## Functions

- Prefer small, focused functions.
- If a function exceeds about 40 lines, consider splitting it.
- Do not split only to satisfy a line count.
- Split when names can explain the steps.
- Split when a sub-step can be tested independently.
- Split when exception handling becomes tangled.
- Split when a loop mixes parsing, validation, and output.
- Keep all steps at the same abstraction level.
- Do not hide one-off code in nested functions only for privacy.
- Use `_private_helper` at module level for testable private helpers.

## Imports

- Import packages and modules by default.
- Avoid wildcard imports.
- Avoid relative imports in normal repository code.
- Use standard aliases such as `pd` and `np` only where conventional.
- Do not invent aliases to shorten local names.
- Sort imports by standard library, third-party, then local modules.
- Do not perform I/O at import time.
- Do not read environment variables at import time unless configuration-only.
- Use `if TYPE_CHECKING:` for type-only imports when it avoids runtime dependency.
- Remove unused imports.

## Typing

- Type public functions.
- Type CLI entry points.
- Type data transformation boundaries.
- Type return values when they are not obvious.
- Use `Any` only at a deliberate boundary.
- Prefer `TypedDict`, dataclass, or Pydantic model over raw `dict[str, Any]`.
- Use `| None` when `None` is a valid return or argument value.
- Do not use mutable default arguments.
- Use type aliases for repeated complex types.
- Do not add runtime dependencies only for typing.

## Comments and Docstrings

- Comments should explain why, not narrate what.
- Do not use comments to compensate for confusing code.
- Simplify confusing code first.
- Explain non-obvious finance or disclosure rules.
- Explain regexes that encode document structure.
- Explain TDnet, EDINET, BQ, or vendor quirks near the code.
- Add module docstrings for executable scripts.
- Add docstrings for public classes and public functions.
- Document side effects, file output, DB writes, and destructive behavior.
- Update stale comments when changing the code.

## Exceptions and Logging

- Do not use bare `except:`.
- Avoid broad `except Exception:` unless recovery is explicit.
- Use `raise ... from exc` when wrapping lower-level failures.
- Include target path, ticker, table, bucket, prefix, or as-of in error messages.
- Do not silently skip rows without recording why.
- For partial batch failure, write structured failure records.
- Prefer repository logging or structlog over scattered `print()`.
- Logs must identify target, period, row count, and output path when relevant.
- Logs must not contain secrets, API keys, or service-account JSON.
- Do not log every row in large loops.

## Files, Encoding, and Time

- Use `pathlib.Path` for paths.
- Do not build paths with string concatenation.
- Use explicit `encoding=` with `open()`.
- Use UTF-8 for Python, JSON, YAML, and Markdown created by Codex.
- Confirm real encoding for external CSV input.
- Use `PYTHONUTF8=1` when running local scripts.
- Check Japanese output for mojibake and accidental `?` replacement.
- Do not use implicit current date in reproducible analysis.
- Pass `as_of` explicitly when analysis depends on a date.
- Use JST where the repository workflow requires JST.

## DataFrame Work

- Validate required input columns before transformation.
- Keep column rename maps visible.
- Keep derived columns named by meaning.
- Avoid chained assignment.
- Reset indexes deliberately after groupby operations.
- Do not rely on incidental row order.
- Make sort keys explicit.
- Validate output row count.
- Validate output column count.
- Validate duplicate keys before writing final artifacts.

## BigQuery, GCS, and APIs

- Check schema before writing SQL against a table.
- Prefer CTEs for multi-step SQL.
- Keep JOIN predicates and filter predicates readable.
- Prefer query parameters or validated constants for dynamic values.
- Avoid correlated subqueries where BigQuery rejects the pattern.
- Use `QUALIFY ROW_NUMBER()` where it clarifies latest-row selection.
- Reuse existing GCS artifacts when the user says they already exist.
- Do not rerun prediction or generation pipelines without a reason.
- Minimize API calls and cache where the workflow already has a cache pattern.
- Verify external writes with actual row counts, paths, timestamps, or job IDs.

## Tests and Verification

- Add tests when changing shared logic.
- Add tests when fixing a bug.
- Add tests when introducing parsing rules.
- Add tests when changing scoring or classification behavior.
- Tests must fail when production behavior is broken.
- Keep tests simple and maintainable.
- Use local fixtures for pure transformations.
- Mock external services at the boundary.
- At minimum, run a syntax check for edited Python.
- Prefer `python -m py_compile` for quick syntax verification.
- Run focused tests for touched logic.
- For scripts, run a small-scope smoke test when safe.
- For generated files, verify the actual path exists.
- If verification is unsafe or unavailable, say why.
- Report residual risk in the final answer.

## Code Review Standard

- The goal is improving overall code health.
- Do not block useful improvement on perfection.
- Do not accept changes that worsen code health.
- Technical facts and data overrule preference.
- Style guides overrule personal style opinions.
- Local consistency matters when no stronger rule applies.
- Review design first.
- Review functionality from the user's perspective.
- Review complexity at line, function, class, and system level.
- Review tests as production-quality code.
- Review documentation impact when behavior changes.

## Codex Implementation Checklist

- Read `AGENTS.md`.
- Read `CLAUDE.md` for routing, but do not edit it.
- Read `docs/codex/operation-knowledge.md`.
- Read the topic runbook when a workflow is involved.
- Check existing code patterns.
- Keep the change scoped.
- Preserve unrelated dirty worktree changes.
- Avoid new dependencies unless explicitly justified.
- Avoid environment changes unless the task is environment work.
- Update Codex docs only under the documented owner path.
- Confirm the module is import-safe.
- Confirm executable behavior is under `main()`.
- Confirm important functions are typed.
- Confirm encodings and dates are explicit.
- Confirm outputs are verified.

## Prohibited Defaults

- Do not create a Codex-only venv.
- Do not create a Codex-only uv cache.
- Do not run `uv sync` by default.
- Do not run `uv lock` by default.
- Do not add dependencies only for Codex convenience.
- Do not edit Claude-owned `CLAUDE.md`.
- Do not perform external writes at import time.
- Do not hide failures behind empty outputs.
- Do not mix unrelated refactors with behavior fixes.
- Do not commit unrelated dirty files.
