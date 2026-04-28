# AGENTS.md

This file defines workspace-level working rules for Codex.

## Startup Instruction Discovery Rule

At the start of any task in this workspace, inspect repository-local instruction files before doing task-specific work.

1. Find the active project directory from the user's request and the current working directory.
2. Look for cloned Claude/Codex instruction files, especially `CLAUDE.md`, `AGENTS.md`, and `docs/codex-operation-knowledge.md`.
3. Read the nearest applicable `AGENTS.md` and the project `CLAUDE.md` before running project workflows, notebooks, batch jobs, or data analysis.
4. If the task mentions an existing notebook, a book/notebook that is already run, answer checking, earnings forecast prediction, or another established workflow, search `CLAUDE.md` first for the task-specific mandatory files.
5. If an expected cloned Claude instruction file is missing or unreadable, say that explicitly and continue with the best local project documentation available.

Do not rely on memory or chat history as the source of truth for project operation rules.

## Finding Validation Rule

Before calling an observation a "bug", "problem", "regression", "duplicate", "abnormal", or similar defect, validate the intended meaning and scope from the relevant code, schema, documentation, workflow, or user instruction.

1. Identify the expected unit of behavior explicitly before judging the result. Examples include business key/grain for data, ownership boundary for files, branch/repository for Git work, lifecycle/state for workflows, and API contract for code.
2. Separate facts from interpretation. Report what was observed first, then label it as a defect only after confirming it conflicts with the expected behavior.
3. Treat incomplete or coarse checks as provisional. If a check omits a relevant dimension, say so and do not present the result as a confirmed issue.
4. Prefer neutral language such as "needs confirmation", "possible risk", or "candidate issue" until the expected behavior has been verified.
5. When the user corrects the expected meaning or scope, acknowledge the correction, update the check, and avoid carrying forward the earlier interpretation.
