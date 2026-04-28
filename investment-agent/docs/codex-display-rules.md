# Codex Display Rules

Created: 2026-04-26

This file records Codex-only presentation rules. It is intentionally named
`docs/codex-*.md` because `docs/claude-md-sync.md` excludes these files from
Claude Code markdown mirroring. Do not move these rules into `CLAUDE.md` or
`docs/knowledges/` unless the user explicitly asks to change Claude Code-side
operation.

## Default Table Format

When showing tabular lists in Codex, default to fixed-width fenced code blocks
instead of Markdown tables.

Reason: Markdown tables are frequently hard to read in Codex when rows contain
Japanese text, mixed-width characters, long names, or narrow numeric columns.
Fixed-width code blocks keep the visual layout stable.

Use Markdown tables only when the table is tiny, all cells are short ASCII, and
the user explicitly asks for a Markdown table.

Default format:

```text
区分     銘柄                         Q    Score  予測      実績      騰落率
ハズレ   3091 ブロンコビリー           1Q       6  UP        NEUTRAL   +0.12%
的中     4684 オービック               FY       3  UP        UP       +10.62%
```

Keep columns short and stable. Prefer a compact fixed set of columns over a wide
table with many optional fields. If grouping improves readability, split the
output into separate fixed-width blocks by group.

## Earnings Answer Check

For earnings prediction answer-check results, use three groups in this order:

1. `[ハズレ]`
2. `[どちらともいえない]`
3. `[的中]`

Render each group as a fixed-width fenced code block (`text`), not a Markdown
table. Keep columns short and stable:

```text
[ハズレ]
銘柄                         Q    Score  予測      実績      騰落率
3091 ブロンコビリー           1Q       6  UP        NEUTRAL   +0.12%
4733 OBC                     FY       1  NEUTRAL   DOWN      -4.28%
```

Grouping rule:

- `ハズレ`: directional prediction and actual category differ in a meaningful way.
- `どちらともいえない`: mostly NEUTRAL/NEUTRAL rows or rows whose usefulness is unclear.
- `的中`: clear directional hits such as UP/UP or DOWN/DOWN.

This is a Codex display workaround only. Claude Code does not currently need
this rule because the display issue has not appeared there.
