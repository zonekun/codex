# Codex Long-Running Work

This file defines Codex-side rules for long-running jobs, monitoring, batch processing, and data processing.

## Monitoring Means Action

Do not answer only "I will monitor" or "I will watch" unless an actual monitoring process, job, log check, or scheduled check has been started or explicitly planned.

Long-running monitoring must check both:

- terminal state
- progress metrics

Stall detection is based on unchanged metrics, not only expected elapsed time.

## Active Context

For long-running jobs, monitoring, batch processing, or data processing, create or update:

```text
data/logs/codex_active_context.md
```

Record:

- monitoring target
- command
- log path
- success condition
- next check item

Before switching tasks, update the same file. When resuming monitoring, read it before relying on chat history.

## Execution Change

When an existing tool, script, runbook, or documented procedure exists and Codex chooses not to use it, explain the change before acting.

State:

- why the existing procedure is not being used
- what alternative will be used
- what the alternative will create, change, delete, or leave behind
- whether later operations, manifests, baselines, or handoff are affected

Do not silently replace an established workflow with an ad-hoc command.

## Time And Storage

- Display and judge timestamps in JST.
- Convert API, BigQuery, gcloud, MCP, and log timestamps to JST before using them for judgment.
- Use local temporary storage for downloads.
- Do not put temporary files in cloud-synced folders.
- In long-running batches, delete per item where practical instead of accumulating all files.
- Watch disk free space.
- Resume-capable jobs should skip already processed data based on existing records.
