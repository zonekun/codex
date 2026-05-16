# Claude Code <-> Codex Message Board

Append-only message board. All writes use `printf >>` (no Read/Edit required).

## Format Rules

### New task entry

```
## TASK: <short-id> <timestamp>
- from: Claude Code | Codex | User
- to: Claude Code | Codex
- 関連計画MD: <path or N/A>

<task body — free-form markdown>
```

### Mark done

Append one line (do NOT edit the original task block):

```
DONE: <short-id> <timestamp> [optional one-line summary]
```

### Result report

Append a block referencing the task:

```
## RESULT: <short-id> <timestamp>

<result body>
```

## How to find active tasks

Active = has a `## TASK:` line but no matching `DONE:` line with the same short-id.

```bash
# Show active task IDs (bash example)
grep -oP '(?<=^## TASK: )\S+' docs/codex-to-claude-handoff.md | while read id; do
  grep -q "^DONE: $id " docs/codex-to-claude-handoff.md || echo "$id"
done
```

## Conventions

- short-id: kebab-case, unique enough (e.g. `supply-chain-dedup`, `tob-paired-ttest`)
- timestamp: ISO-like `YYYY-MM-DD HH:MM` JST
- Do NOT delete or edit past entries. Append only.
- Codex-related messages only. Claude Code terminal-to-terminal uses `docs/terminal-relay.md`.
- Commit & push after every append so the other side sees it.

---

## TASK: supply-chain-dedup 2026-05-16 19:30
- from: Claude Code
- to: Codex
- 関連計画MD: docs/plans/analysis-013_supply_chain_earnings_cascade_20260516_191442.md

サプライチェーンマスタCSVの名寄せ誤マッチ（30件超）を修正し、連想ペアCSVを再生成。

### フルパス

| ファイル | フルパス |
|---------|---------|
| 計画MD | C:\gdrive\claude\investment-agent\docs\plans\analysis-013_supply_chain_earnings_cascade_20260516_191442.md |
| supply_chain.csv | C:\gdrive\claude\investment-agent\data\master\supply_chain.csv |
| association_pairs.csv | C:\gdrive\claude\investment-agent\data\master\association_pairs.csv |
| build_association_pairs.py | C:\gdrive\claude\investment-agent\scripts\build_association_pairs.py |
| 知見MD (013) | C:\gdrive\claude\investment-agent\docs\knowledges\analysis\013_supply_chain_earnings_cascade.md |
| BQ銘柄マスタ | gmailpj-357912.STOCK.stock_code_list |
| EDINET有報チャンク | gmailpj-357912.STOCK.ir_documents_enhanced |

### やること

**計画MD（上記フルパス）を必ず先に読み、類型1〜7の全リストと検証手法セクションを把握してから作業開始すること。**

1. **全量レビュー**: supply_chain.csv の match_score=90 の全エントリを精査し、WRONG / VERIFY / OK に3分類
2. **VERIFYエントリ裏取り（11件）**: 計画MD §要検証エントリの検証手法 に記載の手段A/B/Cに従い検証
3. **supply_chain.csv 一括修正**:
   - WRONG → 正しい customer_code / matched_stock_name に書き換え
   - 帰属先不明 → NONLISTED / PRIVATE_FOREIGN
   - match_score → 修正したものは 0 に変更（手動修正であることを示す）
4. **重複エントリ統合**: 計画MD 類型6の4組。revenue_pctが大きい方を残し、もう一方を削除
5. **association_pairs.csv 再生成**:
   ```powershell
   $env:PYTHONUTF8='1'
   C:\gdrive\claude\investment-agent\.venv\Scripts\python.exe C:\gdrive\claude\investment-agent\scripts\build_association_pairs.py
   ```
6. **差分サマリ作成**: 修正前後の行数変化、主要な変更ペアを報告

### 修正ルール

- customer_code を修正する場合、matched_stock_name も正しい名称に変更
- 正しいマッチ先が上場企業なら正しい銘柄コードを設定
- 正しいマッチ先が非上場なら NONLISTED、外国非上場なら PRIVATE_FOREIGN
- 正しいマッチ先が上場企業の子会社なら <親コード>_SUB（例: 7267_SUB）
- source 列、revenue_pct 列、relationship 列は変更しない

### 報告事項

1. match_score=90 の全件レビュー結果（WRONG / VERIFY→結論 / OK の件数内訳）
2. 修正件数（類型別）
3. 重複統合件数
4. VERIFY 11件の個別判定結果と根拠
5. association_pairs.csv の行数変化（修正前 → 修正後）
6. 主要な変更ペア（削除された偽ペア、新たに正しくなったペアの代表例）
7. エラーがあれば詳細

### 注意事項

- Gemini API 使用禁止
- C:\venvs\investment-agent が存在しない場合は C:\gdrive\claude\investment-agent\.venv\Scripts\python.exe を使用
- supply_chain.csv は Claude Code 側 master のワーキングツリーを直接編集（Codexリポジトリ側ではない）
- 計画MDの作業ステップ6（013知見MD更新）と7（差分確認のコミット）はClaude Code側で実施するため、Codexは手を出さない
