# Claude Code <-> Codex message board

Bidirectional message board between Claude Code and Codex.

Use this file for:

- Codex -> Claude Code implementation results, review requests, and intake notes.
- Claude Code -> Codex task delegation, review results, and operational instructions.

Do not confuse this with Claude Code side `docs/terminal-relay.md`.
`docs/terminal-relay.md` is only for Claude Code terminal-to-terminal handoff.

## Rules

- Keep entries newest first.
- Every entry must include `from`, `to`, `status`, and `task`.
- Use `from` / `to` to distinguish direction. Valid parties are `Claude Code`, `Codex`, and `User` when needed.
- When Codex receives a `to: Codex` entry, mark it `in_progress` or `done` in this file when acting on it.
- When Claude Code receives a `to: Claude Code` entry, Claude Code should mark it `in_progress` or `done` after intake.
- Physically delete entries that are no longer needed.
- Do not put Codex-related messages in `docs/terminal-relay.md`.

---

## 2026-05-09: 株主優待 JSONL→Excel生成

- **from**: Claude Code
- **to**: Codex
- **status**: in_progress
- **task**: 中間JSONL（38件）を加工プロンプトに従いフォーマットし、加工済みJSONL→Excel を生成する

### 入力ファイル

- 中間JSONL: `C:\Users\zonekun\Documents\codex\investment-agent\data\logs\yutai_raw_202605.jsonl`（38レコード）
- 加工プロンプト: `C:\Users\zonekun\Documents\codex\investment-agent\docs\knowledges\tools\098_yutai_format_prompt.md`（v2）

### 作業手順

1. `C:\Users\zonekun\Documents\codex\investment-agent\data\logs\yutai_raw_202605.jsonl` を1行ずつ読み、加工プロンプト（`C:\Users\zonekun\Documents\codex\investment-agent\docs\knowledges\tools\098_yutai_format_prompt.md`）のルールに従い `yutai_content` フィールドを生成
2. 加工済みJSONLを `C:\Users\zonekun\Documents\codex\investment-agent\data\logs\yutai_formatted_202605.jsonl` に出力（1レコード1行、逐次追記）
3. Excel生成: `PYTHONUTF8=1 python C:\Users\zonekun\Documents\codex\investment-agent\scripts\generate_yutai_excel.py --input C:\Users\zonekun\Documents\codex\investment-agent\data\logs\yutai_formatted_202605.jsonl --output "C:\Users\zonekun\Dropbox\stock\優待"`
4. 完了後、本伝言板に完了報告＋出力Excelパスを記載

### 注意事項

- 加工プロンプトの few-shot examples を必ず参照し、除去対象・残留判定に従うこと
- 特に: 自明な利用条件は除去、代替選択肢は残留、継続保有条件は簡潔化
- Excel生成スクリプト（generate_yutai_excel.py）は既にあるのでそのまま実行
